"""Measures GET /api/v1/admin/orders with filters. Standard library only.

    docker compose exec api-1 python -m app.seed_orders 100000
    python scripts/perf_admin_orders.py

Exits non-zero if p95 is 200 ms or more.
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("API_BASE_URL", "http://localhost:8088")
PASSWORD = os.environ.get("SEED_ADMIN_PASSWORD", "Admin-Test-2026")
REQUESTS_PER_CASE = 60
LIMIT_MS = 200
STATUSES = ["COMPLETED", "EXPIRED", "REFUNDED", "PENDING_PAYMENT", "NEEDS_REVIEW"]


def login() -> str:
    body = json.dumps({"email": "viewer@inblackm.test", "password": PASSWORD}).encode()
    request = urllib.request.Request(
        f"{API}/api/v1/admin/auth/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return response.headers["Set-Cookie"].split(";", 1)[0]


def get(cookie: str, params: dict[str, str]) -> tuple[float, dict]:
    url = f"{API}/api/v1/admin/orders?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Cookie": cookie})
    started = time.perf_counter()
    with urllib.request.urlopen(request) as response:
        body = response.read()
    return (time.perf_counter() - started) * 1000, json.loads(body)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]


def main() -> int:
    cookie = login()
    _, first = get(cookie, {"limit": "50"})
    sample_id = first["items"][0]["id"]
    cases = {
        "no filter": lambda: {"limit": "50"},
        "status": lambda: {"limit": "50", "status": random.choice(STATUSES)},
        "status + date range": lambda: {
            "limit": "50",
            "status": random.choice(STATUSES),
            "date_from": "2026-06-01",
            "date_to": "2026-10-01",
        },
        "date range": lambda: {"limit": "50", "date_from": "2026-07-01", "date_to": "2026-08-01"},
        "search by id": lambda: {"limit": "50", "q": sample_id},
    }

    # Deep pagination: follow the cursor forward and time every page.
    deep: list[float] = []
    params = {"limit": "200", "status": "COMPLETED"}
    for _ in range(REQUESTS_PER_CASE):
        elapsed, page = get(cookie, params)
        deep.append(elapsed)
        if page["next_cursor"] is None:
            break
        params["cursor"] = page["next_cursor"]

    failed = False
    all_samples = list(deep)
    print(f"{'case':24} {'p50 ms':>8} {'p95 ms':>8}")
    print(f"{'deep cursor pagination':24} {percentile(deep, 0.5):8.1f} {percentile(deep, 0.95):8.1f}")
    failed |= percentile(deep, 0.95) >= LIMIT_MS
    for name, build in cases.items():
        samples = [get(cookie, build())[0] for _ in range(REQUESTS_PER_CASE)]
        all_samples.extend(samples)
        p95 = percentile(samples, 0.95)
        print(f"{name:24} {percentile(samples, 0.5):8.1f} {p95:8.1f}")
        failed |= p95 >= LIMIT_MS
    print(f"{'overall':24} {percentile(all_samples, 0.5):8.1f} {percentile(all_samples, 0.95):8.1f}")
    print("FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
