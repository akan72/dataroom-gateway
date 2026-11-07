# Synthetic diligence traffic

`usage_demo.synthetic_service` sends real authenticated API traffic through the public gateway.
It does not insert rows into the journal or fabricate SQL results. This is explicitly synthetic
activity, not evidence of real customer demand.

The workload uses nine fixed demo UUIDs: three each on Free, Pro, and Enterprise. In every
complete block of 20 workload attempts, Enterprise user
`f1e74314-7464-5da8-afb3-22c491364a3e` gets four requests; each of the other eight users gets two.
The user order is shuffled deterministically. The target tier mix is Free 30%, Pro 30%, and
Enterprise 40%. About 70% of operations run jobs; the rest read identity or projects. Free jobs
stay within their two-unit limit. Existing tier-specific simulated capacity failures remain active.

At the default one request per second, a three-hour run produces approximately 10,800 workload
requests, plus project setup. Setup, network failures, other visitors, prior traffic, and interrupted
blocks can move the concentration in a SQL snapshot away from exactly 20%. The service records
attempts separately from responses carrying gateway journal sequence headers. These headers are
operational feedback; use independently verified signed receipts to establish trust in results.

Current run metadata is in the ignored local file `data/synthetic/current.json`; it points to the
run's status JSON, launchd plist, and log directory. The status includes per-user workload counts,
HTTP status totals, observed gateway sessions, and an absolute stop time. The runner takes a file
lock, resumes counters after a crash, slows down on transport failures or throttling, and stops
at its deadline. A gateway redeployment resets its journal; multiple observed sessions are listed
in status and cannot be treated as one uninterrupted attested history.

To inspect the current run:

```sh
.venv/bin/python - <<'PY'
import json
from pathlib import Path
run = json.loads(Path('data/synthetic/current.json').read_text())
print(Path(run['state']).read_text())
PY
```

To stop it early:

```sh
.venv/bin/python - <<'PY'
import json
import subprocess
from pathlib import Path
run = json.loads(Path('data/synthetic/current.json').read_text())
subprocess.run(['launchctl', 'bootout', run['domain'] + '/' + run['label']], check=True)
PY
```

The launchd service runs independently of the chat. `caffeinate -i` prevents idle system sleep
while it is running; shutdown, logout, or closing a laptop lid can still interrupt a local run.
It is a one-time workload, not a recurring schedule. No Tinfoil admin or inference keys are needed.

For the diligence demo, request the fixed `customer_concentration` query to show the largest
user's request share, `usage_by_tier` for tier mix, and `job_reliability` for the intentionally
worse Enterprise failure rate. Each query returns a signed receipt over a specific journal
checkpoint; the hardware-backed verifier must endorse the signing key independently.
