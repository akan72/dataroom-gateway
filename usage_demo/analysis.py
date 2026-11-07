"""Embedded gateway SQL: runs inside the enclave when the gateway is deployed to Tinfoil."""

import duckdb

from usage_demo.crypto import digest, verify

MIN_USERS = 3
SCHEMA = """CREATE TABLE usage (
    session_id VARCHAR, seq BIGINT, timestamp VARCHAR, request_id VARCHAR,
    user_id VARCHAR, tier VARCHAR, method VARCHAR, route VARCHAR,
    status BIGINT, elapsed_us BIGINT, trace_id VARCHAR)"""
COLUMNS = (
    "session_id",
    "seq",
    "timestamp",
    "request_id",
    "user_id",
    "tier",
    "method",
    "route",
    "status",
    "elapsed_us",
    "trace_id",
)
QUERIES = {
    "usage_by_tier": """SELECT tier, count(*) AS requests,
        count(DISTINCT user_id) AS active_users,
        count(*) FILTER (WHERE status >= 200 AND status < 300) AS successful_requests,
        count(*) FILTER (WHERE status >= 500) AS server_errors,
        CAST(round(avg(elapsed_us)) AS BIGINT) AS mean_latency_us
        FROM usage WHERE user_id IS NOT NULL
        GROUP BY tier HAVING count(DISTINCT user_id) >= 3 ORDER BY tier""",
    "customer_concentration": """WITH per_user AS (
        SELECT user_id, count(*) AS requests FROM usage
        WHERE user_id IS NOT NULL GROUP BY user_id)
        SELECT count(*) AS active_users, sum(requests)::BIGINT AS requests,
        max(requests) AS largest_user_requests,
        CAST(round(10000.0 * max(requests) / sum(requests)) AS BIGINT)
            AS largest_user_share_basis_points
        FROM per_user HAVING count(*) >= 3""",
    "job_reliability": """SELECT tier, count(*) AS attempted_jobs,
        count(*) FILTER (WHERE status >= 200 AND status < 300) AS successful_jobs,
        count(*) FILTER (WHERE status >= 500) AS server_errors
        FROM usage WHERE route = '/v1/jobs' AND method = 'POST' AND user_id IS NOT NULL
        GROUP BY tier HAVING count(DISTINCT user_id) >= 3 ORDER BY tier""",
}


def insert_records(db, records: list[dict]):
    db.execute(SCHEMA)
    if records:
        db.executemany(
            f"INSERT INTO usage VALUES ({','.join('?' for _ in COLUMNS)})",
            [[row[column] for column in COLUMNS] for row in records],
        )


def run_query(db, query_id: str) -> list[dict]:
    sql = QUERIES[query_id]
    result = db.execute(sql)
    names = [column[0] for column in result.description]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def query_snapshot(records: list[dict], checkpoint: dict, query_id: str, nonce: str) -> dict:
    with duckdb.connect(":memory:", config={"enable_external_access": "false"}) as db:
        insert_records(db, records)
        if db.execute("SELECT count(DISTINCT user_id) FROM usage").fetchone()[0] < MIN_USERS:
            raise ValueError("At least three distinct user scopes are required")
        result = run_query(db, query_id)
    return {
        "kind": "usage-sql-receipt-v1",
        "query_id": query_id,
        "sql": QUERIES[query_id],
        "sql_hash": digest(QUERIES[query_id]),
        "parameters": {},
        "engine": f"duckdb/{duckdb.__version__}",
        "checkpoint": checkpoint,
        "checkpoint_hash": digest(checkpoint),
        "nonce": nonce,
        "result": result,
        "privacy_policy": "Fixed queries; minimum 3 distinct scopes per released cohort; NOT DP",
        "hardware_attestation_verified": False,
    }


def verify_receipt(receipt: dict, checkpoint: dict, public_pem: str, nonce: str) -> dict:
    payload = verify(receipt, public_pem)
    verify(checkpoint, public_pem)
    query_id = payload.get("query_id")
    if (
        payload.get("kind") != "usage-sql-receipt-v1"
        or query_id not in QUERIES
        or payload.get("nonce") != nonce
        or payload.get("checkpoint") != checkpoint
        or payload.get("checkpoint_hash") != digest(checkpoint)
        or payload.get("sql") != QUERIES[query_id]
        or payload.get("sql_hash") != digest(QUERIES[query_id])
        or payload.get("parameters") != {}
    ):
        raise ValueError("Receipt does not match the requested query, nonce, or snapshot")
    return payload
