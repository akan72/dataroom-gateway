from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from usage_demo import inference


def test_missing_inference_key_fails_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(inference, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("TINFOIL_API_KEY", raising=False)
    constructor = Mock()
    monkeypatch.setattr(inference, "TinfoilAI", constructor)
    with pytest.raises(ValueError, match="TINFOIL_API_KEY"):
        inference.private_client()
    constructor.assert_not_called()


def test_inference_credential_loaded_from_local_env_without_logging(monkeypatch, tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text("TINFOIL_API_KEY=test-only-credential\n")
    monkeypatch.setattr(inference, "ENV_PATH", path)
    monkeypatch.delenv("TINFOIL_API_KEY", raising=False)
    constructor = Mock()
    monkeypatch.setattr(inference, "TinfoilAI", constructor)
    inference.private_client()
    assert constructor.call_args.kwargs["api_key"] == "test-only-credential"
    assert capsys.readouterr() == ("", "")
    # dotenv mutates the environment; remove this test-only value before fixture teardown.
    monkeypatch.delenv("TINFOIL_API_KEY", raising=False)


def test_successful_completion_without_verified_attestation_is_rejected(monkeypatch):
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=object()))),
        get_verification_document=lambda: SimpleNamespace(security_verified=False),
        client=SimpleNamespace(close=Mock()),
    )
    monkeypatch.setattr(inference, "private_client", lambda: client)
    with pytest.raises(ValueError, match="attestation"):
        inference.smoke_test()
    client.client.close.assert_called_once()


def test_provider_error_body_and_credentials_are_not_logged(monkeypatch, capsys):
    def fail():
        raise RuntimeError("private-provider-body-and-secret-marker")

    monkeypatch.setattr(inference, "smoke_test", fail)
    with pytest.raises(SystemExit) as exit_info:
        inference.main()
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "private-provider-body-and-secret-marker" not in captured.err
    assert "RuntimeError" in captured.err
    assert not captured.out
