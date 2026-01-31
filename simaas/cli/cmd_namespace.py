from typing import Optional, Dict

from InquirerPy.base import Choice
from tabulate import tabulate

from simaas.core.errors import CLIError
from simaas.cli.helpers import CLICommand, prompt_for_string, prompt_if_missing, prompt_for_selection, \
    extract_address, prompt_for_integer, Argument
from simaas.core.logging import Logging
from simaas.helpers import determine_default_rest_address
from simaas.nodedb.api import NodeDBProxy
from simaas.nodedb.schemas import NamespaceInfo, ResourceDescriptor

logger = Logging.get('cli')


class NamespaceList(CLICommand):
    def __init__(self):
        super().__init__('list', 'lists all known namespaces')

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'address', prompt_for_string, message="Enter address of node:",
                          default=determine_default_rest_address())

        proxy = NodeDBProxy(extract_address(args['address']))
        namespaces: Dict[str, NamespaceInfo] = proxy.get_namespaces()
        if len(namespaces) == 0:
            print("No namespaces found.")
        else:
            print(f"Found {len(namespaces)} namespaces:")

            # headers
            lines = [
                ['NAME', 'BUDGET', 'USAGE', 'AVAILABLE'],
                ['----', '------', '-----', '---------']
            ]

            # list
            for namespace in namespaces.values():
                used = ResourceDescriptor(vcpus=0, memory=0)
                for r in namespace.reservations.values():
                    used.vcpus += r.vcpus
                    used.memory += r.memory

                lines.append([
                    namespace.name,
                    f"{namespace.budget.vcpus} vCPUs, {namespace.budget.memory} MB",
                    f"{used.vcpus} vCPUs, {used.memory} MB",
                    f"{namespace.budget.vcpus - used.vcpus} vCPUs, {namespace.budget.memory - used.memory} MB",
                ])
            print(tabulate(lines, tablefmt="plain"))

        return {
            'namespaces': namespaces
        }


class NamespaceUpdate(CLICommand):
    def __init__(self):
        super().__init__('update', 'updates existing (or creates new) namespace', arguments=[
            Argument('--name', dest='name', action='store', help="the name of the namespace"),
            Argument('--vcpus', dest='vcpus', action='store', help="the number of vCPUs for this namespace (must be a positive integer"),
            Argument('--memory', dest='memory', action='store', help="the amount of memory (in megabytes) for this namespace (must be a positive integer")
        ])

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'address', prompt_for_string, message="Enter address of node:",
                          default=determine_default_rest_address())

        proxy = NodeDBProxy(extract_address(args['address']))

        # prompt user to select an existing namespace
        if args.get('name') in [None, '']:
            namespaces: Dict[str, NamespaceInfo] = proxy.get_namespaces()
            if len(namespaces) == 0:
                prompt_if_missing(args, 'name', prompt_for_string, message="No namespaces found. Enter name to create new namespace:")
            else:
                prompt_if_missing(args, 'name', prompt_for_selection,
                    choices=[Choice(name=name, value=name) for name in namespaces.keys()],
                    message="Select existing namespace:", allow_multiple=False
                )

        # prompt user to determine vCPUs if missing
        if args.get('vcpus') is None:
            prompt_if_missing(args, 'vcpus', prompt_for_integer, message="Enter number of vCPUs:", default='1')

        # prompt user to determine memory if missing
        if args.get('memory') is None:
            prompt_if_missing(args, 'memory', prompt_for_integer, message="Enter memory budget (in megabytes):", default='2048')

        # check resource specifications: must be integers
        try:
            vcpus = int(args['vcpus'])
            memory = int(args['memory'])
        except Exception:
            print(f"Invalid resource specification: {args['vcpus']}/{args['memory']} "
                  f"-> vCPUs/memory must be positive integers.")
            raise CLIError('Non-integer vCPUs and/or memory specification')

        # check resource specifications: must be positive
        if vcpus < 0 or memory < 0:
            print(f"Invalid resource specification: {args['vcpus']}/{args['memory']} "
                  f"-> vCPUs/memory must be positive integers.")
            raise CLIError('Negative vCPUs and/or memory specification')


        budget = ResourceDescriptor(vcpus=int(args['vcpus']), memory=int(args['memory']))
        namespace: NamespaceInfo = proxy.update_namespace_budget(args['name'], budget)

        used = ResourceDescriptor(vcpus=0, memory=0)
        for r in namespace.reservations.values():
            used.vcpus += r.vcpus
            used.memory += r.memory

        print(f"Namespace '{namespace.name}' updated/created:")
        print(f"- vCPUs: {used.vcpus} of {namespace.budget.vcpus} vCPUs used")
        print(f"- Memory: {used.memory} of {namespace.budget.memory} MB used")
        if len(namespace.reservations) > 0:
            print(f"- Active Reservations ({len(namespace.reservations)}): {' '.join(namespace.reservations.keys())}")
        else:
            print("- No Active reservations")

        if len(namespace.jobs) > 0:
            print(f"- Active Jobs ({len(namespace.jobs)}): {' '.join(namespace.jobs)}")
        else:
            print("- No Active Jobs")

        return {
            'namespace': namespace
        }


class NamespaceShow(CLICommand):
    def __init__(self):
        super().__init__('show', 'show details of existing namespace')

    def execute(self, args: dict) -> Optional[dict]:
        prompt_if_missing(args, 'address', prompt_for_string, message="Enter address of node:",
                          default=determine_default_rest_address())

        proxy = NodeDBProxy(extract_address(args['address']))

        # prompt user to select an existing namespace
        if args.get('name', '') == '':
            namespaces: Dict[str, NamespaceInfo] = proxy.get_namespaces()
            prompt_if_missing(args, 'name', prompt_for_selection,
                choices=[Choice(name=name, value=name) for name in namespaces.keys()],
                message="Select namespace:", allow_multiple=False
            )

        namespace: Optional[NamespaceInfo] = proxy.get_namespace(args['name'])
        if namespace is None:
            print(f"Namespace '{args['name']}' not found.")

        else:
            used = ResourceDescriptor(vcpus=0, memory=0)
            for r in namespace.reservations.values():
                used.vcpus += r.vcpus
                used.memory += r.memory

            print(f"Namespace '{namespace.name}' found:")
            print(f"- vCPUs: {used.vcpus} of {namespace.budget.vcpus} vCPUs used")
            print(f"- Memory: {used.memory} of {namespace.budget.memory} MB used")
            if len(namespace.reservations) > 0:
                print(f"- Active Reservations ({len(namespace.reservations)}): {' '.join(namespace.reservations.keys())}")
            else:
                print("- No Active reservations")

            if len(namespace.jobs) > 0:
                print(f"- Active Jobs ({len(namespace.jobs)}): {' '.join(namespace.jobs)}")
            else:
                print("- No Active Jobs")

        return {
            'namespace': namespace
        }
