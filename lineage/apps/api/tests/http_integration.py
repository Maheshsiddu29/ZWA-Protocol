#!/usr/bin/env python3
"""Start the real API on localhost and exercise its public HTTP boundary."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
BINARY = ROOT / 'target/debug/zwa-lineage-api'
FIX = ROOT / 'tests/fixtures/phase2'
OUT = ROOT / 'evidence/phase-3/examples'
N3 = (FIX / 'n3-v3.bin').read_bytes()
CKN = (FIX / 'ckn-v3.bin').read_bytes()
passed = 0
measure = {}


def check(name, condition):
    global passed
    assert condition, name
    passed += 1


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, view='A7', demo=False):
        self.port = port()
        env = dict(os.environ, ZWA_CHAIN_VIEW=view, ZWA_DEMO_ENABLED=str(demo).lower(),
                   ZWA_BIND=f'127.0.0.1:{self.port}', ZWA_LOG_LEVEL='off')
        self.log = open('/tmp/zwa-phase3-http-server.log', 'ab')
        self.start = time.perf_counter()
        self.proc = subprocess.Popen([str(BINARY)], cwd=ROOT, env=env,
                                     stdout=self.log, stderr=self.log)
        for _ in range(300):
            if self.proc.poll() is not None:
                raise RuntimeError(f'API startup failed: {self.proc.returncode}')
            try:
                self.request('/health/live')
                self.startup_ms = (time.perf_counter() - self.start) * 1000
                break
            except urllib.error.URLError:
                time.sleep(.1)
        else:
            raise RuntimeError('API startup timeout')

    def request(self, path, obj=None, raw=None, content_type='application/json'):
        data = None if obj is None and raw is None else (raw if raw is not None else json.dumps(obj).encode())
        headers = {} if data is None else {'content-type': content_type}
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', data=data, headers=headers)
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = resp.read()
                return resp.status, json.loads(body), (time.perf_counter()-start)*1000
        except urllib.error.HTTPError as err:
            body = err.read()
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = {'raw': body.decode(errors='replace')}
            return err.code, parsed, (time.perf_counter()-start)*1000

    def close(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)
        self.log.close()


def envelope(data):
    return {'evidenceEncoding': 'base64', 'evidence': base64.b64encode(data).decode()}


def status(response):
    return response['verification']['status']


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'{name}.json').write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')


def main():
    s = Server(demo=True)
    try:
        measure['startupMs'] = s.startup_ms
        code, live, _ = s.request('/health/live'); check('live', code==200 and live['status']=='LIVE')
        code, ready, _ = s.request('/health/ready'); check('ready', code==200 and ready['status']=='READY')
        code, info, _ = s.request('/v1/verifier/info'); check('info', code==200 and info['relation']=='NormalLineageStepV2' and info['finality']['k']==3)
        code, valid, first = s.request('/v1/lineage/verify', envelope(N3)); check('A7 verify', code==200 and status(valid)=='VERIFIED' and valid['lineagePermission']=='ALLOWED')
        save('a7-verify',valid); measure['firstA7Ms']=round(first,3)
        measure['jsonBase64DecodeUs']=valid['transportDecodeUs']
        measure['firstHandlerUs']=valid['totalHandlerUs']
        samples=[]
        for _ in range(5):
            _, value, ms=s.request('/v1/lineage/verify',envelope(N3)); check('warmed A7',status(value)=='VERIFIED');samples.append(ms)
        measure['warmedA7MsMean']=round(sum(samples)/len(samples),3)
        with ThreadPoolExecutor(max_workers=8) as pool:
            concurrent=list(pool.map(lambda _: s.request('/v1/lineage/verify',envelope(N3)),range(8)))
        check('concurrent requests',all(code==200 and status(value)=='VERIFIED' for code,value,_ in concurrent))
        measure['concurrent8MaxMs']=round(max(ms for _,_,ms in concurrent),3)
        measure['concurrent8MeanMs']=round(sum(ms for _,_,ms in concurrent)/8,3)
        code, auth, _=s.request('/v1/trade/authorize',envelope(N3)); check('authorize A7',code==200 and auth['lineagePermission']=='ALLOWED' and auth['eligibility']=='NOT_EVALUATED' and auth['settlement']=='NOT_EXECUTED')
        save('a7-authorize',auth)
        sc_ids=['valid-rooted-n3','pending-a4','checkpoint-ckn','tampered-proof','bad-root','bad-terminal','reorg']
        for ident in sc_ids:
            code, demo, ms=s.request('/v1/demo/run',{'scenarioId':ident})
            check(f'demo {ident}',code==200 and demo['matchesExpected'] and demo['observed']==demo['expected'])
            check(f'gate {ident}',demo['result']['lineagePermission']==('ALLOWED' if ident=='valid-rooted-n3' else 'DENIED') and demo['result']['settlement']=='NOT_EXECUTED')
            save(f'demo-{ident}',demo)
            if ident in ('pending-a4','checkpoint-ckn'): measure[ident+'Ms']=round(ms,3)
        code, rows, _=s.request('/v1/demo/scenarios');check('list scenarios',code==200 and len(rows['scenarios'])==7)
        bad=bytearray(N3);bad[2527+100]^=1
        code, result, _=s.request('/v1/lineage/verify',envelope(bad));check('tampered proof',code==200 and status(result)=='REJECTED' and result['verification']['code']=='INVALID_STEP_PROOF')
        step=96+2+5120; bad=bytearray(N3); a=2429+step; b=a+step; bad[a:b],bad[b:b+step]=bad[b:b+step],bad[a:b]
        code,result,_=s.request('/v1/lineage/verify',envelope(bad));check('broken join',code==200 and status(result)=='REJECTED' and result['verification']['code']=='BROKEN_CONTINUITY' and result['lineagePermission']=='DENIED')
        for name,offset in [('root',230+100),('terminal',230+1169+100),('chain-ref',49+16+1+32+4)]:
            bad=bytearray(N3);bad[offset]^=1
            code,result,_=s.request('/v1/lineage/verify',envelope(bad));check('bad '+name,code==200 and status(result)!='VERIFIED' and result['lineagePermission']=='DENIED' and result['verification']['code']=={'root':'ROOT_AUTHENTICATION_FAILED','terminal':'TERMINAL_AUTHENTICATION_FAILED','chain-ref':'CHAIN_CONTEXT_REJECTED'}[name])
        bad=bytearray(N3);bad[16]=4
        code,result,_=s.request('/v1/lineage/verify',envelope(bad));check('unsupported version',code==200 and result['verification']['code']=='UNSUPPORTED_VERSION')
        code,result,_=s.request('/v1/lineage/verify',raw=b'{');check('malformed JSON',code==400 and result['error']['code']=='INVALID_JSON')
        code,result,_=s.request('/v1/lineage/verify',{'evidenceEncoding':'base64','evidence':'%%%'});check('malformed base64',code==400 and result['error']['code']=='INVALID_BASE64')
        code,result,_=s.request('/v1/lineage/verify',envelope(N3[:-1]));check('truncated binary',code==200 and status(result)=='REJECTED')
        code,result,_=s.request('/v1/lineage/verify',envelope(N3+b'\x00'));check('trailing bytes',code==200 and status(result)=='REJECTED')
        code,result,_=s.request('/v1/lineage/verify',envelope(N3),raw=b'{' + b'x'*7_200_001);check('oversize request',code==413)
        bad=bytearray(N3);bad[2427:2429]=(1025).to_bytes(2,'little')
        code,result,_=s.request('/v1/lineage/verify',envelope(bad));check('unsupported proof count',code==200 and status(result)=='REJECTED')
        code,result,_=s.request('/v1/lineage/verify',{**envelope(N3),'trustedCheckpoint':True});check('no caller checkpoint',code==400 and result['error']['code']=='INVALID_JSON')
    finally:
        s.close()
    for view,evidence,expected in [('A4',N3,'PENDING_FINALITY'),('CKN',CKN,'CHECKPOINT_TRUST_REQUIRED')]:
        s=Server(view=view)
        try:
            code,result,ms=s.request('/v1/trade/authorize',envelope(evidence));check('authorize '+view,code==200 and status(result)==expected and result['lineagePermission']=='DENIED' and result['settlement']=='NOT_EXECUTED')
            save(view.lower()+'-authorize',result);measure[view+'AuthorizeMs']=round(ms,3)
            code,_,_=s.request('/v1/demo/scenarios');check('demo disabled '+view,code==404)
        finally:s.close()
    s=Server(view='A7')
    try:
        code,result,_=s.request('/v1/lineage/verify',envelope(N3));check('restart A7',code==200 and status(result)=='VERIFIED')
        code,_,_=s.request('/v1/demo/run',{'scenarioId':'valid-rooted-n3'});check('demo disabled',code==404)
    finally:s.close()
    bad_env=dict(os.environ,ZWA_CHAIN_VIEW='UNKNOWN',ZWA_BIND='127.0.0.1:0')
    bad_start=subprocess.run([str(BINARY)],cwd=ROOT,env=bad_env,capture_output=True,timeout=15)
    check('unavailable verifier fails startup',bad_start.returncode!=0)
    report={'passed':passed,'failed':0,'performance':measure}
    (OUT.parent/'http-test-result.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
