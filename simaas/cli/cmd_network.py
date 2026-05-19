import datetime
import time
from typing import Optional

from tabulate import tabulate

from simaas.cli.helpers import CLICommand, Argument, prompt_for_string, prompt_if_missing, extract_address, print_json
from simaas.core.errors import RemoteError, CLIError
from simaas.helpers import determine_default_rest_address
from simaas.nodedb.api import NodeDBProxy


class NetworkList(CLICommand):
    def __init__(self) -> None:
        super().__init__('show', 'retrieves a list of all known nodes in the network', arguments=[
            Argument('--json', dest='json_output', action='store_const', const=True,
                     help="output results in JSON format")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'address', prompt_for_string,
                          message="Enter the target node's REST address",
                          default=determine_default_rest_address())

        db = NodeDBProxy(extract_address(args['address']))
        network = db.get_network()

        if args.get('json_output'):
            # JSON output mode
            output = []
            for node in network:
                output.append({
                    'identity_id': node.identity.id,
                    'dor_service': node.dor_service,
                    'rti_service': node.rti_service,
                    'rest_address': f"{node.rest_address[0]}:{node.rest_address[1]}" if node.rest_address else None,
                    'p2p_address': node.p2p_address,
                    'last_seen': node.last_seen
                })
            print_json(result=output)
        elif len(network) == 0:
            print("No nodes found in the network.")
        else:
            print(f"Found {len(network)} nodes in the network:")

            # headers
            lines = [
                ['NODE IDENTITY ID', 'DOR', 'RTI', 'REST ADDRESS', 'P2P ADDRESS', 'LAST SEEN'],
                ['----------------', '---', '---', '------------', '-----------', '---------']
            ]

            # list
            for node in network:
                lines.append([
                    node.identity.id,
                    node.dor_service,
                    node.rti_service,
                    f"{node.rest_address[0]}:{node.rest_address[1]}" if node.rest_address else '-',
                    f"{node.p2p_address}",
                    datetime.datetime.fromtimestamp(node.last_seen / 1000.0).strftime('%Y-%m-%d %H:%M:%S UTC')
                ])

            print(tabulate(lines, tablefmt="plain"))

        return {
            'network': network
        }


class NetworkPing(CLICommand):
    def __init__(self) -> None:
        super().__init__('ping', 'check if a node is reachable', arguments=[
            Argument('target', metavar='address', type=str, nargs='?',
                     help="the REST address (host:port) of the node to ping"),
            Argument('--json', dest='json_output', action='store_const', const=True,
                     help="output results in JSON format")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'target', prompt_for_string,
                          message="Enter the target node's REST address",
                          default=determine_default_rest_address())

        address = extract_address(args['target'])
        start_time = time.time()

        try:
            db = NodeDBProxy(address)
            node = db.get_node()
            elapsed_ms = int((time.time() - start_time) * 1000)

            result = {
                'reachable': True,
                'latency_ms': elapsed_ms,
                'identity_id': node.identity.id,
                'dor_service': node.dor_service,
                'rti_service': node.rti_service
            }

            if args.get('json_output'):
                print_json(result=result)
            else:
                print(f"Node at {args['target']} is reachable.")
                print(f"  Identity: {node.identity.id}")
                print(f"  DOR: {node.dor_service}")
                print(f"  RTI: {node.rti_service}")
                print(f"  Latency: {elapsed_ms}ms")

            result['node'] = node
            return result

        except RemoteError as e:
            result = {'reachable': False, 'error': e.reason}
            if args.get('json_output'):
                print_json(error=e)
            else:
                print(f"Node at {args['target']} is not reachable.")
                print(f"  Error: {e.reason}")
            return result

        except Exception as e:
            result = {'reachable': False, 'error': str(e)}
            if args.get('json_output'):
                print_json(error=CLIError(str(e)))
            else:
                print(f"Node at {args['target']} is not reachable.")
                print(f"  Error: {str(e)}")
            return result


class NetworkStatus(CLICommand):
    def __init__(self) -> None:
        super().__init__('status', 'show status of a node', arguments=[
            Argument('--json', dest='json_output', action='store_const', const=True,
                     help="output results in JSON format")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'address', prompt_for_string,
                          message="Enter the node's REST address",
                          default=determine_default_rest_address())

        try:
            address = extract_address(args['address'])
            db = NodeDBProxy(address)
            node = db.get_node()
            network = db.get_network()

            result = {
                'identity_id': node.identity.id,
                'name': node.identity.name,
                'email': node.identity.email,
                'rest_address': args['address'],
                'p2p_address': node.p2p_address,
                'dor_service': node.dor_service if node.dor_service else 'disabled',
                'rti_service': node.rti_service if node.rti_service else 'disabled',
                'peer_count': len(network) - 1
            }

            if args.get('json_output'):
                print_json(result=result)
            else:
                print("Node Status")
                print("-----------")
                print(f"Identity: {node.identity.id}")
                print(f"  Name: {node.identity.name}")
                print(f"  Email: {node.identity.email}")
                print(f"REST API: {args['address']} (running)")
                print(f"P2P: {node.p2p_address} (running)")
                print(f"DOR: {node.dor_service if node.dor_service else 'disabled'}")
                print(f"RTI: {node.rti_service if node.rti_service else 'disabled'}")
                print(f"Connected peers: {len(network) - 1}")  # Exclude self

            result['node'] = node
            return result

        except RemoteError as e:
            if args.get('json_output'):
                print_json(error=e)
            else:
                print(f"Could not get status: {e.reason}")
            return None

        except Exception as e:
            if args.get('json_output'):
                print_json(error=CLIError(str(e)))
            else:
                print(f"Could not get status: {str(e)}")
            return None
