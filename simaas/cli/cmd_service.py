import asyncio
import logging
import os
import signal
import subprocess
import time
import traceback

from InquirerPy.base import Choice

from simaas.cli.helpers import CLICommand, Argument, prompt_for_string, prompt_for_confirmation, prompt_if_missing, \
    default_if_missing, initialise_storage_folder, prompt_for_selection, load_keystore, extract_address, \
    use_env_or_prompt_if_missing
from simaas.core.errors import _BaseError
from simaas.helpers import determine_default_rest_address, determine_default_p2p_address
from simaas.node.base import Node
from simaas.node.default import DefaultNode
from simaas.plugins import discover_plugins, get_plugin_class
from simaas.plugins import builtins

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

        except _BaseError as e:
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
    default_dor_service = 'none'
    default_rti_service = 'none'
    default_simaas_repo_path = os.environ.get('SIMAAS_REPO_PATH', '')
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
            Argument('--dor', dest='dor', action='store',
                     help=f"DOR plugin name (e.g., 'fs', 'postgres'). "
                          f"(default: '{self.default_dor_service}')."),
            Argument('--rti', dest='rti', action='store',
                     help=f"RTI plugin name (e.g., 'docker', 'aws'). "
                          f"(default: '{self.default_rti_service}')."),
            Argument('--plugins', dest='plugins', action='append',
                     help="Path to plugin directory (can be specified multiple times)."),
            Argument('--simaas-repo-path', dest='simaas_repo_path', action='store',
                     help="Path to the sim-aas-middleware repository used for building PDIs."),
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

    def execute(self, args: dict, wait_for_termination: bool = True) -> None:
        use_ssh_tunneling = False

        # discover plugins from built-in and user-specified paths
        plugin_paths = [os.path.dirname(builtins.__file__)]
        if args.get('plugins'):
            plugin_paths.extend(args['plugins'])
        plugin_registry = discover_plugins(plugin_paths)

        if args['use-defaults']:
            if self.default_simaas_repo_path == '':
                print("WARNING: using defaults but SIMAAS_REPO_PATH environment variable not defined.")

            default_if_missing(args, 'datastore', self.default_datastore)
            default_if_missing(args, 'rest-address', self.default_rest_address)
            default_if_missing(args, 'p2p-address', self.default_p2p_address)
            default_if_missing(args, 'boot-node', self.default_boot_node_address)
            default_if_missing(args, 'dor', self.default_dor_service)
            default_if_missing(args, 'rti', self.default_rti_service)
            default_if_missing(args, 'simaas_repo_path', self.default_simaas_repo_path)
            default_if_missing(args, 'retain-job-history', self.default_retain_job_history)
            default_if_missing(args, 'strict-deployment', self.default_strict_deployment)
            default_if_missing(args, 'bind-all-address', self.default_bind_all_address)

        else:
            prompt_if_missing(args, 'datastore', prompt_for_string,
                              message="Enter path to datastore:",
                              default=self.default_datastore)

            # determine sim-aas-middleware path
            use_env_or_prompt_if_missing(args, 'simaas_repo_path', 'SIMAAS_REPO_PATH', prompt_for_string,
                                         message="Enter the path to the sim-aas-middleware repository")
            os.environ['SIMAAS_REPO_PATH'] = args['simaas_repo_path']

            if args['dor'] is None:
                dor_choices = [Choice('none', 'None')]
                dor_choices += [Choice(name, name.capitalize()) for name in plugin_registry['dor'].keys()]
                args['dor'] = prompt_for_selection(dor_choices, "Select the type of DOR service:")

            if args['rti'] is None:
                rti_choices = [Choice('none', 'None')]
                rti_choices += [Choice(name, name.capitalize()) for name in plugin_registry['rti'].keys()]
                args['rti'] = prompt_for_selection(rti_choices, "Select the type of RTI service:")

            if args['rti'] not in ['none', None]:
                prompt_if_missing(args, 'retain-job-history', prompt_for_confirmation,
                                  message='Retain RTI job history?', default=False)
                prompt_if_missing(args, 'bind-all-address', prompt_for_confirmation,
                                  message='Bind service to all network addresses?', default=False)
                prompt_if_missing(args, 'strict-deployment', prompt_for_confirmation,
                                  message='Strict processor deployment?', default=True)

            # determine if SSH tunneling requires
            required = ['SSH_TUNNEL_HOST', 'SSH_TUNNEL_USER', 'SSH_TUNNEL_KEY_PATH', 'SIMAAS_CUSTODIAN_HOST']
            use_ssh_tunneling = args['rti'] == 'aws' and all(var in os.environ for var in required)
            if use_ssh_tunneling:
                print("AWS SSH Tunneling information found? YES")
                args['rest-address'] = "localhost:5999"
                args['p2p-address'] = "tcp://localhost:4999"
                args['boot-node'] = "localhost:5999"
            else:
                print("AWS SSH Tunneling information found? NO")

            if not use_ssh_tunneling:
                prompt_if_missing(args, 'rest-address', prompt_for_string,
                                  message="Enter address for REST service:",
                                  default=self.default_rest_address)
                prompt_if_missing(args, 'p2p-address', prompt_for_string,
                                  message="Enter address for P2P service:",
                                  default=self.default_p2p_address)

                prompt_if_missing(args, 'boot-node', prompt_for_string,
                                  message="Enter REST address of boot node:",
                                  default=self.default_boot_node_address)

        keystore = load_keystore(args, ensure_publication=False)

        # initialise storage directory (if necessary)
        initialise_storage_folder(args['datastore'], 'datastore')

        # extract host/ports
        rest_service_address = extract_address(args['rest-address'])
        p2p_service_address = args['p2p-address']
        boot_node_address = extract_address(args['boot-node'])

        # get plugin classes
        dor_plugin_class = get_plugin_class(plugin_registry, 'dor', args['dor'])
        rti_plugin_class = get_plugin_class(plugin_registry, 'rti', args['rti'])

        # validate plugin selection
        if args['dor'] != 'none' and dor_plugin_class is None:
            raise ValueError(f"DOR plugin '{args['dor']}' not found. Available: {list(plugin_registry['dor'].keys())}")
        if args['rti'] != 'none' and rti_plugin_class is None:
            raise ValueError(f"RTI plugin '{args['rti']}' not found. Available: {list(plugin_registry['rti'].keys())}")

        # create a node instance
        node = DefaultNode(keystore, args['datastore'], enable_db=True,
                           dor_plugin_class=dor_plugin_class, rti_plugin_class=rti_plugin_class,
                           retain_job_history=args['retain-job-history'],
                           strict_deployment=args['strict-deployment'])

        # startup and join the network using single event loop
        async def _startup_and_join():
            await node.startup(p2p_service_address, rest_address=rest_service_address,
                               bind_all_address=args['bind-all-address'])
            await node.join_network(boot_node_address)

        asyncio.run(_startup_and_join())

        # print info message
        if args['rti'] == 'none':
            print(
                f"Created '{args['dor']}/{args['rti']}' Sim-aaS node instance at "
                f"{args['rest-address']}/{args['p2p-address']}"
            )
        else:
            print(
                f"Created '{args['dor']}/{args['rti']}' Sim-aaS node instance at "
                f"{args['rest-address']}/{args['p2p-address']} "
                f"(keep RTI job history: {'Yes' if args['retain-job-history'] else 'No'}) "
                f"(strict: {'Yes' if args['strict-deployment'] else 'No'}) "
            )

        # do we need to establish an AWS SSH tunnel?
        ssh_process = None
        if use_ssh_tunneling:
            print("Trying to establish SSH tunnel...")

            # get the environment variables need to establish tunnel
            ssh_host = os.environ.get("SSH_TUNNEL_HOST")
            ssh_user = os.environ.get("SSH_TUNNEL_USER")
            ssh_key_path = os.environ.get("SSH_TUNNEL_KEY_PATH")

            # determine the ports
            rest_port = 5999
            p2p_port = 4999
            # rest_port: int = rest_service_address[1]
            # p2p_port: str = p2p_service_address.split(':')[-1]
            print(f"Using the following REST/P2P ports for AWS SSH tunneling: {rest_port}/{p2p_port}")

            # SSH tunnel command (runs in the background)
            ssh_command = [
                "ssh",
                "-N",  # Do not execute remote commands
                "-R", f"0.0.0.0:{rest_port}:localhost:{rest_port}",  # Forward remote port 5999 to local port 5999
                "-R", f"0.0.0.0:{p2p_port}:localhost:{p2p_port}",  # Forward remote port 4999 to local port 4999
                "-i", ssh_key_path,  # Private key authentication
                f"{ssh_user}@{ssh_host}"  # Remote SSH target
            ]

            # Start the SSH tunnel as a subprocess
            ssh_process = subprocess.Popen(
                ssh_command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            time.sleep(2)
            print("AWS SSH tunnel should be established now!")

        # wait for termination...
        if wait_for_termination:
            WaitForTermination(node).wait_for_termination()
        else:
            time.sleep(2)
            node.shutdown()

        # need to shut down SSH tunnel?
        if ssh_process is not None:
            print("Terminating SSH tunnel process...")
            ssh_process.terminate()
            ssh_process.wait()

