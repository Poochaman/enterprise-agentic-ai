"""Scenario evaluations of mock control flow, not model-quality benchmarks."""
import argparse
import json
import platform
import tempfile
import time
from pathlib import Path
from .workflow import Engine, WorkflowError


def evaluate():
    cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8-sig"))
    results = []
    for case in cases:
        with tempfile.TemporaryDirectory() as directory:
            now = [1000.0]
            engine = Engine(directory, clock=lambda: now[0])
            started = time.perf_counter()
            observed_error = None
            denied_before_approval = True
            try:
                result = engine.submit("demo-acme", {"requestId": case["id"], "message": case["message"], "leadCount": case.get("leadCount", 1)})
                if result["state"] == "awaiting_approval":
                    try:
                        engine.execute("demo-acme", case["id"])
                        denied_before_approval = False
                    except WorkflowError as error:
                        denied_before_approval = error.status == 403
                if case.get("approve"):
                    engine.decide("demo-acme-approver", case["id"], "approve",
                                  action_digest=result["authority"]["actionDigest"],
                                  ttl_seconds=10, max_actions=case.get("maxActions", 1))
                    if case.get("expire"):
                        now[0] += 10
                    if case.get("revoke"):
                        engine.revoke("demo-acme-approver", case["id"])
                    if case.get("revise"):
                        engine.revise("demo-acme", case["id"], {"message": "Updated CRM proposal", "revision": 1})
                    try:
                        try:
                            result = engine.execute("demo-acme", case["id"], fault=case.get("fault"))
                        except TimeoutError:
                            if case.get("expireAfterFault"):
                                now[0] += 10
                            if case.get("revokeAfterFault"):
                                engine.revoke("demo-acme-approver", case["id"])
                            engine.close()
                            engine = Engine(directory, clock=lambda: now[0])
                            result = engine.execute("demo-acme", case["id"])
                        if result["state"] == "completed":
                            engine.execute("demo-acme", case["id"])
                    except WorkflowError as error:
                        observed_error = error.code
                        result = engine.get("demo-acme", case["id"])
                elif case.get("reject"):
                    result = engine.decide("demo-acme-approver", case["id"], "reject")
                elif case.get("cancel"):
                    result = engine.cancel("demo-acme", case["id"])
                count = engine.crm.execute("SELECT count(*) FROM leads").fetchone()[0]
                checks = {"routing": result["agent"] == case["agent"],
                          "state": result["state"] == case["state"],
                          "authority_enforced": denied_before_approval,
                          "expected_error": observed_error == case.get("error"),
                          "external_effects": count == case.get("crmRecords", int(bool(case.get("approve")))),
                          "trace_present": bool(result["trace"])}
                if "authorityStatus" in case:
                    checks["authority_status"] = result["authority"]["status"] == case["authorityStatus"]
                results.append({"id": case["id"], "passed": all(checks.values()), "checks": checks,
                                "latency_ms": round((time.perf_counter()-started)*1000, 3), "crm_records": count})
            finally:
                engine.close()
    return {"scope": "deterministic mock control-flow evaluation; injected clock; not an LLM benchmark",
            "python": platform.python_version(), "total": len(results),
            "passed": sum(r["passed"] for r in results), "model_calls": 0, "provider_cost_usd": 0,
            "cost_note": "Mock execution only; excludes infrastructure and engineering costs.", "cases": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    result = evaluate()
    output = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(output+"\n", encoding="utf-8")
    print(output)
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
