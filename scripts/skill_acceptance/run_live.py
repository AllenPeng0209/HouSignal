"""Explicitly bounded live acceptance against this project's API and worker.

This is an acceptance tool, not a production provider fallback. It never counts
blocked/mocked operations as a pass and only submits cases marked free or the
one explicitly authorized existing-PPU video case.
"""
from __future__ import annotations

import argparse
import fcntl
from collections import Counter
from datetime import datetime
import hashlib
import json
import mimetypes
from pathlib import Path
import re
from html.parser import HTMLParser
import subprocess
import time
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageDraw

from app.config import settings
from app.native_skills import _public_text

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'reports/skill-acceptance'
FIXTURES = REPORT / 'fixtures'
MANIFEST = ROOT / '.runtime/skill-acceptance-fixtures.json'
BASE_URL = 'http://127.0.0.1:8017'


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def save_case(path, state, record):
    """Update one case under a lock; another batch must not replace fresh rows."""
    lock=MANIFEST.parent/(path.stem+'.lock')
    with lock.open('a') as descriptor:
        fcntl.flock(descriptor,fcntl.LOCK_EX)
        existing=json.loads(path.read_text()).get('cases',[]) if path.is_file() else []
        records={row['case_id']:row for row in existing}
        previous=records.get(record['case_id'])
        if previous and previous.get('job_id') and previous.get('job_id')!=record.get('job_id'):
            record['previous_attempts']=[*previous.get('previous_attempts',[]),
                {key:previous.get(key) for key in ('job_id','status','error','completed_at','artifacts')}]
        records[record['case_id']]=record
        save(path,{'verified_at':now(),'project_id':state['project_id'],
                   'billing_policy':'free calls plus explicitly authorized existing PPU only',
                   'summary':dict(Counter(v['status'] for v in records.values())),'cases':list(records.values())})


def request(client, method, url, **kwargs):
    deadline = time.monotonic() + 60
    safe_read = method.upper() == 'GET' or url.endswith('/preflight') or url in {'/api/auth/login','/api/auth/logout'}
    while True:
        try:
            response = client.request(method, url, **kwargs)
            break
        except (httpx.ConnectError, httpx.ReadTimeout) as error:
            # ConnectError occurs before delivery. A read timeout after a run
            # POST is ambiguous and must never produce a duplicate job.
            if (isinstance(error,httpx.ReadTimeout) and not safe_read) or time.monotonic() >= deadline:
                raise
            time.sleep(min(3,max(0,deadline-time.monotonic())))
    if response.is_error:
        try:
            message = response.json().get('detail', '请求失败')
        except ValueError:
            message = '服务未返回可用数据'
        raise ValueError(f'HTTP {response.status_code}: {_public_text(message, 600)}')
    return response.json()


def login(client):
    return request(client, 'POST', '/api/auth/login',
                   json={'email': settings.bootstrap_email, 'password': settings.bootstrap_password})


def make_fixtures():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for index, color in enumerate(('#c36b3c', '#59749c'), 1):
        image = Image.new('RGB', (960, 640), '#f5f3ed')
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((225, 100, 735, 555), 30, fill=color)
        draw.rectangle((325, 290, 635, 510), fill='#ead9be')
        draw.text((25, 25), 'SELF-CREATED ACCEPTANCE FIXTURE / NOT A PROPERTY PHOTO', fill='#172b49')
        draw.text((330, 195), f'HOUSIGNAL SAMPLE {index}', fill='white')
        image.save(FIXTURES / f'image{index if index > 1 else ""}.png')
    for index in (1, 2):
        path = FIXTURES / f'video{index if index > 1 else ""}.mp4'
        subprocess.run([settings.ffmpeg_binary, '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25:duration=4',
            '-f', 'lavfi', '-i', f'sine=frequency={440*index}:sample_rate=48000:duration=4',
            '-af', 'volume=0.15', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-shortest', str(path)], check=True, capture_output=True)
        subprocess.run([settings.ffmpeg_binary, '-hide_banner', '-loglevel', 'error', '-y',
            '-i', str(path), '-vn', '-c:a', 'pcm_s16le',
            str(FIXTURES / f'audio{index if index > 1 else ""}.wav')], check=True, capture_output=True)
    (FIXTURES / 'brief.txt').write_text('楼响原生技能验收材料。全部为自制模拟数据，不代表真实房源。\n'
        '项目：原生商业验收。面积：420平方米。客厅面宽：8.5米。户型：四房。\n'
        '评测目标：产物可打开、参数真实生效、保留数据来源、错误可以恢复。\n'
        '对外发布与客户通知需要具体内容确认。', encoding='utf-8')
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    paper = canvas.Canvas(str(FIXTURES / 'paper.pdf'), pagesize=(595, 842))
    paper.setTitle('Self-created content operations acceptance paper')
    paper.setFont('Helvetica-Bold', 18)
    paper.drawString(40, 790, 'Native Content Operations: A Small Controlled Study')
    paper.setFont('Helvetica', 11)
    for index, line in enumerate([
        'This document is self-created for software acceptance, not published research.',
        'Abstract: A native content pipeline must produce verifiable artifacts.',
        'Methods: Resize two controlled images and compare output dimensions.',
        'Results: Resizing a 960 x 640 image to 320 x 180 changes the aspect ratio.',
        'Figure 1: The following synthetic image is an authorized input fixture.',
        'Limitations: These values do not represent real properties or platform metrics.',
    ]):
        paper.drawString(40, 750-index*25, line)
    paper.drawImage(ImageReader(str(FIXTURES/'image.png')), 80, 180, width=420, height=280)
    paper.showPage()
    paper.setFont('Helvetica', 12)
    paper.drawString(40, 790, 'Conclusion: Decode outputs, check actual dimensions and preserve provenance.')
    paper.save()


def prepare(client):
    if MANIFEST.is_file():
        state = json.loads(MANIFEST.read_text())
        project = request(client, 'GET', f"/api/projects/{state['project_id']}")
        assert project['is_test'] is True, 'Acceptance must use a marked test project'
        return state
    make_fixtures()
    auth = request(client, 'GET', '/api/auth/me')
    project = request(client, 'POST', '/api/projects', json={
        'name': '【全技能实测】楼响原生商业验收 2026-10-14', 'city': '上海',
        'property_type': '自制模拟验收项目', 'owner_name': '技能验收',
        'audience': '测试内容运营与媒体生产流程', 'sales_owner_name': '技能验收',
        'channel': '抖音', 'marketing_confirmed': True,
        'marketing_evidence': '仅为软件验收的自制模拟项目；无真实房源或对外发布授权'})
    pid = project['id']
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.core_models import Property
    from app.security import context_for_user
    engine = create_engine(settings.migration_database_url or settings.database_url, hide_parameters=True)
    with Session(engine) as db:
        context_for_user(db, auth['user']['id'], auth['organization']['id'])
        record = db.get(Property, pid)
        assert record is not None
        record.is_test = True
        db.commit()
    engine.dispose()
    base = f'/api/projects/{pid}'
    state = {'created_at': now(), 'project_id': pid, 'tenant_id': auth['organization']['id'],
             'user_id': auth['user']['id'], 'assets': {}, 'accounts': {},
             'fixture_policy': 'self-created synthetic inputs; never evidence of a real platform publication'}
    save(MANIFEST, state)
    MANIFEST.chmod(0o600)
    values = {'area': ('420', '㎡'), 'width': ('8.5', 'm'), 'layout': ('四房同层', ''),
              'kitchen': ('厨房与餐厅相邻', ''), 'price': ('1000', '万元'), 'view': ('自制场景示意', '')}
    for fact in request(client, 'GET', base+'/facts'):
        if fact['semantic_key'] in values:
            value, unit = values[fact['semantic_key']]
            request(client, 'PATCH', base+'/facts/'+fact['id'],
                    json={'value': value, 'unit': unit, 'source': '自制验收输入，非真实房源事实'})
            request(client, 'POST', base+'/facts/'+fact['id']+'/verify', json={'confirmed': True})
    account = request(client, 'POST', base+'/operations/accounts', json={
        'platform': '抖音', 'name': '【模拟画像】技能验收账号', 'homepage': '',
        'positioning': '检验原生内容管线', 'audience': '内容运营团队',
        'voice': '清楚、具体、保留来源', 'red_lines': '不得对外发布模拟事实或模拟指标', 'status': 'managed'})
    state['accounts']['primary'] = account['id']
    for name, suffix in [('image','.png'),('image2','.png'),('video','.mp4'),('video2','.mp4'),
                         ('audio','.wav'),('audio2','.wav'),('paper','.pdf'),('brief','.txt')]:
        upload_fixture(client, state, name, FIXTURES/(name+suffix))
    # Upload extraction runs while model permission is false: no paid OCR/ASR.
    for _ in range(120):
        jobs = request(client, 'GET', base+'/jobs')
        if not any(j['status'] in ('queued','running') for j in jobs):
            break
        time.sleep(.5)
    else:
        raise ValueError('Initial extraction did not settle; no model permissions granted')
    save(MANIFEST, state)
    return state


def upload_fixture(client, state, name, path):
    base = f"/api/projects/{state['project_id']}"
    asset = request(client, 'POST', base+'/assets', files={
        'file': (path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'application/octet-stream')})
    request(client, 'POST', base+'/assets/'+asset['id']+'/rights', json={
        'rights': 'authorized', 'evidence': '自行制作的合成软件验收素材，仅供项目处理',
        'model_processing_authorized': False})
    state['assets'][name] = asset['id']
    save(MANIFEST, state)


def resolve(value, state):
    if isinstance(value, list):
        return [resolve(v, state) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, state) for k, v in value.items()}
    if isinstance(value, str):
        replacements = {'project_id': state['project_id'], 'account_id': state['accounts']['primary']}
        replacements.update({'asset:'+k:v for k,v in state['assets'].items()})
        for key, replacement in replacements.items():
            value = value.replace('{{'+key+'}}', replacement)
    return value


def inspect_artifact(path):
    suffix = path.suffix.lower()
    result = {'file': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size,
              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    assert result['bytes'] > 0, 'Artifact is empty'
    if suffix in {'.png','.jpg','.jpeg','.webp','.gif'}:
        with Image.open(path) as picture:
            picture.load()
            result.update(width=picture.width, height=picture.height, mode=picture.mode)
    elif suffix in {'.mp4','.mov','.webm','.mp3','.m4a','.wav','.ogg','.flac'}:
        probe = subprocess.run([settings.ffprobe_binary, '-v', 'error', '-show_entries',
            'format=duration:stream=codec_type,codec_name,width,height', '-of', 'json', str(path)],
            check=True, capture_output=True, timeout=30)
        info = json.loads(probe.stdout)
        assert float(info['format']['duration']) > 0 and info.get('streams')
        subprocess.run([settings.ffmpeg_binary, '-hide_banner', '-loglevel', 'error', '-i', str(path),
                        '-f', 'null', '-'], check=True, capture_output=True, timeout=60)
        result['media_probe'] = info
    elif suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        assert len(reader.pages) > 0
        result['pages'] = len(reader.pages)
    elif suffix in {'.zip','.docx','.pptx','.xlsx'}:
        import zipfile
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None and archive.namelist()
            result['archive_files'] = len(archive.namelist())
    elif suffix == '.json':
        json.loads(path.read_text())
    elif suffix in {'.txt','.md','.srt','.vtt','.csv','.html','.svg'}:
        assert path.read_text(encoding='utf-8').strip()
    return result


def visible_artifact_text(path):
    if path.suffix.lower() in {'.txt','.md','.srt','.vtt'}:
        return path.read_text(encoding='utf-8')
    if path.suffix.lower() == '.html':
        class VisibleText(HTMLParser):
            def __init__(self):
                super().__init__(); self.ignored=0; self.parts=[]
            def handle_starttag(self,tag,attrs):
                if tag.lower() in {'style','script'}: self.ignored+=1
            def handle_endtag(self,tag):
                if tag.lower() in {'style','script'}: self.ignored=max(0,self.ignored-1)
            def handle_data(self,value):
                if not self.ignored: self.parts.append(value)
        parser=VisibleText();parser.feed(path.read_text(encoding='utf-8'))
        return '\n'.join(parser.parts)
    return ''


def run_case(client, state, case, catalog, allow_ppu, previous=None, force_regenerate=False):
    name = case['skill_name']
    row = next(r for r in catalog if r['skill_name'] == name)
    skill_id = case.get('skill_id', row['id'])
    result = {'case_id': case.get('case_id', name), 'skill_name': name, 'skill_id': skill_id,
              'started_at': now(), 'commercial_scope': case.get('commercial_scope','base'),
              'input_origin': 'self-created synthetic fixture or explicitly named public source',
              'actual_attempt': False, 'status': 'blocked'}
    billing = case.get('billing','metered')
    if name == 'ai-video-gen' and billing != 'existing_ppu':
        result['blockers'] = ['真实PPU视频已由独立一次性case验收；通用免费批次不得重复提交生成']
        return result
    if billing not in {'free','existing_ppu'} or billing == 'existing_ppu' and not allow_ppu:
        result['blockers'] = case.get('prerequisites') or ['收费调用尚未授权；没有已验收的免费执行路径']
        return result
    if case.get('expected_outcome') == 'blocked':
        result['blockers'] = case.get('prerequisites') or ['所需真实账号、服务或输入尚未提供']
        return result
    body = {'input': resolve(case.get('input',''), state), 'parameters': resolve(case.get('parameters',{}), state),
            'asset_ids': [state['assets'][n] for n in case.get('asset_fixture_names',[])]}
    if case.get('account_fixture_name'):
        body['account_id'] = state['accounts'][case['account_fixture_name']]
    result['request'] = body
    base = f"/api/projects/{state['project_id']}/operations/skills/{skill_id}"
    started = time.monotonic()
    try:
        preflight = request(client, 'POST', base+'/preflight', json=body)
        result['preflight'] = {k: preflight[k] for k in ('status','reason','execution_engine')}
        if preflight['status'] != 'ready':
            result['blockers'] = [preflight['reason']]
            return result
        previous = previous or {}
        existing_job = None
        if previous.get('job_id') and previous.get('request') == body and not force_regenerate:
            existing_job = request(client,'GET','/api/jobs/'+previous['job_id'])
            if existing_job['status'] != 'succeeded':
                existing_job = None
        if existing_job:
            jid = existing_job['id']
            result['revalidated_existing_job'] = True
        else:
            result['submission_attempted'] = True
            submitted = request(client, 'POST', base+'/run', json=body)
            jid = submitted['job_id']
        result['job_id'] = jid
        result['actual_attempt'] = True
        timeout = case.get('timeout_seconds', 1000 if billing == 'existing_ppu' else 240)
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            job = request(client, 'GET', '/api/jobs/'+jid)
            if job['status'] in {'succeeded','failed','cancelled'}:
                break
            time.sleep(.8)
        else:
            request(client, 'POST', '/api/jobs/'+jid+'/cancel')
            raise ValueError('实际任务超时，已取消以免重复执行')
        result['job_status'] = job['status']
        if job['status'] != 'succeeded':
            raise ValueError(job.get('error') or job['status'])
        output = job['result']
        result['response'] = output.get('response')
        result['usage_receipts'] = output.get('usage_receipts',[])
        result['native_data'] = output.get('native_data',{})
        result['source_refs'] = output.get('source_refs',[])
        result['execution_engine'] = output.get('execution_engine')
        assert output.get('execution_engine') == 'housignal_native'
        quality = case.get('quality',{})
        visible = output.get('response','')
        text = visible if isinstance(visible,str) else json.dumps(visible,ensure_ascii=False)
        artifacts = []
        directory = REPORT/'artifacts'/re.sub(r'[^a-zA-Z0-9_-]','_',result['case_id'])/jid
        directory.mkdir(parents=True,exist_ok=True)
        for index, artifact in enumerate(output.get('artifacts',[])):
            response = client.get(artifact['url']); response.raise_for_status()
            filename = Path(artifact['name']).name
            path = directory/f'{index:02d}-{filename}'
            path.write_bytes(response.content)
            info = inspect_artifact(path)
            if artifact.get('sha256'):
                assert info['sha256'] == artifact['sha256'], 'Downloaded artifact hash differs from receipt'
            artifacts.append(info)
        result['artifacts'] = artifacts
        assert len(text.strip()) >= quality.get('min_text_chars',0), 'Output is too short for the declared deliverable'
        artifact_chars=max((len(re.sub(r'\s','',visible_artifact_text(ROOT/a['file']))) for a in artifacts),default=0)
        result['visible_artifact_text_chars']=artifact_chars
        assert artifact_chars >= quality.get('min_artifact_text_chars',0), 'Actual deliverable text is too short'
        produced = {Path(a['file']).suffix.lower() for a in artifacts}
        assert set(quality.get('required_artifact_suffixes',[])) <= produced, 'Required deliverable format is missing'
        documents = [output]
        for artifact in artifacts:
            path = ROOT/artifact['file']
            if path.suffix.lower() == '.json':
                document = json.loads(path.read_text())
                if isinstance(document,dict):
                    documents.append(document)
        required_fields = set(quality.get('required_fields',[]))
        assert not required_fields or any(required_fields <= set(document) for document in documents), 'Required structured deliverable fields are missing'
        if quality.get('require_sources'):
            assert output.get('source_refs'), 'Research has no independently recorded source references'
        result['status'] = 'passed'
        result['semantic_review_required'] = bool(quality.get('semantic_review_required'))
        if result['semantic_review_required']:
            result['commercial_verdict'] = 'pending_semantic_review'
    except (ValueError, AssertionError, httpx.HTTPError, OSError, subprocess.SubprocessError) as error:
        result['status'] = 'failed'
        result['error'] = _public_text(str(error),1200)
    finally:
        result['completed_at'] = now()
        result['duration_seconds'] = round(time.monotonic()-started,2)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--cases', type=Path)
    parser.add_argument('--allow-existing-ppu', action='store_true')
    parser.add_argument('--only', nargs='*')
    parser.add_argument('--exclude', nargs='*', default=[])
    parser.add_argument('--add-fixture', nargs='*', default=[])
    parser.add_argument('--grant-model-processing', action='store_true')
    parser.add_argument('--force-regenerate', action='store_true')
    parser.add_argument('--existing-job')
    args = parser.parse_args()
    REPORT.mkdir(parents=True,exist_ok=True)
    with httpx.Client(base_url=BASE_URL, timeout=30, headers={'Origin':BASE_URL}) as client:
        login(client)
        try:
            state = prepare(client) if args.prepare else json.loads(MANIFEST.read_text())
            for specification in args.add_fixture:
                name, raw_path = specification.split('=',1)
                assert re.fullmatch(r'[a-zA-Z0-9_-]+',name)
                if name not in state['assets']:
                    upload_fixture(client,state,name,Path(raw_path))
            if args.grant_model_processing:
                base=f"/api/projects/{state['project_id']}"
                for _ in range(120):
                    if not any(job['status'] in ('queued','running') for job in request(client,'GET',base+'/jobs')):
                        break
                    time.sleep(.5)
                else:
                    raise ValueError('Do not change model grants while extraction/jobs are still active')
                for asset_id in state['assets'].values():
                    request(client,'POST',base+'/assets/'+asset_id+'/rights',json={
                        'rights':'authorized','evidence':'自行制作的合成软件验收素材；允许所选项目模型处理，不自动识别',
                        'model_processing_authorized':True,'enqueue_extraction':False})
                save(MANIFEST,state)
            if args.prepare:
                print(json.dumps({'prepared':True,'fixture_count':len(state['assets']),'marked_test_project':True}),flush=True)
            if args.cases:
                document = json.loads(args.cases.read_text())
                group = document.get('group',args.cases.stem)
                path = REPORT/f'{group}-live.json'
                previous = json.loads(path.read_text()).get('cases',[]) if path.is_file() else []
                records = {v['case_id']:v for v in previous}
                catalog = request(client,'GET',f"/api/projects/{state['project_id']}/operations/skills")
                for case in document['cases']:
                    if case['skill_name'] in args.exclude or case.get('case_id') in args.exclude:
                        continue
                    if args.only and case['skill_name'] not in args.only and case.get('case_id') not in args.only:
                        continue
                    case_id=case.get('case_id',case['skill_name'])
                    if args.existing_job:
                        assert args.only and len(args.only)==1, 'Existing-job recovery must name one case'
                        job=request(client,'GET','/api/jobs/'+args.existing_job)
                        assert job['property_id']==state['project_id'] and job['status']=='succeeded' and job['result'].get('skill_name')==case['skill_name']
                        body={'input':resolve(case.get('input',''),state),'parameters':resolve(case.get('parameters',{}),state),
                              'asset_ids':[state['assets'][n] for n in case.get('asset_fixture_names',[])]}
                        if case.get('account_fixture_name'):body['account_id']=state['accounts'][case['account_fixture_name']]
                        records[case_id]={'job_id':args.existing_job,'request':body,'case_id':case_id,'status':'recovering_actual_job'}
                    if args.force_regenerate:
                        assert args.only and case.get('billing')=='free', 'Regeneration must explicitly name free cases'
                        assert case['skill_name'] in {row['skill_name'] for row in catalog if row.get('engine') in {'text','media'}}, 'Never repeat project writes or platform submissions'
                        assert case['skill_name'] not in {'ai-video-gen','ai-image-gen','ai-music','voice-clone'}, 'Never repeat cloud media or PPU submissions'
                    if records.get(case_id,{}).get('status') == 'passed' and not args.force_regenerate:
                        record=records[case_id]
                        if record.get('job_id') and 'source_refs' not in record:
                            job=request(client,'GET','/api/jobs/'+record['job_id'])
                            assert job['status']=='succeeded' and job['result'].get('skill_name')==case['skill_name']
                            for key in ('source_refs','native_data','execution_engine'):
                                record[key]=job['result'].get(key, [] if key=='source_refs' else {})
                            save_case(path,state,record)
                        print(json.dumps({'skill_name':case['skill_name'],'status':'previous_real_pass_retained','not_resubmitted':True},ensure_ascii=False),flush=True)
                        continue
                    if case.get('billing') == 'existing_ppu' and records.get(case_id,{}).get('job_id'):
                        print(json.dumps({'skill_name':case['skill_name'],'status':'existing_ppu_task_retained','not_resubmitted':True},ensure_ascii=False),flush=True)
                        continue
                    record = run_case(client,state,case,catalog,args.allow_existing_ppu,records.get(case_id),args.force_regenerate)
                    records[record['case_id']] = record
                    save_case(path,state,record)
                    print(json.dumps({k:record[k] for k in ('skill_name','status','actual_attempt')},ensure_ascii=False),flush=True)
        finally:
            try:
                request(client,'POST','/api/auth/logout')
            except (httpx.HTTPError,ValueError):
                print(json.dumps({'session_cleanup':'unavailable_after_api_restart','saved_results_retained':True}),flush=True)


if __name__ == '__main__':
    main()
