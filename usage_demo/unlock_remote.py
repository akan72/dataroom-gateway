"""Release a locally held storage key through the official verified Tinfoil proxy."""

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", default="ee0d5542-5b9b-4576-beb0-b3529612bf28")
    parser.add_argument("--repo", help="Expected release and optional artifact digest")
    parser.add_argument(
        "--initialize", action="store_true", help="Initialize a newly allocated blank disk"
    )
    parser.add_argument("--port", type=int, default=8004)
    args = parser.parse_args()
    values = dotenv_values(ROOT / ".env")
    encoded = values.get("TINFOIL_VOLUME_KEY")
    if not encoded:
        if not args.initialize:
            raise SystemExit(
                "Original TINFOIL_VOLUME_KEY is required; refusing to generate a replacement"
            )
        encoded = base64.b64encode(secrets.token_bytes(32)).decode()
        with (ROOT / ".env").open("a") as file:
            os.chmod(ROOT / ".env", 0o600)
            file.write("\nTINFOIL_VOLUME_KEY=" + encoded + "\n")
    key = base64.b64decode(encoded, validate=True)
    if len(key) != 32:
        raise SystemExit("Invalid stored volume key length")
    control = (ROOT / "data/tinfoil/control-token").read_text().strip()
    reference = (
        args.repo or json.loads((ROOT / "deploy/release.json").read_text())["expected_reference"]
    )
    log_path = ROOT / "data/tinfoil/unlock-proxy.log"
    with log_path.open("w") as log:
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "usage_demo.deploy",
                "--stream",
                "container",
                "connect",
                args.instance,
                "--port",
                str(args.port),
                "--repo",
                reference,
            ],
            cwd=ROOT,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=90) as client:
                for _ in range(90):
                    if child.poll() is not None:
                        raise SystemExit(
                            "Verified proxy exited; inspect data/tinfoil/unlock-proxy.log"
                        )
                    try:
                        response = client.get("/health", timeout=2)
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                else:
                    raise SystemExit("Verified proxy did not become ready")
                response = client.post(
                    "/evidence/storage/unlock",
                    content=key,
                    headers={
                        "authorization": f"Bearer {control}",
                        "content-type": "application/octet-stream",
                        "x-volume-operation": "initialize" if args.initialize else "unlock",
                    },
                )
                if response.status_code != 200:
                    raise SystemExit(
                        f"Storage unlock failed (HTTP {response.status_code}); key was not logged"
                    )
                print(json.dumps(response.json(), indent=2))
        finally:
            child.terminate()
            child.wait(timeout=10)


if __name__ == "__main__":
    main()
