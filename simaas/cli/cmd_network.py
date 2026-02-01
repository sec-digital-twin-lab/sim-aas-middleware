import datetime
from typing import Optional

from tabulate import tabulate

from simaas.cli.helpers import CLICommand, prompt_for_string, prompt_if_missing, extract_address
from simaas.helpers import determine_default_rest_address
from simaas.nodedb.api import NodeDBProxy


class NetworkList(CLICommand):
    def __init__(self) -> None:
        super().__init__('show', 'retrieves a list of all known nodes in the network', arguments=[])

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'address', prompt_for_string,
                          message="Enter the target node's REST address",
                          default=determine_default_rest_address())

        db = NodeDBProxy(extract_address(args['address']))
        network = db.get_network()
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
