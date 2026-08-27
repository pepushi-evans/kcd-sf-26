"""checkout: a deliberately CPU-bound demo web service.

Each request to /checkout performs a fixed amount of PBKDF2 hashing
(~40-60ms of CPU on a modern core), simulating a service that does real
per-request work (signing, pricing, crypto). With a 150m CPU limit a
single replica sustains only a couple of requests per second, so a modest
concurrent load pins every replica at its limit — exactly the predictable
saturation the HPA (and the capacity agent watching it) needs to see.
"""
import hashlib
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "9898"))
# Fixed work per request: PBKDF2-HMAC-SHA256 iterations.
WORK_ITERATIONS = int(os.environ.get("WORK_ITERATIONS", "120000"))
HOSTNAME = os.environ.get("HOSTNAME", "checkout")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/healthz") or self.path.startswith("/readyz"):
            self._reply(200, {"status": "ok"})
            return
        start = time.monotonic()
        digest = hashlib.pbkdf2_hmac(
            "sha256", b"cart-item", b"kcd-sf-26", WORK_ITERATIONS
        )
        self._reply(
            200,
            {
                "service": "checkout",
                "pod": HOSTNAME,
                "receipt": digest.hex()[:16],
                "cpu_ms": round((time.monotonic() - start) * 1000, 1),
            },
        )

    def _reply(self, code, body):
        data = json.dumps(body).encode() + b"\n"
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass  # keep pod logs quiet under load


if __name__ == "__main__":
    print(f"checkout listening on :{PORT} (work={WORK_ITERATIONS} iters/request)")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
