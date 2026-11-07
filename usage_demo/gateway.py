import asyncio
import hmac
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind
from pydantic import BaseModel

from usage_demo.analysis import query_snapshot
from usage_demo.catalog import authenticate
from usage_demo.crypto import Signer, canonical
from usage_demo.journal import Journal, now
from usage_demo.telemetry import provider

MAX_BODY = 1024 * 1024
JOB_PATH = re.compile(r"^/v1/jobs/[0-9a-fA-F-]{36}$")


def route_name(path: str) -> str:
    if path in ("/v1/me", "/v1/projects", "/v1/jobs"):
        return path
    if JOB_PATH.fullmatch(path):
        return "/v1/jobs/{job_id}"
    return "unmatched"


class QueryRequest(BaseModel):
    query_id: Literal["usage_by_tier", "customer_concentration", "job_reliability"]
    nonce: UUID


def create_app() -> FastAPI:
    data_dir = Path(os.getenv("DEMO_DATA_DIR", "data"))
    token = os.environ.get("GATEWAY_CONTROL_TOKEN")
    if not token or len(token) < 24:
        raise ValueError("Set GATEWAY_CONTROL_TOKEN to at least 24 characters")
    supplied_key = os.getenv("SIGNING_KEY_PATH")
    signer = Signer(
        Path(supplied_key) if supplied_key else data_dir / "signing-key.pem",
        allow_create=not bool(supplied_key),
    )
    public_path = data_dir / "public-key.pem"
    if public_path.exists() and public_path.read_text() != signer.public_pem:
        raise ValueError("Signing key changed: use a new data directory and explicitly repin trust")
    public_path.write_text(signer.public_pem)
    key_source = "configured-key-unverified" if supplied_key else "local-development"
    journal = Journal(data_dir / "journal.sqlite", signer, key_source)
    telemetry = provider("usage-gateway")
    tracer = telemetry.get_tracer(__name__)
    upstream = os.getenv("SAAS_UPSTREAM", "http://127.0.0.1:8001").rstrip("/")
    client = httpx.AsyncClient(base_url=upstream, timeout=5, follow_redirects=False)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()
        telemetry.shutdown()
        journal.close()

    app = FastAPI(title="Usage evidence gateway", lifespan=lifespan)
    # Exposed for focused tests; no runtime endpoint exposes the signer or database.
    app.state.journal = journal
    if os.getenv("DEMO_PUBLIC_UI") == "1":
        from usage_demo.live import install_demo

        install_demo(app, journal, signer)

    async def control(request: Request):
        expected = f"Bearer {token}"
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            raise HTTPException(401, "Control-plane authorization required")

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "mode": key_source,
            "hardware_attestation_verified": False,
            "session_id": journal.session_id,
        }

    @app.get("/evidence/public-key", dependencies=[Depends(control)])
    async def public_key():
        return {
            "public_key_pem": signer.public_pem,
            "key_id": signer.key_id,
            "warning": "An HTTP key response is not independent hardware attestation",
        }

    @app.get("/evidence/checkpoint", dependencies=[Depends(control)])
    async def checkpoint():
        _, result = await asyncio.to_thread(journal.snapshot)
        return result

    @app.post("/evidence/query", dependencies=[Depends(control)])
    async def query(body: QueryRequest):
        records, snapshot = await asyncio.to_thread(journal.snapshot)
        try:
            payload = await asyncio.to_thread(
                query_snapshot,
                records,
                snapshot,
                body.query_id,
                str(body.nonce),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return signer.sign(payload)

    @app.post("/evidence/flush", dependencies=[Depends(control)])
    async def flush():
        success = await asyncio.to_thread(telemetry.force_flush, 10000)
        return {"flushed": success, "note": "OTel flush is not a durability guarantee"}

    @app.get("/evidence/telemetry", dependencies=[Depends(control)])
    async def telemetry_status():
        url = os.getenv("OTEL_COLLECTOR_URL")
        if not url:
            raise HTTPException(503, "Internal collector URL is not configured")
        try:
            result = await client.get(url.rstrip("/") + "/health")
            result.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(503, "Internal collector is unavailable") from exc
        values = result.json()
        return {
            key: values.get(key)
            for key in (
                "status",
                "batches_received",
                "spans_received",
                "bytes_persisted",
            )
        }

    @app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(request: Request, path: str):
        started = time.perf_counter_ns()
        request_id = str(uuid4())
        route = route_name(request.url.path)
        user = authenticate(request.headers.get("authorization"))
        # Ignore caller trace IDs, scope/tier headers, and arbitrary URL text in evidence.
        with tracer.start_as_current_span(f"{request.method} {route}", kind=SpanKind.SERVER):
            headers = {}
            propagate.inject(headers)
            headers["x-request-id"] = request_id
            headers["authorization"] = request.headers.get("authorization", "")
            headers["content-type"] = request.headers.get("content-type", "application/json")
            status, content = 500, b'{"detail":"Gateway failure"}'
            content_type = "application/json"
            if user is None:
                status, content = 401, b'{"detail":"Invalid demo bearer token"}'
            elif route == "unmatched":
                status, content = 404, b'{"detail":"Unknown route"}'
            else:
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        break
                if len(body) > MAX_BODY:
                    status, content = 413, b'{"detail":"Request too large"}'
                else:
                    try:
                        response = await client.request(
                            request.method,
                            request.url.path,
                            content=bytes(body),
                            headers=headers,
                        )
                        status, content = response.status_code, response.content
                        content_type = response.headers.get("content-type", "application/json")
                    except httpx.TimeoutException:
                        status, content = 504, b'{"detail":"Upstream timeout"}'
                    except httpx.RequestError:
                        status, content = 502, b'{"detail":"Upstream unavailable"}'
            span = trace.get_current_span()
            record = {
                "timestamp": now(),
                "request_id": request_id,
                "user_id": user.user_id if user else None,
                "tier": user.tier if user else None,
                "method": request.method,
                "route": route,
                "status": status,
                "elapsed_us": (time.perf_counter_ns() - started) // 1000,
                "trace_id": f"{span.get_span_context().trace_id:032x}",
            }
            # Fail closed if the authoritative journal cannot commit. OTel is a secondary copy.
            envelope = await asyncio.to_thread(journal.append, record)
            span.set_attribute("usage.signed_record", canonical(envelope).decode())
            span.set_attribute("http.response.status_code", status)
            span.set_attribute("http.route", route)
            return Response(
                content,
                status_code=status,
                headers={
                    "content-type": content_type,
                    "x-request-id": request_id,
                    "x-usage-session": journal.session_id,
                    "x-usage-seq": str(envelope["payload"]["seq"]),
                },
            )

    return app
