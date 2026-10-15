#!/usr/bin/env python3
"""Two explicitly bounded generation probes; never activate project config."""
from pathlib import Path
import json
import time
import urllib.request
import urllib.error
import datetime

root=Path(__file__).resolve().parents[2]
auth=(root/'.runtime/native-qwen.auth').read_text().strip()
base='http://127.0.0.1:18891'
def call(route,body=None):
    request=urllib.request.Request(base+route,data=None if body is None else json.dumps(body,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+auth,'Content-Type':'application/json'})
    started=time.monotonic()
    try:
        with urllib.request.urlopen(request,timeout=285) as response:
            return {'http_status':response.status,'elapsed_seconds':time.monotonic()-started,'response':json.load(response)}
    except urllib.error.HTTPError as error:
        result={'http_status':error.code,'elapsed_seconds':time.monotonic()-started,'response':json.load(error)}
        error.close();return result

if __name__=='__main__':
    result={'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'health':call('/health'),'models':call('/v1/models'),'cases':[]}
    print(json.dumps(result,ensure_ascii=False),flush=True)
    for label,prompt in [('english','Reply with exactly these two English words: runtime ready'),('chinese','请只用一句中文介绍你自己，不超过20字。')]:
        body={'model':'housignal-qwen3-vl-32b-text','messages':[{'role':'user','content':prompt}],'max_tokens':64,'temperature':0,'request_id':'ppu-smoke-'+label}
        answer=call('/v1/chat/completions',body)
        result['cases'].append({'label':label,'request':body,**answer})
        print(json.dumps({'label':label,**answer},ensure_ascii=False),flush=True)
    result['health_after']=call('/health')
    (root/'.runtime/native-qwen-smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('smoke record saved',flush=True)
    if any(x['http_status']!=200 for x in result['cases']):raise SystemExit(1)
