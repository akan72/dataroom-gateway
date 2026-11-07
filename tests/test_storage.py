import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider

from usage_demo import gateway, storage
from usage_demo.crypto import Signer, verify
from usage_demo.journal import Journal


def record(n):
    return {
        "timestamp": "2026-11-07T12:00:00+00:00",
        "request_id": str(n),
        "user_id": str(n),
        "tier": "Pro",
        "method": "GET",
        "route": "/v1/me",
        "status": 200,
        "elapsed_us": 1000,
        "trace_id": "a" * 32,
    }


def test_recovery_survives_boot_key_rotation_and_retains_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PERSISTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GATEWAY_CONTROL_TOKEN", "control-token-that-is-long-enough")
    monkeypatch.setenv("DEMO_VISITOR_SECRET", "visitor-secret-that-is-long-enough")
    monkeypatch.setenv("DEMO_PUBLIC_UI", "1")
    monkeypatch.setattr(gateway, "provider", lambda _: TracerProvider())
    boot = Signer(tmp_path / "boot-1.pem")
    monkeypatch.setenv("SIGNING_KEY_PATH", str(tmp_path / "boot-1.pem"))
    first = gateway.create_app()
    with TestClient(first) as client:
        for n in range(3):
            first.state.journal.append(record(n))
        before = client.get("/demo/state").json()
        assert (
            verify(before["key_binding"], boot.public_pem)["public_key_pem"]
            == before["public_key_pem"]
        )
        checkpoint = client.get(
            "/evidence/checkpoint",
            headers={"authorization": "Bearer control-token-that-is-long-enough"},
        ).json()
        assert client.get("/evidence/export").status_code == 401
    new_boot = Signer(tmp_path / "boot-2.pem")
    monkeypatch.setenv("SIGNING_KEY_PATH", str(tmp_path / "boot-2.pem"))
    second = gateway.create_app()
    with TestClient(second) as client:
        after = client.get("/demo/state").json()
        assert after["public_key_pem"] == before["public_key_pem"]
        assert after["state"]["payload"]["seq"] == 3
        assert after["state"]["payload"]["session_id"] == before["state"]["payload"]["session_id"]
        assert (
            verify(after["key_binding"], new_boot.public_pem)["public_key_pem"]
            == after["public_key_pem"]
        )
        with pytest.raises(ValueError):
            verify(after["key_binding"], boot.public_pem)
        appended = second.state.journal.append(record(4))["payload"]
        assert appended["seq"] == 4
        assert appended["previous_hash"] == checkpoint["payload"]["head_hash"]
        assert len(second.state.journal.snapshot()[0]) == 4


def test_recovery_rejects_modified_disk_records(tmp_path):
    signer = Signer(tmp_path / "key.pem")
    path = tmp_path / "journal.sqlite"
    journal = Journal(path, signer, "test", resume=True)
    journal.append(record(1))
    journal.close()
    with sqlite3.connect(path) as db:
        envelope = json.loads(db.execute("SELECT envelope FROM records").fetchone()[0])
        envelope["payload"]["status"] = 503
        db.execute("UPDATE records SET envelope=?", (json.dumps(envelope),))
    with pytest.raises(ValueError, match="signature"):
        Journal(path, signer, "test", resume=True)


def test_locked_storage_denies_app_and_requires_authorized_valid_key(monkeypatch):
    monkeypatch.setenv("GATEWAY_CONTROL_TOKEN", "control-token-that-is-long-enough")
    monkeypatch.setattr(storage, "writable", lambda: False)
    calls = []
    monkeypatch.setattr(storage, "unlock", lambda key, initialize: calls.append((key, initialize)))
    app = storage.gateway_app()

    async def ready():
        pass

    monkeypatch.setattr(app, "start_gateway", ready)
    with TestClient(app) as client:
        assert client.get("/health").json()["ready"] is False
        assert client.get("/v1/me").status_code == 503
        assert client.post("/evidence/storage/unlock", content=b"a" * 32).status_code == 401
        headers = {"authorization": "Bearer control-token-that-is-long-enough"}
        assert (
            client.post("/evidence/storage/unlock", headers=headers, content=b"bad").status_code
            == 422
        )
        assert (
            client.post("/evidence/storage/unlock", headers=headers, content=b"a" * 65).status_code
            == 413
        )
        assert calls == []
        assert (
            client.post("/evidence/storage/unlock", headers=headers, content=b"a" * 32).status_code
            == 200
        )
        assert calls == [(b"a" * 32, False)]
