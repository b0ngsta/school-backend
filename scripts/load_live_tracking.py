"""Load check for live bus tracking: N drivers' phones reporting at the normal rate.

    python -m scripts.load_live_tracking --base http://127.0.0.1:8000 --org LOADTEST \\
        --admin admin --password '...' [--drivers 150] [--duration 180] [--interval 50] [--parents 30]

USE A THROWAWAY SCHOOL (python -m app.cli create-org LOADTEST "Load test"): the script
creates `--drivers` driver accounts, one vehicle each, (with --parents) one class with
students riding the first vehicles, and SETS THE SCHOOL'S TIMINGS so that a tracking
window is open right now. Run it again and it reuses what it made (accounts load-driver-N).

Each simulated phone does what the real one does: signs in once, gets its upload-only key,
then sends a batch of fixes every `--interval` seconds (start times spread evenly). Parents
poll GET /student/transport/live every 9 s. Prints request counts, errors and latencies.
Needs httpx (requirements-dev.txt). Not on a Sunday or a holiday of that school (no window).
"""
from __future__ import annotations

import argparse
import random
import statistics
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx

PASSWORD = "load-test-pass-1"
results: dict[str, list[float]] = {"driver": [], "parent": []}
errors: dict[str, dict[str, int]] = {"driver": {}, "parent": {}}
watched_answers = 0
lock = threading.Lock()


def record(kind: str, started: float, response: httpx.Response | None, exc: Exception | None = None) -> None:
    with lock:
        results[kind].append(time.perf_counter() - started)
        if exc is not None or response is None or response.status_code != 200:
            key = type(exc).__name__ if exc else str(response.status_code if response is not None else "none")
            errors[kind][key] = errors[kind].get(key, 0) + 1


def ok(response: httpx.Response, *codes: int) -> dict | list | None:
    if response.status_code not in (codes or (200, 201)):
        raise SystemExit(f"{response.request.method} {response.request.url}: {response.status_code} {response.text}")
    return response.json() if response.content else None


def login(client: httpx.Client, org: str, username: str, password: str) -> dict:
    return ok(client.post("/auth/login", json={"org_code": org, "username": username, "password": password}))


def open_window_now(client: httpx.Client, headers: dict) -> None:
    """School day "starts" in 30 minutes and tracking begins 4 hours before: a window is open now."""
    local = datetime.now(UTC) + timedelta(hours=5, minutes=30)  # Asia/Kolkata, the API's default school time
    start = local + timedelta(minutes=30)
    if start.date() != local.date():
        raise SystemExit("Too close to midnight in school time for a window; try again later.")
    ok(client.put("/settings/school-timings?force=true", headers=headers, json={
        "start_time": f"{start:%H:%M}", "period_minutes": 10, "periods_per_day": 1,
        "tracking_before_minutes": 240, "tracking_after_minutes": 0}))


def setup(client: httpx.Client, args) -> tuple[list[str], list[str]]:
    admin = login(client, args.org, args.admin, args.password)
    h = {"Authorization": f"Bearer {admin['access_token']}"}
    open_window_now(client, h)
    users = {u["username"]: u["id"] for path in ("/users", "/students") for u in ok(client.get(path, headers=h))}
    vehicles = {v["vehicle_no"]: v["id"] for v in ok(client.get("/vehicles", headers=h))}
    driver_tokens, vehicle_ids = [], []
    for n in range(args.drivers):
        name = f"load-driver-{n}"
        if name not in users:
            users[name] = ok(client.post("/users", headers=h, json={
                "username": name, "password": PASSWORD, "full_name": f"Load Driver {n}", "user_type": "driver"}))["id"]
        plate = f"LOAD {n:04d}"
        if plate not in vehicles:
            vehicles[plate] = ok(client.post("/vehicles", headers=h, json={
                "vehicle_no": plate, "driver_name": f"Load Driver {n}", "driver_id": users[name]}))["id"]
        vehicle_ids.append(vehicles[plate])
        session = login(client, args.org, name, PASSWORD)
        token = ok(client.post("/driver/tracking/token",
                               headers={"Authorization": f"Bearer {session['access_token']}"}))["token"]
        driver_tokens.append(token)
        if n % 25 == 24:
            print(f"  {n + 1} drivers ready")
    parent_tokens = []
    if args.parents:
        classes = {c["name"]: c for c in ok(client.get("/classes", headers=h))}
        cls = classes.get("Load Class") or ok(client.post("/classes", headers=h, json={"name": "Load Class"}))
        section = ok(client.post(f"/classes/{cls['id']}/sections", headers=h, json={"name": "L"}), 200, 201, 409)
        section_id = section["id"] if section and "id" in section else None
        for n in range(args.parents):
            name = f"load-student-{n}"
            if name not in users and section_id:
                users[name] = ok(client.post("/students", headers=h, json={
                    "username": name, "password": PASSWORD, "full_name": f"Load Student {n}", "roll_no": str(n + 1),
                    "admission_no": f"LOAD{n}", "class_id": cls["id"], "section_id": section_id}))["id"]
            if name in users:
                ok(client.put(f"/vehicles/{vehicle_ids[n % len(vehicle_ids)]}/students/{users[name]}", headers=h,
                              json={"stop_name": "Load Stop"}))
                parent_tokens.append(login(client, args.org, name, PASSWORD)["access_token"])
    return driver_tokens, parent_tokens


def drive(base: str, token: str, offset: float, args, stop_at: float) -> None:
    global watched_answers
    lat, lng = 26.9 + random.random() / 10, 75.7 + random.random() / 10
    with httpx.Client(base_url=base, timeout=20, headers={"Authorization": f"Bearer {token}"}) as client:
        time.sleep(offset)
        while time.time() < stop_at:
            now = datetime.now(UTC)
            points = []
            for back in range(5, -1, -1):  # the six fixes taken since the last upload
                lat += random.uniform(-0.0003, 0.0003)
                lng += random.uniform(-0.0003, 0.0003)
                points.append({"latitude": lat, "longitude": lng, "speed": 7.0, "heading": 90.0, "accuracy": 8.0,
                               "recorded_at": (now - timedelta(seconds=9 * back)).isoformat()})
            started = time.perf_counter()
            wait = args.interval
            try:
                r = client.post("/driver/location", json={"points": points})
                record("driver", started, r)
                if r.status_code == 200:
                    body = r.json()
                    if body["watched"]:
                        with lock:
                            watched_answers += 1
                        wait = min(wait, body["interval_sec"])  # watched: the phone reports faster
            except httpx.HTTPError as exc:
                record("driver", started, None, exc)
            time.sleep(wait)


def watch(base: str, token: str, offset: float, stop_at: float) -> None:
    with httpx.Client(base_url=base, timeout=20, headers={"Authorization": f"Bearer {token}"}) as client:
        time.sleep(offset)
        while time.time() < stop_at:
            started = time.perf_counter()
            try:
                record("parent", started, client.get("/student/transport/live"))
            except httpx.HTTPError as exc:
                record("parent", started, None, exc)
            time.sleep(9)


def summary(kind: str, seconds: float) -> None:
    times = sorted(results[kind])
    if not times:
        return
    failed = sum(errors[kind].values())

    def pct(p: float) -> float:
        return times[min(len(times) - 1, int(len(times) * p))] * 1000

    print(f"{kind:>7}: {len(times)} requests ({len(times) / seconds:.1f}/s), {failed} errors {errors[kind] or ''} | "
          f"ms: median {statistics.median(times) * 1000:.0f}, p95 {pct(0.95):.0f}, p99 {pct(0.99):.0f}, "
          f"max {times[-1] * 1000:.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--org", required=True)
    parser.add_argument("--admin", default="admin")
    parser.add_argument("--password", required=True)
    parser.add_argument("--drivers", type=int, default=150)
    parser.add_argument("--parents", type=int, default=0)
    parser.add_argument("--duration", type=int, default=180, help="seconds")
    parser.add_argument("--interval", type=float, default=50, help="seconds between a phone's uploads")
    args = parser.parse_args()

    print(f"Setting up {args.drivers} drivers" + (f" and {args.parents} parents" if args.parents else "") + "…")
    with httpx.Client(base_url=args.base, timeout=60) as client:
        drivers, parents = setup(client, args)
    stop_at = time.time() + args.duration
    threads = [threading.Thread(target=drive, args=(args.base, t, i * args.interval / len(drivers), args, stop_at),
                                daemon=True) for i, t in enumerate(drivers)]
    threads += [threading.Thread(target=watch, args=(args.base, t, i * 9 / len(parents), stop_at), daemon=True)
                for i, t in enumerate(parents)]
    print(f"Running for {args.duration} s…")
    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(args.duration + 90)
    elapsed = time.time() - started
    summary("driver", elapsed)
    summary("parent", elapsed)
    if parents:
        print(f"uploads answered 'watched' (fast rate): {watched_answers}")


if __name__ == "__main__":
    main()
