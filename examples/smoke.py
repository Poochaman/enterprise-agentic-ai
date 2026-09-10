"""Run a fresh no-key HTTP check of the examples. No browser dependency."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent.parent


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="lab-smoke-") as data:
        flags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
        process=subprocess.Popen([sys.executable,str(ROOT/"examples/run.py"),"--port",str(port),"--data",data],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
        base=f"http://127.0.0.1:{port}"
        nonce=""
        def call(path,body=None,token="demo-acme",expected=200):
            headers={"Authorization":"Bearer "+token}
            if body is not None:headers.update({"Content-Type":"application/json","X-Lab-Token":nonce})
            req=Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            try:
                with urlopen(req,timeout=5) as response:
                    status=response.status;raw=response.read();ctype=response.headers.get_content_type()
            except HTTPError as error:
                status=error.code;raw=error.read();ctype=error.headers.get_content_type()
            assert status==expected,f"{path}: expected HTTP {expected}, received {status}"
            return json.loads(raw) if ctype=="application/json" else raw
        try:
            deadline=time.monotonic()+15
            while True:
                try:config=call('/api/config');break
                except (URLError,ConnectionError):
                    if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Example server did not start')
                    time.sleep(.1)
            nonce=config['csrfToken'];assert not config['live']['enabled']
            for app in ('agent-control-room','programme-intelligence','model-evaluation'):
                assert b'AI Systems Lab' in call('/'+app+'/')
                assert b'export async function start' in call('/'+app+'/app.js')
            print('PASS: all three pages and their scripts load; live AI disabled')
            task=call('/v1/workflows',{'requestId':'smoke','message':'CRM quote'})
            call('/v1/workflows/smoke/execute',{},expected=403)
            approval={'decision':'approve','actionDigest':task['authority']['actionDigest']}
            call('/v1/workflows/smoke/decision',approval,expected=403)
            call('/v1/workflows/smoke/decision',approval,'demo-acme-approver')
            print('PASS: unapproved execution and requester self-approval blocked')
            call('/api/control/smoke/simulate',{'fault':'after_write'},expected=202)
            assert call('/api/control')['mockRecords']==1
            assert call('/v1/workflows/smoke/execute',{})['state']=='completed'
            call('/v1/workflows/smoke/execute',{})
            assert call('/api/control')['mockRecords']==1
            print('PASS: lost confirmation recovers; replay leaves exactly one mock record')
            assert call('/api/control',token='demo-beta')['workflows']==[]
            call('/v1/workflows/smoke',token='demo-beta',expected=404)
            print('PASS: tenant register and workflow reads remain isolated')
            impact=call('/api/programme/analyse',{'taskId':'power-equipment','delayDays':15})
            assert impact['baseline']['duration']==37 and impact['forecast']['duration']==52
            assert call('/api/programme/analyse',{'delayDays':0})['delayDays']==0
            call('/api/programme/analyse',{'delayDays':-1},expected=400)
            print('PASS: schedule propagation, unchanged baseline and invalid-input rejection')
            report=call('/api/evaluation/run',{'mode':'baseline'})
            assert [r['correct'] for r in report['runs']]==[9,12]
            assert all(r['providerCalls']==0 for r in report['runs'])
            imported=call('/api/evaluation/import',{'datasetHash':report['datasetHash'],'name':'Smoke import','predictions':report['runs'][0]['predictions']})
            assert imported['runs'][0]['mode']=='imported'
            call('/api/evaluation/run',{'mode':'live','model':'gpt-4.1-mini'},expected=502)
            print('PASS: measured baselines, checked imports and explicit live-mode gate')
            export=call('/api/export',{'name':'report.json','data':report})
            assert call(export['url'])==report
            call('/.env.local',expected=404)
            print('PASS: JSON export matches the report; secret file is not served')
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    print('Examples HTTP smoke check passed (7 groups). Temporary server and data cleaned up.')


if __name__=='__main__':main()
