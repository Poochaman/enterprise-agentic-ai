"""Credential-free reference workflow. All identities and integrations are mocks."""
import hashlib
import json
import sqlite3
from pathlib import Path

IDENTITIES = {
    "demo-acme": ("acme", "requester"),
    "demo-beta": ("beta", "requester"),
    "demo-acme-approver": ("acme", "approver"),
    "demo-beta-approver": ("beta", "approver"),
}
KNOWLEDGE = {
    "acme": "Acme supports CRM integration. Support hours: 09:00–17:00 UK.",
    "beta": "Beta supports ticket integration. Support hours: 24/7.",
}

class WorkflowError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def identity(token):
    if token not in IDENTITIES:
        raise WorkflowError(401, "invalid_identity")
    return IDENTITIES[token]


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != {"requestId", "message"}:
        raise WorkflowError(400, "expected_requestId_and_message_only")
    rid, message = payload["requestId"], payload["message"]
    if not isinstance(rid, str) or not 1 <= len(rid) <= 64 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in rid):
        raise WorkflowError(400, "invalid_request_id")
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000:
        raise WorkflowError(400, "invalid_message")
    return rid, message.strip()


def route(message):
    """Mock routing, not an LLM or a measure of real model quality."""
    words = message.lower()
    if any(term in words for term in ("broken", "support", "error", "help")):
        return "support"
    if any(term in words for term in ("buy", "price", "pricing", "quote", "crm", "sales")):
        return "sales"
    return "review"


class Engine:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "workflow.sqlite")
        self.crm = sqlite3.connect(directory / "mock-crm.sqlite")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS workflows (
            tenant TEXT, id TEXT, fingerprint TEXT, agent TEXT, state TEXT,
            action TEXT, result TEXT, attempts INTEGER DEFAULT 0,
            approved_by TEXT, PRIMARY KEY(tenant,id));
        CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT,
            id TEXT, event TEXT, detail TEXT);
        ''')
        self.crm.execute('''CREATE TABLE IF NOT EXISTS leads (
            tenant TEXT, operation TEXT, action TEXT, PRIMARY KEY(tenant,operation))''')
        self.crm.commit()

    def close(self):
        self.db.close()
        self.crm.close()

    def event(self, tenant, rid, name, detail=None):
        self.db.execute("INSERT INTO events(tenant,id,event,detail) VALUES(?,?,?,?)",
                        (tenant, rid, name, json.dumps(detail or {}, sort_keys=True)))

    def row(self, tenant, rid):
        row = self.db.execute("SELECT agent,state,action,result,attempts,approved_by FROM workflows WHERE tenant=? AND id=?", (tenant,rid)).fetchone()
        if row is None:
            raise WorkflowError(404, "workflow_not_found")
        return row

    def get(self, token, rid):
        tenant, _ = identity(token)
        agent, state, action, result, attempts, approver = self.row(tenant,rid)
        events = self.db.execute("SELECT event,detail FROM events WHERE tenant=? AND id=? ORDER BY sequence", (tenant,rid)).fetchall()
        return {"requestId": rid, "tenant": tenant, "agent": agent, "state": state,
                "action": json.loads(action) if action else None,
                "result": json.loads(result) if result else None,
                "attempts": attempts, "approvedBy": approver,
                "trace": [{"event": e, "detail": json.loads(d)} for e,d in events]}

    def submit(self, token, payload):
        tenant, role = identity(token)
        if role != "requester":
            raise WorkflowError(403, "requester_required")
        rid, message = validate(payload)
        fingerprint = hashlib.sha256(message.encode()).hexdigest()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute("SELECT fingerprint FROM workflows WHERE tenant=? AND id=?", (tenant,rid)).fetchone()
            if existing:
                if existing[0] != fingerprint:
                    raise WorkflowError(409, "request_id_reused_with_different_input")
            else:
                agent = route(message)
                state = {"sales": "awaiting_approval", "support": "completed", "review": "needs_review"}[agent]
                # Retrieved context and action scope come exclusively from authenticated identity.
                result = {"answer": KNOWLEDGE[tenant]} if agent in ("support", "sales") else None
                action = {"tool": "crm.create_lead", "tenant": tenant, "summary": message} if agent == "sales" else None
                self.db.execute("INSERT INTO workflows(tenant,id,fingerprint,agent,state,action,result) VALUES(?,?,?,?,?,?,?)",
                                (tenant,rid,fingerprint,agent,state,json.dumps(action) if action else None,json.dumps(result) if result else None))
                self.event(tenant,rid,"request_validated")
                self.event(tenant,rid,"routed",{"agent":agent,"router":"deterministic-mock-v1"})
                if agent != "review":
                    self.event(tenant,rid,"knowledge_retrieved",{"source":f"{tenant}/knowledge"})
                self.event(tenant,rid,state)
        return self.get(token,rid)

    def decide(self, token, rid, decision):
        tenant, role = identity(token)
        if role != "approver":
            raise WorkflowError(403, "approver_required")
        if decision not in ("approve", "reject"):
            raise WorkflowError(400, "invalid_decision")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            _, state, _, _, _, _ = self.row(tenant,rid)
            if state != "awaiting_approval":
                raise WorkflowError(409, "not_awaiting_approval")
            new_state = "approved" if decision == "approve" else "rejected"
            self.db.execute("UPDATE workflows SET state=?,approved_by=? WHERE tenant=? AND id=?", (new_state,role if decision == "approve" else None,tenant,rid))
            self.event(tenant,rid,new_state,{"actor":f"{tenant}:{role}"})
        return self.get(token,rid)

    def cancel(self, token, rid):
        tenant, _ = identity(token)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            _, state, _, _, _, _ = self.row(tenant,rid)
            if state not in ("awaiting_approval", "approved", "needs_review"):
                raise WorkflowError(409, "cannot_cancel_after_execution_started")
            self.db.execute("UPDATE workflows SET state='cancelled' WHERE tenant=? AND id=?", (tenant,rid))
            self.event(tenant,rid,"cancelled")
        return self.get(token,rid)

    def execute(self, token, rid, *, fault=None):
        """fault is a local test hook; never exposed by the HTTP API."""
        tenant, _ = identity(token)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            _, state, action_json, _, attempts, approver = self.row(tenant,rid)
            if state == "completed":
                return self.get(token,rid)
            if state not in ("approved", "executing", "retry_pending") or not approver:
                raise WorkflowError(403, "execution_not_authorised")
            action = json.loads(action_json)
            if action["tenant"] != tenant or action["tool"] != "crm.create_lead":
                raise WorkflowError(403, "action_outside_scope")
            # Read-after-timeout reconciliation precedes retry budgeting.
            existing = self.crm.execute("SELECT action FROM leads WHERE tenant=? AND operation=?", (tenant,rid)).fetchone()
            if existing:
                if existing[0] != action_json:
                    raise WorkflowError(409, "connector_idempotency_conflict")
                self._complete(tenant,rid,"reconciled")
                return self.get(token,rid)
            if attempts >= 3:
                self.db.execute("UPDATE workflows SET state='needs_review' WHERE tenant=? AND id=?",(tenant,rid))
                self.event(tenant,rid,"retry_budget_exhausted")
                return self.get(token,rid)
            self.db.execute("UPDATE workflows SET state='executing',attempts=attempts+1 WHERE tenant=? AND id=?",(tenant,rid))
            self.event(tenant,rid,"execution_authorised",{"tool":action["tool"],"attempt":attempts+1})
        # Independent connector transaction: no false claim of atomic cross-system writes.
        if fault == "before_write":
            with self.db:
                self.db.execute("UPDATE workflows SET state='retry_pending' WHERE tenant=? AND id=?",(tenant,rid))
                self.event(tenant,rid,"connector_timeout")
            raise TimeoutError("Simulated timeout before CRM write")
        with self.crm:
            self.crm.execute("INSERT OR IGNORE INTO leads VALUES(?,?,?)",(tenant,rid,action_json))
        if fault == "after_write":
            raise TimeoutError("CRM committed; simulated response loss before workflow checkpoint")
        with self.db:
            self._complete(tenant,rid,"connector_confirmed")
        return self.get(token,rid)

    def _complete(self, tenant, rid, evidence):
        self.db.execute("UPDATE workflows SET state='completed',result=? WHERE tenant=? AND id=?", (json.dumps({"leadId":rid}),tenant,rid))
        self.event(tenant,rid,evidence)
        self.event(tenant,rid,"completed")
