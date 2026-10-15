"""Pass development credentials in memory; scope fixture marking to test records."""
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'apps/api'))
from app.config import settings


def mark_test_record():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.core_models import Asset, Property
    from app.security import context_for_user, require_project
    data = json.load(sys.stdin)
    with Session(create_engine(settings.migration_database_url, hide_parameters=True)) as db:
        context = context_for_user(db, data['user_id'], data['tenant_id'])
        project = require_project(db, context, data['project_id'], ('owner', 'admin'))
        if not project.name.startswith('手机端验收 · 合成测试 '):
            raise RuntimeError('Fixture scope rejected')
        project.is_test = True
        if data.get('asset_id'):
            asset = db.get(Asset, data['asset_id'])
            if asset is None or asset.property_id != project.id or asset.tenant_id != context.tenant_id:
                raise RuntimeError('Asset fixture scope rejected')
            if asset.name != '手机端合成验收示意.jpg':
                raise RuntimeError('Asset fixture name rejected')
            asset.origin_type = 'test'
        db.commit()
    print(json.dumps({'marked': True}))


def fixture_usage():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.agent_models import ModelUsage
    from app.security import context_for_user, require_project
    data = json.load(sys.stdin)
    with Session(create_engine(settings.migration_database_url, hide_parameters=True)) as db:
        context = context_for_user(db, data['user_id'], data['tenant_id'])
        project = require_project(db, context, data['project_id'], ('owner', 'admin'))
        if not project.is_test or not project.name.startswith('手机端验收 · 合成测试 '):
            raise RuntimeError('Usage fixture scope rejected')
        grouped = {}
        for row in db.scalars(select(ModelUsage).where(ModelUsage.property_id == project.id, ModelUsage.tenant_id == context.tenant_id)):
            item = grouped.setdefault(row.job_id, {'job_id': row.job_id, 'response_count': 0, 'input_tokens': 0, 'output_tokens': 0})
            item['response_count'] += 1
            item['input_tokens'] += row.input_tokens
            item['output_tokens'] += row.output_tokens
    print(json.dumps({'by_job': list(grouped.values())}))


def main():
    if '--mark-test-record' in sys.argv:
        mark_test_record()
        return 0
    if '--fixture-usage' in sys.argv:
        fixture_usage()
        return 0
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (960, 640), '#EAF0F9')
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((80, 100, 880, 570), fill='#B8CAE0')
    drawing.rectangle((140, 150, 450, 530), fill='#D5E2F0')
    drawing.rectangle((495, 150, 810, 530), fill='#E7F0FA')
    drawing.text((90, 40), 'SYNTHETIC MOBILE ACCEPTANCE - NOT A PROPERTY PHOTO', fill='#173452')
    buffer = BytesIO()
    image.save(buffer, format='JPEG')
    config = {
        'email': settings.bootstrap_email,
        'password': settings.bootstrap_password,
        'web': os.environ.get('MOBILE_WEB_URL', 'http://127.0.0.1:8087'),
        'api': os.environ.get('MOBILE_API_URL', 'http://127.0.0.1:8018'),
        'testImageBase64': base64.b64encode(buffer.getvalue()).decode(),
        'python': sys.executable,
        'allowOneGenerationRetry': False,
        'disallowNewJobs': True,
    }
    result = subprocess.run(['node', 'scripts/browser-acceptance.mjs'], input=json.dumps(config), text=True, cwd=Path(__file__).resolve().parents[1])
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
