"""Run the project-local Tinfoil CLI without saving credentials outside .env."""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


def main():
    values = dotenv_values(ROOT / ".env")
    if not values.get("TINFOIL_ADMIN_KEY"):
        raise SystemExit("Configure TINFOIL_ADMIN_KEY in the project .env")
    arguments = sys.argv[1:]
    if not arguments or arguments[0] in ("login", "logout"):
        raise SystemExit("Pass a Tinfoil command; login/logout are unnecessary with local .env")
    environment = {
        **os.environ,
        "TINFOIL_ADMIN_KEY": values["TINFOIL_ADMIN_KEY"],
        "TINFOIL_CONFIG": str(ROOT / "data" / "tinfoil-cli-config.json"),
        "TINFOIL_NO_UPDATE_CHECK": "1",
    }
    result = subprocess.run(
        [str(ROOT / "data/bin/tinfoil"), *arguments],
        env=environment,
        capture_output=True,
        text=True,
    )
    for stream, output in ((sys.stdout, result.stdout), (sys.stderr, result.stderr)):
        for value in values.values():
            if value:
                output = output.replace(value, "[redacted]")
        print(output, end="", file=stream)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
