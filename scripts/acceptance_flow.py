"""Opt-in live acceptance against owned API, PostgreSQL and Bailian.

Creates a clearly marked test project. Does not delete existing data.
"""
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import subprocess
import time

import httpx
from PIL import Image, ImageDraw
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from app.config import settings
from app.core_models import Property, Membership, User
from app.security import context_for_user

ROOT = Path(__file__).resolve().parents[1]
receipt = {'started_at': datetime.now(timezone.utc).isoformat(), 'checks': {}}


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.is_error:
        raise RuntimeError(f'{method} {path}: {response.status_code} {response.json().get("detail")}')
    return response.json()


def wait(client, jid, timeout=160):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = request(client, 'GET', '/api/jobs/' + jid)
        if job['status'] in ('succeeded', 'failed', 'cancelled'):
            if job['status'] != 'succeeded':
                raise RuntimeError(f'Job {jid} failed: {job.get("error")}')
            return job
        time.sleep(1)
    raise RuntimeError('Task timed out; job id ' + jid)


def main():
    headers = {'Origin': 'http://127.0.0.1:5177'}
    with httpx.Client(base_url='http://127.0.0.1:8017', headers=headers, timeout=20) as c:
        auth = request(c, 'POST', '/api/auth/login', json={'email':settings.bootstrap_email,'password':settings.bootstrap_password})
        receipt['checks']['real_login'] = True
        project = request(c, 'POST', '/api/projects', json={'name':'青屿·庭序（开发验收） '+datetime.now().strftime('%H%M%S'), 'city':'上海','property_type':'测试示例住宅','owner_name':'验收负责人','audience':'家庭生活空间','sales_owner_name':auth['user']['name'],'channel':'抖音','marketing_confirmed':True,'marketing_evidence':'仅用于开发验收的虚构项目，非真实房源宣传'})
        pid = project['id']; receipt['project_id'] = pid
        with Session(create_engine(settings.migration_database_url, hide_parameters=True)) as db:
            context_for_user(db,auth['user']['id'],auth['organization']['id'])
            p = db.get(Property,pid); p.is_test=True; db.commit()
        base = '/api/projects/' + pid
        facts = request(c,'GET',base+'/facts')
        values={'area':('420','㎡'),'width':('8.5','m'),'layout':('4 房同层',''),'kitchen':('厨房与餐厅相邻','')}
        for fact in facts:
            if fact['semantic_key'] in values:
                value,unit=values[fact['semantic_key']]
                request(c,'PATCH',base+'/facts/'+fact['id'],json={'value':value,'unit':unit,'source':'虚构验收资料 · '+fact['label']})
                request(c,'POST',base+'/facts/'+fact['id']+'/verify',json={'confirmed':True})
        receipt['checks']['project_and_verified_facts'] = True
        picture=Image.new('RGB',(960,640),'#e7edf6'); draw=ImageDraw.Draw(picture)
        draw.rectangle((60,80,880,580),fill='#cad7e9'); draw.text((85,115),'SYNTHETIC TEST MATERIAL - NOT A PROPERTY PHOTO',fill='#32486a')
        stream=BytesIO(); picture.save(stream,format='JPEG')
        uploaded=request(c,'POST',base+'/assets',files={'file':('合成验收示意.jpg',stream.getvalue(),'image/jpeg')})
        aid=uploaded['id']
        request(c,'POST',base+'/assets/'+aid+'/rights',json={'rights':'authorized','evidence':'自制合成验收素材，仅测试剪辑；非真实楼盘照片','model_processing_authorized':False})
        receipt['checks']['private_upload_and_rights'] = True
        topics=request(c,'GET',base+'/topics'); topic=next(x for x in topics if x['id']=='space')
        assert topic['eligible']
        generated=wait(c,request(c,'POST',base+'/contents/generate',json={'topic_id':'space'})['job_id'])
        cid=generated['result']['content_version_id']; receipt['content_version_id']=cid
        receipt['checks']['real_bailian_generated_content'] = True
        rendered=wait(c,request(c,'POST',base+'/contents/'+cid+'/render',json={})['job_id'])
        rows=request(c,'GET',base+'/contents'); version=next(v for v in rows if v['id']==cid)
        file=c.get(base+'/assets/'+version['render_asset_id']+'/file'); file.raise_for_status()
        target=ROOT/'.runtime/acceptance-render.mp4'; target.write_bytes(file.content)
        info=json.loads(subprocess.check_output([settings.ffprobe_binary,'-v','error','-show_entries','format=duration:stream=codec_name,width,height','-of','json',str(target)]))
        assert float(info['format']['duration'])>=15
        receipt['render']=info; receipt['checks']['real_mp4_decodes'] = True
        approved=request(c,'POST',base+'/contents/'+cid+'/approve',json={'confirmed':True,'note':'开发验收：人工核对虚构资料与合成示意成片'})
        exported=request(c,'GET',base+'/contents/'+cid+'/export')
        receipt['checks']['review_and_export'] = True
        publication=request(c,'POST',base+'/publications',json={'release_id':approved['id'],'url':'https://example.com/housignal-test/'+cid,'platform_state':'live'})
        request(c,'POST',base+'/publications/'+publication['id']+'/verify',json={'confirmed':True,'platform_state':'live','metrics':{'D7':{'views':100,'inquiries':1},'D30':None}})
        d30=request(c,'GET',base+'/reports?window=D30')
        assert d30['metrics']['views'] is None
        receipt['checks']['manual_receipt_and_unknown_d30'] = True
        inquiry=request(c,'POST',base+'/inquiries',json={'name':'验收客户甲','need':'了解家庭生活空间','contact':'QA-ORIGINAL-CHANNEL-'+pid,'contactable':True,'consent':True,'source_confidence':'unknown'})
        lead=request(c,'POST',base+'/inquiries/'+inquiry['id']+'/qualify',json={'contactable':True,'consent':True,'demand_confirmed':True,'fit_confirmed':True})
        request(c,'POST',base+'/leads/'+lead['id']+'/followups',json={'note':'开发验收：客户希望了解厨餐关系','next_action':'验收下一步：补充厨房实拍说明'})
        revealed=request(c,'POST',base+'/inquiries/'+inquiry['id']+'/contact-reveal',json={'purpose':'本次咨询服务'})
        assert revealed['contact'].startswith('QA-ORIGINAL-CHANNEL-')
        receipt['checks']['encrypted_crm_and_authorized_reveal'] = True
        conv=request(c,'POST',base+'/conversations',json={'title':'端到端验收对话'})
        job=wait(c,request(c,'POST','/api/conversations/'+conv['id']+'/messages',json={'text':'请汇总项目的已核验面积和资料缺口，哪个客户需要跟进？下一步是什么？附上来源。'})['job_id'])
        messages=request(c,'GET','/api/conversations/'+conv['id']+'/messages')
        reply=next(m for m in reversed(messages) if m['role']=='assistant')
        assert reply['status']=='completed' and reply['citations']
        receipt['checks']['real_agent_reads_current_database'] = True
        receipt['agent']={'conversation_id':conv['id'],'message_id':reply['id'],'citations':reply['citations'],'content':reply['content']}
        fact=next(f for f in request(c,'GET',base+'/facts') if f['semantic_key']=='area')
        request(c,'PATCH',base+'/facts/'+fact['id'],json={'value':'421','unit':'㎡','source':'虚构验收资料 · 更新面积'})
        blocked=c.get(base+'/contents/'+cid+'/export'); assert blocked.status_code==409
        receipt['checks']['changed_fact_invalidates_export'] = True
        request(c,'POST',base+'/leads/'+lead['id']+'/stage',json={'stage':'withdrawn'})
        blocked=c.post(base+'/inquiries/'+inquiry['id']+'/contact-reveal',json={'purpose':'本次咨询服务'}); assert blocked.status_code in (403,409,422)
        receipt['checks']['withdrawal_blocks_contact_reveal'] = True
        anonymous=httpx.get('http://127.0.0.1:8017'+base+'/assets/'+aid+'/file'); assert anonymous.status_code==401
        receipt['checks']['private_files_require_login'] = True
        csrf=c.post('/api/projects',headers={'Origin':'https://untrusted.example'},json={'name':'blocked','city':'上海'});assert csrf.status_code==403
        receipt['checks']['cross_origin_write_blocked'] = True
    with create_engine(settings.database_url,hide_parameters=True).connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM facts')).scalar()==0
        role=conn.execute(text('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
        assert role[1] is False and role[2] is False
        receipt['checks']['postgres_rls_denies_unbound_tenant']=True
        receipt['database_role']={'name':role[0],'superuser':False,'bypassrls':False}
    receipt['completed_at']=datetime.now(timezone.utc).isoformat()
    report_path = ROOT/'reports/acceptance-flow.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps({'checks_passed':len(receipt['checks']),'project_id':receipt['project_id'],'report':'reports/acceptance-flow.json'},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
