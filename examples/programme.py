"""Deterministic finish-to-start scheduling; the model never calculates dates."""
import copy
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).parent / "programme-intelligence" / "programme.json"


def sample():
    return json.loads(DATA.read_text(encoding="utf-8"))


def workday(start, offset):
    current = date.fromisoformat(start)
    if current.weekday() >= 5:
        raise ValueError("Programme start must be a weekday")
    for _ in range(offset):
        current += timedelta(days=1)
        while current.weekday() >= 5:
            current += timedelta(days=1)
    return current.isoformat()


def schedule(tasks):
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 200:
        raise ValueError("Expected 1 to 200 tasks")
    by_id = {}
    for task in tasks:
        if not isinstance(task, dict) or not {"id", "name", "duration", "dependsOn"} <= set(task):
            raise ValueError("Invalid task")
        ident = task["id"]
        if not isinstance(ident, str) or not ident or ident in by_id:
            raise ValueError("Task IDs must be unique")
        if type(task["duration"]) is not int or not 0 <= task["duration"] <= 365:
            raise ValueError("Task durations must be integer working days, 0 to 365")
        if not isinstance(task["dependsOn"], list) or not all(isinstance(x, str) for x in task["dependsOn"]):
            raise ValueError("Invalid dependency list")
        if len(set(task["dependsOn"])) != len(task["dependsOn"]):
            raise ValueError("Duplicate dependency")
        by_id[ident] = dict(task)
    for task in tasks:
        if any(dep not in by_id for dep in task["dependsOn"]):
            raise ValueError("Unknown dependency")
    order, remaining = [], set(by_id)
    while remaining:
        ready = [t["id"] for t in tasks if t["id"] in remaining and not (set(t["dependsOn"]) & remaining)]
        if not ready:
            raise ValueError("Dependency cycle detected")
        for ident in ready:
            task = by_id[ident]
            task["start"] = max((by_id[d]["finish"] for d in task["dependsOn"]), default=0)
            task["finish"] = task["start"] + task["duration"]
            order.append(ident)
            remaining.remove(ident)
    finish = max(t["finish"] for t in by_id.values())
    for ident in reversed(order):
        task = by_id[ident]
        successors = [t for t in by_id.values() if ident in t["dependsOn"]]
        task["lateFinish"] = min((t["lateStart"] for t in successors), default=finish)
        task["lateStart"] = task["lateFinish"] - task["duration"]
        task["float"] = task["lateStart"] - task["start"]
        task["critical"] = task["float"] == 0
    return {"duration": finish, "tasks": [by_id[t["id"]] for t in tasks]}


def analyse(payload, programme=None):
    programme = copy.deepcopy(programme or sample())
    if not isinstance(payload, dict) or set(payload) - {"taskId", "delayDays"}:
        raise ValueError("Expected taskId and delayDays only")
    ident = payload.get("taskId", "power-equipment")
    delay = payload.get("delayDays", 15)
    if type(delay) is not int or not 0 <= delay <= 60:
        raise ValueError("Delay must be an integer from 0 to 60 working days")
    task = next((t for t in programme["tasks"] if t["id"] == ident), None)
    if task is None:
        raise ValueError("Unknown task")
    baseline = schedule(programme["tasks"])
    task["duration"] += delay
    scenario = schedule(programme["tasks"])
    affected = []
    for old, new in zip(baseline["tasks"], scenario["tasks"]):
        new["baselineStart"], new["baselineFinish"] = old["start"], old["finish"]
        new["finishDelta"] = new["finish"] - old["finish"]
        new["readyOn"] = workday(programme["startDate"], new["finish"])
        if new["finishDelta"]:
            affected.append(new["id"])
    baseline["readyOn"] = workday(programme["startDate"], baseline["duration"])
    scenario["readyOn"] = workday(programme["startDate"], scenario["duration"])
    delta = scenario["duration"] - baseline["duration"]
    sources = programme["sources"] + [{"id": "WHAT-IF", "title": "Selected scenario", "text": f"Hypothesis: extend {task['name']} by {delay} working days. This is a scenario input, not an observed supplier update."},
        {"id": "CALC", "title": "Calculated impact", "text": f"The finish-to-start calculation moves programme readiness from {baseline['readyOn']} to {scenario['readyOn']}: {delta} working days. {len(affected)} task finish boundaries move."}]
    brief = [
        {"text": f"Programme readiness moves by {delta} working days, to {scenario['readyOn']}.", "sources": ["CALC"]},
        {"text": f"{len(affected)} task finish boundaries change after extending {task['name']} by {delay} working days.", "sources": ["WHAT-IF", "CALC"]},
        {"text": "Review the affected dependencies with the named workstream owners before changing the approved delivery baseline.", "sources": ["GOV-01"]},
        {"text": "This model excludes holidays, resource contention, costs and probabilistic estimates. Scenario dates are not delivery commitments.", "sources": ["PLAN-01"]}]
    return {"programme": programme["name"], "startDate": programme["startDate"], "dataMode": "synthetic",
            "scenario": {"taskId": ident, "delayDays": delay}, "baseline": baseline, "forecast": scenario,
            "delayDays": delta, "affectedTasks": affected, "sources": sources, "brief": brief,
            "briefMode": "deterministic", "version": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]}


def validate_brief(value, source_ids):
    if not isinstance(value, dict) or set(value) != {"items"} or not isinstance(value["items"], list) or not 1 <= len(value["items"]) <= 6:
        raise ValueError("Model returned an invalid briefing")
    for item in value["items"]:
        if not isinstance(item, dict) or set(item) != {"text", "sources"}:
            raise ValueError("Invalid briefing item")
        if not isinstance(item["text"], str) or not 1 <= len(item["text"]) <= 1200:
            raise ValueError("Invalid briefing text")
        if not isinstance(item["sources"], list) or not item["sources"] or not all(isinstance(s, str) and s in source_ids for s in item["sources"]):
            raise ValueError("Model cited an unknown source")
    return value


BRIEF_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"items": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}}, "required": ["text", "sources"]}}}, "required": ["items"]}
