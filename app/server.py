"""Minimal HTTP endpoint: POST /graphql with {"query", "variables"}.

The X-Agent-Id header stands in for real authentication (a session or token) in this demo.
Run:  python -m app.server
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .resolvers import execute
from .seed import demo_database


def make_handler(db):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/graphql":
                return self._send(404, {"errors": [{"message": "not found"}]})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                agent_id = int(self.headers.get("X-Agent-Id", ""))
            except ValueError:
                return self._send(400, {"errors": [{"message": "JSON body and X-Agent-Id header are required"}]})
            data, errors = execute(db, agent_id, body.get("query", ""), body.get("variables"))
            self._send(200, {"data": data, **({"errors": errors} if errors else {})})

        def _send(self, code, payload):
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return Handler


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8000), make_handler(demo_database()))
    print("lead-desk-graphql on http://127.0.0.1:8000/graphql (synthetic data; send X-Agent-Id: 1, 2, or 3)")
    server.serve_forever()
