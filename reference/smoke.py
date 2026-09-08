"""Exercise the actual CLI server over HTTP with fresh data; no credentials needed."""
import json
from contextlib import closing
import os
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener


def run():
    passed = []
    with tempfile.TemporaryDirectory(prefix="governed-workflow-") as directory:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen([sys.executable, "-u", "-m", "reference.server", "--data", directory, "--port", "0"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   creationflags=flags)
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            line = lines.get(timeout=15).strip()
            if not line.startswith("Mock API: http://127.0.0.1:"):
                raise RuntimeError(f"Server did not start: {line}")
            base = line.removeprefix("Mock API: ") + "/v1/workflows"
            opener = build_opener(ProxyHandler({}))

            def call(path="", body=None, token="demo-acme", expected=200):
                request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
                try:
                    response = opener.open(request, timeout=5)
                except HTTPError as error:
                    response = error
                with response:
                    status, payload = response.status, json.load(response)
                if status != expected:
                    raise AssertionError(f"{path}: expected {expected}, got {status}: {payload}")
                return payload

            def submit(rid, count=1):
                return call(body={"requestId": rid, "message": "Please quote a CRM integration", "leadCount": count})

            def approve(rid, current=None, **options):
                current = current or call("/" + rid)
                return call(f"/{rid}/decision", {"decision": "approve", "actionDigest": current["authority"]["actionDigest"], **options}, "demo-acme-approver")

            submit("success")
            call("/success/execute", {}, expected=403)
            approve("success")
            assert call("/success/execute", {})["state"] == "completed"
            call("/success/execute", {})
            passed.append("approved execution and replay")

            submit("expiry")
            approval = approve("expiry", ttlSeconds=1)
            while time.time() < approval["authority"]["expiresAt"]:
                time.sleep(0.02)
            assert call("/expiry/execute", {}, expected=403)["error"] == "approval_expired"
            passed.append("real-clock approval expiry")

            submit("revoked")
            approve("revoked")
            call("/revoked/revoke", {}, expected=403)
            call("/revoked/revoke", {}, "demo-beta-approver", expected=404)
            call("/revoked/revoke", {}, "demo-acme-approver")
            assert call("/revoked/execute", {}, expected=403)["error"] == "approval_revoked"
            passed.append("operator revocation and role isolation")

            original = submit("revision")
            approve("revision", original)
            revised = call("/revision/revise", {"message": "A revised CRM quote", "revision": original["revision"]})
            call("/revision/execute", {}, expected=403)
            call("/revision/decision", {"decision": "approve", "actionDigest": original["authority"]["actionDigest"]}, "demo-acme-approver", expected=409)
            approve("revision", revised)
            assert call("/revision/execute", {})["state"] == "completed"
            passed.append("revised action needs fresh approval")

            submit("budget", count=2)
            approve("budget", maxActions=1)
            assert call("/budget/execute", {}, expected=403)["error"] == "action_limit_exceeded"
            approve("budget", maxActions=2)
            assert len(call("/budget/execute", {})["result"]["leadIds"]) == 2
            call("/budget/execute", {})
            passed.append("batch limit and replay")

            call("/success", token="demo-beta", expected=404)
            call("/success", token="invalid", expected=401)
            call(body={"requestId": "bad", "message": "CRM", "tools": {"all": True}}, expected=400)
            call("/success/execute", {"fault": "after_write"}, expected=400)
            passed.append("tenant isolation and client override rejection")

            # Inspect the independent connector, including denied-operation absence.
            with closing(sqlite3.connect(Path(directory) / "mock-crm.sqlite")) as crm:
                ids = {r[0] for r in crm.execute("SELECT operation FROM leads")}
            assert ids == {"success", "revision", "budget:1", "budget:2"}, ids
            passed.append("exactly four expected mock CRM records; no denied writes")
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
            reader.join(timeout=1)
    return passed


def main():
    results = run()
    for result in results:
        print(f"PASS: {result}")
    print(f"HTTP smoke check passed ({len(results)} checks). Temporary server and data cleaned up.")


if __name__ == "__main__":
    main()
