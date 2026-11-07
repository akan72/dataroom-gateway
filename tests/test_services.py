import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace import TracerProvider

from usage_demo import collector, gateway, saas
from usage_demo.catalog import USERS, authenticate, issue_visitor


@pytest.fixture
def no_export(monkeypatch):
    monkeypatch.setattr(saas, "provider", lambda _: TracerProvider())
    monkeypatch.setattr(gateway, "provider", lambda _: TracerProvider())


def auth(user):
    return {"authorization": f"Bearer {user.token}"}


def test_anonymous_visitors_get_distinct_authenticated_scopes(monkeypatch):
    monkeypatch.setenv("DEMO_VISITOR_SECRET", "test-demo-visitor-secret-at-least-32-characters")
    alice, bob = issue_visitor("Enterprise"), issue_visitor("Enterprise")
    assert alice.user_id != bob.user_id
    assert authenticate(f"Bearer {alice.token}") == alice
    assert authenticate(f"Bearer {bob.token}") == bob
    assert authenticate(f"Bearer {alice.token[:-1]}x") is None
    monkeypatch.setenv(
        "DEMO_VISITOR_SECRET", "different-demo-visitor-secret-at-least-32-characters"
    )
    assert authenticate(f"Bearer {alice.token}") is None


def test_anonymous_visitor_cannot_access_another_visitors_workspace(monkeypatch, no_export):
    monkeypatch.setenv("DEMO_VISITOR_SECRET", "test-demo-visitor-secret-at-least-32-characters")
    alice, bob = issue_visitor("Pro"), issue_visitor("Pro")
    with TestClient(saas.create_app()) as client:
        project = client.post("/v1/projects", headers=auth(alice), json={"name": "mine"}).json()
        assert client.get("/v1/projects", headers=auth(bob)).json() == []
        assert (
            client.post(
                "/v1/jobs",
                headers=auth(bob),
                json={
                    "project_id": project["id"],
                    "units": 1,
                },
            ).status_code
            == 404
        )


def test_public_demo_is_opt_in(gateway_app):
    with TestClient(gateway_app) as client:
        assert client.get("/").status_code == 404
        assert client.get("/demo/state").status_code == 404
        assert client.post("/demo/join", json={"tier": "Free"}).status_code == 404


def test_public_demo_serves_ui_and_signed_state_without_exposing_control_plane(
    monkeypatch,
    tmp_path,
    no_export,
):
    from usage_demo.crypto import verify

    monkeypatch.setenv("DEMO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GATEWAY_CONTROL_TOKEN", "test-control-token-at-least-24-characters")
    monkeypatch.setenv("DEMO_VISITOR_SECRET", "test-demo-visitor-secret-at-least-32-characters")
    monkeypatch.setenv("DEMO_PUBLIC_UI", "1")
    monkeypatch.delenv("SIGNING_KEY_PATH", raising=False)
    app = gateway.create_app()
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/live").status_code == 200
        assert client.get("/assets/app.js").status_code == 200
        state = client.get("/demo/state").json()
        assert verify(state["state"], state["public_key_pem"])["seq"] == 0
        assert state["state"]["payload"]["hardware_attestation_verified"] is False
        visitor = client.post("/demo/join", json={"tier": "Enterprise"}).json()
        assert authenticate(f"Bearer {visitor['token']}").user_id == visitor["user_id"]
        assert client.post("/demo/join", json={"tier": "Admin"}).status_code == 422
        assert (
            client.get(
                "/evidence/checkpoint",
                headers={
                    "authorization": f"Bearer {visitor['token']}",
                },
            ).status_code
            == 401
        )
        qr = client.get("/demo/qr.svg", params={"origin": "https://demo.example.com"})
        assert qr.status_code == 200 and "<svg" in qr.text
        assert (
            client.get("/demo/qr.svg", params={"origin": "javascript:alert(1)"}).status_code == 422
        )
        assert (
            client.post(
                "/demo/query", json={"query_id": "SELECT * FROM usage", "nonce": str(uuid4())}
            ).status_code
            == 422
        )


def test_saas_enforces_uuid_scopes_and_tier_limits(no_export):
    with TestClient(saas.create_app()) as client:
        alice, bob = USERS[:2]
        assert client.get("/v1/me").status_code == 401
        assert client.get("/v1/me", headers=auth(alice)).json()["user_id"] == alice.user_id
        project = client.post("/v1/projects", headers=auth(alice), json={"name": "private"}).json()
        assert client.get("/v1/projects", headers=auth(bob)).json() == []
        assert (
            client.post(
                "/v1/jobs",
                headers=auth(bob),
                json={
                    "project_id": project["id"],
                    "units": 1,
                },
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/v1/jobs",
                headers=auth(alice),
                json={
                    "project_id": project["id"],
                    "units": 3,
                },
            ).status_code
            == 403
        )
        client.post("/v1/projects", headers=auth(alice), json={"name": "second"})
        assert (
            client.post("/v1/projects", headers=auth(alice), json={"name": "third"}).status_code
            == 429
        )


@pytest.fixture
def gateway_app(monkeypatch, tmp_path, no_export):
    monkeypatch.setenv("DEMO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GATEWAY_CONTROL_TOKEN", "test-control-token-at-least-24-characters")
    monkeypatch.delenv("SIGNING_KEY_PATH", raising=False)
    original_client = httpx.AsyncClient

    def upstream(request):
        if request.url.path == "/v1/jobs":
            raise httpx.ReadTimeout("test timeout")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        gateway.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs,
            transport=httpx.MockTransport(upstream),
        ),
    )
    return gateway.create_app()


def test_gateway_counts_observations_and_ignores_forged_identity(gateway_app):
    with TestClient(gateway_app) as client:
        response = client.get(
            "/v1/me",
            headers={**auth(USERS[0]), "x-user-id": USERS[-1].user_id, "x-user-tier": "Enterprise"},
        )
        assert response.status_code == 200
        assert response.headers["x-usage-seq"] == "1"
        assert client.get("/v1/me").status_code == 401
        assert client.post("/v1/jobs", headers=auth(USERS[0]), json={}).status_code == 504
        records, checkpoint = gateway_app.state.journal.snapshot()
        assert checkpoint["payload"]["record_count"] == 3
        assert records[0]["user_id"] == USERS[0].user_id
        assert records[0]["tier"] == "Free"
        assert records[1]["user_id"] is None
        assert records[2]["status"] == 504


def test_control_plane_denies_user_tokens_raw_sql_and_raw_data(gateway_app):
    with TestClient(gateway_app) as client:
        assert client.get("/evidence/telemetry", headers=auth(USERS[0])).status_code == 401
        assert client.get("/evidence/checkpoint", headers=auth(USERS[0])).status_code == 401
        assert client.get("/evidence/raw", headers=auth(USERS[0])).status_code == 404
        assert (
            client.post(
                "/evidence/query", json={"sql": "SELECT * FROM usage"}, headers=auth(USERS[0])
            ).status_code
            == 401
        )
        headers = {"authorization": "Bearer test-control-token-at-least-24-characters"}
        assert (
            client.post(
                "/evidence/query",
                json={"query_id": "SELECT * FROM usage", "nonce": str(uuid4())},
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/evidence/query",
                json={"query_id": "usage_by_tier", "nonce": str(uuid4())},
                headers=headers,
            ).status_code
            == 422
        )


def test_gateway_fails_closed_when_journal_commit_fails(gateway_app, monkeypatch):
    def fail(_):
        raise OSError("disk full")

    monkeypatch.setattr(gateway_app.state.journal, "append", fail)
    with TestClient(gateway_app, raise_server_exceptions=False) as client:
        response = client.get("/v1/me", headers=auth(USERS[0]))
        assert response.status_code == 500
        assert "x-usage-seq" not in response.headers


def test_collector_accepts_real_otlp_protobuf_and_protects_remote_ingest(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COLLECTOR_TOKEN", "receiver-token")
    batch = ExportTraceServiceRequest()
    batch.resource_spans.add().scope_spans.add().spans.add(name="example")
    headers = {"content-type": "application/x-protobuf", "authorization": "Bearer receiver-token"}
    with TestClient(collector.create_app()) as client:
        assert client.post("/v1/traces", content=batch.SerializeToString()).status_code == 401
        assert (
            client.post(
                "/v1/traces", content=batch.SerializeToString(), headers=headers
            ).status_code
            == 200
        )
        assert client.post("/v1/traces", content=b"\xff", headers=headers).status_code == 400
        assert client.get("/health").json()["spans_received"] == 1
    assert (
        json.loads((tmp_path / "otel.jsonl").read_text())["resourceSpans"][0]["scopeSpans"][0][
            "spans"
        ][0]["name"]
        == "example"
    )
