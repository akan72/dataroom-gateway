import asyncio
import random
from collections import Counter

import httpx

from usage_demo.catalog import USERS


async def generate(base_url: str, requests: int, concurrency: int, seed: int) -> dict:
    """A bounded workload plus nine setup requests; every scope gets initial activity."""
    rng = random.Random(seed)
    counts = Counter()
    projects = {}
    async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
        for user in USERS:
            auth = {"authorization": f"Bearer {user.token}"}
            response = await client.get("/v1/projects", headers=auth)
            counts[str(response.status_code)] += 1
            response.raise_for_status()
            existing = response.json()
            if not existing:
                response = await client.post(
                    "/v1/projects", headers=auth, json={"name": f"{user.tier} workspace"}
                )
                counts[str(response.status_code)] += 1
                response.raise_for_status()
                existing = [response.json()]
            projects[user.user_id] = existing[0]["id"]
        semaphore = asyncio.Semaphore(concurrency)

        async def send(user, operation, units):
            async with semaphore:
                auth = {"authorization": f"Bearer {user.token}"}
                if operation == "job":
                    response = await client.post(
                        "/v1/jobs",
                        headers=auth,
                        json={
                            "project_id": projects[user.user_id],
                            "units": units,
                        },
                    )
                else:
                    response = await client.get(operation, headers=auth)
                counts[str(response.status_code)] += 1

        workload = []
        for i in range(requests):
            # One enterprise scope drives disproportionate usage, deliberately.
            user = (
                USERS[i] if i < len(USERS) else rng.choices(USERS, [1, 1, 1, 3, 3, 3, 12, 4, 4])[0]
            )
            operation = (
                "job"
                if i < len(USERS) or rng.random() < 0.7
                else rng.choice(
                    ["/v1/me", "/v1/projects"],
                )
            )
            units = rng.randint(1, 2 if user.tier == "Free" else 8)
            workload.append(send(user, operation, units))
        await asyncio.gather(*workload)
    return {
        "workload_requests": requests,
        "total_requests_including_setup": sum(counts.values()),
        "status_counts": dict(sorted(counts.items())),
        "scopes": len(USERS),
        "seed": seed,
    }
