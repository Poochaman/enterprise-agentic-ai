"""Credential-free reference workflow. All identities and integrations are mocks."""
import hashlib
import json
import sqlite3
import time
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
DEFAULT_APPROVAL_SECONDS = 300
MAX_APPROVAL_SECONDS = 900
MAX_ACTIONS = 3
MAX_ATTEMPTS = 3


class WorkflowError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def identity(token):
    if not isinstance(token, str) or token not in IDENTITIES:
        raise WorkflowError(401, "invalid_identity")
    return IDENTITIES[token]


def integer(value, minimum, maximum, code):
    if type(value) is not int or not minimum <= value <= maximum:
        raise WorkflowError(400, code)
    return value


def validate(payload):
    if (not isinstance(payload, dict) or not {"requestId", "message"} <= set(payload)
            or set(payload) - {"requestId", "message", "leadCount"}):
        raise WorkflowError(400, "expected_requestId_message_optional_leadCount")
    rid, message = payload["requestId"], payload["message"]
    if (not isinstance(rid, str) or not 1 <= len(rid) <= 64
            or not all(c.isascii() and (c.isalnum() or c in "-_") for c in rid)):
        raise WorkflowError(400, "invalid_request_id")
    if not isinstance(message, str) or not message.strip() or len(message) > 2000:
        raise WorkflowError(400, "invalid_message")
    count = integer(payload.get("leadCount", 1), 1, 5, "invalid_lead_count")
    return rid, message.strip(), count


def route(message):
    """Mock routing, not an LLM or a measure of real model quality."""
    words = message.lower()
    if any(term in words for term in ("broken", "support", "error", "help")):
        return "support"
    if any(term in words for term in ("buy", "price", "pricing", "quote", "crm", "sales")):
        return "sales"
    return "review"


def digest(action):
    return hashlib.sha256(json.dumps(action, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Engine:
    def __init__(self, directory, *, clock=time.time):
        self.clock = clock  # Injectable only in Python; HTTP always uses wall time.
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "workflow.sqlite")
        self.db.row_factory = sqlite3.Row
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
        self._migrate()

    def _migrate(self):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in self.db.execute("PRAGMA table_info(workflows)")}
            additions = {"revision": "INTEGER NOT NULL DEFAULT 1", "action_digest": "TEXT",
                         "approved_digest": "TEXT", "approval_expires_at": "REAL",
                         "action_limit": "INTEGER", "revoked": "INTEGER NOT NULL DEFAULT 0"}
            for name, kind in additions.items():
                if name not in columns:
                    self.db.execute(f"ALTER TABLE workflows ADD COLUMN {name} {kind}")
            if "approved_digest" not in columns:
                for row in self.db.execute("SELECT * FROM workflows").fetchall():
                    if row["action"]:
                        self.db.execute("UPDATE workflows SET action_digest=? WHERE tenant=? AND id=?",
                                        (digest(json.loads(row["action"])), row["tenant"], row["id"]))
                    if row["state"] in ("approved", "executing", "retry_pending"):
                        state = "needs_review" if row["attempts"] else "awaiting_approval"
                        self.db.execute("UPDATE workflows SET state=?,approved_by=NULL WHERE tenant=? AND id=?",
                                        (state, row["tenant"], row["id"]))
                        self.event(row["tenant"], row["id"], "legacy_approval_invalidated")

    def close(self):
        self.db.close()
        self.crm.close()

    def event(self, tenant, rid, name, detail=None):
        self.db.execute("INSERT INTO events(tenant,id,event,detail) VALUES(?,?,?,?)",
                        (tenant, rid, name, json.dumps(detail or {}, sort_keys=True)))

    def row(self, tenant, rid):
        row = self.db.execute("SELECT * FROM workflows WHERE tenant=? AND id=?", (tenant, rid)).fetchone()
        if row is None:
            raise WorkflowError(404, "workflow_not_found")
        return row

    def get(self, token, rid):
        tenant, _ = identity(token)
        row = self.row(tenant, rid)
        status = "not_granted"
        if row["approved_digest"]:
            status = "valid"
            if row["revoked"]:
                status = "revoked"
            elif self.clock() >= row["approval_expires_at"]:
                status = "expired"
        events = self.db.execute("SELECT event,detail FROM events WHERE tenant=? AND id=? ORDER BY sequence", (tenant, rid)).fetchall()
        return {"requestId": rid, "tenant": tenant, "agent": row["agent"], "state": row["state"],
                "action": json.loads(row["action"]) if row["action"] else None,
                "result": json.loads(row["result"]) if row["result"] else None,
                "attempts": row["attempts"], "approvedBy": row["approved_by"], "revision": row["revision"],
                "authority": {"actionDigest": row["action_digest"], "approvedDigest": row["approved_digest"],
                              "expiresAt": row["approval_expires_at"], "maxActions": row["action_limit"],
                              "revoked": bool(row["revoked"]), "status": status},
                "trace": [{"event": e, "detail": json.loads(d)} for e, d in events]}

    def submit(self, token, payload):
        tenant, role = identity(token)
        if role != "requester":
            raise WorkflowError(403, "requester_required")
        rid, message, count = validate(payload)
        # Preserve original request fingerprints for default single-lead requests.
        fingerprint = hashlib.sha256(message.encode()).hexdigest() if count == 1 else digest([message, count])
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute("SELECT fingerprint FROM workflows WHERE tenant=? AND id=?", (tenant, rid)).fetchone()
            if existing:
                if existing[0] != fingerprint:
                    raise WorkflowError(409, "request_id_reused_with_different_input")
            else:
                agent = route(message)
                if agent != "sales" and count != 1:
                    raise WorkflowError(400, "lead_count_requires_sales")
                state = {"sales": "awaiting_approval", "support": "completed", "review": "needs_review"}[agent]
                result = {"answer": KNOWLEDGE[tenant]} if agent in ("support", "sales") else None
                action = {"tool": "crm.create_lead", "tenant": tenant, "summary": message, "leadCount": count} if agent == "sales" else None
                self.db.execute("INSERT INTO workflows(tenant,id,fingerprint,agent,state,action,result,action_digest) VALUES(?,?,?,?,?,?,?,?)",
                                (tenant, rid, fingerprint, agent, state, json.dumps(action) if action else None,
                                 json.dumps(result) if result else None, digest(action) if action else None))
                self.event(tenant, rid, "request_validated")
                self.event(tenant, rid, "routed", {"agent": agent, "router": "deterministic-mock-v1"})
                if agent != "review":
                    self.event(tenant, rid, "knowledge_retrieved", {"source": f"{tenant}/knowledge"})
                self.event(tenant, rid, state)
        return self.get(token, rid)

    def revise(self, token, rid, payload):
        tenant, role = identity(token)
        if role != "requester":
            raise WorkflowError(403, "requester_required")
        if (not isinstance(payload, dict) or not {"message", "revision"} <= set(payload)
                or set(payload) - {"message", "revision", "leadCount"}):
            raise WorkflowError(400, "expected_message_revision_optional_leadCount")
        version = integer(payload["revision"], 1, 2147483647, "invalid_revision")
        _, message, count = validate({"requestId": rid, "message": payload["message"], "leadCount": payload.get("leadCount", 1)})
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.row(tenant, rid)
            if row["revision"] != version:
                raise WorkflowError(409, "stale_revision")
            if row["agent"] != "sales" or row["attempts"] or row["state"] not in ("awaiting_approval", "approved", "expired", "budget_exceeded"):
                raise WorkflowError(409, "cannot_revise_this_workflow")
            action = {"tool": "crm.create_lead", "tenant": tenant, "summary": message, "leadCount": count}
            self.db.execute('''UPDATE workflows SET action=?,action_digest=?,revision=revision+1,
                state='awaiting_approval',approved_by=NULL,approved_digest=NULL,approval_expires_at=NULL,
                action_limit=NULL,revoked=0 WHERE tenant=? AND id=?''', (json.dumps(action), digest(action), tenant, rid))
            self.event(tenant, rid, "proposal_revised", {"revision": version + 1})
            self.event(tenant, rid, "approval_invalidated")
        return self.get(token, rid)

    def decide(self, token, rid, decision, *, action_digest=None,
               ttl_seconds=DEFAULT_APPROVAL_SECONDS, max_actions=1):
        tenant, role = identity(token)
        if role != "approver":
            raise WorkflowError(403, "approver_required")
        if decision not in ("approve", "reject"):
            raise WorkflowError(400, "invalid_decision")
        if decision == "approve":
            integer(ttl_seconds, 1, MAX_APPROVAL_SECONDS, "invalid_approval_ttl")
            integer(max_actions, 1, MAX_ACTIONS, "invalid_action_limit")
            if not isinstance(action_digest, str) or len(action_digest) != 64:
                raise WorkflowError(400, "action_digest_required")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.row(tenant, rid)
            if row["state"] not in ("awaiting_approval", "expired", "budget_exceeded") or row["attempts"]:
                raise WorkflowError(409, "not_awaiting_approval")
            if decision == "approve" and action_digest != row["action_digest"]:
                raise WorkflowError(409, "stale_action_digest")
            new_state = "approved" if decision == "approve" else "rejected"
            self.db.execute('''UPDATE workflows SET state=?,approved_by=?,approved_digest=?,
                approval_expires_at=?,action_limit=?,revoked=0 WHERE tenant=? AND id=?''',
                (new_state, role if decision == "approve" else None,
                 action_digest if decision == "approve" else None,
                 self.clock() + ttl_seconds if decision == "approve" else None,
                 max_actions if decision == "approve" else None, tenant, rid))
            self.event(tenant, rid, new_state, {"actor": f"{tenant}:{role}", "actionDigest": action_digest if decision == "approve" else None})
        return self.get(token, rid)

    def revoke(self, token, rid):
        tenant, role = identity(token)
        if role != "approver":
            raise WorkflowError(403, "approver_required")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.row(tenant, rid)
            if row["revoked"]:
                return self.get(token, rid)
            if not row["approved_digest"] or row["state"] in ("completed", "cancelled", "rejected"):
                raise WorkflowError(409, "no_active_approval_to_revoke")
            self.db.execute("UPDATE workflows SET revoked=1,state='revoked' WHERE tenant=? AND id=?", (tenant, rid))
            self.event(tenant, rid, "authority_revoked", {"actor": f"{tenant}:{role}"})
        return self.get(token, rid)

    def cancel(self, token, rid):
        tenant, _ = identity(token)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.row(tenant, rid)
            if row["attempts"] or row["state"] not in ("awaiting_approval", "approved", "needs_review", "expired", "budget_exceeded"):
                raise WorkflowError(409, "cannot_cancel_after_execution_started")
            self.db.execute("UPDATE workflows SET state='cancelled' WHERE tenant=? AND id=?", (tenant, rid))
            self.event(tenant, rid, "cancelled")
        return self.get(token, rid)

    def _authorise(self, row):
        if row["revoked"]:
            raise WorkflowError(403, "approval_revoked")
        if row["state"] not in ("approved", "executing", "retry_pending", "expired", "budget_exceeded") or not row["approved_digest"]:
            raise WorkflowError(403, "execution_not_authorised")
        action = json.loads(row["action"])
        if (row["approved_digest"] != digest(action) or row["approved_digest"] != row["action_digest"]
                or action["tenant"] != row["tenant"] or action["tool"] != "crm.create_lead"):
            raise WorkflowError(403, "action_outside_scope")
        if self.clock() >= row["approval_expires_at"]:
            raise WorkflowError(403, "approval_expired")
        if action.get("leadCount", 1) > row["action_limit"]:
            raise WorkflowError(403, "action_limit_exceeded")

    def _deny(self, tenant, rid, error):
        state = {"approval_expired": "expired", "approval_revoked": "revoked",
                 "action_limit_exceeded": "budget_exceeded"}.get(error.code)
        if state:
            self.db.execute("UPDATE workflows SET state=? WHERE tenant=? AND id=?", (state, tenant, rid))
        self.event(tenant, rid, "execution_blocked", {"reason": error.code})

    @staticmethod
    def _operations(row):
        count = json.loads(row["action"]).get("leadCount", 1)
        # Default single-lead IDs remain compatible with the first release.
        return [row["id"]] if count == 1 else [f'{row["id"]}:{i}' for i in range(1, count + 1)]

    def _reconcile(self, row):
        if not row["attempts"] or not row["action"]:
            return False
        operations = self._operations(row)
        records = [self.crm.execute("SELECT action FROM leads WHERE tenant=? AND operation=?", (row["tenant"], op)).fetchone() for op in operations]
        if any(record and record[0] != row["action"] for record in records):
            raise WorkflowError(409, "connector_idempotency_conflict")
        if all(records):
            self._complete(row, "reconciled")
            return True
        if any(records):
            raise WorkflowError(409, "partial_connector_result_requires_review")
        return False

    def execute(self, token, rid, *, fault=None):
        """Fault hooks are local-only. Reconciliation does not grant new authority."""
        tenant, _ = identity(token)
        blocked = None
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.row(tenant, rid)
            if row["state"] == "completed" or self._reconcile(row):
                return self.get(token, rid)
            try:
                self._authorise(row)
            except WorkflowError as error:
                self._deny(tenant, rid, error)
                blocked = error
            if blocked is None:
                if row["attempts"] >= MAX_ATTEMPTS:
                    self.db.execute("UPDATE workflows SET state='needs_review' WHERE tenant=? AND id=?", (tenant, rid))
                    self.event(tenant, rid, "retry_budget_exhausted")
                    return self.get(token, rid)
                self.db.execute("UPDATE workflows SET state='executing',attempts=attempts+1 WHERE tenant=? AND id=?", (tenant, rid))
                self.event(tenant, rid, "execution_authorised", {"tool": "crm.create_lead", "attempt": row["attempts"] + 1})
        if blocked:
            raise blocked
        # Durable checkpoint precedes the independently committed mock CRM batch.
        # A second authority check catches expiry/revocation between checkpoint and write.
        # Holding the local workflow lock orders revoke/revise against the connector call.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.row(tenant, rid)
            if row["state"] == "completed" or self._reconcile(row):
                return self.get(token, rid)
            try:
                self._authorise(row)
            except WorkflowError as error:
                self._deny(tenant, rid, error)
                blocked = error
            if blocked is None:
                if fault == "before_write":
                    self.db.execute("UPDATE workflows SET state='retry_pending' WHERE tenant=? AND id=?", (tenant, rid))
                    self.event(tenant, rid, "connector_timeout")
                else:
                    with self.crm:
                        self.crm.executemany("INSERT INTO leads VALUES(?,?,?)",
                                             [(tenant, op, row["action"]) for op in self._operations(row)])
                    if fault == "after_write":
                        raise TimeoutError("CRM committed; simulated response loss before workflow checkpoint")
                    self._complete(row, "connector_confirmed")
        if blocked:
            raise blocked
        if fault == "before_write":
            raise TimeoutError("Simulated timeout before CRM write")
        return self.get(token, rid)

    def _complete(self, row, evidence):
        operations = self._operations(row)
        result = {"leadId": operations[0]} if len(operations) == 1 else {"leadIds": operations}
        self.db.execute("UPDATE workflows SET state='completed',result=? WHERE tenant=? AND id=?",
                        (json.dumps(result), row["tenant"], row["id"]))
        self.event(row["tenant"], row["id"], evidence)
        self.event(row["tenant"], row["id"], "completed")
