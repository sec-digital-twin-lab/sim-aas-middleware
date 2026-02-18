import logging
import os
import sys
import traceback

from simaas.cli.cmd_image import PDIBuildLocal, PDIBuildGithub, PDIExport, PDIImport
from simaas.cli.cmd_namespace import NamespaceList, NamespaceUpdate, NamespaceShow
from simaas.helpers import determine_default_rest_address
from simaas.meta import __version__
from simaas.cli.cmd_dor import DORAdd, DORRemove, DORSearch, DORTag, DORUntag, DORAccessGrant, \
    DORAccessRevoke, DORAccessShow, DORDownload, DORMeta
from simaas.cli.cmd_identity import IdentityCreate, IdentityRemove, IdentityShow, IdentityUpdate, IdentityList, \
    IdentityDiscover, IdentityPublish, CredentialsRemove, CredentialsList, CredentialsAddSSHCredentials, \
    CredentialsAddGithubCredentials, CredentialsTestSSHCredentials, CredentialsTestGithubCredentials
from simaas.cli.cmd_job_runner import JobRunner
from simaas.cli.cmd_network import NetworkList
from simaas.cli.cmd_rti import RTIProcDeploy, RTIProcUndeploy, RTIJobSubmit, RTIJobStatus, RTIProcList, \
    RTIProcShow, RTIJobList, RTIJobCancel, RTIVolumeList, RTIVolumeCreateFSRef, RTIVolumeCreateEFSRef, RTIVolumeDelete
from simaas.cli.cmd_service import Service
from simaas.core.errors import CLIError
from simaas.cli.helpers import CLIParser, Argument, CLICommandGroup

# deactivate annoying DEBUG messages by multipart
logging.getLogger('multipart.multipart').setLevel(logging.WARNING)


def main():
    try:
        home_dir = os.environ.get('HOME', os.path.expanduser('~'))
        default_keystore = os.path.join(home_dir, '.keystore')
        default_temp_dir = os.path.join(home_dir, '.temp')
        default_log_level = 'INFO'

        cli = CLIParser(f'SaaS Middleware v{__version__} command line interface (CLI)', arguments=[
            Argument('--keystore', dest='keystore', action='store', default=default_keystore,
                     help=f"path to the keystore (default: '{default_keystore}')"),
            Argument('--temp-dir', dest='temp-dir', action='store', default=default_temp_dir,
                     help=f"path to directory used for intermediate files (default: '{default_temp_dir}')"),
            Argument('--keystore-id', dest='keystore-id', action='store',
                     help="id of the keystore to be used if there are more than one available "
                          "(default: id of the only keystore if only one is available )"),
            Argument('--password', dest='password', action='store',
                     help="password for the keystore"),
            Argument('--log-level', dest='log-level', action='store',
                     choices=['INFO', 'DEBUG'], default=default_log_level,
                     help=f"set the log level (default: '{default_log_level}')"),
            Argument('--log-to-aws', dest='log-to-aws', action='store_const', const=True,
                     help="enables logging to AWS CloudWatch"),
            Argument('--log-path', dest='log-path', action='store',
                     help="enables logging to file using the given path"),
            Argument('--log-console', dest="log-console", action='store_const', const=False,
                     help="enables logging to the console"),

        ], commands=[
            CLICommandGroup('identity', 'manage and explore identities', commands=[
                IdentityCreate(),
                IdentityRemove(),
                IdentityShow(),
                IdentityUpdate(),
                IdentityList(),
                IdentityDiscover(),
                IdentityPublish(),
                CLICommandGroup('credentials', 'manage credentials for a keystore', commands=[
                    CLICommandGroup('add', 'add credentials to a keystore', commands=[
                        CredentialsAddSSHCredentials(),
                        CredentialsAddGithubCredentials()
                    ]),
                    CLICommandGroup('test', 'test credentials', commands=[
                        CredentialsTestSSHCredentials(),
                        CredentialsTestGithubCredentials()
                    ]),
                    CredentialsRemove(),
                    CredentialsList()
                ]),
            ]),
            Service(),
            JobRunner(),
            CLICommandGroup('image', 'manage processor docker images (PDIs)', commands=[
                PDIBuildLocal(),
                PDIBuildGithub(),
                PDIExport(),
                PDIImport()
            ]),
            CLICommandGroup('dor', 'interact with a Data Object Repository (DOR)', arguments=[
                Argument('--address', dest='address', action='store',
                         help=f"the REST address (host:port) of the node (e.g., '{determine_default_rest_address()}')")
            ], commands=[
                DORSearch(),
                DORAdd(),
                DORMeta(),
                DORDownload(),
                DORRemove(),
                DORTag(),
                DORUntag(),
                CLICommandGroup('access', 'manage access to data objects', commands=[
                    DORAccessGrant(),
                    DORAccessRevoke(),
                    DORAccessShow()
                ])
            ]),
            CLICommandGroup('rti', 'interact with a Runtime Infrastructure (RTI)', arguments=[
                Argument('--address', dest='address', action='store',
                         help=f"the REST address (host:port) of the node (e.g., '{determine_default_rest_address()}')")
            ], commands=[
                CLICommandGroup('volume', 'manage volumes', commands=[
                    RTIVolumeList(),
                    CLICommandGroup('create', 'create volumes', commands=[
                        RTIVolumeCreateFSRef(),
                        RTIVolumeCreateEFSRef()
                    ]),
                    RTIVolumeDelete()
                ]),
                CLICommandGroup('proc', 'manage processors', commands=[
                    RTIProcDeploy(),
                    RTIProcUndeploy(),
                    RTIProcList(),
                    RTIProcShow()
                ]),
                CLICommandGroup('job', 'manage job', commands=[
                    RTIJobList(),
                    RTIJobSubmit(),
                    RTIJobStatus(),
                    RTIJobCancel()
                ])
            ]),
            CLICommandGroup('namespace', 'manage namespaces', arguments=[
                Argument('--address', dest='address', action='store',
                         help=f"the REST address (host:port) of the node (e.g., '{determine_default_rest_address()}')")
            ], commands=[
                NamespaceList(),
                NamespaceUpdate(),
                NamespaceShow()
            ]),
            CLICommandGroup('network', 'explore the network of nodes', arguments=[
                Argument('--address', dest='address', action='store',
                         help=f"the REST address (host:port) of the node (e.g., '{determine_default_rest_address()}')")
            ], commands=[
                NetworkList()
            ])
        ])

        cli.execute(sys.argv[1:])
        sys.exit(0)

    except CLIError as e:
        print(e.reason)
        sys.exit(-1)

    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(-2)

    except Exception as e:
        trace = ''.join(traceback.format_exception(None, e, e.__traceback__))
        print(f"Unrefined exception:\n{trace}")
        sys.exit(-3)


if __name__ == "__main__":
    main()
