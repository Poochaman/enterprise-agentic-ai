"""Tests of externally meaningful authority and recovery boundaries."""
import json
import sqlite3
import tempfile
import unittest
from reference.workflow import Engine, WorkflowError


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.engine = Engine(self.tmp.name, clock=lambda: self.now)

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def submit(self, count=1):
        return self.engine.submit("demo-acme", {"requestId": "task", "message": "CRM quote", "leadCount": count})

    def approve(self, **options):
        current = self.engine.get("demo-acme", "task")
        return self.engine.decide("demo-acme-approver", "task", "approve",
                                  action_digest=current["authority"]["actionDigest"], **options)

    def blocked(self, code, operation):
        with self.assertRaises(WorkflowError) as error:
            operation()
        self.assertEqual(code, error.exception.code)

    def count(self):
        return self.engine.crm.execute("SELECT count(*) FROM leads").fetchone()[0]

    def test_expiry_blocks_write_at_exact_boundary_and_survives_restart(self):
        self.submit()
        self.approve(ttl_seconds=10)
        self.now = 1010.0
        self.engine.close()
        self.engine = Engine(self.tmp.name, clock=lambda: self.now)
        self.blocked("approval_expired", lambda: self.engine.execute("demo-acme", "task"))
        self.assertEqual(0, self.count())
        self.assertEqual("expired", self.engine.get("demo-acme", "task")["state"])
        self.approve(ttl_seconds=10)
        self.assertEqual("completed", self.engine.execute("demo-acme", "task")["state"])

    def test_expiry_is_rechecked_between_checkpoint_and_write(self):
        self.submit()
        self.approve(ttl_seconds=10)
        times = iter([1009.0, 1010.0])
        self.engine.clock = lambda: next(times)
        self.blocked("approval_expired", lambda: self.engine.execute("demo-acme", "task"))
        self.assertEqual(0, self.count())

    def test_revocation_blocks_write_and_cannot_be_renewed(self):
        self.submit()
        self.approve()
        self.engine.revoke("demo-acme-approver", "task")
        self.engine.revoke("demo-acme-approver", "task")  # Idempotent operator retry.
        self.blocked("approval_revoked", lambda: self.engine.execute("demo-acme", "task"))
        self.blocked("not_awaiting_approval", lambda: self.approve())
        self.assertEqual(0, self.count())

    def test_requester_and_other_tenant_cannot_revoke(self):
        self.submit()
        self.approve()
        self.blocked("approver_required", lambda: self.engine.revoke("demo-acme", "task"))
        self.blocked("workflow_not_found", lambda: self.engine.revoke("demo-beta-approver", "task"))
        self.assertEqual("completed", self.engine.execute("demo-acme", "task")["state"])

    def test_revision_invalidates_approval_and_rejects_stale_review(self):
        original = self.submit()
        self.approve()
        current = self.engine.revise("demo-acme", "task", {"message": "CRM quote revised", "revision": 1})
        self.assertIsNone(current["approvedBy"])
        self.assertEqual(2, current["revision"])
        self.blocked("execution_not_authorised", lambda: self.engine.execute("demo-acme", "task"))
        self.blocked("stale_action_digest", lambda: self.engine.decide("demo-acme-approver", "task", "approve", action_digest=original["authority"]["actionDigest"]))
        self.approve()
        self.engine.execute("demo-acme", "task")
        stored = self.engine.crm.execute("SELECT action FROM leads").fetchone()[0]
        self.assertEqual("CRM quote revised", json.loads(stored)["summary"])

    def test_stale_revision_and_cross_tenant_revision_blocked(self):
        self.submit()
        self.engine.revise("demo-acme", "task", {"message": "CRM two", "revision": 1})
        self.blocked("stale_revision", lambda: self.engine.revise("demo-acme", "task", {"message": "CRM three", "revision": 1}))
        self.blocked("workflow_not_found", lambda: self.engine.revise("demo-beta", "task", {"message": "CRM", "revision": 2}))

    def test_cannot_revise_after_uncertain_execution(self):
        self.submit()
        self.approve()
        with self.assertRaises(TimeoutError):
            self.engine.execute("demo-acme", "task", fault="after_write")
        self.blocked("cannot_revise_this_workflow", lambda: self.engine.revise("demo-acme", "task", {"message": "CRM changed", "revision": 1}))
        self.blocked("cannot_cancel_after_execution_started", lambda: self.engine.cancel("demo-acme", "task"))
        self.assertEqual(1, self.count())

    def test_action_budget_rejects_whole_batch_before_any_write(self):
        self.submit(count=2)
        self.approve(max_actions=1)
        self.blocked("action_limit_exceeded", lambda: self.engine.execute("demo-acme", "task"))
        self.assertEqual(0, self.count())
        self.approve(max_actions=2)
        result = self.engine.execute("demo-acme", "task")
        self.assertEqual(2, len(result["result"]["leadIds"]))
        self.engine.execute("demo-acme", "task")
        self.assertEqual(2, self.count())

    def test_policy_cap_cannot_be_raised_by_approver(self):
        self.submit(count=4)
        self.blocked("invalid_action_limit", lambda: self.approve(max_actions=4))
        self.approve(max_actions=3)
        self.blocked("action_limit_exceeded", lambda: self.engine.execute("demo-acme", "task"))
        self.assertEqual(0, self.count())

    def test_batch_recovery_after_expiry_or_revocation_is_read_only(self):
        for revoke in (False, True):
            with self.subTest(revoke=revoke):
                rid = "revoked" if revoke else "expired"
                current = self.engine.submit("demo-acme", {"requestId": rid, "message": "CRM", "leadCount": 3})
                self.engine.decide("demo-acme-approver", rid, "approve", action_digest=current["authority"]["actionDigest"], max_actions=3, ttl_seconds=1)
                with self.assertRaises(TimeoutError):
                    self.engine.execute("demo-acme", rid, fault="after_write")
                if revoke:
                    self.engine.revoke("demo-acme-approver", rid)
                else:
                    self.now += 1
                before = self.count()
                result = self.engine.execute("demo-acme", rid)
                self.assertEqual("completed", result["state"])
                self.assertEqual("revoked" if revoke else "expired", result["authority"]["status"])
                self.assertEqual(before, self.count())
                self.assertIn("reconciled", [event["event"] for event in result["trace"]])

    def test_retry_after_revocation_or_expiry_cannot_write(self):
        self.submit()
        self.approve(ttl_seconds=1)
        with self.assertRaises(TimeoutError):
            self.engine.execute("demo-acme", "task", fault="before_write")
        self.now += 1
        self.blocked("approval_expired", lambda: self.engine.execute("demo-acme", "task"))
        self.engine.revoke("demo-acme-approver", "task")
        self.blocked("approval_revoked", lambda: self.engine.execute("demo-acme", "task"))
        self.assertEqual(0, self.count())

    def test_modified_stored_action_fails_digest_check(self):
        self.submit()
        self.approve()
        action = self.engine.get("demo-acme", "task")["action"]
        action["summary"] = "Changed outside proposal API"
        with self.engine.db:
            self.engine.db.execute("UPDATE workflows SET action=? WHERE id='task'", (json.dumps(action),))
        self.blocked("action_outside_scope", lambda: self.engine.execute("demo-acme", "task"))
        self.assertEqual(0, self.count())

    def test_invalid_limits_and_missing_digest(self):
        self.submit()
        for ttl in (0, -1, 901, True, 1.5, "10"):
            self.blocked("invalid_approval_ttl", lambda: self.approve(ttl_seconds=ttl))
        for limit in (0, -1, 4, True, 1.5, "2"):
            self.blocked("invalid_action_limit", lambda: self.approve(max_actions=limit))
        self.blocked("action_digest_required", lambda: self.engine.decide("demo-acme-approver", "task", "approve"))
        for count in (0, 6, True, "2"):
            self.blocked("invalid_lead_count", lambda: self.submit(count=count))


class MigrationTests(unittest.TestCase):
    def test_original_databases_preserve_records_and_invalidate_old_approvals(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            db = sqlite3.connect(Path(directory) / "workflow.sqlite")
            db.execute('''CREATE TABLE workflows (tenant TEXT,id TEXT,fingerprint TEXT,agent TEXT,state TEXT,
                       action TEXT,result TEXT,attempts INTEGER DEFAULT 0,approved_by TEXT,PRIMARY KEY(tenant,id))''')
            action = json.dumps({"tool": "crm.create_lead", "tenant": "acme", "summary": "CRM"})
            db.execute("INSERT INTO workflows VALUES(?,?,?,?,?,?,?,?,?)", ("acme","pending","hash","sales","approved",action,None,0,"approver"))
            db.execute("INSERT INTO workflows VALUES(?,?,?,?,?,?,?,?,?)", ("acme","uncertain","hash","sales","executing",action,None,1,"approver"))
            db.commit()
            db.close()
            crm = sqlite3.connect(Path(directory) / "mock-crm.sqlite")
            crm.execute("CREATE TABLE leads (tenant TEXT,operation TEXT,action TEXT,PRIMARY KEY(tenant,operation))")
            crm.execute("INSERT INTO leads VALUES(?,?,?)", ("acme","uncertain",action))
            crm.commit()
            crm.close()
            engine = Engine(directory)
            try:
                self.assertEqual("awaiting_approval", engine.get("demo-acme","pending")["state"])
                with self.assertRaises(WorkflowError):
                    engine.execute("demo-acme","pending")
                self.assertEqual("completed", engine.execute("demo-acme","uncertain")["state"])
                self.assertEqual(1, engine.crm.execute("SELECT count(*) FROM leads").fetchone()[0])
            finally:
                engine.close()
