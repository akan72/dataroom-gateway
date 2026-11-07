"""Small OTLP/HTTP protobuf receiver, retaining standard OTLP JSON on this machine."""

import gzip
import hmac
import json
import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

MAX_BATCH = 8 * 1024 * 1024


def create_app() -> FastAPI:
    from usage_demo.storage import wait_for_storage

    wait_for_storage()
    app = FastAPI(title="Local OTLP receiver", docs_url=None, redoc_url=None)
    path = Path(os.getenv("DEMO_DATA_DIR", "data")) / "otel.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    token = os.getenv("COLLECTOR_TOKEN")
    counters = {"batches_received": 0, "spans_received": 0, "bytes_persisted": 0}

    @app.get("/health")
    async def health():
        with lock:
            return {"status": "ok", **counters}

    @app.post("/v1/traces")
    async def traces(request: Request):
        if token and not hmac.compare_digest(
            request.headers.get("authorization", ""),
            f"Bearer {token}",
        ):
            raise HTTPException(401, "Collector authorization required")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BATCH:
                raise HTTPException(413, "OTLP batch too large")
        if request.headers.get("content-encoding") == "gzip":
            import io

            try:
                with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                    body = compressed.read(MAX_BATCH + 1)
            except (OSError, EOFError) as exc:
                raise HTTPException(400, "Invalid gzip batch") from exc
        if len(body) > MAX_BATCH:
            raise HTTPException(413, "Expanded OTLP batch too large")
        if request.headers.get("content-type", "").split(";")[0] != "application/x-protobuf":
            raise HTTPException(415, "Expected OTLP/HTTP protobuf")
        batch = ExportTraceServiceRequest()
        try:
            batch.ParseFromString(bytes(body))
        except DecodeError as exc:
            raise HTTPException(400, "Invalid OTLP protobuf") from exc
        line = json.dumps(MessageToDict(batch), separators=(",", ":")) + "\n"
        with lock, path.open("a") as file:
            file.write(line)
            file.flush()
            os.fsync(file.fileno())
            counters["batches_received"] += 1
            counters["bytes_persisted"] += len(line.encode())
            counters["spans_received"] += sum(
                len(scope.spans)
                for resource in batch.resource_spans
                for scope in resource.scope_spans
            )
        return Response(
            ExportTraceServiceResponse().SerializeToString(), media_type="application/x-protobuf"
        )

    return app
