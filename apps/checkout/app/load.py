"""Concurrent load generator for the checkout service.

Runs N worker threads in closed-loop (each fires its next request as soon
as the previous one returns), which yields steady, predictable saturation:
with enough workers, every checkout replica sits pinned at its CPU limit
regardless of how many replicas the HPA adds — until maxReplicas rises
enough to absorb the demand.
"""
import os
import threading
import time
import urllib.request

TARGET = os.environ.get("TARGET", "http://checkout.checkout.svc:9898/checkout")
WORKERS = int(os.environ.get("WORKERS", "16"))
counts = {"ok": 0, "err": 0}
lock = threading.Lock()


def worker():
    while True:
        try:
            with urllib.request.urlopen(TARGET, timeout=30) as resp:
                resp.read()
            key = "ok"
        except Exception:
            key = "err"
            time.sleep(0.5)
        with lock:
            counts[key] += 1


for _ in range(WORKERS):
    threading.Thread(target=worker, daemon=True).start()

print(f"load: {WORKERS} workers -> {TARGET}")
while True:
    time.sleep(10)
    with lock:
        ok, err = counts["ok"], counts["err"]
        counts["ok"] = counts["err"] = 0
    print(f"last 10s: {ok} ok, {err} err ({ok/10:.1f} rps)", flush=True)
