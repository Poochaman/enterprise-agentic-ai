import io
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from examples import programme, evaluation
from examples.provider import Provider, ProviderError
from examples.server import handler


class ProgrammeTests(unittest.TestCase):
    def test_critical_supplier_delay_propagates(self):
        result = programme.analyse({"taskId": "power-equipment", "delayDays": 15})
        self.assertEqual(37, result["baseline"]["duration"])
        self.assertEqual(52, result["forecast"]["duration"])
        self.assertEqual(15, result["delayDays"])
        self.assertIn("handover", result["affectedTasks"])
        self.assertNotIn("network-install", result["affectedTasks"])

    def test_available_float_absorbs_noncritical_delay(self):
        result = programme.analyse({"taskId": "network-hardware", "delayDays": 2})
        self.assertEqual(0, result["delayDays"])
        self.assertIn("network-hardware", result["affectedTasks"])
        self.assertNotIn("handover", result["affectedTasks"])

    def test_baseline_is_not_mutated(self):
        original = programme.sample()
        programme.analyse({"delayDays": 25}, original)
        self.assertEqual(original, programme.sample())
        self.assertEqual(0, programme.analyse({"delayDays": 0})["delayDays"])

    def test_parallel_paths_and_float(self):
        result = programme.schedule([{"id":"a","name":"A","duration":5,"dependsOn":[]},
                                    {"id":"b","name":"B","duration":2,"dependsOn":[]},
                                    {"id":"c","name":"C","duration":1,"dependsOn":["a","b"]}])
        self.assertEqual(6, result["duration"])
        self.assertEqual([0,3,0], [t["float"] for t in result["tasks"]])

    def test_invalid_graphs_rejected(self):
        for tasks in ([{"id":"a","name":"A","duration":1,"dependsOn":["a"]}],
                      [{"id":"a","name":"A","duration":1,"dependsOn":["missing"]}],
                      [{"id":"a","name":"A","duration":True,"dependsOn":[]}],
                      [{"id":"a","name":"A","duration":1,"dependsOn":[]}]*2):
            with self.assertRaises(ValueError):
                programme.schedule(tasks)

    def test_input_boundaries(self):
        for body in ({"delayDays":-1},{"delayDays":True},{"delayDays":61},{"taskId":"nope"},{"tenant":"override"}):
            with self.assertRaises(ValueError):
                programme.analyse(body)

    def test_calendar_skips_weekends(self):
        self.assertEqual("2026-10-12", programme.workday("2026-10-09", 1))
        self.assertEqual("2026-10-09", programme.workday("2026-10-05", 4))

    def test_unknown_ai_citation_rejected(self):
        with self.assertRaises(ValueError):
            programme.validate_brief({"items":[{"text":"Unsupported claim","sources":["INVENTED"]}]},{"PLAN-01"})
        value={"items":[{"text":"Bounded claim","sources":["PLAN-01"]}]}
        self.assertEqual(value, programme.validate_brief(value,{"PLAN-01"}))


class EvaluationTests(unittest.TestCase):
    def test_known_confusion_and_f1(self):
        cases=[{"id":"1","expected":"sales"},{"id":"2","expected":"support"},{"id":"3","expected":"review"}]
        result=evaluation.score([{"id":"1","label":"sales"},{"id":"2","label":"sales"},{"id":"3","label":"review"}],cases)
        self.assertAlmostEqual(2/3,result["accuracy"])
        self.assertAlmostEqual(5/9,result["macroF1"])
        self.assertEqual(1,result["confusion"]["support"]["sales"])

    def test_baselines_expose_failures(self):
        runs=evaluation.baseline_runs()["runs"]
        self.assertEqual(2,len(runs))
        self.assertTrue(all(r["total"]==16 and r["providerCalls"]==0 for r in runs))
        self.assertTrue(all(r["correct"]<16 for r in runs))
        self.assertGreater(runs[0]["missedReview"],runs[1]["missedReview"])

    def test_missing_duplicate_and_unknown_predictions(self):
        predictions=evaluation.baseline_runs()["runs"][0]["predictions"]
        for bad in (predictions[:-1],predictions[:-1]+[predictions[0]],predictions[:-1]+[{"id":"x","label":"sales"}]):
            with self.assertRaises(ValueError):
                evaluation.score(bad)

    def test_dataset_hash_is_portable_across_formatting_and_line_endings(self):
        expected = evaluation.dataset_hash()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"cases.json"
            path.write_bytes(json.dumps(evaluation.dataset(), indent=4).replace("\n","\r\n").encode())
            with patch.object(evaluation,"DATA",path):
                self.assertEqual(expected,evaluation.dataset_hash())

    def test_import_hash_and_mode(self):
        body={"datasetHash":evaluation.dataset_hash(),"name":"External example","predictions":evaluation.baseline_runs()["runs"][0]["predictions"]}
        result=evaluation.imported_run(body)["runs"][0]
        self.assertEqual("imported",result["mode"])
        self.assertIsNone(result["latencyMs"])
        body["datasetHash"]="wrong"
        with self.assertRaises(ValueError):evaluation.imported_run(body)

    def test_live_payload_does_not_include_expected_answers(self):
        class FakeProvider:
            def request(inner,model,instruction,data,schema,name):
                self.assertTrue(all(set(c)=={"id","message"} for c in data["cases"]))
                return {"predictions":evaluation.baseline_runs()["runs"][0]["predictions"]},{"model":"test","mode":"live","latencyMs":1,"usage":{},"providerCalls":1}
        self.assertEqual(16,evaluation.live_run(FakeProvider(),"test")["runs"][0]["total"])


class ProviderTests(unittest.TestCase):
    def test_disabled_calls_never_contact_network(self):
        def fail(*args,**kwargs):self.fail("Unexpected network request")
        with self.assertRaises(ProviderError):Provider(opener=fail).request("gpt-4.1-mini","",{}, {},"test")

    def test_bounded_requests_and_structured_response(self):
        seen=[]
        def fake(req,timeout):
            seen.append(json.loads(req.data))
            return io.BytesIO(json.dumps({"status":"completed","id":"test","model":"gpt-4.1-mini","usage":{"input_tokens":2,"output_tokens":3},"output":[{"type":"message","content":[{"type":"output_text","text":"{\"items\": []}"}]}]}).encode())
        provider=Provider("unit-test-only",True,1,fake)
        value,meta=provider.request("gpt-4.1-mini","instruction",{}, {},"test")
        self.assertEqual([],value["items"])
        self.assertEqual(3,meta["usage"]["output_tokens"])
        self.assertFalse(seen[0]["store"])
        self.assertTrue(seen[0]["text"]["format"]["strict"])
        with self.assertRaises(ProviderError):provider.request("gpt-4.1-mini","",{}, {},"test")
        self.assertEqual(1,len(seen))

    def test_errors_do_not_disclose_response_or_key(self):
        def fail(req,timeout):raise HTTPError(req.full_url,401,"private server details",{},None)
        provider=Provider("unit-test-secret",True,1,fail)
        with self.assertRaises(ProviderError) as caught:provider.request("gpt-4.1-mini","",{}, {},"test")
        self.assertNotIn("unit-test-secret",str(caught.exception))
        self.assertNotIn("private server",str(caught.exception))
        self.assertEqual(0,provider.status()["remainingCalls"])


class BrowserApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),handler(cls.temp.name))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f"http://127.0.0.1:{cls.server.server_port}"
        cls.nonce=cls.call("/api/config")[1]["csrfToken"]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()

    @classmethod
    def call(cls,path,body=None,token="demo-acme",headers=None):
        h={"Authorization":"Bearer "+token,**(headers or {})}
        if body is not None:h.update({"Content-Type":"application/json","X-Lab-Token":getattr(cls,"nonce","")})
        req=Request(cls.base+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        try:
            with urlopen(req,timeout=10) as response:return response.status,json.loads(response.read())
        except HTTPError as error:return error.code,json.loads(error.read())

    def test_all_pages_and_static_allowlist(self):
        for page in ("agent-control-room","programme-intelligence","model-evaluation"):
            with urlopen(self.base+"/"+page+"/") as response:
                self.assertIn(b'AI Systems Lab',response.read())
                self.assertIn("frame-ancestors 'none'",response.headers["Content-Security-Policy"])
        for path in ("/.env.local","/shared/../../.env.local","/examples/provider.py"):
            self.assertEqual(404,self.call(path)[0])

    def test_export_is_attachment_and_single_use(self):
        status, result = self.call('/api/export', {"name":"test.json","data":{"checked":True}})
        self.assertEqual(200,status)
        with urlopen(self.base+result["url"]) as response:
            self.assertEqual('attachment; filename="test.json"',response.headers['Content-Disposition'])
            self.assertEqual({"checked":True},json.loads(response.read()))
        self.assertEqual(404,self.call(result["url"])[0])
        self.assertEqual(400,self.call('/api/export',{"name":"../../.env.local","data":{}})[0])

    def test_cross_origin_and_host_blocked(self):
        self.assertEqual(403,self.call("/api/config",headers={"Origin":"https://untrusted.example"})[0])
        self.assertEqual(403,self.call("/api/config",headers={"Host":"untrusted.example"})[0])

    def test_missing_session_token_rejected(self):
        req=Request(self.base+"/api/evaluation/run",data=b'{"mode":"baseline"}',headers={"Content-Type":"application/json"})
        with self.assertRaises(HTTPError) as caught:urlopen(req)
        self.assertEqual(403,caught.exception.code)

    def test_reference_api_and_fault_recovery(self):
        status,task=self.call('/v1/workflows',{"requestId":"recover","message":"CRM quote"})
        self.assertEqual(200,status)
        self.assertEqual(403,self.call('/v1/workflows/recover/execute',{})[0])
        decision={"decision":"approve","actionDigest":task["authority"]["actionDigest"]}
        self.assertEqual(403,self.call('/v1/workflows/recover/decision',decision)[0])
        self.assertEqual(200,self.call('/v1/workflows/recover/decision',decision,'demo-acme-approver')[0])
        self.assertEqual(202,self.call('/api/control/recover/simulate',{"fault":"after_write"})[0])
        self.assertEqual(1,self.call('/api/control')[1]['mockRecords'])
        self.assertEqual('completed',self.call('/v1/workflows/recover/execute',{})[1]['state'])
        self.call('/v1/workflows/recover/execute',{})
        self.assertEqual(1,self.call('/api/control')[1]['mockRecords'])
        self.assertEqual(0,self.call('/api/control',token='demo-beta')[1]['mockRecords'])
        self.assertEqual(404,self.call('/v1/workflows/recover',token='demo-beta')[0])

    def test_calculation_and_disabled_live_api(self):
        status,result=self.call('/api/programme/analyse',{"delayDays":15})
        self.assertEqual(200,status);self.assertEqual(15,result['delayDays'])
        self.assertEqual(400,self.call('/api/programme/analyse',{"delayDays":-1})[0])
        self.assertEqual(502,self.call('/api/programme/brief',{"scenario":{},"model":"gpt-4.1-mini"})[0])
        self.assertEqual(2,len(self.call('/api/evaluation/run',{"mode":"baseline"})[1]['runs']))
        self.assertEqual(502,self.call('/api/evaluation/run',{"mode":"live","model":"gpt-4.1-mini"})[0])


if __name__=='__main__':unittest.main()
