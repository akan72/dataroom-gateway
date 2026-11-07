"""Paced, time-bounded demo traffic; all activity goes through the real gateway."""

import argparse
import asyncio
import fcntl
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import httpx

from usage_demo.catalog import USERS

HOT_USER = USERS[6]


def scheduled_user(index: int, seed: int):
    # Exactly 20% for the hot scope in every complete block of 20 requests.
    block = [HOT_USER] * 4 + [user for user in USERS if user != HOT_USER for _ in range(2)]
    random.Random(seed + index // 20).shuffle(block)
    return block[index % 20]


def save(path: Path, state: dict):
    state["updated_at"] = datetime.now(UTC).isoformat()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(path)


def account(state: dict, user, response: httpx.Response | None, phase: str):
    state[f"{phase}_requests"] += 1
    status = str(response.status_code) if response is not None else "transport_error"
    state["status_counts"][status] = state["status_counts"].get(status, 0) + 1
    counts = state["users"][user.user_id]
    counts[phase] += 1
    if response is not None:
        session = response.headers.get("x-usage-session")
        if session:
            if session not in state["gateway_sessions"]:
                state["gateway_sessions"].append(session)
            state["journaled_responses"] += 1
            state["last_usage_seq"] = response.headers.get("x-usage-seq")
    total = state["workload_requests"]
    state["hot_user_workload_share"] = (
        state["users"][HOT_USER.user_id]["workload"] / total if total else 0
    )


async def ensure_project(client, user, state, deadline: datetime):
    headers = {"authorization": f"Bearer {user.token}"}
    response = await client.get("/v1/projects", headers=headers)
    account(state, user, response, "setup")
    response.raise_for_status()
    projects = response.json()
    if not projects:
        if datetime.now(UTC) >= deadline:
            return
        response = await client.post(
            "/v1/projects", headers=headers, json={"name": f"Synthetic {user.tier} workspace"}
        )
        account(state, user, response, "setup")
        response.raise_for_status()
        projects = [response.json()]
    state["projects"][user.user_id] = projects[0]["id"]


async def run(client, state: dict, path: Path, interval: float, deadline: datetime, seed: int):
    state["status"] = "running"
    save(path, state)
    failures = 0
    while datetime.now(UTC) < deadline:
        started = asyncio.get_running_loop().time()
        user = scheduled_user(state["workload_requests"], seed)
        response = None
        attempted = False
        try:
            if user.user_id not in state["projects"]:
                await ensure_project(client, user, state, deadline)
            if datetime.now(UTC) >= deadline:
                break
            rng = random.Random(seed + state["workload_requests"] * 101)
            headers = {"authorization": f"Bearer {user.token}"}
            attempted = True
            if rng.random() < 0.7:
                response = await client.post(
                    "/v1/jobs",
                    headers=headers,
                    json={
                        "project_id": state["projects"][user.user_id],
                        "units": rng.randint(1, 2 if user.tier == "Free" else 8),
                    },
                )
                if response.status_code == 404:
                    state["projects"].pop(user.user_id, None)
            else:
                response = await client.get(rng.choice(["/v1/me", "/v1/projects"]), headers=headers)
            account(state, user, response, "workload")
            failures = failures + 1 if response.status_code in (429, 502, 504) else 0
            state["last_error"] = None
        except httpx.HTTPError as error:
            # A failed setup is not a workload attempt; retry this same scheduled user.
            if attempted:
                account(state, user, None, "workload")
            failures += 1
            state["last_error"] = type(error).__name__
        save(path, state)
        if state["workload_requests"] % 60 == 0 or failures:
            print(
                json.dumps(
                    {
                        "at": state["updated_at"],
                        "workload_requests": state["workload_requests"],
                        "hot_user_share": state["hot_user_workload_share"],
                        "status_counts": state["status_counts"],
                        "last_error": state["last_error"],
                    }
                ),
                flush=True,
            )
        elapsed = asyncio.get_running_loop().time() - started
        delay = max(interval - elapsed, min(30, 2 ** min(failures, 5)) if failures else 0)
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        if remaining > 0:
            await asyncio.sleep(min(delay, remaining))
    state["status"] = "completed"
    state["completed_at"] = datetime.now(UTC).isoformat()
    save(path, state)
    print(json.dumps({"status": "completed", "workload_requests": state["workload_requests"]}))


def initial_state(gateway: str, deadline: datetime, interval: float, seed: int):
    return {
        "gateway": gateway,
        "started_at": datetime.now(UTC).isoformat(),
        "stop_at": deadline.isoformat(),
        "interval_seconds": interval,
        "seed": seed,
        "synthetic": True,
        "target_hot_user_share": 0.2,
        "hot_user_id": HOT_USER.user_id,
        "hot_user_tier": HOT_USER.tier,
        "workload_requests": 0,
        "setup_requests": 0,
        "journaled_responses": 0,
        "status_counts": {},
        "gateway_sessions": [],
        "projects": {},
        "users": {user.user_id: {"tier": user.tier, "setup": 0, "workload": 0} for user in USERS},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--until", required=True, help="Absolute timezone-aware ISO stop time")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--seed", type=int, default=20261107)
    args = parser.parse_args()
    deadline = datetime.fromisoformat(args.until)
    if deadline.tzinfo is None or not 0.1 <= args.interval <= 60:
        parser.error("until must have a timezone; interval must be 0.1..60 seconds")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = (
            json.loads(args.state.read_text())
            if args.state.exists()
            else initial_state(args.gateway, deadline, args.interval, args.seed)
        )
        expected = (args.gateway, deadline.isoformat(), args.interval, args.seed)
        actual = tuple(state[k] for k in ("gateway", "stop_at", "interval_seconds", "seed"))
        if actual != expected:
            parser.error("Existing state belongs to a different run; choose a new --state path")

        async def connected():
            async with httpx.AsyncClient(base_url=args.gateway, timeout=10) as client:
                await run(client, state, args.state, args.interval, deadline, args.seed)

        asyncio.run(connected())


if __name__ == "__main__":
    main()
