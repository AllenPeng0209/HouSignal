"""Opt-in live Pi secretary acceptance with isolated, explicitly synthetic data.

Run with PYTHONPATH=apps/api .venv/bin/python scripts/acceptance_secretary_agent.py --live.
No publishing, contacts, paid image/video/audio generation, or real property changes.
The private receipt makes retries reuse the same fixture and queued jobs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageDraw
from sqlalchemy import select

from app.config import settings
from app.core import storage_path
from app.core_models import Asset, Fact, Property
from app.db import SessionLocal
from app.security import context_for_user, decrypt_pii, require_project
from app.agent_models import ChatMessage, ModelUsage, ToolCall
from app.business_models import Inquiry, Lead
from app.secretary_models import CustomerProfile, ViewingAppointment
from app.job_models import Job

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / '.runtime/secretary-agent-acceptance-state.json'
REPORT_PATH = ROOT / 'reports/secretary-agent-20261015.json'
LOCAL_ZONE = ZoneInfo('Asia/Shanghai')
SYNTHETIC_PHONE = '13800009999'


class AcceptanceFailure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise AcceptanceFailure(message)


def safe(value):
    if isinstance(value, str):
        if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
            return value
        value = value.replace(SYNTHETIC_PHONE, '[synthetic contact redacted]')
        return re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[contact redacted]', value)
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items()
                if not re.search(r'^(?:password|cookie|token|cipher|credential|authorization|contact)$', key, re.I)}
    return value


def stamp():
    return datetime.now(timezone.utc).isoformat()


class Acceptance:
    def __init__(self, client):
        self.client = client
        self.state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {
            'started_at': stamp(), 'jobs': {}, 'stages': {}}
        self.report = {
            'started_at': self.state['started_at'], 'updated_at': stamp(),
            'mode': 'live local HTTP → real Pi 1.2.0 → configured Bailian → tenant-scoped PostgreSQL',
            'fixture': 'explicitly synthetic test property, customer, contact and PNG',
            'paid_media_generation': False, 'external_publication': False,
            'checks': self.state.get('stages', {}),
        }
        self.actor = None

    def persist(self):
        self.state['updated_at'] = stamp()
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        STATE_PATH.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + '\n')
        STATE_PATH.chmod(0o600)
        self.report.update(updated_at=stamp(), checks=self.state['stages'], attempts=self.state.get('attempts', []))
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(safe(self.report), ensure_ascii=False, indent=2) + '\n')

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        if response.is_error:
            raise AcceptanceFailure(f'Local API request failed with HTTP {response.status_code} at {path.split("?")[0]}')
        return response.json()

    def db(self):
        db = SessionLocal()
        context_for_user(db, self.actor['user'], self.actor['tenant'])
        return db

    def prepare(self):
        password = os.environ.get('HOUSIGNAL_ACCEPTANCE_PASSWORD', settings.bootstrap_password)
        require(isinstance(password, str) and bool(password.strip()),
                'Configure HOUSIGNAL_ACCEPTANCE_PASSWORD or BOOTSTRAP_PASSWORD before acceptance login')
        auth = self.request('POST', '/api/auth/login', json={
            'email': os.environ.get('HOUSIGNAL_ACCEPTANCE_EMAIL', settings.bootstrap_email or 'owner@housignal.local'),
            'password': password})
        self.actor = {'user': auth['user']['id'], 'tenant': auth['organization']['id']}
        status = self.request('GET', '/api/model/status')
        require(status.get('configured') and status.get('agent_engine', {}).get('available'), 'Configured Bailian/Pi runtime is unavailable')
        require(status['agent_engine'].get('name') == 'pi-agent-core', 'Local application is not running Pi core')
        self.report['model_status'] = status
        with self.db() as db:
            ctx = context_for_user(db, self.actor['user'], self.actor['tenant'])
            if self.state.get('project_id'):
                project = require_project(db, ctx, self.state['project_id'])
                require(project.is_test and project.name.startswith('秘书Agent验收（合成）'), 'Saved fixture is not the isolated synthetic project')
            else:
                project = Property(tenant_id=ctx.tenant_id, name='秘书Agent验收（合成）20261015', city='上海',
                    property_type='软件验收合成住宅', owner_name='合成验收业主', audience='仅软件测试',
                    sales_owner_name=ctx.name, channel='本地验收', is_test=True, marketing_confirmed=False,
                    marketing_evidence='合成软件验收数据，不对应真实房源，不用于对外营销')
                db.add(project); db.flush()
                for key, label, value, unit in [
                    ('area', '建筑面积', '420', '㎡'), ('width', '客厅面宽', '8.5', 'm'),
                    ('layout', '户型', '四房两厅', ''), ('kitchen', '厨房与空间关系', '厨房与餐厅相邻', ''),
                    ('view', '景观与配套', '合成示意江景', '')]:
                    db.add(Fact(tenant_id=ctx.tenant_id, property_id=project.id, semantic_key=key, label=label,
                        value=value, unit=unit, source='合成验收初始资料，非真实房源', status='verified', required=key != 'view'))
                db.commit(); self.state['project_id'] = project.id; self.persist()
            if not self.state.get('asset_id'):
                image = Image.new('RGB', (960, 640), '#eaf2f6')
                draw = ImageDraw.Draw(image)
                draw.rectangle((90, 100, 870, 560), fill='#c3d5df')
                draw.text((120, 145), 'SYNTHETIC SECRETARY ACCEPTANCE - NOT A PROPERTY PHOTO', fill='#213b4a')
                output = BytesIO(); image.save(output, format='PNG'); data = output.getvalue()
                key = f'{ctx.tenant_id}/{project.id}/secretary-acceptance-{uuid4().hex}.png'
                path = storage_path(key); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.write_bytes(data); path.chmod(0o600)
                asset = Asset(tenant_id=ctx.tenant_id, property_id=project.id, name='秘书验收合成素材.png',
                    kind='image', storage_key=key, mime_type='image/png', sha256=hashlib.sha256(data).hexdigest(),
                    byte_size=len(data), rights='authorized', rights_evidence='验收脚本生成，仅用于软件测试', origin_type='test',
                    metadata_json={'width': 960, 'height': 640, 'tags': ['合成验收'], 'description': '合成示意图，非真实房源实拍', 'model_processing_authorized': False})
                db.add(asset); db.commit(); self.state['asset_id'] = asset.id; self.persist()
        if not self.state.get('conversation_id'):
            conversation = self.request('POST', f'/api/projects/{self.state["project_id"]}/conversations', json={'title': '合成秘书 Agent 完整验收'})
            self.state['conversation_id'] = conversation['id']; self.persist()
        self.report['project_id'] = self.state['project_id']
        self.report['conversation_id'] = self.state['conversation_id']
        self.report['asset_id'] = self.state['asset_id']
        self.persist()
        print('fixture: ready; Pi runtime available', flush=True)

    def chat(self, stage, text):
        job_id = self.state['jobs'].get(stage)
        if not job_id:
            submitted = self.request('POST', f'/api/conversations/{self.state["conversation_id"]}/messages', json={'text': text})
            job_id = submitted['job_id']; self.state['jobs'][stage] = job_id; self.persist()
        deadline = time.monotonic() + 360
        last_phase = None
        while time.monotonic() < deadline:
            job = self.request('GET', '/api/jobs/' + job_id)
            phase = job.get('phase')
            if phase != last_phase:
                print(f'{stage}: {job["status"]}; {safe(phase or "waiting")}', flush=True); last_phase = phase
            if job['status'] in ('failed', 'cancelled'):
                raise AcceptanceFailure(f'{stage}: {safe(job.get("error") or job["status"])}')
            if job['status'] == 'succeeded':
                break
            time.sleep(1)
        else:
            raise AcceptanceFailure(f'{stage}: task still running; persisted job can be resumed')
        messages = self.request('GET', f'/api/conversations/{self.state["conversation_id"]}/messages')
        answer = next((m for m in reversed(messages) if m['role'] == 'assistant' and m.get('job_id') == job_id), None)
        require(answer is not None and answer['status'] == 'completed', f'{stage}: no completed assistant answer')
        require(SYNTHETIC_PHONE not in json.dumps(messages, ensure_ascii=False), f'{stage}: contact exposed in conversation')
        with self.db() as db:
            usages = db.scalars(select(ModelUsage).where(ModelUsage.job_id == job_id)).all()
            calls = db.scalars(select(ToolCall).where(ToolCall.job_id == job_id)).all()
            require(usages and sum(u.input_tokens + u.output_tokens for u in usages) > 0, f'{stage}: real model usage receipt missing')
            evidence = {'job_id': job_id, 'answer': answer['content'], 'citations': answer.get('citations', []),
                'actions': [{'name': a['name'], 'status': a['status']} for a in answer.get('actions', [])],
                'tools': [call.name for call in calls], 'model_usage': [{'model': u.model, 'input': u.input_tokens, 'output': u.output_tokens} for u in usages]}
        return evidence

    def lead(self):
        rows = self.request('GET', f'/api/projects/{self.state["project_id"]}/leads')
        matches = [row for row in rows if row['name'] in ('验收彭先生', '彭先生')]
        require(len(matches) == 1, 'Expected one synthetic customer, with no duplicate records')
        self.state['lead_id'] = matches[0]['id']; self.persist()
        return self.request('GET', f'/api/projects/{self.state["project_id"]}/leads/{matches[0]["id"]}')

    def run_stage(self, name, prompt, verify):
        if self.state['stages'].get(name, {}).get('status') == 'passed':
            print(name + ': reused prior acceptance', flush=True); return
        print(name + (': checking persisted job' if self.state['jobs'].get(name) else ': starting live model request'), flush=True)
        evidence = {}
        try:
            evidence = self.chat(name, prompt)
            evidence.update(verify())
            self.state['stages'][name] = {'status': 'passed', 'verified_at': stamp(), **safe(evidence)}
            self.persist(); print(name + ': passed', flush=True)
        except Exception as error:
            reason = str(error) if isinstance(error, AcceptanceFailure) else type(error).__name__
            self.state['stages'][name] = {'status': 'failed', 'verified_at': stamp(), **safe(evidence), 'reason': safe(reason)}
            self.persist(); print(name + ': failed; ' + safe(reason), flush=True)
            raise

    def verify_created(self):
        detail = self.lead(); profile = detail['profile']; viewing = detail['viewings']
        require('制造' in profile['background'] and '3500' in profile['budget'], 'Customer background/budget was not saved')
        require('现金' in profile['financial_situation'] and ('未核验' in profile['financial_situation'] or '自述' in profile['financial_situation']), 'Self-reported finances lost their unverified qualification')
        require('江景' in profile['preferences'] and '安静' in profile['preferences'], 'Customer preferences were not saved')
        require(profile['provenance']['financial_situation']['confidence'] == 'self_reported', 'Finances were incorrectly marked independently verified')
        require(len(viewing) == 1 and viewing[0]['date'] == '2026-10-21', 'Next Wednesday date or appointment count is incorrect')
        require(viewing[0]['starts_at'] is None and viewing[0]['time_window'] in ('下午', 'afternoon'), 'A precise appointment time was invented')
        require(viewing[0]['location'] == '项目会所接待厅' and viewing[0]['status'] == 'pending_details', 'Tentative appointment details are incorrect')
        return {'lead_id': detail['lead']['id'], 'budget': profile['budget'], 'financial_confidence': 'self_reported',
            'viewing': viewing[0], 'missing_exact_time_preserved': True}

    def verify_updated(self):
        detail = self.lead(); profile = detail['profile']; viewing = detail['viewings']
        require('3800' in profile['budget'] and '低楼层' in profile['preferences'], 'Updated budget/preferences were not saved')
        require(len(viewing) == 1 and viewing[0]['date'] == '2026-10-22', 'Original appointment was duplicated or not moved to Thursday')
        require(viewing[0]['starts_at'] and 'T15:30:00+08:00' in viewing[0]['starts_at'], 'Exact viewing time is incorrect')
        require(viewing[0]['location'] == '南门大堂' and viewing[0]['status'] == 'scheduled', 'Confirmed appointment details are incorrect')
        require(any('低楼层' in row['note'] for row in detail['followups']), 'Follow-up note was not recorded')
        return {'budget': profile['budget'], 'preferences': profile['preferences'], 'viewing': viewing[0], 'followup_count': len(detail['followups'])}

    def verify_contact(self):
        detail = self.lead()
        with self.db() as db:
            lead = db.get(Lead, self.state['lead_id']); inquiry = db.get(Inquiry, lead.inquiry_id)
            require(bool(inquiry.contact_cipher) and SYNTHETIC_PHONE not in inquiry.contact_cipher, 'Contact is not encrypted at rest')
            require(decrypt_pii(inquiry.contact_cipher) == SYNTHETIC_PHONE, 'Encrypted synthetic contact does not round trip')
            require(inquiry.consent and inquiry.contactable, 'Explicit contact consent was not saved')
            calls = db.scalars(select(ToolCall).where(ToolCall.conversation_id == self.state['conversation_id'])).all()
            require(SYNTHETIC_PHONE not in json.dumps([call.arguments for call in calls], ensure_ascii=False), 'Raw contact leaked into tool arguments')
            jobs = db.scalars(select(Job).where(Job.property_id == self.state['project_id'])).all()
            require(SYNTHETIC_PHONE not in json.dumps([job.payload for job in jobs], ensure_ascii=False), 'Raw contact leaked into persistent job payload')
        return {'encrypted_at_rest': True, 'authorized_contact_roundtrip': True, 'conversation_redacted': True, 'tool_arguments_redacted': True, 'job_payload_redacted': True}

    def verify_estate(self):
        base = f'/api/projects/{self.state["project_id"]}'
        facts = self.request('GET', base + '/facts')
        area = next(row for row in facts if row['semantic_key'] == 'area')
        require(area['value'] == '430' and area['source'] == '验收业主补充' and area['status'] == 'unverified', 'Fact update/source/pending verification is incorrect')
        asset = next(row for row in self.request('GET', base + '/assets') if row['id'] == self.state['asset_id'])
        tags = asset['metadata_json'].get('tags', [])
        require('江景' in tags and '带看' in tags, 'Synthetic material tags were not updated')
        require('仅供软件测试' in asset['metadata_json'].get('description', ''), 'Synthetic material description was not updated')
        require(asset['origin_type'] == 'test', 'Synthetic material lost its test designation')
        return {'area': area, 'asset': {'id': asset['id'], 'origin_type': asset['origin_type'], 'tags': tags, 'description': asset['metadata_json']['description']}}

    def verify_capabilities(self):
        with self.db() as db:
            calls = db.scalars(select(ToolCall).where(ToolCall.job_id == self.state['jobs']['production_capabilities'])).all()
            require(any(call.name == 'get_production_capabilities' for call in calls), 'Agent did not query actual production capabilities')
            require(not any(call.name.startswith(('submit_', 'run_', 'generate_', 'render_', 'publish_')) for call in calls), 'Capability-only query unexpectedly submitted production')
        return {'actual_capability_query': True, 'production_submission': False, 'local_roi_execution': 'not requested; capability round trip only'}

    def run(self):
        self.prepare()
        self.run_stage('create_customer_and_tentative_viewing',
            '请给验收彭先生建档，背景制造企业经营者、客户自述可现金购房未核验，预算3500万元，喜欢江景安静。下周三下午在项目会所接待厅看楼，具体时间待确认。', self.verify_created)
        self.run_stage('update_customer_reschedule_and_followup',
            '把验收彭先生预算改为3800万元，喜好补充低楼层（保留喜欢江景安静）；把原看楼预约改到下周四15:30，在南门大堂见面；记录跟进：客户要求低楼层、已确认下周四看楼，下一步当天上午再次确认。', self.verify_updated)
        self.run_stage('save_consented_encrypted_contact',
            f'验收彭先生的联系方式为{SYNTHETIC_PHONE}，客户已明确同意后续联系，请保存到CRM；联系方式只用于后续回访，不要在回复中显示号码。', self.verify_contact)
        self.run_stage('update_property_fact_and_material',
            '请把本项目建筑面积资料修改为430平方米，来源写“验收业主补充”，保留待核验状态；把“秘书验收合成素材.png”的标签改为“江景、带看”，说明改为“秘书验收合成示意，仅供软件测试，不代表真实楼盘实拍”。', self.verify_estate)
        self.run_stage('production_capabilities',
            '当前项目有哪些内容生产能力？请查阅真实能力与限制。只查能力，不提交任何生产任务。', self.verify_capabilities)
        self.report['status'] = 'passed'; self.report['completed_at'] = stamp(); self.persist()
        print('acceptance: passed; 5 live model stages; safe report saved', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='run authorized local application and configured Bailian text model')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; no live requests have been sent')
    run = None
    try:
        with httpx.Client(base_url='http://127.0.0.1:8017', headers={'Origin': 'http://127.0.0.1:8017'}, timeout=30) as client:
            run = Acceptance(client); run.run()
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, AcceptanceFailure) else type(error).__name__
        if run:
            run.report['status'] = 'failed'; run.report['reason'] = safe(reason); run.persist()
        print('acceptance: failed; ' + safe(reason), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
