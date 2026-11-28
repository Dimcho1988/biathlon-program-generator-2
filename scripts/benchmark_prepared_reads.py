"""Replay synthetic prepared-page reads through real uvicorn/FastAPI.

No database, credentials or athlete data. Example:
  python scripts/benchmark_prepared_reads.py --repo . --counts 1,5,10,30,60
Requires psutil in the test environment. RSS is sampled every 10 ms; exceeding
the guard terminates the isolated server. This is not a cgroup or Render CPU
emulation. Use the same fixture/host for before/after comparisons.
"""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import threading
import time


def fixture():
    row = {"date": "2026-11-28", "method": "synthetic-intervals",
           "seconds": 900.25, "zones": {f"Z{i}": i * 30.5 for i in range(1, 6)},
           "repetitions": 4, "rest_seconds": 120,
           "evidence": {"source": "synthetic", "confidence": .8, "valid": True}}
    count = 930_000 // len(json.dumps(row).encode())
    return {"active": {"revision": 1, "plan": {"days": [row] * count},
                       "individual_learning": {"mode": "observe", "private_answers": ["redact"]}},
            "fixture_athlete": "synthetic"}


def serve(args):
    sys.path.insert(0, str(Path(args.repo).resolve()))
    from apps.api import dependencies, main, prepared_views
    import uvicorn
    from types import SimpleNamespace
    encoded = json.dumps(fixture()).encode()
    os.environ["ONFLOWS_SERVICE_TOKEN"] = "synthetic-load-test"

    def read(operation, **kwargs):
        assert operation == "READ"
        time.sleep(.15)  # Simulated network/storage wait; no scientific work.
        payload = json.loads(encoded)  # Fresh database-like objects per request.
        payload["fixture_athlete"] = kwargs["alias"]
        return {"ready": True, "athlete_alias": kwargs["alias"],
                "kind": kwargs["kind"], "payload": payload}

    dependencies.repository = lambda: SimpleNamespace(prepared_view=read)
    prepared_views.model_key()  # Exclude first fingerprint calculation in both versions.
    uvicorn.run(main.app, host="127.0.0.1", port=args.port, log_level="error", access_log=False)


def benchmark(args):
    import httpx
    import psutil
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [sys.executable, __file__, "--repo", args.repo, "--serve", "--port", str(port)]
    server = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    process = psutil.Process(server.pid)
    base = f"http://127.0.0.1:{port}"
    stop = threading.Event()
    peaks = []
    exceeded = []

    def monitor():
        while not stop.wait(.01):
            try:
                rss = process.memory_info().rss
                peaks.append(rss)
                if rss > args.rss_limit_mib * 1024**2:
                    exceeded.append(rss)
                    server.kill()
                    return
            except psutil.NoSuchProcess:
                return

    sampler = threading.Thread(target=monitor, daemon=True)
    results = []
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError(server.stderr.read().decode()[-2000:])
                try:
                    if client.get(base + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("Server startup timeout")
        baseline = process.memory_info().rss
        sampler.start()
        with httpx.Client(timeout=30, trust_env=False, limits=httpx.Limits(max_connections=100)) as client:
            expected = fixture()
            expected["active"]["individual_learning"] = {"mode": "observe"}
            expected["active"]["capabilities"] = {"automatic_publication": True}
            for count in map(int, args.counts.split(",")):
                start_sample = len(peaks)
                barrier = threading.Barrier(count)

                def request(i):
                    alias = f"ath-synthetic-{i}"
                    barrier.wait()
                    start = time.perf_counter()
                    try:
                        response = client.get(base + "/api/v2/athlete/management/view", headers={
                            "Authorization": "Bearer synthetic-load-test", "X-OnFlows-Athlete-Alias": alias})
                        elapsed = time.perf_counter() - start
                        if response.status_code != 200:
                            return {"status": response.status_code, "seconds": elapsed}
                        value = response.json()
                        wanted = {**expected, "fixture_athlete": alias}
                        assert value == wanted, "Payload parity/scope failed"
                        return {"status": 200, "seconds": elapsed, "bytes": len(response.content)}
                    except httpx.HTTPError as exc:
                        return {"status": type(exc).__name__, "seconds": time.perf_counter() - start}

                with ThreadPoolExecutor(max_workers=count) as pool:
                    futures = [pool.submit(request, i) for i in range(count)]
                    health = []
                    while not all(f.done() for f in futures):
                        started = time.perf_counter()
                        try:
                            r = client.get(base + "/health", timeout=2)
                            health.append((r.status_code, time.perf_counter() - started))
                        except httpx.HTTPError:
                            health.append((0, time.perf_counter() - started))
                        time.sleep(.05)
                    responses = [f.result() for f in futures]
                times = sorted(r["seconds"] for r in responses)
                results.append({"concurrent_distinct_synthetic_athletes": count,
                    "success": sum(r["status"] == 200 for r in responses),
                    "statuses": {str(s): sum(r["status"] == s for r in responses)
                                 for s in set(r["status"] for r in responses)},
                    "median_seconds": round(statistics.median(times), 3),
                    "max_seconds": round(max(times), 3),
                    "peak_rss_mib": round(max(peaks[start_sample:] or [baseline]) / 1024**2, 1),
                    "health_success": sum(s == 200 for s, _ in health), "health_samples": len(health),
                    "health_max_seconds": round(max((t for _, t in health), default=0), 3)})
                if exceeded or any(r["status"] != 200 for r in responses):
                    break
        return {"environment": "local uvicorn, synthetic fresh JSON per athlete; no Render CPU emulation",
                "synthetic_stored_bytes": len(json.dumps(fixture()).encode()),
                "baseline_rss_mib": round(baseline / 1024**2, 1),
                "rss_guard_mib": args.rss_limit_mib, "guard_exceeded": bool(exceeded), "results": results}
    finally:
        stop.set()
        if sampler.is_alive():
            sampler.join()
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--counts", default="1,5,10,30,60")
    parser.add_argument("--rss-limit-mib", type=int, default=512)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args()
    if arguments.serve:
        serve(arguments)
    else:
        print(json.dumps(benchmark(arguments), indent=2))
