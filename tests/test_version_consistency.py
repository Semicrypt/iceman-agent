from importlib.metadata import version

import iceman


def test_runtime_version_matches_installed_package_metadata():
    assert iceman.__version__ == version("agent-iceman")
