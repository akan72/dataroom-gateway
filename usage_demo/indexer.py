import base64
import json
from pathlib import Path

import duckdb

from usage_demo.analysis import insert_records
from usage_demo.crypto import canonical, verify
from usage_demo.journal import verify_snapshot


def trace_id_hex(value: str) -> str:
    """Protobuf JSON encodes bytes in base64; usage records use W3C hexadecimal IDs."""
    try:
        raw = bytes.fromhex(value) if len(value) == 32 else base64.b64decode(value, validate=True)
    except ValueError:
        return ""
    return raw.hex() if len(raw) == 16 else ""


def index(otel_path: Path, database: Path, checkpoint: dict, public_pem: str) -> dict:
    """OTLP service names are untrusted. Only cryptographically valid records count."""
    manifest = verify(checkpoint, public_pem)
    envelopes: dict[int, dict] = {}
    spans = []
    rejected = duplicates = 0
    with otel_path.open() as file:
        for line in file:
            batch = json.loads(line)
            for resource in batch.get("resourceSpans", []):
                resource_attrs = {
                    a["key"]: a["value"].get("stringValue")
                    for a in resource.get("resource", {}).get("attributes", [])
                }
                for scope in resource.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        spans.append(
                            (
                                resource_attrs.get("service.name", "unknown"),
                                span.get("name", ""),
                                trace_id_hex(span.get("traceId", "")),
                                json.dumps(span),
                            )
                        )
                        attrs = {
                            a["key"]: a["value"].get("stringValue")
                            for a in span.get("attributes", [])
                        }
                        if "usage.signed_record" not in attrs:
                            continue
                        try:
                            envelope = json.loads(attrs["usage.signed_record"])
                            payload = verify(envelope, public_pem)
                            if payload.get("kind") != "usage-record-v1":
                                raise ValueError("Wrong evidence type")
                        except (ValueError, KeyError, TypeError):
                            rejected += 1
                            continue
                        if payload["session_id"] != manifest["session_id"]:
                            continue
                        if payload["seq"] > manifest["record_count"]:
                            continue  # Newer traffic is outside this specific witnessed snapshot.
                        seq = payload["seq"]
                        if seq in envelopes:
                            if canonical(envelopes[seq]) != canonical(envelope):
                                raise ValueError("Conflicting signed records at the same sequence")
                            duplicates += 1  # OTLP exporters may retry batches.
                        envelopes[seq] = envelope
    records = verify_snapshot(list(envelopes.values()), checkpoint, public_pem)
    database.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database)) as db:
        db.execute("BEGIN")
        try:
            db.execute("DROP TABLE IF EXISTS usage")
            db.execute("DROP TABLE IF EXISTS otel_spans")
            db.execute("DROP TABLE IF EXISTS evidence_snapshot")
            insert_records(db, records)
            db.execute(
                "CREATE TABLE otel_spans(service VARCHAR, name VARCHAR, trace_id VARCHAR, "
                "span_json VARCHAR)"
            )
            if spans:
                db.executemany("INSERT INTO otel_spans VALUES (?, ?, ?, ?)", spans)
            db.execute("CREATE TABLE evidence_snapshot(checkpoint_json VARCHAR)")
            db.execute("INSERT INTO evidence_snapshot VALUES (?)", [canonical(checkpoint).decode()])
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {
        "verified_usage_records": len(records),
        "otel_spans": len(spans),
        "duplicate_records": duplicates,
        "rejected_signed_records": rejected,
        "session_id": manifest["session_id"],
        "database": str(database),
    }
