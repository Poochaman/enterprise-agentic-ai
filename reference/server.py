"""Loopback-only mock HTTP adapter. Demo tokens are public fixtures, not security."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit
from .workflow import Engine, WorkflowError


def handler(data):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Avoid logging headers or request content.

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def dispatch(self, method):
            engine = Engine(data)
            try:
                authorization = self.headers.get("Authorization", "")
                token = authorization[7:] if authorization.startswith("Bearer ") else ""
                path = urlsplit(self.path).path.strip("/").split("/")
                payload = None
                if method == "POST":
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 12000:
                        raise WorkflowError(400, "invalid_content_length")
                    if self.headers.get_content_type() != "application/json":
                        raise WorkflowError(415, "json_required")
                    payload = json.loads(self.rfile.read(length))
                if path == ["v1", "workflows"] and method == "POST":
                    result = engine.submit(token,payload)
                elif len(path) == 3 and path[:2] == ["v1", "workflows"] and method == "GET":
                    result = engine.get(token,path[2])
                elif len(path) == 4 and path[:2] == ["v1", "workflows"] and method == "POST":
                    rid, operation = path[2:]
                    if operation == "decision":
                        if (not isinstance(payload, dict) or "decision" not in payload
                                or set(payload) - {"decision", "actionDigest", "ttlSeconds", "maxActions"}):
                            raise WorkflowError(400, "invalid_decision_payload")
                        if payload["decision"] == "reject" and set(payload) != {"decision"}:
                            raise WorkflowError(400, "reject_accepts_decision_only")
                        result = engine.decide(token, rid, payload["decision"],
                                               action_digest=payload.get("actionDigest"),
                                               ttl_seconds=payload.get("ttlSeconds", 300),
                                               max_actions=payload.get("maxActions", 1))
                    elif operation == "revise":
                        result = engine.revise(token, rid, payload)
                    elif operation in ("execute", "cancel", "revoke"):
                        if payload != {}:
                            raise WorkflowError(400,"expected_empty_object")
                        result = getattr(engine,operation)(token,rid)
                    else:
                        raise WorkflowError(404,"route_not_found")
                else:
                    raise WorkflowError(404,"route_not_found")
                self.reply(200,result)
            except WorkflowError as error:
                self.reply(error.status,{"error":error.code})
            except (ValueError, UnicodeDecodeError):
                self.reply(400,{"error":"invalid_json_or_length"})
            except Exception:
                self.reply(500,{"error":"internal_error"})
            finally:
                engine.close()

        def reply(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data",required=True)
    parser.add_argument("--port",type=int,default=8765)
    args = parser.parse_args()
    with HTTPServer(("127.0.0.1",args.port),handler(args.data)) as server:
        print(f"Mock API: http://127.0.0.1:{server.server_port}",flush=True)
        server.serve_forever()

if __name__ == "__main__":
    main()
