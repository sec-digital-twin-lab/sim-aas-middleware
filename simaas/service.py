import os
import signal
import sys
import time
import traceback
from typing import List

from simaas.cli.exceptions import CLIRuntimeError
from simaas.cli.helpers import CLIParser, Argument, CLICommand, default_if_missing, initialise_storage_folder, \
    extract_address
from simaas.core.exceptions import SaaSRuntimeException
from simaas.core.keystore import Keystore
from simaas.helpers import determine_default_rest_address, determine_default_p2p_address
from simaas.node.default import DefaultNode, DORType, RTIType


class RunNode(CLICommand):
    # define the default values
    default_datastore = os.path.join(os.environ['HOME'], '.datastore')
    default_rest_address = determine_default_rest_address()
    default_p2p_address = determine_default_p2p_address()
    default_boot_node_address = determine_default_p2p_address()
    default_dor_service = DORType.BASIC.value
    default_rti_service = RTIType.DOCKER.value
    default_retain_job_history = False
    default_strict_deployment = True
    default_bind_all_address = False
    default_job_concurrency = True

    def __init__(self):
        super().__init__('run', 'run a node as service provider', arguments=[
            Argument('--datastore', dest='datastore', action='store',
                     help=f"path to the datastore (default: '{self.default_datastore}')"),
            Argument('--rest-address', dest='rest-address', action='store',
                     help=f"address used by the REST service interface (default: '{self.default_rest_address}')."),
            Argument('--p2p-address', dest='p2p-address', action='store',
                     help=f"address used by the P2P service interface (default: '{self.default_p2p_address}')."),
            Argument('--boot-node', dest='boot-node', action='store',
                     help=f"address of an existing node for joining a network "
                          f"(default: '{self.default_boot_node_address}')."),
            Argument('--dor-type', dest='dor_type', action='store', choices=[t.value for t in DORType],
                     help=f"indicate the type of DOR service provided by the node "
                          f"(default: '{self.default_dor_service}')."),
            Argument('--rti-type', dest='rti_type', action='store', choices=[t.value for t in RTIType],
                     help=f"indicate the type of RTI service provided by the node "
                          f"(default: '{self.default_rti_service}')."),
            Argument('--retain-job-history', dest="retain-job-history", action='store_const', const=True,
                     help="[for execution/full nodes only] instructs the RTI to retain the job history (default: "
                          "disabled, i.e., delete information of completed jobs). This flag should only be used for "
                          "debug/testing purposes."),
            Argument('--disable-strict-deployment', dest="strict-deployment", action='store_const', const=False,
                     help="[for execution/full nodes only] instructs the RTI to disable strict processor deployment "
                          "(default: enabled, i.e., only the node owner identity can deploy/undeploy processors.)"),
            Argument('--bind-all-address', dest="bind-all-address", action='store_const', const=True,
                     help="allows REST and P2P service to bind and accept connections pointing to any address of the "
                          "machine i.e. 0.0.0.0 (useful for docker)")
        ])

    def execute(self, args: dict) -> None:
        default_if_missing(args, 'datastore', self.default_datastore)
        default_if_missing(args, 'rest-address', self.default_rest_address)
        default_if_missing(args, 'p2p-address', self.default_p2p_address)
        default_if_missing(args, 'boot-node', self.default_boot_node_address)
        default_if_missing(args, 'dor_type', self.default_dor_service)
        default_if_missing(args, 'rti_type', self.default_rti_service)
        default_if_missing(args, 'retain-job-history', self.default_retain_job_history)
        default_if_missing(args, 'strict-deployment', self.default_strict_deployment)
        default_if_missing(args, 'bind-all-address', self.default_bind_all_address)

        # do we have keystore credentials?
        if not args['keystore-id'] or not args['password']:
            raise CLIRuntimeError("No keystore credentials provided (use --keystore-id and --password arguments). "
                                  "Aborting.")

        # try to unlock the keystore
        try:
            keystore = Keystore.from_file(os.path.join(args['keystore'], f"{args['keystore-id']}.json"), args['password'])

        except SaaSRuntimeException as e:
            raise CLIRuntimeError(f"Could not open keystore {args['keystore-id']} because '{e.reason}'. Aborting.")

        # initialise storage directory (if necessary)
        initialise_storage_folder(args['datastore'], 'datastore')

        # extract host/ports
        rest_service_address = extract_address(args['rest-address'])
        p2p_service_address = extract_address(args['p2p-address'])
        boot_node_address = extract_address(args['boot-node'])

        # create a node instance
        try:
            node = DefaultNode.create(keystore, args['datastore'],
                                      p2p_address=p2p_service_address,
                                      rest_address=rest_service_address,
                                      boot_node_address=boot_node_address,
                                      bind_all_address=args['bind-all-address'],
                                      enable_db=True,
                                      dor_type=DORType[args['dor_type']],
                                      rti_type=RTIType[args['rti_type']],
                                      retain_job_history=args['retain-job-history'],
                                      strict_deployment=args['strict-deployment'])

        except SaaSRuntimeException as e:
            raise CLIRuntimeError(f"Could not start node because '{e.reason}'. Aborting.")

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

        # wait until stop signal
        try:
            signal_listener = SignalListener([signal.SIGTERM])
            while not signal_listener.triggered:
                time.sleep(1)
            print("Received stop signal. Shutting down.")
        except KeyboardInterrupt:
            print("Interrupted by user. Shutting down.")
        finally:
            node.shutdown()


class SignalListener:
    def __init__(self, signals: List[int]):
        self.triggered = False

        for sig in signals:
            signal.signal(sig, self.trigger)

    def trigger(self, signum, frame):
        self.triggered = True


def main():
    try:
        default_keystore = os.path.join(os.environ['HOME'], '.keystore')
        default_temp_dir = os.path.join(os.environ['HOME'], '.temp')
        default_log_level = 'INFO'

        cli = CLIParser('SaaS Middleware Service', arguments=[
            Argument('--keystore', dest='keystore', action='store', default=default_keystore,
                     help=f"path to the keystore (default: '{default_keystore}')"),
            Argument('--keystore-id', dest='keystore-id', action='store',
                     help="id of the keystore to be used"),
            Argument('--password', dest='password', action='store',
                     help="password for the keystore"),
            Argument('--temp-dir', dest='temp-dir', action='store', default=default_temp_dir,
                     help=f"path to directory used for intermediate files (default: '{default_temp_dir}')"),
            Argument('--log-level', dest='log-level', action='store',
                     choices=['INFO', 'DEBUG'], default=default_log_level,
                     help=f"set the log level (default: '{default_log_level}')"),
            Argument('--log-path', dest='log-path', action='store',
                     help="enables logging to file using the given path (default: disabled)"),
            Argument('--log-console', dest="log-console", action='store_const', const=False,
                     help="enables logging to the console (default: disabled)"),

        ], commands=[
            RunNode()
        ])

        cli.execute(sys.argv[1:])
        sys.exit(0)

    except CLIRuntimeError as e:
        print(e.reason)
        sys.exit(-1)

    except Exception as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        print(f"Unrefined exception:\n{trace}")
        sys.exit(-2)


if __name__ == "__main__":
    main()
