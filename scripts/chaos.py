"""Kills real containers while orders are being fulfilled, then checks that nothing was lost.

Needs only the Python standard library and a stack started with APP_ENV=test:

    APP_ENV=test docker compose up --build -d --wait
    python scripts/chaos.py

Exits non-zero if any top-up happened twice, any order was left behind, or any order stayed
IN_PROGRESS for more than two minutes after the killed service came back.
"""

import hashlib
import hmac
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

API = "http://localhost:8088"
GAME = "http://localhost:8091"
SECRET = "whsec_test_inblackm_2026"
PROJECT = "inblack-topup"
TERMINAL = ("COMPLETED", "FAILED", "NEEDS_REVIEW")
STUCK_LIMIT_SECONDS = 120
ORDERS_PER_SCENARIO = 24


def http(method: str, url: str, body: bytes | None = None, headers: dict | None = None):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def post_json(url: str, payload: object, headers: dict | None = None):
    merged = {"Content-Type": "application/json", **(headers or {})}
    return http("POST", url, json.dumps(payload).encode(), merged)


def sql(query: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres",
         "psql", "-U", "inblack", "-d", "inblack", "-Atc", query],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    return result.stdout.strip()


def kill(service: str) -> None:
    subprocess.run(["docker", "kill", f"{PROJECT}-{service}-1"], capture_output=True, check=True)


def start(service: str) -> None:
    subprocess.run(["docker", "compose", "start", service], capture_output=True, check=True)


def create_order() -> tuple[str, int]:
    body = {
        "items": [{"sku": "DIAMOND-1000", "qty": 1}],
        "email": f"{uuid.uuid4().hex}@example.com",
        "game_uid": "123456789",
        "payment_method": "promptpay",
    }
    status, raw = post_json(
        f"{API}/api/v1/orders", body, {"Idempotency-Key": uuid.uuid4().hex}
    )
    assert status == 201, (status, raw)
    created = json.loads(raw)
    return created["id"], created["total_satang"]


def pay(order_id: str, amount: int) -> None:
    """Delivers the webhook and retries like a real provider until it gets a 2xx."""
    raw = json.dumps(
        {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "type": "payment.succeeded",
            "order_id": order_id,
            "amount_satang": amount,
            "currency": "THB",
            "occurred_at": "2026-11-06T10:15:00Z",
        }
    ).encode()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        stamp = int(time.time())
        digest = hmac.new(SECRET.encode(), f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-IBM-Signature": f"t={stamp},v1={digest}"}
        try:
            status, _ = http("POST", f"{API}/api/v1/webhooks/payment", raw, headers)
            if status == 200:
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise AssertionError(f"webhook for {order_id} never accepted")


def count_by_status() -> dict[str, int]:
    rows = sql("SELECT status, count(*) FROM orders GROUP BY 1")
    return {line.split("|")[0]: int(line.split("|")[1]) for line in rows.splitlines()}


def wait_until_settled(restarted_at: float, total: int) -> tuple[bool, float]:
    while time.monotonic() - restarted_at < STUCK_LIMIT_SECONDS + 30:
        try:
            counts = count_by_status()
            if sum(counts.get(status, 0) for status in TERMINAL) == total:
                return True, time.monotonic() - restarted_at
        except (subprocess.CalledProcessError, ValueError):
            pass
        time.sleep(0.5)
    return False, time.monotonic() - restarted_at


def run_scenario(
    name: str,
    services: list[str],
    latency_ms: int,
    kills: int = 1,
    order_count: int = ORDERS_PER_SCENARIO,
    kill_after: float = 1,
) -> list[str]:
    print(f"\n--- {name}")
    assert http("POST", f"{API}/api/v1/_test/reset")[0] == 200
    post_json(f"{GAME}/_test/config", {"LATENCY_MS": latency_ms})

    orders = [create_order() for _ in range(order_count)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        payments = [pool.submit(pay, order_id, amount) for order_id, amount in orders]

        restarted_at = time.monotonic()
        for round_no in range(kills):
            time.sleep(kill_after)
            for service in services:
                kill(service)
            print(f"killed {services} (round {round_no + 1}) status={_safe_counts()}")
            time.sleep(3)
            for service in services:
                start(service)
            restarted_at = time.monotonic()
        for payment in payments:
            payment.result()

    settled, seconds = wait_until_settled(restarted_at, order_count)
    post_json(f"{GAME}/_test/config", {"LATENCY_MS": 0})
    return _verify(name, orders, settled, seconds)


def _safe_counts() -> str:
    try:
        return str(count_by_status())
    except (subprocess.CalledProcessError, ValueError):
        return "unavailable"


def _verify(name: str, orders: list, settled: bool, seconds: float) -> list[str]:
    counts = count_by_status()
    stats = json.loads(http("GET", f"{GAME}/stats")[1])
    problems = []
    if not settled:
        problems.append(f"orders left unfinished after {seconds:.0f}s: {counts}")
    if seconds > STUCK_LIMIT_SECONDS:
        problems.append(f"took {seconds:.0f}s to settle, the limit is {STUCK_LIMIT_SECONDS}s")
    doubled = {ref: n for ref, n in stats.items() if n > 1}
    if doubled:
        problems.append(f"DOUBLE TOP-UP: {doubled}")
    for order_id, _ in orders:
        status = sql(f"SELECT status FROM orders WHERE id = '{order_id}'")
        delivered = stats.get(f"{order_id}-1", 0)
        if status == "COMPLETED" and delivered != 1:
            problems.append(f"{order_id} is COMPLETED but was delivered {delivered} times")
        if status == "FAILED" and delivered != 0:
            problems.append(f"{order_id} is FAILED but was delivered {delivered} times")
    payments = int(sql("SELECT count(*) FROM ledger_entries WHERE kind = 'payment'"))
    if payments != len(orders):
        problems.append(f"expected {len(orders)} payments in the ledger, found {payments}")
    print(f"settled in {seconds:.1f}s after restart: {counts}")
    print(f"result: {'PASS' if not problems else 'FAIL'}")
    return [f"{name}: {problem}" for problem in problems]


def main() -> int:
    lock = threading.Lock()  # keeps output readable if scenarios are ever run in parallel
    failures: list[str] = []
    scenarios = [
        ("worker killed mid-top-up", ["worker"], 400, 1, ORDERS_PER_SCENARIO, 1),
        ("worker killed three times in a row", ["worker"], 400, 3, ORDERS_PER_SCENARIO, 1),
        ("mock-game killed mid-top-up", ["mock-game"], 400, 1, ORDERS_PER_SCENARIO, 1),
        ("postgres killed mid-top-up", ["postgres"], 400, 1, ORDERS_PER_SCENARIO, 1),
        # Many orders and an early kill, so webhooks are genuinely in flight when api-1 dies.
        ("api-1 killed while webhooks arrive", ["api-1"], 100, 1, 200, 0.1),
        ("worker and mock-game killed together", ["worker", "mock-game"], 400, 1,
         ORDERS_PER_SCENARIO, 1),
    ]
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    scenarios = [scenario for scenario in scenarios if only in scenario[0]]
    for name, services, latency_ms, kills, order_count, kill_after in scenarios:
        result = run_scenario(name, services, latency_ms, kills, order_count, kill_after)
        with lock:
            failures.extend(result)

    print("\n=== summary")
    if failures:
        for failure in failures:
            print("FAIL", failure)
        return 1
    print(f"all {len(scenarios)} scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
