"""Live, resumable conversational CRM → matching → local video → Asset archive acceptance.

Explicit modes:
  --prepare-only  create/reuse isolated synthetic fixtures, but submit no model request
  --observe       adopt the fixture's exact user instruction submitted in the app, then observe
  --live          submit that single instruction through HTTP if absent, then observe

After fixture preparation this harness only observes the agent's own business operations.
It never calls CRM/matching/production/archive mutations itself, raises budgets, publishes,
contacts anyone, or requests paid image/video/audio generation.
"""
from __future__ import annotations

import argparse
from array import array
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import subprocess
import shutil
import sys
import time
from uuid import uuid4
import wave

import httpx
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select

from app.config import settings
from app.core import storage_path
from app.core_models import Asset, Fact, Property
from app.db import SessionLocal, utcnow
from app.security import context_for_user, require_project
from app.agent_models import ChatMessage, Conversation, ModelUsage, ToolCall
from app.business_models import Lead
from app.secretary_models import CustomerProfile
from app.job_models import Job

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / '.runtime/secretary-video-chain-acceptance-state.json'
REPORT_PATH = ROOT / 'reports/secretary-video-chain-20261015.json'
OUTPUT_DIR = ROOT / 'reports/secretary-video-chain-20261015'
PROJECT_NAME = '秘书视频全链路验收（合成）20261015'
CUSTOMER_NAME = '全链路验收客户甲'
PROMPT = (
    '请帮我一条龙办完：给全链路验收客户甲建立客户档案，预算3500万元，'
    '要四房、300到450平方米、低楼层、江景和安静。然后根据这些条件，'
    '匹配当前项目的房源资料和已经授权的住宅图片，选出合适的三张图片，排除不相关的高楼层办公室素材。'
    '请用匹配的三张图给这位客户制作一条20秒的竖屏介绍短片，720×1280、9:16，'
    '三个镜头依次6秒、7秒、7秒，直接切换，不加交叠转场；'
    '用清楚的中文字幕介绍380平方米四房、低楼层江景和安静环境，并标明合成示意、非真实房源。'
    '使用素材库里“全链路验收原创轻配乐.wav”作轻背景音，只用现有图片和配乐做本地剪辑，'
    '不需要配音，不调用付费的AI图片、视频或音乐生成。'
    '生成完成后，请自动继续核验真实成片，保存到当前项目素材库，关联这位客户，'
    '加上“江景、客户定向、全链路验收”三个标签，最后给我可播放的视频。'
    '整个流程请自主继续到归档完成，不对外发布，也不联系任何人。'
)
TERMINAL = {'succeeded', 'failed', 'cancelled'}


class AcceptanceFailure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise AcceptanceFailure(message)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def safe(value):
    if isinstance(value, str):
        if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
            return value
        value = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[contact redacted]', value)
        return re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[credential redacted]', value)
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items()
                if not re.search(r'^(password|cookie|token|cipher|credential|authorization|contact)$', key, re.I)}
    return value


def font(size):
    for candidate in ('/System/Library/Fonts/STHeiti Light.ttc', '/System/Library/Fonts/PingFang.ttc',
                      '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def synthetic_image(index):
    colors = [('#e8eff3', '#2d5668'), ('#f1eadf', '#755d45'), ('#e8eee6', '#365b46'), ('#e5e6eb', '#4c4665')]
    bg, accent = colors[index]
    image = Image.new('RGB', (720, 1280), bg)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((35, 35, 685, 1245), 25, outline=accent, width=3)
    draw.text((62, 70), '全链路验收 · 合成示意', font=font(36), fill=accent)
    draw.text((62, 126), '非真实房源｜仅软件功能验证', font=font(25), fill=accent)
    if index == 0:
        draw.rectangle((65, 245, 655, 760), fill='#b8d9e7', outline=accent, width=5)
        for x in (260, 460): draw.line((x, 245, x, 760), fill=accent, width=7)
        draw.polygon([(65, 600), (655, 550), (655, 760), (65, 760)], fill='#609ab4')
        draw.rounded_rectangle((110, 800, 610, 1000), 24, fill='#c5b8a4', outline=accent, width=4)
        title, detail = '江景客厅 · 低楼层', '380㎡四房｜报价3200万元'
    elif index == 1:
        for box, label in [((80, 260, 350, 560), '卧室一'), ((370, 260, 640, 560), '卧室二'),
                           ((80, 580, 350, 830), '卧室三'), ((370, 580, 640, 830), '卧室四'),
                           ((80, 850, 640, 1030), '客餐厅与厨房')]:
            draw.rectangle(box, fill='#fffaf2', outline=accent, width=6)
            draw.text((box[0]+25, box[1]+40), label, font=font(30), fill=accent)
        title, detail = '四房布局 · 家庭空间', '380㎡｜低楼层住宅示意'
    elif index == 2:
        draw.rectangle((65, 260, 655, 1000), fill='#c4d4bc', outline=accent, width=4)
        draw.polygon([(70, 430), (650, 380), (650, 570), (70, 620)], fill='#91c8d7')
        for x, y in ((140, 740), (330, 800), (535, 700)):
            draw.rectangle((x-9, y, x+9, y+120), fill='#795f49')
            draw.ellipse((x-65, y-90, x+65, y+40), fill='#608a65')
        title, detail = '安静环境 · 江景步道', '与客户偏好匹配的合成社区示意'
    else:
        for row in range(5):
            for col in range(4):
                x, y = 100+col*140, 270+row*135
                draw.rectangle((x, y, x+95, y+90), fill='#bbb7cb', outline=accent, width=3)
        title, detail = '高楼层办公室 · 无关素材', '办公用途｜不匹配住宅客户需求'
    draw.text((62, 1080), title, font=font(32), fill=accent)
    draw.text((62, 1140), detail, font=font(24), fill=accent)
    output = BytesIO(); image.save(output, format='PNG')
    return output.getvalue()


def synthetic_audio():
    output = BytesIO()
    rate = 24000
    pcm = array('h')
    chords = [(261.63, 329.63, 392.00), (220.00, 261.63, 329.63), (174.61, 220.00, 261.63), (196.00, 246.94, 293.66)]
    for i in range(rate * 20):
        t = i / rate
        notes = chords[min(3, int(t // 5))]
        envelope = min(1., t / .8, (20.-t) / 1.2)
        pulse = .65 + .35*math.cos(2*math.pi*(t % 1.25)/1.25)
        sample = int(1600*max(0., envelope)*pulse*sum(math.sin(2*math.pi*f*t) for f in notes)/3)
        pcm.extend((sample, sample))
    if sys.byteorder != 'little': pcm.byteswap()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(2); writer.setsampwidth(2); writer.setframerate(rate); writer.writeframes(pcm.tobytes())
    return output.getvalue()


class ChainAcceptance:
    def __init__(self, client):
        self.client = client
        self.state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {'started_at': stamp(), 'checks': {}, 'attempts': []}
        self.state.setdefault('prompt', PROMPT)
        self.state.setdefault('attempt_index', 1)
        self.state.setdefault('project_name', PROJECT_NAME if self.state['attempt_index']==1 else PROJECT_NAME+f' / v{self.state["attempt_index"]}')
        self.report = {'started_at': self.state['started_at'], 'mode': 'single conversation → actual Pi/Bailian → native local video → Asset archive',
            'synthetic_only': True, 'paid_media_generation': False, 'external_publication': False, 'external_contact': False,
            'checks': self.state.get('checks', {}), 'prompt': self.state['prompt'], 'attempts': self.state.get('attempts', [])}
        self.actor = None
        self.last_phases = {}

    @property
    def project_name(self):
        return self.state['project_name']

    def new_attempt(self):
        previous = dict(self.state)
        index = previous.get('attempt_index', 1)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_path = OUTPUT_DIR/f'attempt-{index:02d}-report.json'
        prior_report=json.loads(REPORT_PATH.read_text()) if REPORT_PATH.exists() else dict(self.report)
        prior_artifacts=prior_report.get('artifacts') or {}
        destination_root=OUTPUT_DIR/f'attempt-{index:02d}-artifacts'
        def preserve_artifact(value):
            if not isinstance(value,str): return value
            source=Path(value).resolve()
            if not source.is_file() or not source.is_relative_to(OUTPUT_DIR.resolve()): return value
            destination=destination_root/source.relative_to(OUTPUT_DIR.resolve())
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,destination)
            return str(destination.resolve())
        for key in ('video_path','contact_sheet_path'):
            if prior_artifacts.get(key):prior_artifacts[key]=preserve_artifact(prior_artifacts[key])
        if prior_artifacts.get('frame_paths'):prior_artifacts['frame_paths']=[preserve_artifact(path) for path in prior_artifacts['frame_paths']]
        snapshot_path.write_text(json.dumps(safe(prior_report), ensure_ascii=False, indent=2)+'\n')
        attempt = {key:previous.get(key) for key in ('attempt_index','project_id','conversation_id','initial_job_id','started_at','expected_fixture')}
        attempt.update(report_snapshot=str(snapshot_path.resolve()), saved_at=stamp(), prior_status=self.report.get('status'))
        attempt.update(prior_status=prior_report.get('status'),reason=prior_report.get('reason'))
        attempts=[*previous.get('attempts', []), attempt]
        self.state={'started_at':stamp(),'attempt_index':index+1,'project_name':PROJECT_NAME+f' / v{index+1}',
            'prompt':PROMPT,'checks':{},'attempts':attempts}
        self.report={'started_at':self.state['started_at'],'mode':'single conversation → actual Pi/Bailian → native local video → Asset archive',
            'synthetic_only':True,'paid_media_generation':False,'external_publication':False,'external_contact':False,
            'checks':{},'prompt':PROMPT,'attempts':attempts}
        self.last_phases={}
        self.save('new_attempt_ready')

    def save(self, status=None):
        self.state['updated_at'] = stamp()
        if status: self.report['status'] = status
        self.report.update(updated_at=stamp(), checks=self.state['checks'], attempts=self.state.get('attempts', []),
            project_id=self.state.get('project_id'), conversation_id=self.state.get('conversation_id'), initial_job_id=self.state.get('initial_job_id'),
            expected_fixture=self.state.get('expected_fixture', {}), attempt_index=self.state.get('attempt_index'), project_name=self.project_name)
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        STATE_PATH.write_text(json.dumps(self.state, ensure_ascii=False, indent=2)+'\n'); STATE_PATH.chmod(0o600)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(safe(self.report), ensure_ascii=False, indent=2)+'\n')

    def db(self):
        db = SessionLocal(); context_for_user(db, self.actor['user'], self.actor['tenant']); return db

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        if response.is_error: raise AcceptanceFailure(f'Local API HTTP {response.status_code} at {path.split("?")[0]}')
        return response.json()

    def login(self):
        password = os.environ.get('HOUSIGNAL_ACCEPTANCE_PASSWORD', settings.bootstrap_password)
        require(isinstance(password, str) and bool(password.strip()),
                'Configure HOUSIGNAL_ACCEPTANCE_PASSWORD or BOOTSTRAP_PASSWORD before acceptance login')
        auth = self.request('POST', '/api/auth/login', json={
            'email': os.environ.get('HOUSIGNAL_ACCEPTANCE_EMAIL', settings.bootstrap_email or 'owner@housignal.local'),
            'password': password})
        self.actor = {'user': auth['user']['id'], 'tenant': auth['organization']['id']}
        status = self.request('GET', '/api/model/status')
        require(status.get('configured') and status.get('agent_engine', {}).get('available'), 'Configured Pi/Bailian is unavailable')
        self.report['model_status'] = status
        self.budget()

    def budget(self):
        if not self.actor: return
        with self.db() as db:
            today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            used = db.scalar(select(func.coalesce(func.sum(ModelUsage.input_tokens+ModelUsage.output_tokens), 0)).where(
                ModelUsage.tenant_id == self.actor['tenant'], ModelUsage.created_at >= today,
                ~(ModelUsage.model.startswith('local:') | ModelUsage.model.startswith('ppu:')))) or 0
        limit = settings.development_token_budget if settings.app_env == 'development' else 100000
        self.report['budget'] = {'recorded_at': stamp(), 'tenant_recorded_tokens_today': used,
            'configured_limit': limit, 'remaining': max(0, limit-used), 'budget_raised': False}

    def prepare(self):
        with self.db() as db:
            ctx = context_for_user(db, self.actor['user'], self.actor['tenant'])
            if self.state.get('project_id'):
                project = require_project(db, ctx, self.state['project_id'])
                require(project.is_test and project.name == self.project_name, 'Saved project is not this isolated synthetic fixture')
            else:
                project = Property(tenant_id=ctx.tenant_id, name=self.project_name, city='上海', property_type='合成住宅',
                    owner_name='合成验收业主', audience='仅用于软件流程验收', sales_owner_name=ctx.name, channel='本地验收',
                    is_test=True, marketing_confirmed=True, marketing_evidence='脚本自制合成示意，仅授权本项目软件测试，不是真实房源或对外营销授权')
                db.add(project); db.flush()
                for key, label, value, unit in [('price','报价','3200','万元'), ('area','建筑面积','380','㎡'),
                    ('layout','户型','四房两厅',''), ('floor','楼层','低楼层',''), ('view','景观','江景',''),
                    ('quiet','环境','安静',''), ('width','客厅面宽','8.5','m'), ('kitchen','厨房与空间关系','厨房与餐厅相邻','')]:
                    db.add(Fact(tenant_id=ctx.tenant_id, property_id=project.id, semantic_key=key, label=label,
                        value=value, unit=unit, source='全链路合成验收事实，非真实房源', status='verified', required=key in ('area','width','layout','kitchen')))
                db.commit(); self.state['project_id'] = project.id; self.save('preparing')
            if not self.state.get('fixture_asset_ids'):
                rows = [
                    ('全链路验收_江景客厅.png', ['江景','低楼层','四房','住宅'], '380平方米四房低楼层江景客厅，合成住宅示意，环境安静'),
                    ('全链路验收_四房布局.png', ['四房','380平方米','低楼层','住宅'], '380平方米四房两厅家庭空间，合成住宅平面示意'),
                    ('全链路验收_安静社区.png', ['安静','江景','住宅','配套'], '安静江景社区步道，当前合成住宅配套示意'),
                    ('全链路验收_高楼层办公室.png', ['高楼层','办公','办公室','无关素材'], '高楼层办公室示意，不是住宅图片，不符合本客户需求'),
                ]
                ids = []
                for index, (name, tags, description) in enumerate(rows):
                    raw = synthetic_image(index)
                    key = f'{ctx.tenant_id}/{project.id}/secretary-chain-{uuid4().hex}.png'
                    path = storage_path(key); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700); path.write_bytes(raw); path.chmod(0o600)
                    asset = Asset(tenant_id=ctx.tenant_id, property_id=project.id, name=name, kind='image', storage_key=key,
                        mime_type='image/png', sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw), rights='authorized',
                        rights_evidence='验收脚本原创图形，仅用于本项目软件测试', origin_type='test', metadata_json={
                            'width':720, 'height':1280, 'tags':tags, 'description':description, 'model_processing_authorized':False,
                            'user_metadata':{'synthetic_only':True, 'property_use':'住宅' if index < 3 else '办公'}})
                    db.add(asset); db.flush(); ids.append(asset.id)
                raw = synthetic_audio(); key = f'{ctx.tenant_id}/{project.id}/secretary-chain-{uuid4().hex}.wav'
                path = storage_path(key); path.write_bytes(raw); path.chmod(0o600)
                audio = Asset(tenant_id=ctx.tenant_id, property_id=project.id, name='全链路验收原创轻配乐.wav', kind='audio',
                    storage_key=key, mime_type='audio/wav', sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw), rights='authorized',
                    rights_evidence='脚本原创正弦和弦音轨，仅用于本项目软件测试，无第三方版权材料', origin_type='test', metadata_json={
                        'tags':['原创配乐','合成音轨'], 'description':'20秒本地原创轻配乐，无第三方版权音源',
                        'duration':20, 'model_processing_authorized':False, 'user_metadata':{'synthetic_only':True}})
                db.add(audio); db.commit()
                self.state['fixture_asset_ids'] = ids; self.state['audio_asset_id'] = audio.id
                self.state['expected_fixture'] = {'project_name':self.project_name, 'customer_name':CUSTOMER_NAME,
                    'matching_image_ids':ids[:3], 'irrelevant_image_id':ids[3], 'audio_asset_id':audio.id,
                    'price_wan':3200, 'area_sqm':380, 'layout':'四房', 'floor':'低楼层', 'view':'江景', 'quiet':'安静',
                    'customer_budget_wan':3500, 'customer_area_range_sqm':[300,450], 'target_seconds':20, 'width':720, 'height':1280,
                    'archive_tags':['江景','客户定向','全链路验收']}
                self.save('preparing')
        if not self.state.get('conversation_id'):
            conversation = self.request('POST', f'/api/projects/{self.state["project_id"]}/conversations', json={'title':'客户定向视频全链路验收'})
            self.state['conversation_id'] = conversation['id']
        self.state.setdefault('prepared_at', stamp())
        self.state['checks']['synthetic_fixture'] = {'status':'passed', 'authorized_test_images':3, 'irrelevant_control_images':1, 'original_audio_seconds':20}
        self.save('prepared')
        print('fixture: prepared; no model/production request submitted', flush=True)

    def adopt(self):
        with self.db() as db:
            messages = db.scalars(select(ChatMessage).where(ChatMessage.tenant_id == self.actor['tenant'],
                ChatMessage.property_id == self.state['project_id'], ChatMessage.role == 'user').order_by(ChatMessage.created_at.desc()).limit(50)).all()
            message = next((m for m in messages if m.content.strip() == self.state['prompt'].strip() and m.job_id), None)
            if not message: return False
            job = db.get(Job, message.job_id)
            if not job or job.user_id != self.actor['user']: return False
            self.state.update(initial_job_id=job.id, conversation_id=message.conversation_id)
        self.save('observing'); return True

    def submit_once(self, observe_only=False):
        if self.state.get('initial_job_id') or self.adopt(): return
        if observe_only:
            deadline = time.monotonic()+60
            while time.monotonic()<deadline:
                if self.adopt(): return
                time.sleep(1)
            raise AcceptanceFailure('No exact fixture instruction found in the app; nothing was submitted by this observer')
        require(self.report['budget']['remaining'] >= 15000, 'Existing model token budget is too low for the full chain; budget was not increased')
        self.state['submission_attempted_at'] = stamp(); self.save('submitting')
        try:
            result = self.request('POST', f'/api/conversations/{self.state["conversation_id"]}/messages', json={'text':self.state['prompt']})
            self.state['initial_job_id'] = result['job_id']; self.save('observing')
        except httpx.TimeoutException:
            # A blocked HTTP request can commit after the client disconnects.
            # Never blindly submit a second business instruction.
            until = time.monotonic()+60
            while time.monotonic()<until:
                if self.adopt(): return
                time.sleep(2)
            raise AcceptanceFailure('Initial HTTP submission timed out; no duplicate was sent. Re-run --observe to adopt any later commit') from None

    def snapshot(self):
        with self.db() as db:
            pid = self.state['project_id']
            initial = db.get(Job, self.state['initial_job_id']) if self.state.get('initial_job_id') else None
            cutoff = initial.created_at if initial else None
            job_query = select(Job).where(Job.tenant_id == self.actor['tenant'], Job.property_id == pid, Job.user_id == self.actor['user'])
            if cutoff is not None: job_query = job_query.where(Job.created_at >= cutoff)
            jobs = db.scalars(job_query.order_by(Job.created_at)).all()
            calls = db.scalars(select(ToolCall).where(ToolCall.tenant_id == self.actor['tenant'], ToolCall.property_id == pid,
                ToolCall.conversation_id == self.state['conversation_id'], *([ToolCall.created_at >= cutoff] if cutoff is not None else [])).order_by(ToolCall.created_at)).all()
            leads = db.scalars(select(Lead).where(Lead.tenant_id == self.actor['tenant'], Lead.property_id == pid)).all()
            profiles = db.scalars(select(CustomerProfile).where(CustomerProfile.tenant_id == self.actor['tenant'], CustomerProfile.property_id == pid)).all()
            assets = db.scalars(select(Asset).where(Asset.tenant_id == self.actor['tenant'], Asset.property_id == pid)).all()
            messages = db.scalars(select(ChatMessage).where(ChatMessage.tenant_id == self.actor['tenant'], ChatMessage.property_id == pid,
                ChatMessage.conversation_id == self.state['conversation_id']).order_by(ChatMessage.created_at)).all()
            return {'jobs':[{'id':j.id,'kind':j.kind,'status':j.status,'phase':j.phase,'error':j.error,
                'payload':{k:(j.payload or {}).get(k) for k in ('skill_id','skill_name','asset_ids','parameters','depends_on_job_id','workflow_parent_job_id','workflow_depth','conversation_id')},
                'result':j.result or {}} for j in jobs],
                'calls':[{'id':c.id,'job_id':c.job_id,'name':c.name,'arguments':c.arguments or {},'sources':c.result_refs or []} for c in calls],
                'leads':[{'id':l.id,'name':l.name,'stage':l.stage,'need':l.need} for l in leads],
                'profiles':[{'lead_id':p.lead_id,'details':p.details,'provenance':p.provenance} for p in profiles],
                'assets':[{'id':a.id,'name':a.name,'kind':a.kind,'storage_key':a.storage_key,'sha256':a.sha256,'byte_size':a.byte_size,
                    'rights':a.rights,'origin_type':a.origin_type,'metadata_json':a.metadata_json or {}} for a in assets],
                'messages':[{'id':m.id,'job_id':m.job_id,'role':m.role,'status':m.status,'content':m.content} for m in messages]}

    def observe(self):
        deadline = time.monotonic()+1200
        quiet_since = None
        while time.monotonic()<deadline:
            snapshot = self.snapshot(); self.report['observed'] = snapshot
            jobs = snapshot['jobs']; initial = next((j for j in jobs if j['id'] == self.state['initial_job_id']), None)
            require(initial is not None, 'Initial chat job is missing from the scoped project')
            for job in jobs:
                phase = (job['status'], job['phase'])
                if self.last_phases.get(job['id']) != phase:
                    print(f'{job["kind"]}: {safe(job["status"])}; {safe(job["phase"] or "waiting")}', flush=True)
                    self.last_phases[job['id']] = phase
            failed = [job for job in jobs if job['status'] in ('failed','cancelled')]
            if failed:
                self.save('failed')
                raise AcceptanceFailure('Conversation/production workflow failed: '+safe(failed[-1]['error'] or failed[-1]['status']))
            active = [job for job in jobs if job['status'] not in TERMINAL]
            archived = [a for a in snapshot['assets'] if a['kind']=='video']
            children = [j for j in jobs if j['kind']=='chat' and j['payload'].get('depends_on_job_id')]
            if archived and children and not active:
                self.save('verifying'); return snapshot
            if initial['status']=='succeeded' and not active:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic()-quiet_since>5:
                    self.save('failed')
                    raise AcceptanceFailure('The conversation stopped before automatic production, continuation and video archive all completed')
            else: quiet_since = None
            self.save('observing'); time.sleep(1)
        raise AcceptanceFailure('Workflow is still pending; state preserved for --observe, no duplicate instruction sent')

    @staticmethod
    def metadata_paths(value, expected, path=''):
        if value == expected: return [path]
        if isinstance(value, dict): return [found for key,item in value.items() for found in ChainAcceptance.metadata_paths(item, expected, path+'.'+key)]
        if isinstance(value, list): return [found for index,item in enumerate(value) for found in ChainAcceptance.metadata_paths(item, expected, path+f'[{index}]')]
        return []

    def verify_business(self, snapshot):
        require(len(snapshot['leads'])==1, 'Exactly one persistent synthetic customer must exist; CRM must not be duplicated by workflow children')
        lead = snapshot['leads'][0]; profile = next((p for p in snapshot['profiles'] if p['lead_id']==lead['id']), None)
        require(profile is not None, 'Persistent customer profile is missing')
        details = profile['details']; combined = ' '.join(str(v) for v in details.values())
        require('3500' in details.get('budget','') and re.search(r'(?:四|4)(?:房|室)', combined) and all(v in combined for v in ('300','450','低楼层','江景','安静')), 'Persistent customer profile does not match the original business request')
        calls = snapshot['calls']; match = [c for c in calls if c['name']=='match_customer_resources']
        require(match and any(c['arguments'].get('lead_id')==lead['id'] for c in match), 'Agent did not actually match resources for this saved customer')
        matched_source_assets = {source.get('id') for call in match for source in call['sources'] if source.get('kind')=='asset'}
        require(set(self.state['expected_fixture']['matching_image_ids']).issubset(matched_source_assets), 'Matched source receipts do not cover the selected authorized images')
        require(any(source.get('kind')=='fact' for call in match for source in call['sources']), 'Matching did not cite current verified property facts')
        production = [j for j in snapshot['jobs'] if j['kind']=='content_skill' and j['payload'].get('skill_name') in {'auto-short-video','slideshow-video','video-production'}]
        require(len(production)==1 and production[0]['status']=='succeeded', 'Expected one actual successful native auto-short-video job')
        producer = production[0]; selected = producer['payload'].get('asset_ids') or []
        fixture = self.state['expected_fixture']
        require(set(fixture['matching_image_ids']).issubset(selected), 'Video did not use all three matched authorized residential fixtures')
        require(fixture['irrelevant_image_id'] not in selected, 'Irrelevant office image leaked into the customer-targeted video')
        require(fixture['audio_asset_id'] in selected, 'Original authorized audio was not used')
        require((producer['result'] or {}).get('execution_engine')=='housignal_native', 'Video job was not actually executed by native composition')
        require((producer['result'] or {}).get('publication_state')=='not_published', 'Unexpected publication state')
        children = [j for j in snapshot['jobs'] if j['kind']=='chat' and j['payload'].get('depends_on_job_id')==producer['id']]
        require(children and all(j['status']=='succeeded' for j in children), 'Automatic conversation continuation did not complete after real production')
        require(any(j['payload'].get('workflow_parent_job_id')==self.state['initial_job_id'] for j in children), 'Child workflow is not linked to the original conversation task')
        archive = [c for c in calls if c['name']=='archive_production_artifacts' and c['job_id'] in {j['id'] for j in children}]
        require(archive, 'Workflow child never actually called archive_production_artifacts')
        videos = [a for a in snapshot['assets'] if a['kind']=='video']
        require(len(videos)==1, 'Expected one final archived video Asset')
        video = videos[0]; metadata = video['metadata_json']
        lead_paths = self.metadata_paths(metadata, lead['id']); producer_paths = self.metadata_paths(metadata, producer['id'])
        require(metadata.get('lead_id')==lead['id'], 'Archived video lacks a real customer linkage')
        require(metadata.get('source_job_id')==producer['id'], 'Archived video lacks source production-job provenance')
        require(set(selected)==set(metadata.get('source_asset_ids', [])), 'Archived provenance does not retain the actual selected input assets')
        require(metadata.get('source_sha256')==video['sha256'], 'Archived source hash does not match its final file')
        require(video['origin_type']=='test', 'Archived synthetic video must retain test origin')
        require(set(fixture['archive_tags']).issubset(metadata.get('tags',[])), 'Archived video tags are incomplete')
        require(video['rights']=='authorized', 'Archived video did not retain valid input-derived authorization')
        self.state.update(lead_id=lead['id'], production_job_id=producer['id'], video_asset_id=video['id'])
        self.state['checks'].update({
            'customer_saved_without_duplicates':{'status':'passed','lead_id':lead['id'],'profile':details},
            'actual_customer_resource_match':{'status':'passed','calls':match,'selected_image_ids':fixture['matching_image_ids'],'irrelevant_excluded':True},
            'actual_native_video_job':{'status':'passed','job_id':producer['id'],'skill_id':producer['payload']['skill_id'],'selected_asset_ids':selected,'paid_media':False},
            'automatic_workflow_and_archive':{'status':'passed','child_job_ids':[j['id'] for j in children],'archive_call_ids':[c['id'] for c in archive],
                'asset_id':video['id'],'lead_link_paths':lead_paths,'source_job_paths':producer_paths,'metadata':metadata,'origin_type':video['origin_type']}})
        self.save('verifying'); return producer, video

    def run_command(self, arguments, *, binary=False, timeout=240):
        result = subprocess.run(arguments, capture_output=True, text=not binary, timeout=timeout)
        require(result.returncode==0, 'Local media verification failed: '+Path(arguments[0]).name)
        return result.stdout

    def verify_media(self, producer, video):
        path = storage_path(video['storage_key'])
        require(path.is_file() and path.stat().st_size==video['byte_size'], 'Archived video file is missing or has changed size')
        raw = path.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
        require(digest==video['sha256'], 'Archived video SHA256 does not match its Asset record')
        url = f'/api/projects/{self.state["project_id"]}/assets/{video["id"]}/file'
        downloaded = self.client.get(url); require(downloaded.status_code==200 and hashlib.sha256(downloaded.content).hexdigest()==digest, 'Authenticated Asset download does not match final video')
        anonymous = httpx.get('http://127.0.0.1:8017'+url, timeout=30)
        require(anonymous.status_code==401, 'Final private video unexpectedly permits anonymous download')
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUTPUT_DIR/'final.mp4'; target.write_bytes(raw)
        probe = json.loads(self.run_command([settings.ffprobe_binary,'-v','error','-show_entries','format=duration:stream=codec_type,codec_name,width,height,channels,sample_rate','-of','json',str(target)]))
        video_stream = next((s for s in probe['streams'] if s['codec_type']=='video'), {})
        audio_stream = next((s for s in probe['streams'] if s['codec_type']=='audio'), {})
        duration = float(probe['format']['duration'])
        require(abs(duration-20)<=.25, 'Final video duration is not the requested 20 seconds')
        require(video_stream.get('width')==720 and video_stream.get('height')==1280 and video_stream.get('codec_name')=='h264', 'Final video resolution/codec is incorrect')
        require(audio_stream.get('codec_name')=='aac' and audio_stream.get('channels')==2, 'Final video audio codec/channels are incorrect')
        self.run_command([settings.ffmpeg_binary,'-nostdin','-v','error','-i',str(target),'-f','null','-'])
        pcm = self.run_command([settings.ffmpeg_binary,'-nostdin','-v','error','-i',str(target),'-map','0:a:0','-ac','1','-ar','16000','-f','s16le','pipe:1'],binary=True)
        samples = array('h'); samples.frombytes(pcm)
        if sys.byteorder!='little': samples.byteswap()
        rms = math.sqrt(sum(sample*sample for sample in samples)/max(1,len(samples)))
        require(rms>10 and max(abs(sample) for sample in samples)>15, 'Final audio is silent instead of the original authorized background sound')
        caption_receipt = next((a for a in (producer['result'].get('artifacts') or []) if a.get('name')=='caption-cues.json'), None)
        require(caption_receipt is not None, 'Real burned-in Chinese caption manifest is missing')
        caption_response = self.client.get(caption_receipt['url']); require(caption_response.status_code==200, 'Caption manifest is not retrievable with authorization')
        captions = caption_response.json(); cues = captions.get('cues') or []
        require(len(cues)>=3 and all(re.search(r'[\u4e00-\u9fff]', str(cue.get('text',''))) for cue in cues), 'Caption cues do not contain three real Chinese captions')
        require(captions.get('transition')=='none', 'Requested exact-duration direct cuts were not used')
        frame_dir = OUTPUT_DIR/'frames'; frame_dir.mkdir(exist_ok=True)
        frame_paths = []
        for index, at in enumerate((1.,4.,8.,12.,16.,19.)):
            frame = frame_dir/f'frame-{index+1:02d}-{int(at):02d}s.png'
            self.run_command([settings.ffmpeg_binary,'-nostdin','-v','error','-y','-ss',str(at),'-i',str(target),'-frames:v','1',str(frame)])
            frame_paths.append(frame)
        sheet = Image.new('RGB',(720,944),'#f5f5f2'); draw = ImageDraw.Draw(sheet)
        for index, frame in enumerate(frame_paths):
            with Image.open(frame) as image: thumb=image.convert('RGB').resize((240,426))
            x,y=(index%3)*240,(index//3)*472
            draw.text((x+8,y+8),f'{(1,4,8,12,16,19)[index]}秒 · 实际成片采样',font=font(17),fill='#24323a')
            sheet.paste(thumb,(x,y+36))
        sheet_path = OUTPUT_DIR/'contact-sheet.png'; sheet.save(sheet_path)
        self.report['artifacts']={'video_path':str(target.resolve()),'contact_sheet_path':str(sheet_path.resolve()),'frame_paths':[str(p.resolve()) for p in frame_paths],
            'authenticated_asset_url':url,'sha256':digest,'byte_size':len(raw)}
        self.state['checks'].update({
            'actual_video_file_and_private_download':{'status':'passed','sha256':digest,'byte_size':len(raw),'authenticated_download':True,'anonymous_status':anonymous.status_code},
            'media_full_decode_and_audio':{'status':'passed','probe':probe,'duration_seconds':duration,'full_decode':True,'audio_rms_pcm16':round(rms,3)},
            'burned_chinese_caption_manifest':{'status':'passed','cues':cues,'caption_layer_policy':captions.get('caption_layer_policy')},
            'sampled_frame_visual_review':{'status':'pending','contact_sheet_path':str(sheet_path.resolve()),'note':'Agent must open actual frames before marking this check passed'}})
        self.budget(); self.save('automated_passed_visual_review_pending')
        print('video: actual file, private download, SHA256, full decode, 20s 720x1280 H.264/AAC and non-silent audio passed',flush=True)
        print('visual_review: pending; actual contact sheet saved',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare-only',action='store_true')
    mode.add_argument('--observe',action='store_true')
    mode.add_argument('--live',action='store_true')
    parser.add_argument('--new-attempt',action='store_true',help='preserve the previous report and create a fresh isolated fixture; only with --prepare-only')
    args=parser.parse_args()
    if args.new_attempt and not args.prepare_only: parser.error('--new-attempt requires --prepare-only; it never sends a chat')
    run=None
    try:
        with httpx.Client(base_url='http://127.0.0.1:8017',headers={'Origin':'http://127.0.0.1:8017'},timeout=45) as client:
            run=ChainAcceptance(client)
            if args.new_attempt: run.new_attempt()
            run.login()
            if args.observe:
                require(run.state.get('project_id') and run.state.get('fixture_asset_ids') and run.state.get('conversation_id'), 'Run authorized --prepare-only before observing the app')
            else:run.prepare()
            if args.prepare_only:
                print('prepared: '+run.state['project_id']+'; conversation '+run.state['conversation_id'],flush=True)
                return 0
            run.submit_once(observe_only=args.observe)
            snapshot=run.observe();producer,video=run.verify_business(snapshot);run.verify_media(producer,video)
        return 0
    except Exception as error:
        reason=str(error) if isinstance(error,AcceptanceFailure) else type(error).__name__
        if run:
            run.report['reason']=safe(reason)
            if run.actor:
                try:run.budget()
                except Exception:pass
            run.save('failed')
        print('chain_acceptance: failed; '+safe(reason),flush=True)
        return 1


if __name__=='__main__':
    sys.exit(main())
