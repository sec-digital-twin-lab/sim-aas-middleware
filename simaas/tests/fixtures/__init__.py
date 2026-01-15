# Test fixtures package
"""Test fixtures for simaas tests.

Import fixtures from submodules to make them available to pytest.
"""

from simaas.tests.fixtures.core import (
    TestContext,
    generate_random_file,
    test_context,
    docker_available,
    aws_available,
    github_credentials_available,
    session_keystore,
    temp_directory,
    extra_keystores,
    REPOSITORY_URL,
    REPOSITORY_COMMIT_ID,
    BASE_DIR,
)

from simaas.tests.fixtures.mocks import (
    DummyProgressListener,
    DummyNamespace,
    dummy_namespace,
)

from simaas.tests.fixtures.dor import (
    session_node,
    session_data_dir,
    dor_proxy,
    node_db_proxy,
)

from simaas.tests.fixtures.rti import (
    RTIBackend,
    RTIBackendConfig,
    add_test_processor,
    wait_for_processor_ready,
    wait_for_processor_undeployed,
    rti_proxy,
    deployed_abc_processor,
    deployed_ping_processor,
    deployed_room_processor,
    deployed_thermostat_processor,
    docker_non_strict_node,
    docker_strict_node,
    ssh_tunnel,
    aws_session_node,
    aws_dor_proxy,
    aws_rti_proxy,
    aws_node_db_proxy,
    aws_non_strict_node,
    aws_strict_node,
    aws_deployed_abc_processor,
    aws_deployed_room_processor,
    aws_deployed_thermostat_processor,
    known_user,
    aws_known_user,
    PROC_ABC_PATH,
    PROC_PING_PATH,
    PROC_ROOM_PATH,
    PROC_THERMOSTAT_PATH,
)
