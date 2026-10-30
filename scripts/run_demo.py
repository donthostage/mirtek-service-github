"""Run two loopback servers. Stop only owned child processes on exit."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parent.parent


def stop(process):
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=3)


def wait_ready(process,url,field,value):
    deadline=time.monotonic()+10
    with httpx.Client(trust_env=False,timeout=.5) as client:
        while time.monotonic()<deadline:
            if process.poll() is not None: raise RuntimeError('Server exited during startup')
            try:
                r=client.get(url+'/health')
                if r.status_code==200 and r.json().get(field)==value: return
            except (httpx.HTTPError,ValueError): pass
            time.sleep(.1)
    raise RuntimeError('Server did not become ready')


def smoke(api_url,source_process,report_path):
    rows=[]
    with httpx.Client(base_url=api_url,trust_env=False,timeout=5) as client:
        def post(path,body,status,reason=None):
            started=time.perf_counter()
            response=client.post(path,json=body)
            response.raise_for_status()
            actual=response.json()
            reasons=(reason,) if isinstance(reason,str) else reason
            matched=actual['status']==status and (reasons is None or actual['reason_code'] in reasons)
            rows.append({'path':path,'request':body,'response':actual,'matched':matched,
                         'tcp_elapsed_ms':round((time.perf_counter()-started)*1000,3)})
            return actual
        for sku,wh,status,reason in [('10001','WH-DEMO-1','answered','source_answer'),
                                     ('10001','WH-DEMO-2','answered','source_answer'),
                                     ('10002','WH-DEMO-2','handoff','missing_stock')]:
            post('/api/v1/availability',{'sku':sku,'warehouse_id':wh},status,reason)
        post('/api/v1/messages',{'text':'Есть артикул 10001 WH-DEMO-1?'},'answered')
        first=post('/api/v1/messages',{'text':'Как оформить возврат?'},'needs_clarification','missing_purchase_channel')
        post('/api/v1/messages',{'text':'В магазине','context_id':first['context_id']},'answered')
        post('/api/v1/messages',{'text':'Как оставить жалобу?'},'answered')
        post('/api/v1/messages',{'text':'Жалоба на обслуживание'},'handoff','individual_review_required')
        post('/api/v1/messages',{'text':'Как работает S1 2026-10-29?'},'answered')
        post('/api/v1/messages',{'text':'Какие акции?'},'answered')
        post('/api/v1/messages',{'text':'Какие условия доставки?'},'answered')
        post('/api/v1/messages',{'text':'Как забрать покупку?'},'answered')
        post('/api/v1/messages',{'text':'Есть 10001-2 WH-DEMO-1?'},'handoff','unknown_product')
        post('/api/v1/messages',{'text':'Есть артикул 10001 WH-DEMO-1.2?'},'handoff','unknown_warehouse')
        post('/api/v1/messages',{'text':'Есть артикул 10001 и 10002 WH-DEMO-1?'},'needs_clarification','ambiguous_product')
        stop(source_process)
        post('/api/v1/availability',{'sku':'10001','warehouse_id':'WH-DEMO-1'},'handoff',('source_unavailable','source_timeout'))
    report={'label':'REAL LOOPBACK HTTP PULL WITH SYNTHETIC DATA; NOT COMPANY KPI',
            'generated_at':datetime.now().astimezone().isoformat(),
            'source_is_separate_process':True,'llm_calls':False,'company_connected':False,
            'requests':rows,'passed':all(r['matched'] for r in rows)}
    if report_path:
        Path(report_path).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if not report['passed']: raise RuntimeError('Smoke check failed')
    print(f'HTTP pull smoke passed: {len(rows)} requests',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--api-port',type=int,default=8766)
    parser.add_argument('--source-port',type=int,default=8767)
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--report')
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--reload', action='store_true', help='Local development: reload code without re-entering the key')
    args=parser.parse_args()
    if args.reload and args.smoke: raise ValueError('Reload and smoke must be run separately')
    if args.api_port==args.source_port: raise ValueError('Ports must differ')
    for port in (args.api_port,args.source_port):
        with socket.socket() as sock: sock.bind(('127.0.0.1',port))
    owned=[]
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    source_env=os.environ.copy()
    for key in ('AITUNNEL_API_KEY','MIRTEK_SOURCE_API_KEY'): source_env.pop(key,None)
    api_env=os.environ.copy()
    api_env.update(MIRTEK_SOURCE_BACKEND='http_pull_mock',MIRTEK_SOURCE_URL=f'http://127.0.0.1:{args.source_port}')
    api_env.pop('MIRTEK_SOURCE_API_KEY',None)
    if args.smoke: api_env['MIRTEK_CLASSIFIER']='baseline'
    try:
        for module,port,env,field,value in [
            ('contracts.mock_api:app',args.source_port,source_env,'mode','synthetic_policy_mock'),
            ('app.main:app',args.api_port,api_env,'source_backend','http_pull_mock')]:
            command=[sys.executable,'-m','uvicorn',module,'--host','127.0.0.1','--port',str(port)]
            if args.reload:
                command += ['--reload','--reload-dir',str(ROOT/'app'),'--reload-dir',str(ROOT/'contracts')]
            process=subprocess.Popen(command,
                                      cwd=ROOT,env=env,creationflags=flags)
            owned.append(process)
            wait_ready(process,f'http://127.0.0.1:{port}',field,value)
        print(f'Demo: http://127.0.0.1:{args.api_port} | Mock source: http://127.0.0.1:{args.source_port}/docs',flush=True)
        if args.open_browser and not args.smoke:
            import webbrowser
            webbrowser.open(f'http://127.0.0.1:{args.api_port}')
        if args.smoke:
            smoke(f'http://127.0.0.1:{args.api_port}',owned[0],args.report)
            return
        while all(p.poll() is None for p in owned): time.sleep(.3)
        raise RuntimeError('One of the demo servers stopped')
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(owned): stop(process)


if __name__=='__main__': main()
