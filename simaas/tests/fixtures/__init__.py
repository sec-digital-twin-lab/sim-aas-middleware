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
