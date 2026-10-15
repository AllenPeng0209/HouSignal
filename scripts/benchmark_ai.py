"""Explicit live latency check using fictional development projects only.

No credentials, chat content or tool payloads are written to the receipt.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='baseline')
    parser.add_argument('--kind', choices=['chat', 'content'], default='chat')
    parser.add_argument('--scenario', choices=['fact','overview','customer'], default='fact')
    parser.add_argument('--first-text-limit', type=float, default=5)
    parser.add_argument('--timeout', type=float, default=160)
    args = parser.parse_args()
    report = {'label': args.label, 'kind': args.kind, 'at': datetime.now(timezone.utc).isoformat()}
    start = time.perf_counter()
    with httpx.Client(base_url='http://127.0.0.1:8017', headers={'Origin':'http://127.0.0.1:8017'},timeout=20) as client:
        response = client.post('/api/auth/login', json={'email':settings.bootstrap_email,'password':settings.bootstrap_password})
        response.raise_for_status()
        projects = client.get('/api/projects'); projects.raise_for_status()
        project = next(p for p in projects.json() if p['name']=='云境·壹号' and p.get('is_test'))
        base = '/api/projects/' + project['id']
        cid = None
        if args.kind == 'chat':
            response = client.post(base+'/conversations',json={'title':'性能验收 · '+args.label}); response.raise_for_status()
            cid = response.json()['id']
            path = '/api/conversations/'+cid+'/messages'
            questions = {'fact':'当前项目已核验建筑面积多少？请给出处。',
                         'overview':'请完整汇总当前项目情况、缺少什么、关键客户是谁以及下一步。请附来源。',
                         'customer':'陈女士现在最需要解决的具体问题是什么？请结合最近跟进记录说明并附来源。'}
            body = {'text':questions[args.scenario]}
        else:
            path = base+'/contents/generate'; body = {'topic_id':'space'}
        submitted = time.perf_counter()
        response = client.post(path,json=body); response.raise_for_status()
        jid = response.json()['job_id']
        report.update({'job_id':jid, 'conversation_id':cid, 'setup_ms':round((submitted-start)*1000,1), 'enqueue_ms':round((time.perf_counter()-submitted)*1000,1)})
        first_running = first_text = None
        counts = {'job_requests':0,'message_requests':0}
        deadline = submitted + args.timeout
        while time.perf_counter() < deadline:
            response = client.get('/api/jobs/'+jid); response.raise_for_status()
            job = response.json(); counts['job_requests'] += 1
            elapsed = time.perf_counter()-submitted
            if job['status'] != 'queued' and first_running is None: first_running = elapsed
            if cid and (job.get('result') or {}).get('preview_text') and first_text is None:
                first_text = time.perf_counter()-submitted
            if cid and job['status'] in ('succeeded','failed','cancelled'):
                response = client.get('/api/conversations/'+cid+'/messages'); response.raise_for_status()
                counts['message_requests'] += 1
                replies = [m for m in response.json() if m['role']=='assistant' and m['job_id']==jid]
                if any(m.get('content') for m in replies) and first_text is None: first_text = time.perf_counter()-submitted
            if job['status'] in ('succeeded','failed','cancelled'):
                report['status'] = job['status']; break
            time.sleep(.2)
        else:
            report['status']='timeout'
        report.update({'first_running_ms':round(first_running*1000,1) if first_running is not None else None,'first_text_ms':round(first_text*1000,1) if first_text is not None else None,'complete_ms':round((time.perf_counter()-submitted)*1000,1),'request_counts':counts})
        report['server_timings'] = {key:job.get(key) for key in ('queue_ms','execution_ms','first_token_ms','elapsed_ms')}
        report['first_text_within_target'] = first_text is not None and first_text <= args.first_text_limit if cid else None
        report['first_text_target_ms'] = args.first_text_limit*1000 if cid else None
    target = ROOT/'reports'/('ai-latency-'+args.label+'-'+args.kind+'.json')
    target.parent.mkdir(parents=True,exist_ok=True); target.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False),flush=True)
    if report['status']!='succeeded' or (cid and not report['first_text_within_target']): raise SystemExit(1)


if __name__=='__main__': main()
