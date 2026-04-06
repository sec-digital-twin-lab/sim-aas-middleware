"""Node diagnostic commands."""

import json
from typing import Optional

from simaas.cli.helpers import CLICommand, Argument, prompt_for_string, prompt_if_missing, extract_address
from simaas.core.errors import RemoteError
from simaas.helpers import determine_default_rest_address
from simaas.meta import __version__
from simaas.nodedb.api import NodeDBProxy
from simaas.rti.api import RTIProxy
from simaas.dor.api import DORProxy


class NodeStatus(CLICommand):
    """Health check - shows if services are running."""

    def __init__(self) -> None:
        super().__init__('status', 'show service status of a node', arguments=[
            Argument('--json', dest='json_output', action='store_const', const=True,
                     help="output results in JSON format")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'address', prompt_for_string,
                          message="Enter the node's REST address",
                          default=determine_default_rest_address())

        address = extract_address(args['address'])
        status = {
            'rest': {'status': 'unknown', 'address': args['address']},
            'dor': {'status': 'unknown'},
            'rti': {'status': 'unknown'}
        }

        # Check REST/NodeDB service
        try:
            db = NodeDBProxy(address)
            node = db.get_node()
            status['rest'] = {
                'status': 'running',
                'address': args['address'],
                'identity': node.identity.id
            }
            status['p2p'] = {
                'status': 'running',
                'address': node.p2p_address
            }

            # Check DOR service
            if node.dor_service and node.dor_service.lower() != 'none':
                try:
                    dor = DORProxy(address)
                    objects = dor.search()
                    status['dor'] = {
                        'status': 'running',
                        'type': node.dor_service,
                        'object_count': len(objects)
                    }
                except Exception:
                    status['dor'] = {
                        'status': 'error',
                        'type': node.dor_service
                    }
            else:
                status['dor'] = {'status': 'disabled'}

            # Check RTI service
            if node.rti_service and node.rti_service.lower() != 'none':
                try:
                    rti = RTIProxy(address)
                    procs = rti.get_all_procs()
                    status['rti'] = {
                        'status': 'running',
                        'type': node.rti_service,
                        'processor_count': len(procs)
                    }
                except Exception:
                    status['rti'] = {
                        'status': 'error',
                        'type': node.rti_service
                    }
            else:
                status['rti'] = {'status': 'disabled'}

        except RemoteError as e:
            status['rest'] = {
                'status': 'error',
                'address': args['address'],
                'error': e.reason
            }

        except Exception as e:
            status['rest'] = {
                'status': 'error',
                'address': args['address'],
                'error': str(e)
            }

        if args.get('json_output'):
            print(json.dumps(status, indent=2))
        else:
            print("Service Status")
            print("--------------")

            # REST
            rest = status['rest']
            if rest['status'] == 'running':
                print(f"REST:   running   {rest['address']}")
            else:
                print(f"REST:   error     {rest.get('error', 'unknown')}")

            # P2P
            if 'p2p' in status:
                p2p = status['p2p']
                print(f"P2P:    running   {p2p['address']}")

            # DOR
            dor = status['dor']
            if dor['status'] == 'running':
                print(f"DOR:    running   {dor['type']}, {dor['object_count']} objects")
            elif dor['status'] == 'disabled':
                print("DOR:    disabled")
            else:
                print(f"DOR:    error     {dor.get('type', 'unknown')}")

            # RTI
            rti = status['rti']
            if rti['status'] == 'running':
                print(f"RTI:    running   {rti['type']}, {rti['processor_count']} processors")
            elif rti['status'] == 'disabled':
                print("RTI:    disabled")
            else:
                print(f"RTI:    error     {rti.get('type', 'unknown')}")

        return status


class NodeInfo(CLICommand):
    """Show version and configuration information."""

    def __init__(self) -> None:
        super().__init__('info', 'show node version and configuration', arguments=[
            Argument('--json', dest='json_output', action='store_const', const=True,
                     help="output results in JSON format")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'address', prompt_for_string,
                          message="Enter the node's REST address",
                          default=determine_default_rest_address())

        info = {
            'client_version': __version__
        }

        try:
            address = extract_address(args['address'])
            db = NodeDBProxy(address)
            node = db.get_node()

            info.update({
                'identity': {
                    'id': node.identity.id,
                    'name': node.identity.name,
                    'email': node.identity.email
                },
                'services': {
                    'rest_address': args['address'],
                    'p2p_address': node.p2p_address,
                    'dor': node.dor_service,
                    'rti': node.rti_service
                }
            })

        except RemoteError as e:
            info['error'] = e.reason

        except Exception as e:
            info['error'] = str(e)

        if args.get('json_output'):
            print(json.dumps(info, indent=2))
        else:
            print("Node Information")
            print("----------------")
            print(f"Client version: {info['client_version']}")

            if 'identity' in info:
                print(f"Identity ID: {info['identity']['id']}")
                print(f"Identity name: {info['identity']['name']}")
                print(f"Identity email: {info['identity']['email']}")
                print(f"REST address: {info['services']['rest_address']}")
                print(f"P2P address: {info['services']['p2p_address']}")
                print(f"DOR service: {info['services']['dor']}")
                print(f"RTI service: {info['services']['rti']}")
            elif 'error' in info:
                print(f"Error: {info['error']}")

        return info
