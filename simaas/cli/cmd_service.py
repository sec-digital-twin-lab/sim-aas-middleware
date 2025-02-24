import logging
import os
import signal
import time
import traceback

from InquirerPy.base import Choice

from simaas.cli.helpers import CLICommand, Argument, prompt_for_string, prompt_for_confirmation, prompt_if_missing, \
    default_if_missing, initialise_storage_folder, prompt_for_selection, load_keystore, extract_address
from simaas.core.exceptions import SaaSRuntimeException
from simaas.core.logging import Logging
from simaas.helpers import determine_default_rest_address, determine_default_p2p_address
from simaas.node.base import Node
from simaas.node.default import DefaultNode, DORType, RTIType

logger = Logging.get('cli')

# deactivate annoying DEBUG messages by multipart
logging.getLogger('multipart.multipart').setLevel(logging.WARNING)


class WaitForTermination:
    def __init__(self, node: Node) -> None:
        self._node = node
        self._running = True

    def terminate(self) -> None:
        self._running = False

    def wait_for_termination(self):
        def handle_sigterm(_signum, _frame):
            print("SIGTERM signal received.")
            self.terminate()

        def handle_keyboard_interruption(_signum, _frame):
            print("Keyboard interruption (CTRL+C) received.")
            self.terminate()

        # register signal handler
        signal.signal(signal.SIGTERM, handle_sigterm)
        signal.signal(signal.SIGINT, handle_keyboard_interruption)

        # keep looping until instructed to terminate...
        while self._running:
            try:
                time.sleep(0.5)

            except Exception as e:
                trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
                print(f"Unexpected exception while waiting to receive termination signals: {e} {trace}")
                self.terminate()

        # shut down the node gracefully...
        try:
            print("Shutting down the node...")
            self._node.shutdown()

        except SaaSRuntimeException as e:
            print(f"Exception while shutting down node: {e}")

        except Exception as e:
            trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
            print(f"Unexpected exception while shutting down node: {e} {trace}")


class Service(CLICommand):
    # define the default values
    default_datastore = os.path.join(os.environ['HOME'], '.datastore')
    default_rest_address = determine_default_rest_address()
    default_p2p_address = determine_default_p2p_address()
    default_boot_node_address = determine_default_rest_address()
    default_dor_service = DORType.BASIC.value
    default_rti_service = RTIType.DOCKER.value
    default_retain_job_history = False
    default_strict_deployment = True
    default_bind_all_address = False

    def __init__(self):
        super().__init__('service', 'start a node as service provider', arguments=[
            Argument('--use-defaults', dest="use-defaults", action='store_const', const=True,
                     help="use defaults in case arguments are not specified (or prompt otherwise)"),
            Argument('--datastore', dest='datastore', action='store',
                     help=f"path to the datastore (default: '{self.default_datastore}')"),
            Argument('--rest-address', dest='rest-address', action='store',
                     help=f"address used by the REST service interface (default: '{self.default_rest_address}')."),
            Argument('--p2p-address', dest='p2p-address', action='store',
                     help=f"address used by the P2P service interface (default: '{self.default_p2p_address}')."),
            Argument('--boot-node', dest='boot-node', action='store',
                     help=f"REST address of an existing node for joining a network "
                          f"(default: '{self.default_boot_node_address}')."),
            Argument('--dor-type', dest='dor_type', action='store', choices=[t.value for t in DORType],
                     help=f"indicate the type of DOR service provided by the node "
                          f"(default: '{self.default_dor_service}')."),
            Argument('--rti-type', dest='rti_type', action='store', choices=[t.value for t in RTIType],
                     help=f"indicate the type of RTI service provided by the node "
                          f"(default: '{self.default_rti_service}')."),
            Argument('--retain-job-history', dest="retain-job-history", action='store_const', const=True,
                     help="[for execution/full nodes only] instructs the RTI to retain the job history (default "
                          "behaviour is to delete information of completed jobs). This flag should only be used for "
                          "debug/testing purposes."),
            Argument('--disable-strict-deployment', dest="strict-deployment", action='store_const', const=False,
                     help="[for execution/full nodes only] instructs the RTI to disable strict processor deployment "
                          "(default: enabled, i.e., only the node owner identity can deploy/undeploy processors.)"),
            Argument('--bind-all-address', dest="bind-all-address", action='store_const', const=True,
                     help="allows REST and P2P service to bind and accept connections pointing to any address of the "
                          "machine i.e. 0.0.0.0 (useful for docker)")
        ])

    def execute(self, args: dict) -> None:
        if args['use-defaults']:
            default_if_missing(args, 'datastore', self.default_datastore)
            default_if_missing(args, 'rest-address', self.default_rest_address)
            default_if_missing(args, 'p2p-address', self.default_p2p_address)
            default_if_missing(args, 'boot-node', self.default_boot_node_address)
            default_if_missing(args, 'dor_type', self.default_dor_service)
            default_if_missing(args, 'rti_type', self.default_rti_service)
            default_if_missing(args, 'retain-job-history', self.default_retain_job_history)
            default_if_missing(args, 'strict-deployment', self.default_strict_deployment)
            default_if_missing(args, 'bind-all-address', self.default_bind_all_address)

        else:
            prompt_if_missing(args, 'datastore', prompt_for_string,
                              message="Enter path to datastore:",
                              default=self.default_datastore)
            prompt_if_missing(args, 'rest-address', prompt_for_string,
                              message="Enter address for REST service:",
                              default=self.default_rest_address)
            prompt_if_missing(args, 'p2p-address', prompt_for_string,
                              message="Enter address for P2P service:",
                              default=self.default_p2p_address)
            prompt_if_missing(args, 'boot-node', prompt_for_string,
                              message="Enter REST address of boot node:",
                              default=self.default_boot_node_address)

            if args['dor_type'] is None:
                args['dor_type'] = prompt_for_selection([
                    Choice(DORType.NONE.value, 'None'),
                    Choice(DORType.BASIC.value, 'Basic')
                ], "Select the type of DOR service:")

            if args['rti_type'] is None:
                args['rti_type'] = prompt_for_selection([
                    Choice(RTIType.NONE.value, 'None'),
                    Choice(RTIType.DOCKER.value, 'Docker'),
                    Choice(RTIType.AWS.value, 'AWS')
                ], "Select the type of RTI service:")

            if args['rti_type'] in [RTIType.DOCKER.value, RTIType.AWS.value]:
                prompt_if_missing(args, 'retain-job-history', prompt_for_confirmation,
                                  message='Retain RTI job history?', default=False)
                prompt_if_missing(args, 'bind-all-address', prompt_for_confirmation,
                                  message='Bind service to all network addresses?', default=False)
                prompt_if_missing(args, 'strict-deployment', prompt_for_confirmation,
                                  message='Strict processor deployment?', default=True)

        keystore = load_keystore(args, ensure_publication=False)

        # initialise storage directory (if necessary)
        initialise_storage_folder(args['datastore'], 'datastore')

        # extract host/ports
        rest_service_address = extract_address(args['rest-address'])
        p2p_service_address = args['p2p-address']
        boot_node_address = extract_address(args['boot-node'])

        # create a node instance
        node = DefaultNode.create(keystore, args['datastore'],
                                  p2p_address=p2p_service_address,
                                  rest_address=rest_service_address,
                                  boot_node_address=boot_node_address,
                                  dor_type=DORType[args['dor_type']],
                                  rti_type=RTIType[args['rti_type']],
                                  retain_job_history=args['retain-job-history'],
                                  strict_deployment=args['strict-deployment'],
                                  bind_all_address=args['bind-all-address'])

        # print info message
        if args['rti_type'] == RTIType.NONE.value:
            print(
                f"Created '{args['dor_type']}/{args['rti_type']}' Sim-aaS node instance at "
                f"{args['rest-address']}/{args['p2p-address']}"
            )
        else:
            print(
                f"Created '{args['dor_type']}/{args['rti_type']}' Sim-aaS node instance at "
                f"{args['rest-address']}/{args['p2p-address']}"
                f"(keep RTI job history: {'Yes' if args['retain-job-history'] else 'No'}) "
                f"(strict: {'Yes' if args['strict-deployment'] else 'No'}) "
            )

        # wait for termination...
        WaitForTermination(node).wait_for_termination()
