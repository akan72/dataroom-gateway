import base64
import copy
import json

import duckdb
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from usage_demo.analysis import query_snapshot, run_query, verify_receipt
from usage_demo.crypto import Signer, canonical, verify
from usage_demo.indexer import index, trace_id_hex
from usage_demo.journal import Journal, verify_snapshot


@pytest.fixture
def evidence(tmp_path):
    signer = Signer(tmp_path / "key.pem")
    journal = Journal(tmp_path / "journal.sqlite", signer, "local-development")
    envelopes = []
    for i in range(9):
        envelopes.append(
            journal.append(
                {
                    "timestamp": "2026-11-07T12:00:00+00:00",
                    "request_id": f"request-{i}",
                    "user_id": f"user-{i % 3}",
                    "tier": "Pro",
                    "method": "POST",
                    "route": "/v1/jobs",
                    "status": 503 if i == 8 else 201,
                    "elapsed_us": 1000,
                    "trace_id": "a" * 32,
                }
            )
        )
    _, checkpoint = journal.snapshot()
    yield signer, journal, envelopes, checkpoint
    journal.close()


def test_chain_detects_tampering_missing_records_and_wrong_key(evidence, tmp_path):
    signer, _, envelopes, checkpoint = evidence
    assert len(verify_snapshot(envelopes, checkpoint, signer.public_pem)) == 9
    changed = copy.deepcopy(envelopes)
    changed[0]["payload"]["status"] = 201
    changed[0]["payload"]["tier"] = "Enterprise"
    with pytest.raises(ValueError):
        verify_snapshot(changed, checkpoint, signer.public_pem)
    for incomplete in (envelopes[:-1], envelopes[1:], envelopes[:3] + envelopes[4:]):
        with pytest.raises(ValueError):
            verify_snapshot(incomplete, checkpoint, signer.public_pem)
    wrong = Signer(tmp_path / "wrong.pem")
    with pytest.raises(ValueError):
        verify_snapshot(envelopes, checkpoint, wrong.public_pem)


def test_query_receipt_binds_sql_snapshot_nonce_and_result(evidence):
    signer, journal, _, checkpoint = evidence
    records, _ = journal.snapshot()
    payload = query_snapshot(records, checkpoint, "usage_by_tier", "buyer-nonce")
    receipt = signer.sign(payload)
    assert verify_receipt(receipt, checkpoint, signer.public_pem, "buyer-nonce")["result"] == [
        {
            "tier": "Pro",
            "requests": 9,
            "active_users": 3,
            "successful_requests": 8,
            "server_errors": 1,
            "mean_latency_us": 1000,
        }
    ]
    with pytest.raises(ValueError):
        verify_receipt(receipt, checkpoint, signer.public_pem, "different-nonce")
    later = copy.deepcopy(checkpoint)
    later["payload"]["record_count"] = 10
    with pytest.raises(ValueError):
        verify_receipt(receipt, signer.sign(later["payload"]), signer.public_pem, "buyer-nonce")
    changed = copy.deepcopy(receipt)
    changed["payload"]["result"][0]["requests"] = 999
    with pytest.raises(ValueError):
        verify(changed, signer.public_pem)
    payload["sql"] = "SELECT * FROM usage"
    with pytest.raises(ValueError):
        verify_receipt(signer.sign(payload), checkpoint, signer.public_pem, "buyer-nonce")


def test_cohort_suppression_and_no_external_access(evidence):
    _, journal, _, checkpoint = evidence
    rows, _ = journal.snapshot()
    with pytest.raises(ValueError, match="three distinct"):
        query_snapshot(rows[:2], checkpoint, "usage_by_tier", "n")
    rows[0]["tier"] = "Enterprise"
    result = query_snapshot(rows, checkpoint, "usage_by_tier", "n")["result"]
    assert [r["tier"] for r in result] == ["Pro"]


def otlp_line(envelopes):
    return (
        json.dumps(
            {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {
                                    "key": "service.name",
                                    "value": {
                                        "stringValue": "a-forged-service-name-does-not-confer-trust"
                                    },
                                }
                            ]
                        },
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "name": "request",
                                        "traceId": "a" * 32,
                                        "attributes": [
                                            {
                                                "key": "usage.signed_record",
                                                "value": {"stringValue": canonical(e).decode()},
                                            }
                                        ],
                                    }
                                    for e in envelopes
                                ]
                            }
                        ],
                    }
                ]
            }
        )
        + "\n"
    )


def test_otlp_index_deduplicates_rejects_forgery_and_checks_completeness(evidence, tmp_path):
    signer, _, envelopes, checkpoint = evidence
    forged = copy.deepcopy(envelopes[0])
    forged["payload"]["tier"] = "Enterprise"
    otel = tmp_path / "otel.jsonl"
    otel.write_text(otlp_line(envelopes + envelopes[:2] + [forged]))
    database = tmp_path / "usage.duckdb"
    stats = index(otel, database, checkpoint, signer.public_pem)
    assert stats["verified_usage_records"] == 9
    assert stats["duplicate_records"] == 2
    assert stats["rejected_signed_records"] == 1
    with duckdb.connect(str(database)) as db:
        assert run_query(db, "usage_by_tier")[0]["requests"] == 9
        assert run_query(db, "job_reliability")[0]["server_errors"] == 1
        assert run_query(db, "customer_concentration")[0]["largest_user_share_basis_points"] == 3333
    otel.write_text(otlp_line(envelopes[:-1]))
    with pytest.raises(ValueError, match="checkpoint"):
        index(otel, database, checkpoint, signer.public_pem)


def test_configured_signing_key_never_falls_back(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        Signer(tmp_path / "absent.pem", allow_create=False)
    # Keep the algorithm explicit: an unrelated key type is not silently accepted.
    assert isinstance(Signer(tmp_path / "dev.pem").key, Ed25519PrivateKey)


@pytest.mark.parametrize("value", ["a" * 32, base64.b64encode(bytes.fromhex("a" * 32)).decode()])
def test_trace_ids_join_between_protobuf_export_and_signed_records(value):
    assert trace_id_hex(value) == "a" * 32


def test_malformed_app_span_trace_id_does_not_gain_trust():
    assert trace_id_hex("not-a-valid-trace-id") == ""


@pytest.mark.parametrize("envelope", [None, [], 42, {}, {"payload": []}])
def test_malformed_unsigned_evidence_is_rejected(envelope, tmp_path):
    signer = Signer(tmp_path / "key.pem")
    with pytest.raises(ValueError, match="Malformed"):
        verify(envelope, signer.public_pem)
