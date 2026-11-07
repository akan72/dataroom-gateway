import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import duckdb
import httpx

from usage_demo.analysis import QUERIES, run_query, verify_receipt
from usage_demo.indexer import index
from usage_demo.traffic import generate


def output(value):
    print(json.dumps(value, indent=2))


def control_token(data_dir: Path, filename: str = "control-token") -> str:
    path = data_dir / filename
    if not path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        with path.open("x") as file:
            os.chmod(path, 0o600)
            file.write(secrets.token_urlsafe(32))
    return path.read_text().strip()


def serve(args):
    data_dir = args.data_dir.resolve()
    env = {
        **os.environ,
        "DEMO_DATA_DIR": str(data_dir),
        "GATEWAY_CONTROL_TOKEN": control_token(data_dir),
        "DEMO_VISITOR_SECRET": control_token(data_dir, "visitor-secret"),
        "DEMO_PUBLIC_UI": os.getenv("DEMO_PUBLIC_UI", "1"),
        "SAAS_UPSTREAM": "http://127.0.0.1:8001",
        "OTEL_COLLECTOR_URL": "http://127.0.0.1:4318",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:4318/v1/traces",
    }
    ports = [("collector", 4318), ("saas", 8001), ("gateway", 8000)]
    for _, port in ports:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except PermissionError as exc:
                raise RuntimeError("Local networking is blocked by the execution sandbox") from exc
            except OSError as exc:
                raise ValueError(f"Port {port} is already in use; no processes started") from exc
    processes = []
    logs = []
    try:
        for name, port in ports:
            log = (data_dir / f"{name}.log").open("a")
            logs.append(log)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    f"usage_demo.{name}:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                env=env,
                stdout=log,
                stderr=log,
            )
            processes.append(process)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(f"{name} exited; inspect {data_dir / (name + '.log')}")
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5).is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError(f"{name} readiness timed out")
        print("Ready: gateway http://127.0.0.1:8000 | SaaS :8001 | OTLP :4318", flush=True)
        print(f"Local development signing only. Data/logs: {data_dir}", flush=True)
        print(
            "Ctrl-C stops all three services. Run traffic/capture in another terminal.", flush=True
        )
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise RuntimeError("A service stopped; inspect the service logs")
    except KeyboardInterrupt:
        print("Stopping services", flush=True)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()


def capture(args):
    headers = {"authorization": f"Bearer {control_token(args.data_dir)}"}
    nonce = str(uuid4())
    with httpx.Client(base_url=args.gateway, headers=headers, timeout=30) as client:
        response = client.post("/evidence/query", json={"query_id": args.query_id, "nonce": nonce})
        response.raise_for_status()
        receipt = response.json()
        checkpoint = receipt["payload"]["checkpoint"]
        # Capture expected nonce separately from the service's signed response.
        public_pem = (args.data_dir / "public-key.pem").read_text()
        verify_receipt(receipt, checkpoint, public_pem, nonce)
        response = client.post("/evidence/flush")
        response.raise_for_status()
        if not response.json()["flushed"]:
            raise ValueError("Gateway OTel flush timed out")
    for name, value in (
        ("checkpoint.json", checkpoint),
        ("receipt.json", receipt),
        ("expected-request.json", {"nonce": nonce, "query_id": args.query_id}),
    ):
        (args.data_dir / name).write_text(json.dumps(value, indent=2) + "\n")
    output(
        {
            "checkpoint_records": checkpoint["payload"]["record_count"],
            "query_id": args.query_id,
            "result": receipt["payload"]["result"],
            "hardware_attestation_verified": False,
        }
    )


def main():
    parser = argparse.ArgumentParser(description="Private usage demo")
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("DEMO_DATA_DIR", "data")))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    traffic = sub.add_parser("traffic")
    traffic.add_argument("--requests", type=int, default=1000)
    traffic.add_argument("--concurrency", type=int, default=12)
    traffic.add_argument("--seed", type=int, default=42)
    traffic.add_argument("--gateway", default="http://127.0.0.1:8000")
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--query-id", choices=list(QUERIES), default="usage_by_tier")
    capture_parser.add_argument("--gateway", default="http://127.0.0.1:8000")
    sub.add_parser("index")
    query = sub.add_parser("query")
    query.add_argument("query_id", choices=list(QUERIES), default="usage_by_tier", nargs="?")
    sub.add_parser("verify")
    args = parser.parse_args()
    if args.command == "serve":
        serve(args)
    elif args.command == "traffic":
        if not 1 <= args.requests <= 100000 or not 1 <= args.concurrency <= 100:
            parser.error("requests must be 1..100000; concurrency must be 1..100")
        output(asyncio.run(generate(args.gateway, args.requests, args.concurrency, args.seed)))
    elif args.command == "capture":
        capture(args)
    elif args.command == "index":
        output(
            index(
                args.data_dir / "otel.jsonl",
                args.data_dir / "usage.duckdb",
                json.loads((args.data_dir / "checkpoint.json").read_text()),
                (args.data_dir / "public-key.pem").read_text(),
            )
        )
    elif args.command == "query":
        with duckdb.connect(str(args.data_dir / "usage.duckdb"), read_only=True) as db:
            output(run_query(db, args.query_id))
    elif args.command == "verify":
        receipt = json.loads((args.data_dir / "receipt.json").read_text())
        checkpoint = json.loads((args.data_dir / "checkpoint.json").read_text())
        expected = json.loads((args.data_dir / "expected-request.json").read_text())
        payload = verify_receipt(
            receipt, checkpoint, (args.data_dir / "public-key.pem").read_text(), expected["nonce"]
        )
        if payload["query_id"] != expected["query_id"]:
            raise ValueError("Unexpected query ID")
        # Rebuild from signed OTLP records: do not trust a previously mutable DuckDB file.
        index(
            args.data_dir / "otel.jsonl",
            args.data_dir / "usage.duckdb",
            checkpoint,
            (args.data_dir / "public-key.pem").read_text(),
        )
        with duckdb.connect(str(args.data_dir / "usage.duckdb"), read_only=True) as db:
            local = run_query(db, payload["query_id"])
        if local != payload["result"]:
            raise ValueError("Local SQL result disagrees with the signed receipt")
        output(
            {
                "signature_valid": True,
                "complete_snapshot": True,
                "sql_result_reproduced": True,
                "hardware_attestation_verified": False,
                "records": checkpoint["payload"]["record_count"],
                "result": local,
            }
        )


if __name__ == "__main__":
    main()
