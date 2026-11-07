from pathlib import Path

import pytest

from usage_demo import cli


@pytest.mark.parametrize("explicit", [False, True])
def test_cli_uses_container_data_directory_unless_overridden(monkeypatch, explicit):
    monkeypatch.setenv("DEMO_DATA_DIR", "/data")
    arguments = ["demo"]
    if explicit:
        arguments.extend(["--data-dir", "/custom-data"])
    arguments.append("serve")
    monkeypatch.setattr("sys.argv", arguments)
    received = []
    monkeypatch.setattr(cli, "serve", lambda args: received.append(args.data_dir))
    cli.main()
    assert received == [Path("/custom-data" if explicit else "/data")]
