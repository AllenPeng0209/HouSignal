"""Opt-in live research workflow acceptance using explicitly synthetic material.

Uses the configured local ASR, visual model, script model and renderer. It does
not publish, contact anyone or delete data. A receipt permits safe continuation.
"""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import time
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.core_models import Asset, Property
from app.db import SessionLocal


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'reports/product-refinement-20261014/live-flow.json'
FIXTURE = ROOT / '.runtime/research-product-acceptance'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='explicitly run configured model services')
    options = parser.parse_args()
    if not options.live:
        parser.error('--live is required; this check uses configured model capacity')
    state = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else {
        'mode':'live API/PostgreSQL/local ASR/configured vision and script model; synthetic input only',
        'started_at':datetime.now(timezone.utc).isoformat(), 'checks':{}, 'external_publication':False}
    def save(stage):
        state['stage'] = stage
        state['updated_at'] = datetime.now(timezone.utc).isoformat()
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
        print(stage, flush=True)
    with httpx.Client(base_url='http://127.0.0.1:8017',
            headers={'Origin':'http://127.0.0.1:8017'},timeout=30) as client:
        def request(method,path,**kwargs):
            response=client.request(method,path,**kwargs)
            if response.is_error:
                detail=response.json().get('detail')
                raise RuntimeError(f'{method} {path}: HTTP {response.status_code}: '
                                   + (detail if isinstance(detail,str) else '请求格式或状态无效'))
            return response.json()
        def wait(jid,seconds=600):
            until=time.monotonic()+seconds
            while time.monotonic()<until:
                job=request('GET','/api/jobs/'+jid)
                if job['status']=='succeeded':return job
                if job['status'] in ('failed','cancelled'):
                    raise RuntimeError(f'{job["kind"]}: {job.get("error") or job["status"]}')
                time.sleep(1)
            raise RuntimeError('任务仍在运行；保留编号，重新运行此脚本可继续等待')
        auth=request('POST','/api/auth/login',json={'email':settings.bootstrap_email,'password':settings.bootstrap_password})
        tenant,user=auth['organization']['id'],auth['user']['id']
        if not state.get('project_id'):
            project=request('POST','/api/projects',json={'name':'[合成验收] 房地产研究完整流程 20261014',
                'city':'上海','property_type':'软件验收示例住宅','owner_name':'合成验收负责人',
                'audience':'改善家庭的软件验收样本','channel':'抖音','marketing_confirmed':True,
                'marketing_evidence':'仅验证软件，不对应真实楼盘、客户或对外营销授权'})
            state['project_id']=project['id']
            with SessionLocal() as db:
                db.info.update(tenant_id=tenant,user_id=user)
                db.get(Property,project['id']).is_test=True;db.commit()
            save('created_synthetic_project')
        pid=state['project_id'];base='/api/projects/'+pid;research=base+'/operations/research'
        if not state['checks'].get('project_facts'):
            values={'area':('120','㎡'),'width':('6','m'),'layout':('三房两厅',''),
                    'kitchen':('厨房与餐厅相邻','')}
            for fact in request('GET',base+'/facts'):
                if fact['semantic_key'] not in values:continue
                value,unit=values[fact['semantic_key']]
                request('PATCH',base+'/facts/'+fact['id'],json={'value':value,'unit':unit,
                    'source':'合成软件验收事实，不是真实房源资料'})
                request('POST',base+'/facts/'+fact['id']+'/verify',json={'confirmed':True})
            state['checks']['project_facts']=True;save('prepared_synthetic_project_facts')
        if not state.get('target_asset_ids'):
            font=ImageFont.truetype('/System/Library/Fonts/STHeiti Light.ttc',34)
            ids=[]
            for i in range(2):
                image=Image.new('RGB',(720,1280),(233,242,250));draw=ImageDraw.Draw(image)
                for line,text in enumerate(('当前项目 · 合成验收示意','不用于对外宣传','核验建筑面积 120 平方米',
                                           '客厅与生活动线' if i==0 else '厨房与餐厅联系')):
                    draw.text((45,90+line*110),text,font=font,fill='#345372')
                path=FIXTURE/f'target-{i}.png';image.save(path);path.chmod(0o600)
                with path.open('rb') as file:
                    asset=request('POST',base+'/assets',files={'file':(path.name,file,'image/png')})
                request('POST',base+'/assets/'+asset['id']+'/rights',json={'rights':'authorized',
                    'evidence':'由本验收脚本生成的示意图，仅用于软件验证','model_processing_authorized':False,
                    'enqueue_extraction':False})
                with SessionLocal() as db:
                    db.info.update(tenant_id=tenant,user_id=user)
                    db.get(Asset,asset['id']).origin_type='test';db.commit()
                ids.append(asset['id'])
            state['target_asset_ids']=ids;save('prepared_separate_target_assets')
        if not state.get('reference_id'):
            reference=request('POST',research+'/references',json={
                'title':'[合成验收] 示意动线与口播，不是真实平台作品',
                'source_url':'https://www.douyin.com/video/7390000000000000001',
                'note':'完整流程验收；链接仅作规范身份输入，未下载或核验该平台作品。'})
            state['reference_id']=reference['id'];save('saved_reference_independently')
        rid=state['reference_id']
        reference=request('GET',research+'/references/'+rid)
        if not reference.get('media_asset_id'):
            with (FIXTURE/'source.mp4').open('rb') as file:
                uploaded=request('POST',research+'/references/'+rid+'/media',
                    data={'expected_revision':str(reference['revision']),
                          'evidence':'本验收脚本制作的示意视频，允许本项目研究和AI识别，不用于营销成片',
                          'model_processing_authorized':'true'},files={'file':('synthetic-source.mp4',file,'video/mp4')})
            reference=uploaded['reference'];state['source_asset_id']=uploaded['asset']['id']
            assert uploaded['asset']['origin_type']=='reference'
            save('uploaded_research_only_video')
        if not state.get('analysis_job_id'):
            started=request('POST',research+'/references/'+rid+'/analyze',json={
                'expected_revision':reference['revision'],'idempotency_key':str(uuid4())})
            state['analysis_job_id']=started['job_id'];save('analyzing_actual_audio_and_frames')
        analyzed=wait(state['analysis_job_id'],360)
        analysis=analyzed['result']['reference_analysis']
        assert analysis['transcript_segments'] and analysis['frames']
        assert all(0<=item['time_seconds']<analysis['duration_seconds'] for item in analysis['frames'])
        frame=client.get(analysis['frames'][0]['frame_url']);frame.raise_for_status()
        assert frame.content.startswith(b'\xff\xd8')
        state['analysis']={'model':analysis['model'],'frame_count':len(analysis['frames']),
            'transcript_segment_count':len(analysis['transcript_segments']),
            'duration_seconds':analysis['duration_seconds'],'coverage':analysis['coverage'],
            'usage_receipts':analysis['usage_receipts']}
        state['checks']['actual_asr_and_visual_analysis']=True;save('actual_source_analysis_passed')
        from app.reference_media import analysis_notes
        notes={**analysis_notes(analysis),'target_seconds':45,'topic_id':'space'}
        reference=request('GET',research+'/references/'+rid)
        reference=request('PATCH',research+'/references/'+rid,json={'expected_revision':reference['revision'],
            'research_notes':notes,'note':'合成验收：已实际转写和分析采样画面；内容不证明真实房源或爆款效果。'})
        assert client.get(analysis['frames'][0]['frame_url']).status_code==200
        state['checks']['notes_saved_without_invalidating_source_frames']=True
        source={'source_url':reference['source_url'],'title':reference['title'],**notes,
                'reference_id':rid,'analysis_job_id':state['analysis_job_id']}
        preview=request('POST',base+'/operations/discovery/breakdown',json=source)
        assert preview['source']['evidence_basis']=='asset_asr_and_sampled_frames'
        if not state.get('plan_id'):
            saved=request('POST',base+'/operations/discovery/breakdown/plan',json={
                'reference':source,'plan':preview['plan_seed'],'idempotency_key':str(uuid4())})
            state['plan_id']=saved['id'];assert saved['reference_id']==rid
            save('saved_fact_grounded_adaptation_plan')
        if not state.get('script_job_id') and not state.get('reviewed_content_id'):
            created=request('POST',base+'/contents/generate',json={'topic_id':'space','plan_id':state['plan_id']})
            state['script_job_id']=created['job_id'];save('generating_script_with_configured_model')
        if state.get('reviewed_content_id'):
            # This ID is set only by an explicit editorial acceptance operation;
            # failed model output is never silently substituted or auto-approved.
            cid=state['reviewed_content_id']
        else:
            generated=wait(state['script_job_id'],900)
            cid=generated['result']['content_version_id']
        state['content_id']=cid
        content=next(row for row in request('GET',base+'/contents') if row['id']==cid)
        shots=content['storyboard'].get('shots',[]) if isinstance(content['storyboard'],dict) else content['storyboard']
        assert abs(sum(shot['duration'] for shot in shots)-45)<.11
        assert all(shot['asset_id'] in state['target_asset_ids'] for shot in shots)
        state['checks']['actual_script_uses_target_facts_and_assets']=True;save('actual_script_passed')
        if not state.get('render_job_id'):
            state['render_job_id']=request('POST',base+'/contents/'+cid+'/render')['job_id'];save('rendering_actual_mp4')
        rendered=wait(state['render_job_id'],240)
        content=next(row for row in request('GET',base+'/contents') if row['id']==cid)
        video=client.get(base+'/assets/'+content['render_asset_id']+'/file');video.raise_for_status()
        target=ROOT/'reports/product-refinement-20261014/acceptance-render.mp4';target.write_bytes(video.content)
        state['render_bytes']=len(video.content)
        outcomes=request('GET',research+'/references/'+rid+'/outcomes')
        assert any(plan['id']==state['plan_id'] for plan in outcomes['plans'])
        assert outcomes['verified_live_count']==0
        state['checks']['reference_plan_script_render_outcomes_linked']=True
        state['not_exercised']=['真实平台召回率','真实原片商业研究质量','公开发布','客户咨询或成交效果']
        state['status']='passed';save('complete')


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        if OUTPUT.exists():
            state=json.loads(OUTPUT.read_text());state['status']='failed';state['error']=str(error)
            OUTPUT.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
        raise
