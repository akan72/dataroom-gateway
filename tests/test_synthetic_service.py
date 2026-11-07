import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx

from usage_demo.catalog import BY_TOKEN, USERS
from usage_demo.synthetic_service import HOT_USER, initial_state, run, scheduled_user


def test_concentration_is_exact_and_schedule_resumes():
    users = [scheduled_user(i, 42) for i in range(10000)]
    counts = Counter(user.user_id for user in users)
    assert counts[HOT_USER.user_id] == 2000
    assert sorted(counts.values()) == [1000] * 8 + [2000]
    assert {user.tier for user in users} == {"Free", "Pro", "Enterprise"}
    assert [scheduled_user(i, 42) for i in range(135, 155)] == users[135:155]


def test_paced_run_uses_scoped_projects_respects_tiers_and_stops(tmp_path):
    projects = {}

    def respond(request):
        user = BY_TOKEN[request.headers["authorization"].removeprefix("Bearer ")]
        if request.url.path == "/v1/projects":
            if request.method == "GET":
                result = [projects[user.user_id]] if user.user_id in projects else []
            else:
                result = {"id": user.user_id}
                projects[user.user_id] = result
        elif request.url.path == "/v1/jobs":
            body = json.loads(request.content)
            assert body["project_id"] == projects[user.user_id]["id"]
            assert 1 <= body["units"] <= (2 if user.tier == "Free" else 8)
            result = {"status": "complete"}
        else:
            assert request.url.path == "/v1/me"
            result = {"user_id": user.user_id}
        return httpx.Response(
            200, json=result, headers={"x-usage-session": "test-session", "x-usage-seq": "1"}
        )

    deadline = datetime.now(UTC) + timedelta(seconds=0.3)
    state = initial_state("https://demo.test", deadline, 0.001, 42)
    path = tmp_path / "state.json"

    async def exercise():
        async with httpx.AsyncClient(
            base_url="https://demo.test", transport=httpx.MockTransport(respond)
        ) as client:
            await run(client, state, path, 0.001, deadline, 42)

    asyncio.run(exercise())
    persisted = json.loads(path.read_text())
    assert persisted["status"] == "completed"
    assert persisted["workload_requests"] >= 20
    assert len(projects) == len(USERS)
    assert persisted["gateway_sessions"] == ["test-session"]
    assert persisted["journaled_responses"] == (
        persisted["workload_requests"] + persisted["setup_requests"]
    )


def test_expired_run_sends_nothing(tmp_path):
    def reject(request):
        raise AssertionError("Expired service must not contact gateway")

    deadline = datetime.now(UTC) - timedelta(seconds=1)
    state = initial_state("https://demo.test", deadline, 1, 42)

    async def exercise():
        async with httpx.AsyncClient(
            base_url="https://demo.test", transport=httpx.MockTransport(reject)
        ) as client:
            await run(client, state, tmp_path / "state.json", 1, deadline, 42)

    asyncio.run(exercise())
    assert state["status"] == "completed"
    assert state["workload_requests"] == state["setup_requests"] == 0
