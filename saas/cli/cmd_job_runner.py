import json
import logging
import os
import threading
import time
import traceback
from typing import Dict, Tuple, Set, Union

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from saas.cli.exceptions import CLIRuntimeError
from saas.cli.helpers import CLICommand, Argument, prompt_for_string, prompt_if_missing
from saas.core.exceptions import SaaSRuntimeException
from saas.core.helpers import validate_json, hash_json_object
from saas.core.identity import Identity
from saas.core.logging import Logging
from saas.dor.protocol import DataObjectRepositoryP2PProtocol
from saas.dor.proxy import DORProxy
from saas.dor.schemas import ProcessorDescriptor, DataObject, GitProcessorPointer
from saas.nodedb.proxy import NodeDBProxy
from saas.rest.exceptions import UnsuccessfulRequestError
from saas.rti.exceptions import UnresolvedInputDataObjectsError, AccessNotPermittedError, MissingUserSignatureError, \
    InputDataObjectMissing, MismatchingDataTypeOrFormatError, InvalidJSONDataObjectError, \
    DataObjectOwnerNotFoundError, DataObjectContentNotFoundError
from saas.rti.proxy import JOB_ENDPOINT_PREFIX, RTIProxy
from saas.rti.schemas import JobStatus, Severity, JobResult, ExitCode, Task, Job
from saas.core.processor import find_processors, ProcessorBase, ProgressListener


class OutputObjectHandler(threading.Thread):
    def __init__(self, logger: logging.Logger, owner, obj_name: str, max_attempts: int = 10, retry_delay: int = 10):
        super().__init__()
        self._logger = logger
        self._owner: JobRunner = owner
        self._obj_name: str = obj_name
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay

    def run(self) -> None:
        try:
            for i in range(self._max_attempts):
                try:
                    # upload the data object to the target DOR
                    obj = self._owner.push_data_object(self._obj_name)

                    # remove the output from the pending set
                    self._logger.info(f"pushing output data object '{self._obj_name}' SUCCESSFUL.")
                    self._owner.remove_pending_output(self._obj_name, obj)

                    return

                except UnsuccessfulRequestError as e:
                    self._logger.warning(
                        f"[attempt={i + 1}/{self._max_attempts}] pushing output data object '{self._obj_name}' "
                        f"FAILED: {e.reason} {e.details} -> trying again in {self._retry_delay * (i + 1)} second(s)."
                    )

            # if we reach here it didn't work
            raise CLIRuntimeError("Number of attempts to push output data object exceeded limit.")

        except SaaSRuntimeException as e:
            self._logger.error(f"pushing output data object '{self._obj_name}' FAILED: {e.reason}")
            error = JobStatus.Error(message=f"Pushing output data object '{self._obj_name}' failed.",
                                    exception=e.content)
            self._owner.remove_pending_output(self._obj_name, error)

        except Exception as e:
            self._logger.error(f"pushing output data object '{self._obj_name}' FAILED: {e}")
            error = JobStatus.Error(message=f"Pushing output data object '{self._obj_name}' failed.",
                                    exception=SaaSRuntimeException(str(e)).content)
            self._owner.remove_pending_output(self._obj_name, error)


class JobRunner(CLICommand, ProgressListener):
    def __init__(self):
        super().__init__('run', 'runs a job with a processor', arguments=[
            Argument('--job-path', dest='job_path', action='store', help="path to the job"),
            Argument('--proc-path', dest='proc_path', action='store', help="path to the processor"),
            Argument('--proc-name', dest='proc_name', action='store', help="name of the processor"),
            Argument('--log-level', dest='log_level', action='store', help="log level: debug, info, warning, error"),
            Argument('--rest-address', dest='rest_address', action='store',
                     help="address used by the REST job interface")
        ])

        self._mutex = threading.Lock()
        self._logger = None
        self._interrupted = False
        self._wd_path = None
        self._address_mapping: Dict[str, int] = {}
        self._proc = None
        self._job = None
        self._gpp = None
        self._keystore = None
        self._user = None
        self._input_interface: Dict[str, ProcessorDescriptor.IODataObject] = {}
        self._output_interface: Dict[str, ProcessorDescriptor.IODataObject] = {}
        self._pending_output: Set[str] = set()
        self._failed_output: Set[str] = set()
        self._rti_proxy = None
        self._job_status = None

    async def job_status(self) -> JobStatus:
        with self._mutex:
            return self._job_status

    async def job_cancel(self) -> JobStatus:
        # interrupt the processor. note: whether this request is honored or even implemented depends on the
        # actual processor.
        with self._mutex:
            self._logger.info("received request to cancel job...")
            self._interrupted = True
            self._proc.interrupt()

            # update state
            self._job_status.state = JobStatus.State.CANCELLED
            self._store_job_status()
            return self._job_status

    def on_progress_update(self, progress: int) -> None:
        with self._mutex:
            self._logger.info(f"on_progress_update: progress={progress}")
            if progress != self._job_status.progress:
                self._job_status.progress = progress
                self._store_job_status()

    def on_output_available(self, output_name: str) -> None:
        with self._mutex:
            if output_name not in self._job_status.output and output_name not in self._failed_output:
                self._logger.info(f"on_output_available: output_name={output_name}")
                self._pending_output.add(output_name)
                handler = OutputObjectHandler(self._logger, self, output_name)
                handler.start()

    def on_message(self, severity: Severity, message: str) -> None:
        with self._mutex:
            self._logger.info(f"on_message: severity={severity} message={message}")
            if self._job_status.message is None or severity != self._job_status.message.severity \
                    or message != self._job_status.message.content:
                self._job_status.message = JobStatus.Message(severity=severity, content=message)
                self._store_job_status()

    def _store_job_status(self) -> None:
        # store the job status
        job_status_path = os.path.join(self._wd_path, 'job.status')
        with open(job_status_path, 'w') as f:
            json.dump(self._job_status.dict(), f, indent=2)

        # try to push the status to the RTI (if any)
        try:
            self._rti_proxy.update_job_status(self._job.id, self._job_status)
        except Exception as e:
            self._logger.warning(f"pushing job status failed: {e}")

    def _write_exitcode(self, exitcode: ExitCode, e: Exception = None) -> None:
        exitcode_path = os.path.join(self._wd_path, 'job.exitcode')
        with open(exitcode_path, 'w') as f:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__)) if e else None
            result = JobResult(exitcode=exitcode, trace=trace)
            json.dump(result.dict(), f, indent=2)

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

    def _initialise_job(self, proc_path: str, proc_name: str = None) -> None:
        # does the processor path exist?
        if not os.path.isdir(proc_path):
            raise CLIRuntimeError(f"Processor path '{proc_path}' does not exist.")
        print(f"Using processor path at {proc_path}")

        # find processors at the given location
        procs_by_name = find_processors(proc_path)
        print(f"Found the following processors: {list(procs_by_name.keys())}")

        # do we have a processor name?
        if proc_name is None:
            # try to read the descriptor in the proc path
            descriptor_path = os.path.join(proc_path, 'descriptor.json')
            if not os.path.isfile(descriptor_path):
                raise CLIRuntimeError(f"No processor descriptor found at '{proc_path}'.")

            # read the descriptor
            with open(descriptor_path) as f:
                # try to get the processor by the descriptor name
                descriptor = ProcessorDescriptor.parse_obj(json.load(f))
                proc_name = descriptor.name

        # do we have the processor we are looking for?
        self._proc: ProcessorBase = procs_by_name.get(proc_name, None)
        if self._proc is None:
            raise CLIRuntimeError(f"No processor '{proc_name}' found at '{proc_path}'.")
        print(f"Found processor '{proc_name}' at '{proc_path}'")

        # read the job descriptor
        job_descriptor_path = os.path.join(self._wd_path, 'job.descriptor')
        with open(job_descriptor_path, 'r') as f:
            content = json.load(f)
            self._job = Job.parse_obj(content)
        print(f"Created job descriptor at {job_descriptor_path}")

        # read the gpp descriptor
        gpp_descriptor_path = os.path.join(self._wd_path, 'gpp.descriptor')
        with open(gpp_descriptor_path, 'r') as f:
            content = json.load(f)
            self._gpp = GitProcessorPointer.parse_obj(content)
        print(f"Created GPP descriptor at {gpp_descriptor_path}")

        # prepare input/output interfaces
        self._input_interface: Dict[str, ProcessorDescriptor.IODataObject] = \
            {item.name: item for item in self._gpp.proc_descriptor.input}
        self._output_interface: Dict[str, ProcessorDescriptor.IODataObject] = \
            {item.name: item for item in self._gpp.proc_descriptor.output}

        # fetch the user identity
        db_proxy = NodeDBProxy(self._job.custodian.rest_address)
        self._user: Identity = db_proxy.get_identity(self._job.task.user_iid)
        if self._user is None:
            raise CLIRuntimeError(f"User with id={self._job.task.user_iid} not known to node.")
        print(f"Using user identity with id={self._user.id}")

        # setup the custodian RTI proxy
        self._rti_proxy = RTIProxy(self._job.custodian.rest_address)
        print(f"Using {self._job.custodian.rest_address} to update custodian about job status changes.")

        # update the job status
        self._job_status = JobStatus(state=JobStatus.State.INITIALISED, progress=0, output={}, notes={},
                                     errors=[], message=None)
        self._store_job_status()

    # def _create_ephemeral_keystore(self) -> Identity:
    #     # create the ephemeral job keystore
    #     self._keystore = Keystore.create(self._wd_path, f"job:{self._job.id}")
    #     print(f"Created ephemeral job keystore with id={self._keystore.identity.id}")
    #
    #     # publish the identity
    #     db_proxy = NodeDBProxy(self._job.custodian.rest_address)
    #     identity = db_proxy.update_identity(self._keystore.identity)
    #
    #     return identity

    def _setup_rest_server(self, rest_address: str) -> None:
        app = FastAPI(openapi_url='/openapi.json', docs_url='/docs')

        # setup CORS
        app.add_middleware(
            CORSMiddleware,
            allow_origins=['*'],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # register endpoints
        app.get(JOB_ENDPOINT_PREFIX + '/status', response_model=JobStatus,
                description=self.job_status.__doc__)(self.job_status)
        app.put(JOB_ENDPOINT_PREFIX + '/cancel', response_model=JobStatus,
                description=self.job_cancel.__doc__)(self.job_cancel)

        # create and start the REST server
        address = rest_address.split(":")
        server_thread = threading.Thread(target=uvicorn.run, args=(app,), daemon=True,
                                         kwargs={"host": address[0], "port": int(address[1]), "log_level": "info"})
        server_thread.start()

    def _store_value_input_data_objects(self) -> None:
        for item in self._job.task.input:
            # if it is a 'value' input then store it to the working directory
            if item.type == 'value':
                # write the content
                with open(os.path.join(self._wd_path, item.name), 'w') as f:
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
        db_proxy = NodeDBProxy(self._job.custodian.rest_address)
        network = db_proxy.get_network()
        network = [node for node in network if node.dor_service]

        # try to fetch all referenced data objects using the P2P protocol
        protocol = DataObjectRepositoryP2PProtocol((self._user, self._wd_path))
        pending: Dict[str, str] = {item.obj_id: item.user_signature for item in relevant.values()}
        found: Dict[str, str] = {}
        for peer in network:
            # does the remote DOR have any of the pending data objects?
            try:
                result: Dict[str, DataObject] = protocol.lookup(peer.p2p_address, list(pending.keys()))
            except Exception:
                continue

            # process the results (if any)
            for obj_id, meta in result.items():
                meta_path = os.path.join(self._wd_path, f"{obj_id}.meta")
                content_path = os.path.join(self._wd_path, f"{obj_id}.content")

                # store the meta information
                with open(meta_path, 'w') as f:
                    json.dump(meta.dict(), f, indent=2)

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
                    protocol.fetch(peer.p2p_address, obj_id, meta_path, content_path, self._user.id, signature)

                else:
                    # try to download it
                    protocol.fetch(peer.p2p_address, obj_id, meta_path, content_path)

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
        db_proxy = NodeDBProxy(self._job.custodian.rest_address)
        for o in self._job.task.output:
            owner = db_proxy.get_identity(o.owner_iid)
            if owner is None:
                raise DataObjectOwnerNotFoundError({
                    'output_name': o.name,
                    'owner_iid': o.owner_iid
                })

    def remove_pending_output(self, obj_name: str, result: Union[DataObject, JobStatus.Error]) -> None:
        with self._mutex:
            self._pending_output.remove(obj_name)

            if isinstance(result, DataObject):
                self._job_status.output[obj_name] = result
                self._store_job_status()

            else:
                self._job_status.errors.append(result)
                self._failed_output.add(obj_name)

    def has_pending_output(self) -> bool:
        with self._mutex:
            return len(self._pending_output) > 0

    def push_data_object(self, obj_name: str) -> DataObject:
        # convenience variables
        task_out_items = {item.name: item for item in self._job.task.output}
        task_out = task_out_items[obj_name]
        output_spec = self._output_interface[obj_name]

        # check if the output data object exists
        output_content_path = os.path.join(self._wd_path, obj_name)
        if not os.path.isfile(output_content_path):
            raise DataObjectContentNotFoundError({
                'output_name': obj_name,
                'content_path': output_content_path
            })

        # get the owner
        db_proxy = NodeDBProxy(self._job.custodian.rest_address)
        owner = db_proxy.get_identity(task_out.owner_iid)
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

        # do we have a target node specified for storing the data object?
        target_address = self._job.custodian.rest_address
        if task_out.target_node_iid:
            # check with the node db to see if we know about this node
            network = {item.identity.id: item for item in db_proxy.get_network()}
            if task_out.target_node_iid not in network:
                raise CLIRuntimeError("Target node not found in network", details={
                    'target_node_iid': task_out.target_node_iid,
                    'network': network
                })

            # extract the rest address from that node record
            node = network[task_out.target_node_iid]
            target_address = node.rest_address

        # check if the target node has DOR capabilities
        dor_proxy = NodeDBProxy(target_address)
        node = dor_proxy.get_node()
        if not node.dor_service:
            raise CLIRuntimeError("Target node does not support DOR capabilities", details={
                'target_address': target_address,
                'node': node.dict()
            })

        # determine recipe
        recipe = {
            'name': obj_name,
            'processor': self._gpp.dict(),
            'consumes': {},
            'product': {
                'c_hash': '',  # valid content hash will be set by the DOR
                'data_type': output_spec.data_type,
                'data_format': output_spec.data_format,
                'content': None
            }
        }

        # update recipe inputs
        for item0 in self._job.task.input:
            spec = self._input_interface[item0.name]
            if item0.type == 'value':
                recipe['consumes'][item0.name] = {
                    'c_hash': hash_json_object(item0.value).hex(),
                    'data_type': spec.data_type,
                    'data_format': spec.data_format,
                    'content': item0.value
                }
            else:
                recipe['consumes'][item0.name] = {
                    'c_hash': item0.c_hash,
                    'data_type': spec.data_type,
                    'data_format': spec.data_format,
                    'content': None
                }

        # upload the data object to the DOR
        dor_proxy = DORProxy(target_address)
        obj = dor_proxy.add_data_object(output_content_path, owner, restricted_access, content_encrypted,
                                        output_spec.data_type, output_spec.data_format, recipe=recipe,
                                        tags=[
                                            DataObject.Tag(key='name', value=obj_name),
                                            DataObject.Tag(key='job_id', value=self._job.id)
                                        ])

        return obj

    def execute(self, args: dict) -> None:
        prompt_if_missing(args, 'job_path', prompt_for_string, message="Enter path to the job:")
        prompt_if_missing(args, 'proc_path', prompt_for_string, message="Enter path to the processor:")
        prompt_if_missing(args, 'rest_address', prompt_for_string, message="Enter address for REST service:")

        # does the job path exist?
        if not os.path.isdir(args['job_path']):
            raise CLIRuntimeError(f"Job path '{args['job_path']}' does not exist.")
        self._wd_path = args['job_path']
        print(f"Using job path at {self._wd_path}")

        # setup logger
        self._setup_logger(args.get('log_level'))

        try:
            self._logger.info(f"begin processing job at {self._wd_path}")

            # initialise job
            self._initialise_job(args['proc_path'], args.get('proc_name'))

            # create ephemeral keystore
            # self._create_ephemeral_keystore()

            # setup REST server
            self._setup_rest_server(args['rest_address'])

            # update state
            self._job_status.state = JobStatus.State.PREPROCESSING
            self._store_job_status()

            # store by-value input data objects (if any)
            self._store_value_input_data_objects()

            # fetch by-reference input data objects (if any)
            self._fetch_reference_input_data_objects()

            # verify that data types of input data objects match
            self._verify_inputs_and_outputs()

            # update state
            self._job_status.state = JobStatus.State.RUNNING
            self._store_job_status()

            # run the processor
            self._proc.run(self._wd_path, self, self._logger)

            # wait until pending output data objects are taken care of
            while self.has_pending_output():
                time.sleep(0.5)

            # write exit code
            if self._interrupted:
                self._logger.info(f"end processing job at {self._wd_path} -> INTERRUPTED")
                self._write_exitcode(ExitCode.INTERRUPTED)

            elif len(self._failed_output) > 0:
                raise CLIRuntimeError(f"Failed to upload some outputs: {self._failed_output}")

            else:
                # update state
                self._job_status.state = JobStatus.State.SUCCESSFUL
                self._store_job_status()

                msg = f"end processing job at {self._wd_path} -> DONE"
                print(msg)
                self._logger.info(msg)
                self._write_exitcode(ExitCode.DONE)

        except SaaSRuntimeException as e:
            # update state
            if self._job_status:
                self._job_status.state = JobStatus.State.FAILED
                self._store_job_status()

            msg = f"end processing job at {self._wd_path} -> FAILED: {e.reason}"
            print(msg)
            self._logger.error(msg)
            self._write_exitcode(ExitCode.ERROR, e)

        except Exception as e:
            # update state
            if self._job_status:
                self._job_status.state = JobStatus.State.FAILED
                self._store_job_status()

            msg = f"end processing job at {self._wd_path} -> FAILED: {e}"
            print(msg)
            self._logger.error(msg)
            self._write_exitcode(ExitCode.ERROR, e)
