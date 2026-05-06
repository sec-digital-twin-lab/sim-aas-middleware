import os

from simaas.cli.helpers import (
    CLICommand, Argument, prompt_if_missing, prompt_for_string, extract_address, initialise_storage_folder,
)
from simaas.gateway.db import DatabaseWrapper
from simaas.gateway.service import proxies, app
from simaas.helpers import LOCAL_IP, find_available_port

default_datastore = os.path.join(os.environ.get('HOME', os.path.expanduser('~')), '.simaas', 'gateway')
default_host = LOCAL_IP if LOCAL_IP else "127.0.0.1"
default_saas_address = f"{default_host}:5001"
default_service_address = f"{default_host}:{find_available_port(default_host, (5101, 5199))}"


def initialise_gateway(args: dict) -> None:
    prompt_if_missing(args, 'datastore', prompt_for_string,
                      message="Enter path to gateway datastore:", default=default_datastore)
    initialise_storage_folder(args['datastore'], 'gateway datastore')

    db_url = f"sqlite:///{os.path.join(args['datastore'], 'db.dat')}"
    DatabaseWrapper.initialise(db_url)

    prompt_if_missing(args, 'address', prompt_for_string,
                      message="Enter REST address of SaaS Node:", default=default_saas_address)
    proxies.address = extract_address(args['address'])


class GatewayService(CLICommand):
    def __init__(self):
        super().__init__('gateway', 'start a gateway API service', arguments=[
            Argument('--datastore', dest='datastore', action='store',
                     help=f"path to the gateway datastore (default: '{default_datastore}')"),
            Argument('--address', dest='address', action='store',
                     help=f"the REST address (host:port) of the SaaS node (e.g., '{default_saas_address}')"),
            Argument('--service-address', dest='service-address', action='store',
                     help="address used to expose the gateway REST API service")
        ])

    def execute(self, args: dict) -> None:
        initialise_gateway(args)

        prompt_if_missing(args, 'service-address', prompt_for_string,
                          message="Enter REST address for the gateway service:",
                          default=default_service_address)

        service_address = extract_address(args['service-address'])

        import uvicorn
        uvicorn.run(app, host=service_address[0], port=service_address[1])
