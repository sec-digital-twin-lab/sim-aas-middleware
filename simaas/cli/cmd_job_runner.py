import asyncio
import json
import logging
import os
import re
import threading
import time
import traceback
from typing import Dict, Union, Optional, Tuple, Set, List

from simaas.namespace.default import DefaultNamespace
from simaas.nodedb.schemas import NodeInfo

from simaas.dor.protocol import P2PLookupDataObject, P2PFetchDataObject, P2PPushDataObject
from simaas.nodedb.protocol import P2PGetIdentity, P2PGetNetwork
from simaas.p2p.base import P2PAddress
from simaas.p2p.protocol import P2PLatency
from simaas.p2p.service import P2PService
from simaas.cli.exceptions import CLIRuntimeError
from simaas.cli.helpers import CLICommand, Argument, prompt_for_string, prompt_if_missing, env_if_missing
from simaas.core.exceptions import SaaSRuntimeException, ExceptionContent
from simaas.core.helpers import validate_json, hash_json_object, get_timestamp_now
from simaas.core.identity import Identity
from simaas.core.keystore import Keystore
from simaas.core.logging import Logging
from simaas.dor.schemas import ProcessorDescriptor, DataObject, GitProcessorPointer, DataObjectRecipe, CObjectNode
from simaas.rti.exceptions import InputDataObjectMissing, MismatchingDataTypeOrFormatError, \
    InvalidJSONDataObjectError, DataObjectOwnerNotFoundError, DataObjectContentNotFoundError, RTIException, \
    AccessNotPermittedError, MissingUserSignatureError, UnresolvedInputDataObjectsError
from simaas.rti.protocol import P2PRunnerPerformHandshake, P2PPushJobStatus, P2PInterruptJob, BatchBarrier
from simaas.rti.schemas import JobStatus, Severity, JobResult, ExitCode, Job, Task, BatchStatus
from simaas.core.processor import ProgressListener, ProcessorBase
from simaas.helpers import find_processors


class OutputObjectHandler(threading.Thread):
    def __init__(self, logger: logging.Logger, owner, obj_name: str):
        super().__init__()
        self._logger = logger
        self._owner: JobRunner = owner
        self._obj_name: str = obj_name

    async def push_data_object(self, obj_name: str) -> DataObject:
        # convenience variables
        task_out_items = {item.name: item for item in self._owner.job.task.output}
        task_out = task_out_items.get(obj_name)
        output_spec = self._owner.output_interface.get(obj_name)
        if task_out is None or output_spec is None:
            raise RTIException(f"Unexpected output '{obj_name}'", details={
                'task_out_items': list(task_out_items.keys()),
                'output_interface': list(self._owner.output_interface.keys())
            })

        # check if the output data object exists
        output_content_path = os.path.join(self._owner.wd_path, obj_name)
        if not os.path.isfile(output_content_path):
            raise DataObjectContentNotFoundError({
                'output_name': obj_name,
                'content_path': output_content_path
            })

        # get the owner
        owner = await P2PGetIdentity.perform(self._owner.custodian_address, task_out.owner_iid)
        if owner is None:
            raise DataObjectOwnerNotFoundError({
                'output_name': obj_name,
                'owner_iid': task_out.owner_iid
            })

        # is the output a JSONObject? validate if we have a schema
        if output_spec.data_format == 'json' and output_spec.data_schema is not None:
            with open(output_content_path, 'r') as f:
                content = json.load(f)
                if not validate_json(content, output_spec.data_schema):
                    raise InvalidJSONDataObjectError({
                        'obj_name': obj_name,
                        'content': content,
                        'schema': output_spec.data_schema
                    })

        restricted_access = task_out.restricted_access
        content_encrypted = task_out.content_encrypted

        # TODO: figure out what is supposed to happen with the content key here
        # if content_encrypted:
        #     content_key = encrypt_file(output_content_path, encrypt_for=owner, delete_source=True)

        # get the network
        network: List[NodeInfo] = await P2PGetNetwork.perform(self._owner.custodian_address)

        # do we have a target node specified for storing the data object?
        target_node = self._owner.job.custodian
        if task_out.target_node_iid:
            # check with the node db to see if we know about this node
            nodes_by_id: Dict[str, NodeInfo] = {node.identity.id: node for node in network}
            if task_out.target_node_iid not in nodes_by_id:
                raise CLIRuntimeError("Target node not found in network", details={
                    'target_node_iid': task_out.target_node_iid,
                    'network': network
                })

            # extract the rest address from that node record
            target_node = nodes_by_id[task_out.target_node_iid]

        # check if the target node has DOR capabilities
        if not target_node.dor_service:
            raise CLIRuntimeError("Target node does not support DOR capabilities", details={
                'target_node': target_node.model_dump()
            })

        # check if the target node is the custodian, if so override the P2P address
        if target_node.identity.id == self._owner.custodian_identity.id:
            self._logger.info(
                f"target node is custodian -> overriding P2P address: {self._owner.custodian_address.address}"
            )
            target_node.p2p_address = self._owner.custodian_address.address

        # determine recipe
        recipe = DataObjectRecipe(
            name=obj_name,
            processor=self._owner.gpp,
            consumes={},
            product=CObjectNode(
                c_hash='',  # valid content hash will be set by the DOR
                data_type=output_spec.data_type,
                data_format=output_spec.data_format,
                content=None
            )
        )

        # update recipe inputs
        for item in self._owner.job.task.input:
            spec = self._owner.input_interface[item.name]
            if item.type == 'value':
                recipe.consumes[item.name] = CObjectNode(
                    c_hash=hash_json_object(item.value).hex(),
                    data_type=spec.data_type,
                    data_format=spec.data_format,
                    content=item.value
                )
            else:
                recipe.consumes[item.name] = CObjectNode(
                    c_hash=item.c_hash,
                    data_type=spec.data_type,
                    data_format=spec.data_format,
                    content=None
                )

        # creator(s) is assumed to be the user on whose behalf the job is executed
        creator_iids = [self._owner.user.id]

        # push the data object to the DOR
        self._logger.info(
            f"BEGIN push output '{obj_name}' to {target_node.identity.id} at {target_node.p2p_address}"
        )

        obj = await P2PPushDataObject.perform(
            target_node.p2p_address, self._owner.keystore, target_node.identity,
            output_content_path, output_spec.data_type, output_spec.data_format, owner.id, creator_iids,
            restricted_access, content_encrypted,
            license=DataObject.License(by=True, sa=True, nc=True, nd=True),
            recipe=recipe,
            tags={
                'name': obj_name,
                'job_id': self._owner.job.id
            }
        )

        self._logger.info(f"END push output '{obj_name}'")
        return obj

    def run(self) -> None:
        try:
            # upload the data object to the target DOR
            obj = asyncio.run(self.push_data_object(self._obj_name))

            # remove the output from the pending set
            self._logger.info(f"pushing output data object '{self._obj_name}' SUCCESSFUL.")
            self._owner.remove_pending_output(self._obj_name, obj)

        except SaaSRuntimeException as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            self._logger.error(f"pushing output data object '{self._obj_name}' FAILED: {e.reason}\n{trace}")
            error = JobStatus.Error(message=f"Pushing output data object '{self._obj_name}' failed.",
                                    exception=e.content)
            self._owner.remove_pending_output(self._obj_name, error)

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            self._logger.error(f"pushing output data object '{self._obj_name}' FAILED: {e}\n{trace}")
            error = JobStatus.Error(message=f"Pushing output data object '{self._obj_name}' failed.",
                                    exception=SaaSRuntimeException(str(e)).content)
            self._owner.remove_pending_output(self._obj_name, error)


class StatusHandler(threading.Thread):
    def __init__(self, logger: logging.Logger, peer_address: P2PAddress, job_id: str, job_status_path: str):
        super().__init__(daemon=False)
        self._mutex = threading.Lock()
        self._logger = logger
        self._peer_address = peer_address
        self._job_id = job_id
        self._job_status_path = job_status_path
        self._job_status = JobStatus(
            state=JobStatus.State.UNINITIALISED, progress=0, output={}, notes={}, errors=[], message=None
        )
        self._last_update = None
        self._last_push = None

    def has_output(self, output_name: str) -> bool:
        return output_name in self._job_status.output

    def update(
            self, state: Optional[JobStatus.State] = None, progress: Optional[int] = None,
            message: Optional[JobStatus.Message] = None, output: Optional[Dict[str, DataObject]] = None,
            error: Optional[JobStatus.Error] = None
    ) -> None:
        with self._mutex:
            is_dirty = False

            if state and state != self._job_status.state:
                self._job_status.state = state
                is_dirty = True

            if progress and progress != self._job_status.progress:
                self._job_status.progress = progress
                is_dirty = True

            if message and (self._job_status.message is None or
                            message.severity != self._job_status.message.severity or
                            message.content != self._job_status.message.content):
                self._job_status.message = message
                is_dirty = True

            if output:
                for output_name, obj in output.items():
                    if output_name not in self._job_status.output:
                        self._job_status.output[output_name] = obj
                        is_dirty = True

            if error:
                self._job_status.errors.append(error)
                is_dirty = True

            # has there been any actual update?
            if is_dirty:
                self._last_update = get_timestamp_now()

    def _handle(self, last_update: int) -> None:
        # update the last push timestamp to reflect the timestamp of the update that has been
        # attempted to pushed.
        self._last_push = last_update

        try:
            # update the file
            with open(self._job_status_path, 'w') as f:
                # noinspection PyTypeChecker
                json.dump(self._job_status.model_dump(), f, indent=2)

            # push the job status to the custodian
            asyncio.run(P2PPushJobStatus.perform(self._peer_address, self._job_id, self._job_status))
            self._logger.info(f"Pushing job status {last_update} -> SUCCESSFUL.")

        except SaaSRuntimeException as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            self._logger.error(f"Pushing job status {last_update} -> FAILED: {e.reason}\n{trace}")

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            self._logger.error(f"Pushing job status {last_update} -> FAILED: {e}\n{trace}")

    def run(self) -> None:
        self._logger.info("BEGIN status handler...")

        while self._job_status.state not in [
            JobStatus.State.SUCCESSFUL, JobStatus.State.CANCELLED, JobStatus.State.FAILED
        ]:
            time.sleep(0.5)

            # has there been any change since we pushed it the last time?
            with self._mutex:
                last_update = self._last_update
                dirty = last_update != self._last_push

            # push only if dirty
            if dirty:
                self._handle(last_update)

        # before ending the thread. send a final update.
        self._handle(get_timestamp_now())

        self._logger.info("END status handler.")


class JobRunner(CLICommand, ProgressListener):
    def __init__(self):
        super().__init__('run', 'runs a job with a processor', arguments=[
            Argument('--job-path', dest='job_path', action='store', help="path to the job"),
            Argument('--proc-path', dest='proc_path', action='store', help="path to the processor"),
            Argument('--log-level', dest='log_level', action='store', help="log level: debug, info, warning, error"),
            Argument('--service-address', dest='service_address', action='store',
                     help="address used by P2P service for secure communication"),
            Argument('--custodian-address', dest='custodian_address', action='store',
                     help="P2P address of the custodian"),
            Argument('--custodian-pub-key', dest='custodian_pub_key', action='store',
                     help="Public curve key of custodian"),
            Argument('--job-id', dest='job_id', action='store',
                     help="Id of the job (will be used by the runner to retrieve job information from the custodian)")
        ])

        self._mutex = threading.Lock()
        self._interrupted = False
        self._wd_path: Optional[str] = None
        self._logger: Optional[logging.Logger] = None

        # set during initialise_processor
        self._gpp: Optional[GitProcessorPointer] = None
        self._proc: Optional[ProcessorBase] = None

        # set/used during initialise_p2p
        self._keystore: Optional[Keystore] = None
        self._p2p: Optional[P2PService] = None
        self._custodian_address: Optional[P2PAddress] = None
        self._custodian: Optional[Identity] = None
        self._job: Optional[Job] = None
        self._batch_status: Optional[BatchStatus] = None
        self._barrier = BatchBarrier(self)

        # set upon job update
        self._input_interface: Dict[str, ProcessorDescriptor.IODataObject] = {}
        self._output_interface: Dict[str, ProcessorDescriptor.IODataObject] = {}
        self._status_handler: Optional[StatusHandler] = None
        self._user: Optional[Identity] = None

        # set/used during batch sync
        self._batch_ports: Dict[str, Dict[str, Optional[str]]] = {}
        self._batch_identities: Dict[str, Identity] = {}

        self._pending_output: Set[str] = set()
        self._failed_output: Set[str] = set()

    @property
    def wd_path(self) -> str:
        return self._wd_path

    @property
    def keystore(self) -> Keystore:
        return self._keystore

    @property
    def identity(self) -> Identity:
        return self._keystore.identity

    @property
    def gpp(self) -> GitProcessorPointer:
        return self._gpp

    @property
    def job(self) -> Optional[Job]:
        return self._job

    @property
    def input_interface(self) -> Dict[str, ProcessorDescriptor.IODataObject]:
        return self._input_interface

    @property
    def output_interface(self) -> Dict[str, ProcessorDescriptor.IODataObject]:
        return self._output_interface

    @property
    def user(self) -> Optional[Identity]:
        return self._user

    @property
    def custodian_identity(self) -> Optional[Identity]:
        return self._custodian

    @property
    def custodian_address(self) -> Optional[P2PAddress]:
        return self._custodian_address

    def on_job_cancel(self) -> None:
        # interrupt the processor. note: whether this request is honored or even implemented depends on the
        # actual processor.
        with self._mutex:
            try:
                self._interrupted = True
                self._proc.interrupt()
                self._logger.info("Received job cancellation notification")

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
                self._logger.error(f"Received job cancellation notification -> INTERRUPT FAILED: {e}\n{trace}")

    def on_progress_update(self, progress: int) -> None:
        self._logger.info(f"Received progress update notification: {progress}")
        self._status_handler.update(progress=progress)

    def on_output_available(self, output_name: str) -> None:
        with self._mutex:
            # do we already have this output?
            has_output = self._status_handler.has_output(output_name)
            if has_output:
                self._logger.warning(f"Received output available notification: {output_name} -> already handled.")
                return

            # has it failed previously?
            has_failed = output_name in self._failed_output
            if has_failed:
                self._logger.warning(f"Received output available notification: {output_name} -> previously failed.")
                return

            # handle it but starting a dedicated output object handler instance
            self._logger.info(f"Received output available notification: {output_name}")
            self._pending_output.add(output_name)
            handler = OutputObjectHandler(self._logger, self, output_name)
            handler.start()

    def on_message(self, severity: Severity, message: str) -> None:
        with self._mutex:
            self._logger.info(f"Received message notification: [{severity}] {message}")
            self._status_handler.update(message=JobStatus.Message(severity=severity, content=message))

    def _write_exitcode(self, exitcode: ExitCode, e: Exception = None) -> None:
        exitcode_path = os.path.join(self._wd_path, 'job.exitcode')
        with open(exitcode_path, 'w') as f:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            result = JobResult(exitcode=exitcode, trace=trace)
            # noinspection PyTypeChecker
            json.dump(result.model_dump(), f, indent=2)

    def _setup_logger(self, log_level: str) -> None:
        # do we have a log level?
        log_level = log_level if log_level else 'info'

        # log_level mapping exist?
        log_level_mapping = {
            'debug': logging.DEBUG,
            'info': logging.INFO,
            'warning': logging.WARNING,
            'error': logging.ERROR
        }
        if log_level not in log_level_mapping:
            print(f"Log level '{log_level}' does not exist. Defaulting to 'info' log level.")
            log_level = 'info'

        # create the logger
        log_path = os.path.join(self._wd_path, 'job.log')
        print(f"Using logger with: level={log_level} path={log_path}")
        self._logger = Logging.get('cli.job_runner', level=log_level_mapping[log_level], custom_log_path=log_path)

    def _initialise_processor(self, proc_path: str) -> None:
        # do we have a GPP?
        gpp_path = os.path.join(proc_path, 'gpp.json')
        if not os.path.isfile(gpp_path):
            raise CLIRuntimeError(f"No GPP descriptor found at '{gpp_path}'.")

        # read the GPP
        try:
            with open(gpp_path, 'r') as f:
                content = json.load(f)
            self._gpp = GitProcessorPointer.model_validate(content)
            self._logger.info(f"Read GPP at {gpp_path}")
        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise CLIRuntimeError(f"Reading GPP failed: {trace}")

        # find processors at the given location
        procs_by_name = find_processors(proc_path)
        self._logger.info(f"Found the following processors: {list(procs_by_name.keys())}")

        # do we have the processor we are looking for?
        proc_name = self._gpp.proc_descriptor.name
        self._proc: ProcessorBase = procs_by_name.get(proc_name, None)
        if self._proc is None:
            raise CLIRuntimeError(f"Processor '{proc_name}' not found at '{proc_path}'.")

    def _initialise_p2p(
            self, service_address: str, custodian_address: str, custodian_pub_key: str, job_id: str
    ) -> None:
        # create the ephemeral job keystore
        self._keystore = Keystore.new('runner')
        self._logger.info(f"Using runner ephemeral keystore with id={self._keystore.identity.id}")

        # start the secured P2P service
        self._p2p = P2PService(self._keystore, service_address)
        self._p2p.add(P2PLatency())
        self._p2p.add(P2PInterruptJob(self))
        self._p2p.add(self._barrier)
        self._p2p.start_service(encrypt=True)
        self._logger.info("P2P service interface is up.")

        # determine the full P2P address of the custodian
        self._custodian_address = P2PAddress(
            address=custodian_address,
            curve_secret_key=self._keystore.curve_secret_key(),
            curve_public_key=self._keystore.curve_public_key(),
            curve_server_key=custodian_pub_key
        )

        # figure out the port of the P2P service
        fields = re.split(r'[:/]+', service_address)
        port = fields[-1]

        # determine the external address (resolve redirection to host name if applicable)
        external_address = os.environ.get('EXTERNAL_P2P_ADDRESS', service_address)
        if external_address == 'HOSTNAME':
            hostname = os.environ['HOSTNAME']
            external_address = f"tcp://{hostname}:{port}"
        self._logger.info(f"P2P service determined external address as {external_address}")

        # perform handshake with custodian
        self._logger.info(f"P2P handshake: trying to connect to {self._custodian_address.address}...")
        self._job, self._custodian, self._batch_status = asyncio.run(P2PRunnerPerformHandshake.perform(
            self._custodian_address, self._keystore.identity, external_address, job_id, self._gpp
        ))
        self._logger.info(f"P2P handshake: successful -> custodian at {self._custodian_address.address} "
                          f"has id={self._custodian.id}")

    def _initialise_job(self) -> None:
        # write the job descriptor
        job_descriptor_path = os.path.join(self._wd_path, 'job.descriptor')
        with open(job_descriptor_path, 'w') as f:
            json.dump(self._job.model_dump(), f, indent=2)

        # prepare input/output interfaces
        self._input_interface: Dict[str, ProcessorDescriptor.IODataObject] = \
            {item.name: item for item in self._gpp.proc_descriptor.input}
        self._output_interface: Dict[str, ProcessorDescriptor.IODataObject] = \
            {item.name: item for item in self._gpp.proc_descriptor.output}

        # update job and set up status handler
        job_status_path = os.path.join(self._wd_path, 'job.status')
        self._status_handler = StatusHandler(self._logger, self._custodian_address, self._job.id, job_status_path)
        self._status_handler.start()

    def _extract_batch_status(self) -> bool:
        mappings_complete = True
        for member in self._batch_status.members:
            self._batch_ports[member.name] = member.ports
            self._batch_identities[member.name] = member.identity
            for address in member.ports.values():
                if address is None:
                    mappings_complete = False

        return mappings_complete

    def _await_batch(self) -> None:
        # extract identity and port mappings for convenience. figure out if we have complete mapping information.
        mappings_complete = self._extract_batch_status()

        # members need to wait for all other members to be ready (i.e., they must have completed the handshake).
        # how do we know that's the case? members may perform handshake with the custodian in any order. each
        # member informs the custodian about its own address. the custodian then updates the batch status and
        # uses that for the next handshake. with each handshake, the batch status becomes incrementally completed.
        # the last member will thus receive a batch status that has COMPLETE port mappings of all members. this last
        # member is responsible to release the barrier.

        # are we the one with complete mappings?
        if mappings_complete:
            # prepare the release
            mappings: List[Tuple[str, P2PAddress]] = []
            for name in self._batch_ports.keys():
                # get the identity and the P2P address (by convention that's the 6000/tcp mapping) of the member
                member_identity = self._batch_identities[name]
                member_p2p_address = P2PAddress(
                    address=self._batch_ports[name]['6000/tcp'],
                    curve_secret_key=self._keystore.curve_secret_key(),
                    curve_public_key=self._keystore.curve_public_key(),
                    curve_server_key=member_identity.c_public_key
                )
                mappings.append((name, member_p2p_address))

            suffix = ' '.join(f"{item[0]}->{item[1].address}" for item in mappings)
            self._logger.info(f"[barrier] complete mapping available: {suffix}")
            self._logger.info(f"[barrier] batch status: {self._batch_status.model_dump()}")

            # release the barrier
            for name, p2p_address in mappings:
                try:
                    # send the barrier release message to the member
                    self._logger.info(
                        f"[barrier] send release for 'initial_barrier' to {name} at {p2p_address.address}"
                    )
                    asyncio.run(BatchBarrier.perform(p2p_address, 'initial_barrier', self._batch_status))
                except Exception as e:
                    trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                    self._logger.error(f"[barrier] error: {e} -> {trace}")

        # wait for the barrier to be released
        self._logger.info("[barrier] waiting for barrier release 'initial_barrier'...")
        result: dict = self._barrier.wait_for_release('initial_barrier')
        self._logger.info("[barrier] barrier release 'initial_barrier' received.")

        # update batch status and extract it
        self._batch_status = BatchStatus.model_validate(result)
        if not self._extract_batch_status():
            raise RTIException("Incomplete port mappings after barrier", details={
                'ports': self._batch_ports
            })

        # log the member information
        for name in self._batch_ports.keys():
            self._logger.info(f"[batch:{name}] identity={self._batch_identities[name].id} ports: {self._batch_ports[name]}")

    def _store_value_input_data_objects(self) -> None:
        for item in self._job.task.input:
            # if it is a 'value' input then store it to the working directory
            if item.type == 'value':
                # write the content
                with open(os.path.join(self._wd_path, item.name), 'w') as f:
                    # noinspection PyTypeChecker
                    json.dump(item.value, f, indent=2)

                # write the meta information
                with open(os.path.join(self._wd_path, f"{item.name}.meta"), 'w') as f:
                    json.dump({
                        'data_type': self._input_interface[item.name].data_type,
                        'data_format': 'json'
                    }, f, indent=2)

    def _fetch_reference_input_data_objects(self) -> Dict[str, Tuple[DataObject, str]]:
        # do we have any by-reference input data objects in the first place?
        relevant: Dict[str, Task.InputReference] = {
            item.name: item for item in self._job.task.input if isinstance(item, Task.InputReference)
        }
        if len(relevant) == 0:
            return {}

        # obtain a list of nodes in the network and filter by peers with DOR capability
        network = asyncio.run(P2PGetNetwork.perform(self._custodian_address))
        network = [node for node in network if node.dor_service]

        loop = asyncio.new_event_loop()
        try:
            class KeystoreWrapper:
                def __init__(self, keystore: Keystore):
                    self.keystore = keystore

            # try to fetch all referenced data objects using the P2P protocol
            lookup = P2PLookupDataObject(KeystoreWrapper(self._keystore))
            fetch = P2PFetchDataObject(KeystoreWrapper(self._keystore))
            pending: Dict[str, str] = {item.obj_id: item.user_signature for item in relevant.values()}
            found: Dict[str, str] = {}
            for peer in network:
                # check if the peer is the custodian, if so override the P2P address
                if peer.identity.id == self.custodian_identity.id:
                    self._logger.info(
                        f"peer is custodian -> overriding P2P address: {self.custodian_address.address}"
                    )
                    peer.p2p_address = self.custodian_address.address

                # does the remote DOR have any of the pending data objects?
                result: Dict[str, DataObject] = loop.run_until_complete(
                    lookup.perform(peer, list(pending.keys()))
                )

                # process the results (if any)
                for obj_id, meta in result.items():
                    meta_path = os.path.join(self._wd_path, f"{obj_id}.meta")
                    content_path = os.path.join(self._wd_path, f"{obj_id}.content")

                    # store the meta information
                    with open(meta_path, 'w') as f:
                        # noinspection PyTypeChecker
                        json.dump(meta.model_dump(), f, indent=2)

                    if meta.access_restricted:
                        # does the user have access?
                        if self._user.id not in meta.access:
                            raise AccessNotPermittedError({
                                'obj_id': obj_id,
                                'user_iid': self._user.id
                            })

                        # do we have a user signature?
                        signature = pending[obj_id]
                        if signature is None:
                            raise MissingUserSignatureError({
                                'obj_id': obj_id,
                                'user_iid': self._user.id
                            })

                        # try to download it
                        loop.run_until_complete(
                            fetch.perform(
                                peer, obj_id, meta_path, content_path, user_iid=self._user.id, user_signature=signature
                            )
                        )

                    else:
                        # try to download it
                        loop.run_until_complete(
                            fetch.perform(
                                peer, obj_id, meta_path, content_path
                            )
                        )

                    found[obj_id] = meta.c_hash
                    pending.pop(obj_id)

                # still pending object ids? if not, we are done.
                if len(pending) == 0:
                    break

            # do we still have pending data objects?
            if len(pending) > 0:
                raise UnresolvedInputDataObjectsError({
                    'pending': pending,
                    'found': found
                })

            # create symbolic links to the contents for every input AND update references with c_hash
            for name, item in relevant.items():
                # link the content
                os.symlink(
                    src=os.path.join(self._wd_path, f"{item.obj_id}.content"),
                    dst=os.path.join(self._wd_path, item.name)
                )

                # link the meta information
                os.symlink(
                    src=os.path.join(self._wd_path, f"{item.obj_id}.meta"),
                    dst=os.path.join(self._wd_path, f"{item.name}.meta")
                )

                # set the content hash
                item.c_hash = found[item.obj_id]

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            raise CLIRuntimeError("Fetching reference input objects failed", details={
                'trace': trace
            })

        finally:
            loop.close()

    def _verify_inputs_and_outputs(self) -> None:
        proc_descriptor: ProcessorDescriptor = self._gpp.proc_descriptor
        for i in proc_descriptor.input:
            # does the input exist?
            input_path0 = os.path.join(self._wd_path, f"{i.name}.meta")
            input_path1 = os.path.join(self._wd_path, i.name)
            if not os.path.isfile(input_path0) or not os.path.isfile(input_path1):
                raise InputDataObjectMissing(i.name, input_path0, input_path1)

            # read the meta information
            with open(input_path0) as f:
                meta = json.load(f)

            # compare data types and format
            if meta['data_type'] != i.data_type or meta['data_format'] != i.data_format:
                raise MismatchingDataTypeOrFormatError({
                    'obj_name': i.name,
                    'expected': {
                        'data_type': i.data_type,
                        'data_format': i.data_format
                    },
                    'actual': {
                        'data_type': meta['data_type'],
                        'data_format': meta['data_format']
                    }
                })

            # in case of 'json' data format, verify using the schema (if any)
            if i.data_format == 'json' and i.data_schema is not None:
                # read the content
                with open(input_path1, 'r') as f:
                    content = json.load(f)

                # validate the content using the schema
                if not validate_json(content, i.data_schema):
                    raise InvalidJSONDataObjectError({
                        'obj_name': i.name,
                        'content': content,
                        'schema': i.data_schema
                    })

        # check if the owner identity exists for each output data object
        for o in self._job.task.output:
            owner = asyncio.run(P2PGetIdentity.perform(self._custodian_address, o.owner_iid))
            if owner is None:
                raise DataObjectOwnerNotFoundError({
                    'output_name': o.name,
                    'owner_iid': o.owner_iid
                })

    def remove_pending_output(self, obj_name: str, result: Union[DataObject, JobStatus.Error]) -> None:
        with self._mutex:
            self._pending_output.remove(obj_name)

            if isinstance(result, DataObject):
                self._status_handler.update(output={
                    obj_name: result
                })

            else:
                self._status_handler.update(error=result)
                self._failed_output.add(obj_name)

    def has_pending_output(self) -> bool:
        with self._mutex:
            return len(self._pending_output) > 0

    def execute(self, args: dict) -> None:
        prompt_if_missing(args, 'job_path', prompt_for_string, message="Enter path to the job working directory:")
        prompt_if_missing(args, 'proc_path', prompt_for_string, message="Enter path to the processor directory:")
        prompt_if_missing(args, 'service_address', prompt_for_string, message="Enter address for the P2P service:")
        env_if_missing(args, 'custodian_address', 'SIMAAS_CUSTODIAN_ADDRESS')
        env_if_missing(args, 'custodian_pub_key', 'SIMAAS_CUSTODIAN_PUBKEY')
        env_if_missing(args, 'job_id', 'JOB_ID')

        # check if required args are defined
        print(f"Environment: {os.environ}")
        print(f"Arguments: {args}")
        if not all(key in args for key in ['custodian_address', 'custodian_pub_key', 'job_id']):
            raise CLIRuntimeError("Required custodian and job arguments missing")

        # determine working directory
        self._wd_path = args['job_path']
        if os.path.isdir(self._wd_path):
            print(f"Using existing job path at {self._wd_path}")
        else:
            print(f"Creating job path at {self._wd_path}")

        # setup logger
        self._setup_logger(args.get('log_level'))

        try:
            # initialise processor
            self._logger.info("BEGIN initialising job runner...")
            self._initialise_processor(args['proc_path'])

            # initialise P2P services
            self._initialise_p2p(
                args['service_address'], args['custodian_address'], args['custodian_pub_key'], args['job_id']
            )

            # if, for some reason, we have not received a job, then we can't proceed.
            if self._job is None:
                raise CLIRuntimeError("Handshake failed: no job received")

            # initialise the job
            self._initialise_job()

            # update state
            self._logger.info("END initialising job runner.")
            self._status_handler.update(state=JobStatus.State.INITIALISED)
            self._logger.info(f"BEGIN processing job {self._job.id}...")

            # do we have a batch status? if so this means this job is part of a batch and we need to wait for
            # all batch members to be initialised
            if self._batch_status is not None:
                self._await_batch()

            # fetch the user identity
            self._user: Optional[Identity] = asyncio.run(
                P2PGetIdentity.perform(self._custodian_address, self._job.task.user_iid)
            )
            if self._user is None:
                raise CLIRuntimeError(f"User with id={self._job.task.user_iid} not known to node.")
            self._logger.info(f"Using user identity with id={self._user.id}")

            # store by-value input data objects (if any)
            self._store_value_input_data_objects()

            # fetch by-reference input data objects (if any)
            self._fetch_reference_input_data_objects()

            # verify that data types of input data objects match
            self._verify_inputs_and_outputs()

            # update state
            self._status_handler.update(state=JobStatus.State.RUNNING)

            # run the processor
            self._logger.info(f"Using namespace: {self._job.task.namespace}")
            namespace = DefaultNamespace(
                self._job.task.namespace, self._custodian, self.custodian_address.address, self._keystore
            )
            self._proc.run(self._wd_path, self._job, self, namespace, self._logger)

            # was the processor interrupted?
            if self._interrupted:
                # wrap up
                self._logger.info(f"END processing job {self._job.id if self._job else '?'} -> INTERRUPTED")
                self._status_handler.update(state=JobStatus.State.CANCELLED)
                self._write_exitcode(ExitCode.INTERRUPTED)

            else:
                # wait until pending output data objects are taken care of
                while self.has_pending_output():
                    time.sleep(0.25)

                # do we have failed outputs?
                if len(self._failed_output) > 0:
                    raise CLIRuntimeError(f"Failed to upload some outputs: {self._failed_output}")

                # wrap up
                self._logger.info(f"END processing job {self._job.id if self._job else '?'} -> DONE")
                self._status_handler.update(state=JobStatus.State.SUCCESSFUL)
                self._write_exitcode(ExitCode.DONE)

        except SaaSRuntimeException as e:
            # create error information
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            exception = e.content
            exception.details = exception.details if exception.details else {}
            exception.details['trace'] = trace
            error = JobStatus.Error(message="Job failed", exception=exception)

            # wrap up
            self._logger.error(f"END processing job {self._job.id if self._job else '?'} "
                               f"-> FAILED: {e.reason}\n{trace}")
            if self._status_handler:
                self._status_handler.update(state=JobStatus.State.FAILED, error=error)
            self._write_exitcode(ExitCode.ERROR, e)

        except Exception as e:
            # create error information
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            exception = ExceptionContent(id='none', reason=str(e), details={'trace': trace})
            error = JobStatus.Error(message="Job failed", exception=exception)

            # wrap up
            self._logger.error(f"END processing job {self._job.id if self._job else '?'} "
                               f"-> FAILED: {e}\n{trace}")
            if self._status_handler:
                self._status_handler.update(state=JobStatus.State.FAILED, error=error)
            self._write_exitcode(ExitCode.ERROR, e)

        finally:
            if self._status_handler:
                self._status_handler.join(5)
