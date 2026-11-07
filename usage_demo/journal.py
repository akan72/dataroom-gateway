import json
import sqlite3
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from usage_demo.catalog import public_catalog
from usage_demo.crypto import Signer, canonical, digest, verify

ZERO = "0" * 64


def now() -> str:
    return datetime.now(UTC).isoformat()


class Journal:
    """SQLite FULL commits precede successful gateway responses; one writer per process."""

    def __init__(self, path: Path, signer: Signer, key_source: str, *, resume: bool = False):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS records (
            session_id TEXT, seq INTEGER, envelope TEXT,
            PRIMARY KEY (session_id, seq))""")
        self.signer = signer
        self.lock = threading.Lock()
        self.session_id = str(uuid4())
        self.started_at = now()
        self.seq = 0
        self.head = ZERO
        self.key_source = key_source
        self.catalog_hash = digest(public_catalog())
        self.tier_counts = Counter()
        self.scopes = set()
        self.successful = 0
        self.server_errors = 0
        if resume:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS journal_meta (id INTEGER PRIMARY KEY, data TEXT)"
            )
            saved = self.db.execute("SELECT data FROM journal_meta WHERE id=1").fetchone()
            if saved:
                metadata = json.loads(saved[0])
                self.session_id = metadata["session_id"]
                self.started_at = metadata["started_at"]
                previous = ZERO
                envelopes = self.db.execute(
                    "SELECT envelope FROM records WHERE session_id=? ORDER BY seq",
                    (self.session_id,),
                ).fetchall()
                for (raw,) in envelopes:
                    envelope = json.loads(raw)
                    record = verify(envelope, signer.public_pem)
                    if (
                        record["session_id"] != self.session_id
                        or record["seq"] != self.seq + 1
                        or record["previous_hash"] != previous
                        or record["kind"] != "usage-record-v1"
                    ):
                        raise ValueError("Durable journal recovery found a broken chain")
                    self.seq = record["seq"]
                    previous = digest(record)
                    self._count(record)
                self.head = previous
            else:
                if self.db.execute("SELECT count(*) FROM records").fetchone()[0]:
                    raise ValueError("Existing journal requires an explicit migration")
                with self.db:
                    self.db.execute(
                        "INSERT INTO journal_meta VALUES (1, ?)",
                        (
                            canonical(
                                {"session_id": self.session_id, "started_at": self.started_at}
                            ).decode(),
                        ),
                    )

    def _count(self, record: dict):
        if record["user_id"] is not None:
            self.tier_counts[record["tier"]] += 1
            self.scopes.add(record["user_id"])
        self.successful += int(200 <= record["status"] < 300)
        self.server_errors += int(record["status"] >= 500)

    def append(self, record: dict) -> dict:
        with self.lock:
            payload = {
                **record,
                "kind": "usage-record-v1",
                "session_id": self.session_id,
                "seq": self.seq + 1,
                "previous_hash": self.head,
            }
            envelope = self.signer.sign(payload)
            with self.db:
                self.db.execute(
                    "INSERT INTO records VALUES (?, ?, ?)",
                    (
                        self.session_id,
                        payload["seq"],
                        canonical(envelope).decode(),
                    ),
                )
            self.seq = payload["seq"]
            self.head = digest(payload)
            self._count(record)
            return envelope

    def tail(self, after: int, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT envelope FROM records WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?",
                (self.session_id, after, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def recent_cursor(self) -> int:
        cutoff = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        with self.lock:
            first = self.db.execute(
                "SELECT min(seq) FROM records WHERE session_id=? "
                "AND json_extract(envelope, '$.payload.timestamp') >= ?",
                (self.session_id, cutoff),
            ).fetchone()[0]
            return max(0, (first - 1) if first is not None else self.seq)

    def live_state(self) -> dict:
        with self.lock:
            return self.signer.sign(
                {
                    "kind": "public-demo-state-v1",
                    "session_id": self.session_id,
                    "seq": self.seq,
                    "head_hash": self.head,
                    "timestamp": now(),
                    "tier_counts": dict(self.tier_counts),
                    "active_scopes": len(self.scopes),
                    "successful_requests": self.successful,
                    "server_errors": self.server_errors,
                    "key_source": self.key_source,
                    "hardware_attestation_verified": False,
                    "data_classification": "Public synthetic demo; anonymous self-selected tiers",
                }
            )

    def snapshot(self) -> tuple[list[dict], dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT envelope FROM records WHERE session_id=? ORDER BY seq",
                (self.session_id,),
            ).fetchall()
            checkpoint = self.signer.sign(
                {
                    "kind": "usage-checkpoint-v1",
                    "session_id": self.session_id,
                    "record_count": self.seq,
                    "head_hash": self.head,
                    "started_at": self.started_at,
                    "issued_at": now(),
                    "identity_catalog_hash": self.catalog_hash,
                    "identity_policy": "Demo catalog and visitor tokens; billing unverified",
                    "key_source": self.key_source,
                    "hardware_attestation_verified": False,
                    "coverage": "This gateway process only; synthetic demo identities",
                }
            )
        envelopes = [json.loads(row[0]) for row in rows]
        records = verify_snapshot(envelopes, checkpoint, self.signer.public_pem)
        return records, checkpoint

    def close(self):
        self.db.close()


def verify_snapshot(envelopes: list[dict], checkpoint: dict, public_pem: str) -> list[dict]:
    manifest = verify(checkpoint, public_pem)
    if manifest.get("kind") != "usage-checkpoint-v1":
        raise ValueError("Expected a usage checkpoint")
    records = [verify(e, public_pem) for e in envelopes]
    records.sort(key=lambda row: row["seq"])
    previous = ZERO
    for seq, record in enumerate(records, 1):
        if (
            record.get("kind") != "usage-record-v1"
            or record["session_id"] != manifest["session_id"]
            or record["seq"] != seq
            or record["previous_hash"] != previous
        ):
            raise ValueError("Broken journal chain, missing record, or wrong session")
        previous = digest(record)
    if len(records) != manifest["record_count"] or previous != manifest["head_hash"]:
        raise ValueError("Snapshot does not match the witnessed checkpoint")
    return records
