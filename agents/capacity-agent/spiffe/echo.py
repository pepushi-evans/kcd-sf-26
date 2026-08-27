"""Loopback echo upstream for the SPIFFE demo: prints request headers back,
so you can SEE the JWT-SVID that nono's supervisor injected — an identity
the sandboxed agent process never held."""
import base64
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        claims = None
        if auth.startswith("Bearer ") and auth.count(".") == 2:
            payload = auth.split(" ", 1)[1].split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
        body = json.dumps(
            {"authorization_header": auth[:60] + ("..." if len(auth) > 60 else ""),
             "jwt_svid_claims": claims},
            indent=2,
        ).encode() + b"\n"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("svid echo on 127.0.0.1:8099")
    HTTPServer(("127.0.0.1", 8099), H).serve_forever()
