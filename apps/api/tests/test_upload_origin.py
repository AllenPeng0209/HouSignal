"""External AI files retain an honest origin without acquiring model or review rights."""
import asyncio
import hashlib
import io

from fastapi import FastAPI
import httpx
from PIL import Image
import pytest
from sqlalchemy import select

from app import core
from app.core_models import Asset
from app.db import get_db
from app.job_models import Job
from app.security import Context, get_context
from test_business import scenario


def png():
    output = io.BytesIO()
    Image.new('RGB', (4, 3), (80, 100, 120)).save(output, format='PNG')
    return output.getvalue()


def request(scenario, *, origin=None, duplicates=None, role=None):
    db, ctx, *_ = scenario
    if role:
        ctx = Context(user_id=ctx.user_id, tenant_id=ctx.tenant_id, role=role,
                      name=ctx.name, email=ctx.email, project_ids=ctx.project_ids)
    app = FastAPI()
    app.include_router(core.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_context] = lambda: ctx
    files = [('file', ('original-ai-cover.png', png(), 'image/png'))]
    if duplicates is not None:
        files += [('origin_type', (None, value)) for value in duplicates]
    elif origin is not None:
        files.append(('origin_type', (None, origin)))

    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://upload.test') as client:
            response = await client.post('/api/projects/p/assets', files=files)
        return response
    return asyncio.run(send())


def test_explicit_ai_origin_does_not_grant_rights_or_forge_a_native_job(scenario, tmp_path):
    response = request(scenario, origin='ai_generated')
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['origin_type'] == 'ai_generated'
    assert payload['rights'] == 'pending' and payload['rights_evidence'] == ''
    assert payload['metadata_json']['model_processing_authorized'] is False
    assert not {'source_job_id', 'source_artifact_name', 'content_version_id'} & payload['metadata_json'].keys()
    assert payload['sha256'] == hashlib.sha256(png()).hexdigest()
    db = scenario[0]
    asset = db.get(Asset, payload['id'])
    assert asset.origin_type == 'ai_generated'
    assert (tmp_path / asset.storage_key).read_bytes() == png()
    jobs = db.scalars(select(Job)).all()
    assert len(jobs) == 1 and jobs[0].kind == 'extract_asset'
    assert jobs[0].payload == {'asset_id': asset.id}


@pytest.mark.parametrize('origin', [None, 'actual'], ids=['legacy-omitted', 'explicit-normal-upload'])
def test_normal_upload_default_stays_compatible_and_unconfirmed(scenario, origin):
    response = request(scenario, origin=origin)
    assert response.status_code == 200
    payload = response.json()
    assert payload['origin_type'] == 'actual'
    assert payload['rights'] == 'pending'
    assert payload['metadata_json']['model_processing_authorized'] is False
    assert scenario[3].revision == 1
    assert scenario[3].value == '420'


@pytest.mark.parametrize('origin', ['test', 'template', 'reference', 'AI_GENERATED', 'ai_generated ', '0', ''])
def test_unaccepted_origin_is_rejected_before_any_asset_or_job_write(scenario, tmp_path, origin):
    response = request(scenario, origin=origin)
    assert response.status_code == 422
    assert len(scenario[0].scalars(select(Asset)).all()) == 1
    assert not scenario[0].scalars(select(Job)).all()
    assert not list(tmp_path.glob('tenant/p/*'))


def test_repeated_origin_fields_cannot_silently_replace_the_requested_classification(scenario):
    response = request(scenario, duplicates=['ai_generated', 'actual'])
    assert response.status_code == 422
    assert len(scenario[0].scalars(select(Asset)).all()) == 1
    assert not scenario[0].scalars(select(Job)).all()


def test_ai_origin_does_not_expand_existing_upload_roles(scenario):
    response = request(scenario, origin='ai_generated', role='sales')
    assert response.status_code == 403
    assert len(scenario[0].scalars(select(Asset)).all()) == 1
    assert not scenario[0].scalars(select(Job)).all()


def test_openapi_advertises_the_optional_origin_contract_for_safe_new_clients():
    app = FastAPI()
    app.include_router(core.router)
    document = app.openapi()
    ref = document['paths']['/api/projects/{pid}/assets']['post']['requestBody']['content']['multipart/form-data']['schema']['$ref']
    body = document['components']['schemas'][ref.rsplit('/', 1)[1]]
    assert 'origin_type' in body['properties']
    assert body['properties']['origin_type']['enum'] == ['actual', 'ai_generated']
    assert body['properties']['origin_type']['default'] == 'actual'
    assert 'origin_type' not in body['required']
