import datetime
import json
import os
from json import JSONDecodeError
from typing import List, Union, Optional, Dict, Tuple

import jsonschema
from InquirerPy.base import Choice
from pydantic import ValidationError
from tabulate import tabulate

from simaas.cli.exceptions import CLIRuntimeError
from simaas.cli.helpers import CLICommand, Argument, prompt_if_missing, prompt_for_string, prompt_for_selection, \
    get_nodes_by_service, prompt_for_confirmation, load_keystore, extract_address, label_data_object, shorten_id, \
    label_identity, default_if_missing
from simaas.core.logging import Logging
from simaas.dor.api import DORProxy
from simaas.helpers import determine_default_rest_address
from simaas.nodedb.api import NodeDBProxy
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.rest.exceptions import UnsuccessfulRequestError
from simaas.rti.api import RTIProxy
from simaas.rti.schemas import Processor, Task, JobStatus, Job, ProcessorVolume
from simaas.dor.schemas import ProcessorDescriptor, DataObject

logger = Logging.get('cli')


def _require_rti(args: dict) -> RTIProxy:
    prompt_if_missing(args, 'address', prompt_for_string,
                      message="Enter the node's REST address",
                      default=determine_default_rest_address())

    db = NodeDBProxy(extract_address(args['address']))
    if db.get_node().rti_service.lower() == 'none':
        raise CLIRuntimeError(f"Node at {args['address'][0]}:{args['address'][1]} does "
                              f"not provide a RTI service. Aborting.")

    return RTIProxy(extract_address(args['address']))


def _require_rti_with_type(args: dict) -> Tuple[RTIProxy, str]:
    prompt_if_missing(args, 'address', prompt_for_string,
                      message="Enter the node's REST address",
                      default=determine_default_rest_address())

    db = NodeDBProxy(extract_address(args['address']))
    rti_type = db.get_node().rti_service.lower()
    if rti_type == 'none':
        raise CLIRuntimeError(f"Node at {args['address'][0]}:{args['address'][1]} does "
                              f"not provide a RTI service. Aborting.")

    return RTIProxy(extract_address(args['address'])), rti_type


def proc_info(proc: Processor) -> str:
    if proc.gpp:
        return (f"{shorten_id(proc.id)}: {proc.gpp.proc_descriptor.name} [{proc.state}] "
                f"{proc.gpp.repository}@{proc.gpp.commit_id[:6]}...")
    else:
        return f"{shorten_id(proc.id)} [{proc.state}] (no GPP available yet)"


def job_label(job: Job, status: JobStatus, deployed: Dict[str, Processor]) -> str:
    proc_name = deployed[job.task.proc_id].gpp.proc_descriptor.name \
        if job.task.proc_id in deployed else '(unknown processor)'

    result = f"{job.id} [{status.state}] {shorten_id(job.task.user_iid)}@{proc_name}"
    if job.task.description:
        result = result + f" {job.task.description}"
    return result


# define the default values
default_datastore = os.path.join(os.environ['HOME'], '.datastore')


class RTIVolumeList(CLICommand):
    def __init__(self) -> None:
        super().__init__('list', 'show a list of volume references', arguments=[
            Argument('--datastore', dest='datastore', action='store',
                     help=f"path to the datastore (default: '{default_datastore}')"),
        ])

    def execute(self, args: dict) -> Optional[List[dict]]:
        default_if_missing(args, 'datastore', default_datastore)

        # does the volume references file exist?
        vol_refs_path = os.path.join(args['datastore'], 'volume-references.json')
        if not os.path.isfile(vol_refs_path):
            print("No volume references found.")
            return []

        # load the volume reference file
        with open(vol_refs_path, 'r') as f:
            vol_refs = json.load(f)

        # do we have any references?
        if len(vol_refs) == 0:
            print("No volume references found.")
            return []

        # headers
        lines = [
            ['VOLUME NAME', 'TYPE', 'REFERENCE'],
            ['-----------', '----', '---------']
        ]

        # prepare lines unsorted
        for item in vol_refs:
            lines.append([item['name'], item['type'], item['reference']])

        print(f"Found {len(lines)-1} volume references:")
        print(tabulate(lines, tablefmt="plain"))
        print()

        return vol_refs


class RTIVolumeCreateFSRef(CLICommand):
    def __init__(self) -> None:
        super().__init__('fs', 'create filesystem volume reference', arguments=[
            Argument('--datastore', dest='datastore', action='store',
                     help=f"path to the datastore (default: '{default_datastore}')"),
            Argument('--name', dest='name', action='store', help="the name for this volume reference"),
            Argument('--path', dest='path', action='store', help="the path to the local filesystem location")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        default_if_missing(args, 'datastore', default_datastore)

        # does the volume references file exist?
        vol_refs_path = os.path.join(args['datastore'], 'volume-references.json')
        if os.path.isfile(vol_refs_path):
            # load the volume reference file
            with open(vol_refs_path, 'r') as f:
                vol_refs = json.load(f)
        else:
            vol_refs = []

        # get the name for the reference
        prompt_if_missing(args, 'name', prompt_for_string,
                          message="Enter the name of volume reference:", allow_empty=False)
        for item in vol_refs:
            if item['name'] == args['name']:
                raise CLIRuntimeError(f"Volume reference with name '{args['name']}' already exists")

        # get the path for the reference
        prompt_if_missing(args, 'path', prompt_for_string,
                          message="Enter the path to the local filesystem location:", allow_empty=False)
        if not os.path.isdir(args['path']):
            raise CLIRuntimeError(f"Location '{args['path']}' does not exist or is not a directory")

        reference = {
            'name': args['name'],
            'type': 'fs',
            'reference': {
                'path': args['path']
            }
        }

        # store the updated volume references
        vol_refs.append(reference)
        with open(vol_refs_path, 'w') as f:
            json.dump(vol_refs, f, indent=2)

        return reference


class RTIVolumeCreateEFSRef(CLICommand):
    def __init__(self) -> None:
        super().__init__('efs', 'create AWS elastic filesystem volume reference', arguments=[
            Argument('--datastore', dest='datastore', action='store',
                     help=f"path to the datastore (default: '{default_datastore}')"),
            Argument('--name', dest='name', action='store', help="the name for this volume reference"),
            Argument('--efs-fs-id', dest='efs_fs_id', action='store', help="the AWS EFS filesystem Id")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        default_if_missing(args, 'datastore', default_datastore)

        # does the volume references file exist?
        vol_refs_path = os.path.join(args['datastore'], 'volume-references.json')
        if os.path.isfile(vol_refs_path):
            # load the volume reference file
            with open(vol_refs_path, 'r') as f:
                vol_refs = json.load(f)
        else:
            vol_refs = []

        # get the name for the reference
        prompt_if_missing(args, 'name', prompt_for_string,
                          message="Enter the name of volume reference:", allow_empty=False)
        for item in vol_refs:
            if item['name'] == args['name']:
                raise CLIRuntimeError(f"Volume reference with name '{args['name']}' already exists")

        # get the EFS FS id for the reference
        prompt_if_missing(args, 'efs_fs_id', prompt_for_string,
                          message="Enter the path to the local filesystem location:", allow_empty=False)

        reference = {
            'name': args['name'],
            'type': 'efs',
            'reference': {
                'efsFileSystemId': args['efs_fs_id'],
                'rootDirectory': '/',
                'transitEncryption': 'ENABLED'
            }
        }

        # store the updated volume references
        vol_refs.append(reference)
        with open(vol_refs_path, 'w') as f:
            json.dump(vol_refs, f, indent=2)

        return reference


class RTIVolumeDelete(CLICommand):
    def __init__(self):
        super().__init__('delete', 'delete volume references', arguments=[
            Argument('--datastore', dest='datastore', action='store',
                     help=f"path to the datastore (default: '{default_datastore}')"),
            Argument('--name', metavar='name', type=str, nargs='*',
                     help="the name of the volume references to be deleted"),
        ])

    def execute(self, args: dict) -> Optional[List[dict]]:
        default_if_missing(args, 'datastore', default_datastore)

        # does the volume references file exist?
        vol_refs_path = os.path.join(args['datastore'], 'volume-references.json')
        if not os.path.isfile(vol_refs_path):
            print("No volume references found.")
            return []

        # load the volume reference file
        with open(vol_refs_path, 'r') as f:
            vol_refs = json.load(f)

        # do we have any references?
        if len(vol_refs) == 0:
            print("No volume references found.")
            return []

        # determine the choices
        if not args['name'] or len(args['name']) == 0:
            choices = []
            for item in vol_refs:
                if item['name'] in args['name']:
                    choices.append(Choice(value=item['name'], name=f"{item['name']} ({item['type']})"))
            selected: List[str] = prompt_for_selection(choices, message="Select the volumes:", allow_multiple=True)

        else:
            selected: List[str] = args['name']

        # remove the items
        filtered = []
        removed = []
        for item in vol_refs:
            if item['name'] not in selected:
                filtered.append(item)
            else:
                removed.append(item)

        with open(vol_refs_path, 'w') as f:
            json.dump(filtered, f, indent=2)

        return removed


class RTIProcDeploy(CLICommand):
    def __init__(self) -> None:
        super().__init__('deploy', 'deploys a processor', arguments=[
            Argument('--proc-id', dest='proc_id', action='store',
                     help="the id of the PDI data object of the processor to be deployed"),
            Argument('--datastore', dest='datastore', action='store',
                     help=f"path to the datastore (default: '{default_datastore} - required for volumes')"),
            Argument('volumes', metavar='volumes', type=str, nargs='*',
                     help="attach volumes (use this format: '<volume name>:<mount point>:<read only>', "
                          "e.g., 'ref_1:/mnt/storage:true'")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        default_if_missing(args, 'datastore', default_datastore)

        rti, rti_type = _require_rti_with_type(args)
        keystore = load_keystore(args, ensure_publication=False)

        # discover nodes by service
        nodes, _ = get_nodes_by_service(extract_address(args['address']))
        if len(nodes) == 0:
            raise CLIRuntimeError("Could not find any nodes with DOR service in the network. Try again later.")

        # lookup all the PDI data objects
        choices = []
        custodian = {}
        for node in nodes:
            dor = DORProxy(node.rest_address)
            try:
                result = dor.search(data_type='ProcessorDockerImage')
                for item in result:
                    pdi: DataObject = dor.get_meta(item.obj_id)
                    proc_descriptor = ProcessorDescriptor.model_validate(pdi.tags['proc_descriptor'])

                    choices.append(Choice(pdi.obj_id, f"{proc_descriptor.name}:{pdi.tags['content_hash'][:6]} "
                                                      f"<{shorten_id(pdi.obj_id)}> "
                                                      f"{pdi.tags['repository']}:{pdi.tags['commit_id'][:6]}..."))
                    custodian[item.obj_id] = node

            except Exception:
                logger.warning(f"Failed to send request (dor.search) to "
                               f"node {node.identity.id} at {node.rest_address}")

        # do we have any processors to choose from?
        if len(choices) == 0:
            raise CLIRuntimeError("No processors found for deployment. Aborting.")

        # do we have a processor id?
        interactive = False
        if args['proc_id'] is None:
            interactive = True
            args['proc_id'] = prompt_for_selection(
                choices, "Select the processor you would like to deploy:", allow_multiple=False
            )

        # do we have a custodian for this processor id?
        if args['proc_id'] not in custodian:
            raise CLIRuntimeError(f"Custodian of processor {shorten_id(args['proc_id'])} not found. Aborting.")

        # get all the available references and filter by RTI type
        vol_refs_path = os.path.join(args['datastore'], 'volume-references.json')
        if os.path.isfile(vol_refs_path):
            with open(vol_refs_path, 'r') as f:
                vol_refs = json.load(f)
        else:
            vol_refs = []

        eligible: Dict[str, dict] = {}
        eligible_fs_type = {
            'docker': 'fs',
            'aws': 'efs'
        }.get(rti_type)
        choices: List[Choice] = []
        for item in vol_refs:
            if item['type'] == eligible_fs_type:
                eligible[item['name']] = item['reference']
                choices.append(Choice(value=item['name'], name=f"{item['name']}: {item['reference']}"))

        # attach volumes (if any)
        volumes: List[ProcessorVolume] = []
        if interactive:
            if len(eligible) == 0:
                print("No eligible volumes found to attach.")
            else:
                if prompt_for_confirmation(
                        f"Attach volumes to this processor deployment (found {len(eligible)} eligible volumes)?",
                        default=False
                ):
                    selection = prompt_for_selection(choices, message="Select volumes to attach:", allow_multiple=True)

                    for volume in selection:
                        mount_point = prompt_for_string(f"Enter mount point for volume '{volume}':", allow_empty=False)
                        read_only = prompt_for_confirmation(f"Enter if volume '{volume}' is read only:", default=True)
                        volumes.append(ProcessorVolume(
                            name=volume, mount_point=mount_point, read_only=read_only, reference=eligible[volume]
                        ))
        elif args.get('volumes'):
            for item in args['volumes']:
                volume, mount_point, read_only = item.split(':')
                read_only = bool(read_only)

                # is this volume eligible?
                if volume not in eligible:
                    raise CLIRuntimeError(f"Volume '{volume}' not found or not eligible for mounting")

                volumes.append(ProcessorVolume(
                    name=volume, mount_point=mount_point, read_only=read_only, reference=eligible[volume]
                ))

        # deploy the processor
        print(f"Deploying processor {shorten_id(args['proc_id'])}...", end='')
        result = {}
        try:
            result['proc'] = rti.deploy(args['proc_id'], keystore, volumes=volumes if volumes else None)
            print("Done")

        except UnsuccessfulRequestError as e:
            print(f"{e.reason} details: {e.details}")

        return result


class RTIProcUndeploy(CLICommand):
    def __init__(self):
        super().__init__('undeploy', 'undeploys a processor', arguments=[
            Argument('--proc-id', metavar='proc_id', type=str, nargs='*',
                     help="the ids of the processors to be undeployed"),
            Argument('--force', dest="force", action='store_const', const=True,
                     help="Force undeployment even if there are still active jobs."),
        ])

    def execute(self, args: dict) -> Optional[dict]:
        rti = _require_rti(args)
        keystore = load_keystore(args, ensure_publication=False)

        # get the deployed processors
        deployed = {proc.id: proc for proc in rti.get_all_procs()}
        if len(deployed) == 0:
            raise CLIRuntimeError(f"No processors deployed at {args['address']}. Aborting.")

        # do we have a proc_id?
        if not args['proc_id']:
            choices = [Choice(proc.id, proc_info(proc)) for proc in rti.get_all_procs()]
            if not choices:
                raise CLIRuntimeError(f"No processors deployed at {args['address']}")

            args['proc_id'] = prompt_for_selection(choices, message="Select the processor:", allow_multiple=True)

        # do we have a selection?
        if len(args['proc_id']) == 0:
            raise CLIRuntimeError("No processors selected. Aborting.")

        # are the processors deployed?
        result = {}
        for proc_id in args['proc_id']:
            if proc_id not in deployed:
                print(f"Processor {proc_id} is not deployed at {args['address']}. Skipping.")
                continue

            # are there any jobs pending for this processor?
            jobs = rti.get_jobs_by_proc(proc_id)
            if len(jobs) > 0 and not args['force']:
                if not prompt_for_confirmation(f"Processor {proc_id} has pending/active jobs. Proceed to undeploy "
                                               f"processor? If yes, all pending/active jobs will be purged.",
                                               default=False):
                    continue

            # undeploy the processor
            print(f"Undeploying processor {proc_id}...", end='')
            try:
                result[proc_id] = rti.undeploy(proc_id, keystore)
                print("Done")
            except UnsuccessfulRequestError as e:
                print(f"{e.reason} details: {e.details}")

        return result


class RTIProcList(CLICommand):
    def __init__(self) -> None:
        super().__init__('list', 'retrieves a list of all deployed processors', arguments=[])

    def execute(self, args: dict) -> Optional[dict]:
        rti = _require_rti(args)
        deployed = rti.get_all_procs()
        if len(deployed) == 0:
            print(f"No processors deployed at {args['address']}")
        else:
            print(f"Found {len(deployed)} processor(s) deployed at {args['address']}:")
            for proc in deployed:
                print(f"- {proc_info(proc)}")

        return {
            'deployed': deployed
        }


class RTIProcShow(CLICommand):
    def __init__(self) -> None:
        super().__init__('show', 'show details of a deployed processor', arguments=[
            Argument('--proc-id', dest='proc_id', action='store',
                     help="the id of the processor")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        rti = _require_rti(args)

        # do we have a proc_id?
        if not args['proc_id']:
            choices = [Choice(proc.id, proc_info(proc)) for proc in rti.get_all_procs()]
            if not choices:
                raise CLIRuntimeError(f"No processors deployed at {args['address']}")

            args['proc_id'] = prompt_for_selection(choices, message="Select the processor:", allow_multiple=False)

        # get the proc and the jobs
        proc = rti.get_proc(args['proc_id'])
        jobs = rti.get_jobs_by_proc(proc.id)

        # print detailed information
        print("Processor Information:")
        print(f"- Id: {proc.id}")
        print(f"- State: {proc.state}")
        if proc.gpp:
            proc_desc = proc.gpp.proc_descriptor
            print(f"- Name: {proc_desc.name}")
            print(f"- Image: {proc.image_name}")
            input_items = '\n   '.join([f"{i.name} -> {i.data_type}:{i.data_format}" for i in proc_desc.input])
            print(f"- Input:\n   {input_items}")
            output_items = '\n   '.join([f"{i.name} -> {i.data_type}:{i.data_format}" for i in proc_desc.output])
            print(f"- Output:\n   {output_items}")
        else:
            print(f"- Image: {proc.image_name}")
        print(f"- Error: {proc.error if proc.error else '(none)'}")
        print(f"- Jobs: {[job.id for job in jobs] if len(jobs) > 0 else '(none)'}")

        return {
            'processor': proc,
            'jobs': jobs
        }


class RTIJobSubmit(CLICommand):
    def __init__(self) -> None:
        super().__init__('submit', 'submit a new job', arguments=[
            Argument('task', metavar='task', type=str, nargs='?', help="path to a task descriptor")
        ])

    def _prepare(self, args: dict) -> None:
        address = extract_address(args['address'])

        # create identity choices
        self._db = NodeDBProxy(address)
        self._identity_choices = {}
        for identity in self._db.get_identities().values():
            self._identity_choices[identity.id] = Choice(identity, label_identity(identity))

        # create node choices
        self._dor = None
        self._node_choices = []
        for node in self._db.get_network():
            # does the node have a DOR?
            if node.dor_service is False:
                continue

            # use the fist eligible node
            if self._dor is None:
                self._dor = DORProxy(node.rest_address)

            # get the identity of the node
            identity = self._db.get_identity(node.identity.id)

            # add the choice
            self._node_choices.append(
                Choice(node, f"{shorten_id(identity.id)}: {identity.name} at "
                             f"{node.rest_address[0]}:{node.rest_address[1]}")
            )

        # create processor choices
        self._rti = RTIProxy(address)
        no_gpp_procs = []
        self._proc_choices = {}
        for proc in self._rti.get_all_procs():
            if proc.gpp is None:
                no_gpp_procs.append(shorten_id(proc.id))
            else:
                self._proc_choices[proc.id] = Choice(proc, proc_info(proc))

        # do we have processors without GPPs?
        if len(no_gpp_procs) > 0:
            print(f"Ignoring processors without GPP (they are probably still deploying): {', '.join(no_gpp_procs)}")

        # do we have processor choices?
        if not self._proc_choices:
            raise CLIRuntimeError(f"No processors deployed at {address[0]}:{address[1]}. Aborting.")

    def _create_job_input(self, proc_desc: ProcessorDescriptor) -> List[Union[Task.InputReference, Task.InputValue]]:
        job_input = []
        for item in proc_desc.input:
            print(f"Specify input interface item "
                  f"\033[1m'{item.name}'\033[0m with data type/format "
                  f"\033[1m{item.data_type}/{item.data_format}\033[0m")
            selection = prompt_for_selection([Choice('value', 'by-value'), Choice('reference', 'by-reference')],
                                             "How to specify?")

            if selection == 'value':
                while True:
                    if item.data_schema:
                        print(f"JSON schema available: \033[1m yes\033[0m\n{json.dumps(item.data_schema, indent=2)}")
                    else:
                        print("JSON schema available: \033[1m no\033[0m")
                    content = prompt_for_string("Enter a valid JSON object:")

                    try:
                        content = json.loads(content)
                    except JSONDecodeError as e:
                        print(f"Problem while parsing JSON object: {e.msg}. Try again.")
                        continue

                    if item.data_schema:
                        try:
                            jsonschema.validate(instance=content, schema=item.data_schema)

                        except jsonschema.exceptions.ValidationError as e:
                            logger.error(e.message)
                            continue

                        except jsonschema.exceptions.SchemaError as e:
                            logger.error(e.message)
                            raise CLIRuntimeError("Schema used for input is not valid", details={
                                'schema': item.data_schema
                            })

                    job_input.append(Task.InputValue(name=item.name, type='value', value=content))
                    break

            else:
                # get the data object choices for this input item
                object_choices = []
                for found in self._dor.search(data_type=item.data_type, data_format=item.data_format):
                    object_choices.append(Choice(found.obj_id, label_data_object(found)))

                # do we have any matching objects?
                if len(object_choices) == 0:
                    raise CLIRuntimeError(f"No data objects found that match data type/format ({item.data_type}/"
                                          f"{item.data_format}) of input '{item.name}'. Aborting.")

                # select an object
                obj_id = prompt_for_selection(object_choices,
                                              message=f"Select the data object to be used for input '{item.name}':",
                                              allow_multiple=False)

                job_input.append(Task.InputReference(name=item.name, type='reference', obj_id=obj_id,
                                                     user_signature=None, c_hash=None))

        return job_input

    def _create_job_output(self, proc_desc: ProcessorDescriptor) -> List[Task.Output]:
        # select the owner for the output data objects
        owner = prompt_for_selection(list(self._identity_choices.values()),
                                     message="Select the owner for the output data objects:",
                                     allow_multiple=False)

        # select the target node for the output data objects
        target = prompt_for_selection(self._node_choices,
                                      message="Select the destination node for the output data objects:",
                                      allow_multiple=False)

        # confirm if access should be restricted
        restricted_access = prompt_for_confirmation("Should access to output data objects be restricted?",
                                                    default=False)

        # create the job output
        job_output = []
        for item in proc_desc.output:
            job_output.append(Task.Output(
                name=item.name, owner_iid=owner.id, restricted_access=restricted_access, content_encrypted=False,
                target_node_iid=target.identity.id
            ))

        return job_output

    def execute(self, args: dict) -> Optional[dict]:
        keystore = load_keystore(args, ensure_publication=True)

        prompt_if_missing(args, 'address', prompt_for_string,
                          message="Enter the target node's REST address:",
                          default=determine_default_rest_address())

        # do some preparation
        self._prepare(args)

        tasks: List[Task] = []

        # do we have a task descriptor?
        if args['task']:
            for task_path in args['task']:
                # does the file exist?
                if not os.path.isfile(task_path):
                    raise CLIRuntimeError(f"No task descriptor at '{task_path}'. Aborting.")

                # read the job descriptor
                try:
                    with open(task_path, 'r') as f:
                        task = Task.model_validate(json.load(f))
                except ValidationError as e:
                    raise CLIRuntimeError(f"Invalid task descriptor: {e.errors()}. Aborting.")

                # is the processor deployed?
                if task.proc_id not in self._proc_choices:
                    raise CLIRuntimeError(f"Processor {task.proc_id} is not deployed at {args['address']}. "
                                          f"Aborting.")

                tasks.append(task)

        # if we don't have a job descriptor then we obtain all the information interactively
        else:
            while True:
                # select the processor
                proc: Processor = prompt_for_selection(choices=list(self._proc_choices.values()),
                                                       message="Select the processor for this task:",
                                                       allow_multiple=False)

                # configure input/output for the task
                task_input = self._create_job_input(proc.gpp.proc_descriptor)
                task_output = self._create_job_output(proc.gpp.proc_descriptor)

                # ask for a task name
                name = prompt_for_string(message="Give the task a name:", allow_empty=False)

                # ask for a budget
                budgets = [
                    (1, 2048),
                    (2, 2*2048),
                    (4, 4*2048),
                    (8, 8*2048),
                ]

                budget_idx = prompt_for_selection(
                    choices=[
                        Choice(i, f"{budgets[i][0]} vCPUs, {budgets[i][1]} GB RAM memory") for i in range(len(budgets))
                    ],
                    message="Select a budget for this task:",
                    allow_multiple=False
                )

                # create the task
                tasks.append(Task(
                    proc_id=proc.id,
                    user_iid=keystore.identity.id,
                    input=task_input,
                    output=task_output,
                    name=name,
                    description=None,
                    budget=ResourceDescriptor(vcpus=budgets[budget_idx][0], memory=budgets[budget_idx][1]),
                    namespace=None
                ))

                if not prompt_for_confirmation(
                    "Add another task? Note: multiple tasks submitted together will be executed as batch.",
                    default=False
                ):
                    break

        # submit the job
        result = self._rti.submit(tasks, with_authorisation_by=keystore)
        batch_id: Optional[str] = result[0].batch_id
        if batch_id:
            print(f"Batch submitted: {batch_id}")
            for job in result:
                print(f"- Task '{job.task.name}' executed by job {job.id}")

            return {
                'jobs': result
            }

        else:
            job: Job = result[0]
            print(f"Job submitted: {job.id}")

            return {
                'job': job
            }


class RTIJobList(CLICommand):
    def __init__(self):
        super().__init__('list', 'retrieve a list of all jobs by the user (or all jobs if the user is the node owner)',
                         arguments=[
                             Argument('--period', dest='period', action='store',
                                      help="time period to consider using format <number><unit> where unit can be one "
                                           "of these ('h': hours, 'd': days, 'w': weeks). Default is '1d', i.e., one "
                                           "day.")
                         ])

    def execute(self, args: dict) -> Optional[dict]:
        rti = _require_rti(args)
        keystore = load_keystore(args, ensure_publication=True)

        # determine time period
        if 'period' in args and args['period'] is not None:
            try:
                unit = args['period'][-1:]
                number = int(args['period'][:-1])
                multiplier = {'h': 1, 'd': 24, 'w': 7*24}
                period = number * multiplier[unit]
                print(f"Listing all jobs submitted within time period of {number}{unit} -> {period} hours")

            except Exception:
                print(f"Invalid time period '{args['period']}. Listing currently active jobs only.")
                period = None
        else:
            print("No time period provided. Listing currently active jobs only.")
            period = None

        # get all jobs in that time period
        try:
            jobs = rti.get_jobs_by_user(keystore, period=period)
            if jobs:
                # get all deployed procs
                deployed: dict[str, Processor] = {proc.id: proc for proc in rti.get_all_procs()}

                # headers
                lines = [
                    ['SUBMITTED', 'JOB ID', 'OWNER', 'PROC NAME', 'STATE', 'DESCRIPTION'],
                    ['---------', '------', '-----', '---------', '-----', '-----------']
                ]

                # prepare lines unsorted
                unsorted = []
                for job in jobs:
                    proc_name = deployed[job.task.proc_id].gpp.proc_descriptor.name \
                        if job.task.proc_id in deployed else 'unknown'

                    status = rti.get_job_status(job.id, with_authorisation_by=keystore)

                    unsorted.append([job.t_submitted, job.id, shorten_id(job.task.user_iid), proc_name, status.state,
                                     job.task.description if job.task.description else 'none'])

                # sort and add to lines
                for line in sorted(unsorted, key=lambda x: x[0]):
                    line[0] = datetime.datetime.fromtimestamp(line[0]/1000.0).strftime('%Y-%m-%d %H:%M:%S')
                    lines.append(line)

                print(tabulate(lines, tablefmt="plain"))
                print()

            else:
                print("No jobs found.")

            return {
                'jobs': jobs
            }

        except UnsuccessfulRequestError as e:
            print(e.reason)


class RTIJobStatus(CLICommand):
    def __init__(self):
        super().__init__('status', 'retrieve the status of a job', arguments=[
            Argument('job-id', metavar='job-id', type=str, nargs='?', help="the id of the job"),
            Argument('--period', dest='period', action='store',
                     help="time period to consider using format <number><unit> where unit can be one "
                          "of these ('h': hours, 'd': days, 'w': weeks). Default is '1d', i.e., one "
                          "day.")

        ])

    def execute(self, args: dict) -> Optional[dict]:
        rti = _require_rti(args)
        keystore = load_keystore(args, ensure_publication=True)

        # do we have a job id?
        if not args['job-id']:
            # get all deployed procs
            deployed: Dict[str, Processor] = {proc.id: proc for proc in rti.get_all_procs()}

            # ask for time period
            if 'period' not in args or args['period'] is None:
                args['period'] = prompt_for_string("Enter a valid time period (or leave blank for active jobs only):",
                                                   default='1d', allow_empty=True)

            # interpret time period
            if args['period'] == '':
                print("No time period provided. Listing currently active jobs only.")
                period = None
            else:
                try:
                    unit = args['period'][-1:]
                    number = int(args['period'][:-1])
                    multiplier = {'h': 1, 'd': 24, 'w': 7 * 24}
                    period = number * multiplier[unit]
                    print(f"Listing all jobs submitted within time period of {number}{unit} -> {period} hours")

                except Exception:
                    print(f"Invalid time period '{args['period']}. Listing currently active jobs only.")
                    period = None

            # get all jobs by this user and select
            choices = []
            for job in rti.get_jobs_by_user(keystore, period=period):
                status = rti.get_job_status(job.id, with_authorisation_by=keystore)
                choices.append(Choice(job.id, job_label(job, status, deployed)))

            if not choices:
                raise CLIRuntimeError("No jobs found.")

            args['job-id'] = prompt_for_selection(choices, message="Select job:", allow_multiple=False)

        result = {}
        try:
            status = rti.get_job_status(args['job-id'], with_authorisation_by=keystore)
            result['status'] = status
            print(f"Job status:\n{json.dumps(status.model_dump(), indent=4)}")

        except UnsuccessfulRequestError:
            print(f"No status for job {args['job-id']}.")

        return result


class RTIJobCancel(CLICommand):
    def __init__(self):
        super().__init__('cancel', 'attempts to cancel a job', arguments=[
            Argument('job-id', metavar='job-id', type=str, nargs='?', help="the id of the job"),
            Argument('--purge', dest="purge", action='store_const', const=True,
                     help="Attempts to cancel the job and, regardless of the outcome, "
                          "removes the job from the database.")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        rti = _require_rti(args)
        keystore = load_keystore(args, ensure_publication=True)

        # do we have a job id?
        if not args['job-id']:
            # get all deployed procs
            deployed: dict[str, Processor] = {proc.id: proc for proc in rti.get_all_procs()}

            # get all jobs by this user and select
            choices = []
            for job in rti.get_jobs_by_user(keystore):
                # don't show jobs that are not running
                status = rti.get_job_status(job.id, with_authorisation_by=keystore)
                if status.state not in [JobStatus.State.SUCCESSFUL.value, JobStatus.State.FAILED.value,
                                        JobStatus.State.CANCELLED.value]:
                    choices.append(Choice(job.id, job_label(job, status, deployed)))

            if not choices:
                raise CLIRuntimeError("No active jobs found.")

            args['job-id'] = prompt_for_selection(choices, message="Select job:", allow_multiple=False)

        result = {}
        try:
            if args.get('purge'):
                status = rti.purge_job(args['job-id'], with_authorisation_by=keystore)
                print(f"Job {args['job-id']} purged. Last status:\n{json.dumps(status.model_dump(), indent=4)}")
            else:
                status = rti.cancel_job(args['job-id'], with_authorisation_by=keystore)
                print(f"Job {args['job-id']} cancelled. Last status:\n{json.dumps(status.model_dump(), indent=4)}")

            result['status'] = status

        except UnsuccessfulRequestError:
            print(f"Job {args['job-id']} not found.")

        return result
