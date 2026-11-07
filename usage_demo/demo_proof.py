"""Public demo: anchor a request record and SQL receipt to a freshly attested key."""

import argparse
import copy
import json
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

from usage_demo.analysis import verify_receipt
from usage_demo.crypto import verify

ROOT = Path(__file__).resolve().parent.parent


def latest_record(client: httpx.Client, public_pem: str) -> dict:
    candidate = None
    deadline = time.monotonic() + 10
    event = ""
    with client.stream("GET", "/demo/events") as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                break
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                envelope = json.loads(line.removeprefix("data: "))
                payload = verify(envelope, public_pem)
                if event == "record" and payload.get("kind") == "usage-record-v1":
                    candidate = envelope
                elif event == "state" and candidate:
                    if candidate["payload"]["session_id"] != payload.get("session_id"):
                        raise ValueError("The journal session changed during the proof")
                    return candidate
    raise ValueError("No signed requests yet. Have a visitor run a job, then retry.")


def proof(client: httpx.Client, public_pem: str) -> dict:
    record = latest_record(client, public_pem)
    nonce = str(uuid4())
    for attempt in range(3):
        response = client.post("/demo/query", json={"query_id": "usage_by_tier", "nonce": nonce})
        if response.status_code != 429 or attempt == 2:
            break
        time.sleep(2.1)
    response.raise_for_status()
    receipt = response.json()
    checkpoint = receipt["payload"]["checkpoint"]
    payload = verify_receipt(receipt, checkpoint, public_pem, nonce)
    if payload["query_id"] != "usage_by_tier" or receipt["key_id"] != record["key_id"]:
        raise ValueError("The SQL result was not signed by the same endorsed key")
    if checkpoint["payload"]["session_id"] != record["payload"]["session_id"]:
        raise ValueError("The receipt and request belong to different journal sessions")
    if checkpoint["payload"]["record_count"] < record["payload"]["seq"]:
        raise ValueError("The SQL snapshot precedes the observed request")

    changed_record = copy.deepcopy(record)
    changed_record["payload"]["status"] = 200 if record["payload"]["status"] != 200 else 503
    changed_receipt = copy.deepcopy(receipt)
    changed_receipt["payload"]["result"].append({"tampered": True})
    checks = {}
    for label, envelope in (
        ("modified_request_rejected", changed_record),
        ("modified_sql_result_rejected", changed_receipt),
    ):
        try:
            verify(envelope, public_pem)
        except ValueError:
            checks[label] = True
        else:
            raise ValueError("Modified evidence was unexpectedly accepted")
    try:
        verify_receipt(receipt, checkpoint, public_pem, str(uuid4()))
    except ValueError:
        checks["old_receipt_rejected_for_new_nonce"] = True
    else:
        raise ValueError("A receipt was accepted for a different client challenge")

    return {
        "endorsed_key_id": record["key_id"],
        "request_record_signature_verified": True,
        "request_sequence": record["payload"]["seq"],
        "request": f"{record['payload']['method']} {record['payload']['route']}",
        "request_status": record["payload"]["status"],
        "sql_receipt_signature_verified": True,
        "checkpoint_signature_verified": True,
        "same_endorsed_key": True,
        "fresh_nonce_matches": True,
        "sql_engine": payload["engine"],
        "snapshot_records": checkpoint["payload"]["record_count"],
        "sql_result": payload["result"],
        **checks,
        "scope": "One API record and a SQL receipt, anchored to the independently verified key",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/tinfoil")
    args = parser.parse_args()
    release = json.loads((ROOT / "deploy/release.json").read_text())
    url = args.url or release["url"]
    # Never trust an uploaded JSON report or a same-origin public key as the trust anchor.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "usage_demo.verify_remote",
            url,
            "--data-dir",
            str(args.data_dir),
        ],
        check=True,
    )
    public_pem = (args.data_dir / "public-key.pem").read_text()
    with httpx.Client(base_url=url, timeout=httpx.Timeout(30, read=10)) as client:
        result = proof(client, public_pem)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
