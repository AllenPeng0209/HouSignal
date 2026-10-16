"""External HTTP/MCP adapters retain OAuth scope and Web data identity."""
import hashlib
import importlib
import importlib.util
import json
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.external_agent_tools import AgentInvocation
from test_external_agent_auth import env as oauth_env, credentials, ORIGIN


@pytest.fixture
def env(oauth_env):
    client, db, auth = oauth_env
    if importlib.util.find_spec('app.external_agent_api'):
        module = importlib.import_module('app.external_agent_api')
        client.app.include_router(module.router)
    else:
        module = None
    from app.operations import router as operations_router
    client.app.include_router(operations_router)
    return client, db, module


def token(client, scopes=None):
    _, value = credentials(client, scopes)
    return {'Authorization': 'Bearer ' + value['access_token']}


def rpc(client, headers, method, params=None, rid=1, **kwargs):
    message = {'jsonrpc': '2.0', 'method': method}
    if params is not None:
        message['params'] = params
    if rid is not ...:
        message['id'] = rid
    return client.post('/mcp', headers=headers, json=message, **kwargs)


def plan_args(key='case-plan'):
    return {'project_id': 'p1', 'idempotency_key': key,
            'plan': {'title': '三代同住先看动线', 'topic_id': 'layout', 'channel': 'B站',
                     'planning': {'target_seconds': 45, 'outline': ['共享空间', '独处空间', '待补镜头']}}}


def revision(client, pid='p1'):
    response = client.get(f'/api/projects/{pid}/agent-changes')
    assert response.status_code == 200, response.text
    assert set(response.json()) == {'revision'}
    assert len(response.json()['revision']) == 64
    return response.json()['revision']


def test_routes_exist_and_anonymous_mcp_never_uses_web_cookie(env):
    client, _, _ = env
    response = rpc(client, {}, 'tools/list')
    assert response.status_code == 401, response.text
    assert response.headers['www-authenticate'].startswith('Bearer resource_metadata="' + ORIGIN)
    assert 'list_projects' not in response.text
    assert client.get('/api/agent/tools').status_code == 401


@pytest.mark.parametrize('version', ['2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25'])
def test_standard_mcp_initialize_tools_ping_and_notifications(env, version):
    client, _, _ = env
    headers = token(client)
    response = rpc(client, headers, 'initialize', {'protocolVersion': version, 'capabilities': {},
        'clientInfo': {'name': 'official-sdk', 'version': '1.0'}})
    assert response.status_code == 200, response.text
    value = response.json()['result']
    assert value['protocolVersion'] == version
    assert value['serverInfo']['name'] == 'louxiang'
    assert 'tools' in value['capabilities']
    assert rpc(client, headers, 'notifications/initialized', rid=...).status_code == 202
    assert rpc(client, headers, 'ping').json()['result'] == {}
    definitions = rpc(client, headers, 'tools/list').json()['result']['tools']
    assert {row['name'] for row in definitions} >= {'list_projects', 'save_plan', 'get_job'}
    result = rpc(client, headers, 'tools/call', {'name': 'list_projects', 'arguments': {}}).json()['result']
    assert result['isError'] is False
    assert result['structuredContent']['projects'][0]['id'] == 'p1'
    assert json.loads(result['content'][0]['text']) == result['structuredContent']
    assert client.get('/mcp', headers=headers).status_code == 405
    assert client.delete('/mcp', headers=headers).status_code == 405


def modern_rpc(client, headers, method, params=None, **overrides):
    params = {'_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                        'io.modelcontextprotocol/clientCapabilities': {},
                        'io.modelcontextprotocol/clientInfo': {'name': 'SDK-v2', 'version': '2'}}} | (params or {})
    headers = headers | {'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': method}
    if method == 'tools/call':
        headers['Mcp-Name'] = params.get('name', '')
    return rpc(client, headers | overrides, method, params)


def test_current_mcp_supports_per_request_discovery_and_complete_results_without_initialize(env):
    client, _, _ = env
    headers = token(client)
    discovered = modern_rpc(client, headers, 'server/discover')
    assert discovered.status_code == 200, discovered.text
    value = discovered.json()['result']
    assert value['resultType'] == 'complete'
    assert '2026-07-28' in value['supportedVersions']
    assert value['_meta']['io.modelcontextprotocol/serverInfo']['name'] == 'louxiang'
    listed = modern_rpc(client, headers, 'tools/list')
    assert listed.json()['result']['resultType'] == 'complete'
    called = modern_rpc(client, headers, 'tools/call', {'name': 'list_projects', 'arguments': {}})
    assert called.json()['result']['resultType'] == 'complete'
    assert called.json()['result']['structuredContent']['projects'][0]['id'] == 'p1'


@pytest.mark.parametrize('override', [{'Mcp-Method': 'tools/call'}, {'MCP-Protocol-Version': '2025-11-25'}])
def test_current_mcp_rejects_request_metadata_header_mismatch(env, override):
    client, _, _ = env
    response = modern_rpc(client, token(client), 'tools/list', **override)
    assert response.status_code == 400
    assert response.json()['error']['code'] == -32020


def test_current_mcp_requires_matching_tool_name_and_provides_supported_versions_on_error(env):
    client, _, _ = env
    headers = token(client)
    response = modern_rpc(client, headers, 'tools/call', {'name': 'list_projects'}, **{'Mcp-Name': 'save_plan'})
    assert response.status_code == 400
    assert response.json()['error']['code'] == -32020
    bad = rpc(client, headers | {'MCP-Protocol-Version': '2040-01-01'}, 'tools/list')
    assert bad.status_code == 400
    assert bad.json()['error']['code'] == -32022
    assert bad.json()['error']['data']['requested'] == '2040-01-01'
    assert '2026-07-28' in bad.json()['error']['data']['supported']


def test_mcp_writes_commit_to_web_and_idempotent_retry_does_not_duplicate(env):
    client, db, _ = env
    headers = token(client)
    before = revision(client)
    first = rpc(client, headers, 'tools/call', {'name': 'save_plan', 'arguments': plan_args()})
    assert first.status_code == 200, first.text
    value = first.json()['result']['structuredContent']
    assert value['receipt']['changed'] is True
    web = client.get('/api/projects/p1/operations/plans')
    assert web.status_code == 200, web.text
    assert web.json()[0]['id'] == value['plan']['id']
    assert web.json()[0]['planning']['outline'] == ['共享空间', '独处空间', '待补镜头']
    changed = revision(client)
    assert changed != before
    again = rpc(client, headers, 'tools/call', {'name': 'save_plan', 'arguments': plan_args()})
    assert again.json()['result']['structuredContent']['receipt']['changed'] is False
    assert revision(client) == changed
    from app.operations_models import ContentPlan
    assert db.scalar(select(func.count()).select_from(ContentPlan)) == 1


def test_business_failure_is_mcp_tool_error_and_transaction_is_rolled_back(env):
    client, db, _ = env
    headers = token(client, ['louxiang:read'])
    response = rpc(client, headers, 'tools/call', {'name': 'save_plan', 'arguments': plan_args()})
    assert response.status_code == 200, response.text
    result = response.json()['result']
    assert result['isError'] is True
    assert result['structuredContent']['error']['status'] == 403
    from app.external_agent_tools import AgentInvocation
    assert db.scalar(select(func.count()).select_from(AgentInvocation)) == 0
    assert rpc(client, headers, 'tools/call', {'name': 'get_project_context', 'arguments': {'project_id': 'p3'}}).json()['result']['structuredContent']['error']['status'] == 404


@pytest.mark.parametrize('method,params,code', [
    ('unknown/private', {}, -32601), ('tools/call', {'name': 5}, -32602),
    ('tools/call', {'name': 'list_projects', 'arguments': []}, -32602),
    ('initialize', {'protocolVersion': 1}, -32602), ('tools/list', [], -32602),
])
def test_protocol_errors_are_jsonrpc(env, method, params, code):
    client, _, _ = env
    response = rpc(client, token(client), method, params)
    assert response.status_code == 200, response.text
    assert response.json()['error']['code'] == code
    assert response.json()['id'] == 1


def test_malformed_oversized_and_untrusted_origin_mcp_requests_are_rejected(env):
    client, _, _ = env
    headers = token(client)
    bad = client.post('/mcp', headers=headers, content='{')
    assert bad.status_code == 400
    assert bad.json()['error']['code'] == -32700
    assert rpc(client, headers, 'tools/call', {'name': 'list_projects', 'arguments': {'text': 'x'*200000}}).status_code == 413
    assert rpc(client, headers | {'Origin': 'https://attacker.invalid'}, 'tools/list').status_code == 403
    assert rpc(client, headers | {'Origin': 'https://chatgpt.com'}, 'tools/list').status_code == 200
    assert rpc(client, headers | {'MCP-Protocol-Version': 'unsupported'}, 'tools/list').status_code == 400


def test_json_cli_tools_use_same_services_and_bearer_scope(env):
    client, _, _ = env
    headers = token(client)
    definitions = client.get('/api/agent/tools', headers=headers)
    assert definitions.status_code == 200
    assert any(t['name'] == 'save_plan' for t in definitions.json()['tools'])
    response = client.post('/api/agent/tools/call', headers=headers, json={'name': 'save_plan', 'arguments': plan_args()})
    assert response.status_code == 200, response.text
    assert response.json()['plan']['id'] == client.get('/api/projects/p1/operations/plans').json()[0]['id']
    assert client.post('/api/agent/tools/call', headers=headers, json={'name': 'get_project_context', 'arguments': {'project_id': 'p3'}}).status_code == 404


def test_official_mcp_sdk_can_initialize_list_and_write_same_web_plan(env):
    # Optional interoperability dependency belongs to test tooling, not the API
    # runtime. This test is also run explicitly with the official SDK installed.
    sdk = pytest.importorskip('mcp')
    if hasattr(sdk, 'Client'):
        pytest.skip('requires official MCP SDK v1 transport tuple')
    import anyio
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    client, _, _ = env
    headers = token(client)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
            base_url=ORIGIN, headers=headers) as http:
            async with streamable_http_client(ORIGIN + '/mcp', http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == 'louxiang'
                    listed = await session.list_tools()
                    assert any(t.name == 'save_plan' for t in listed.tools)
                    saved = await session.call_tool('save_plan', plan_args('official-sdk'))
                    assert saved.isError is False
                    return saved.structuredContent['plan']['id']
    plan_id = anyio.run(check)
    assert client.get('/api/projects/p1/operations/plans').json()[0]['id'] == plan_id


def test_official_mcp_v2_sdk_discovers_and_calls_without_legacy_handshake(env):
    sdk = pytest.importorskip('mcp')
    if not hasattr(sdk, 'Client'):
        pytest.skip('requires official MCP SDK v2')
    import anyio
    import httpx2
    from mcp.client.streamable_http import streamable_http_client
    client, _, _ = env
    headers = token(client)
    seen_methods = []
    async def observe(request):
        if request.method == 'POST' and request.url.path == '/mcp':
            seen_methods.append(json.loads(request.content)['method'])
    async def check():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=client.app),
            base_url=ORIGIN, headers=headers, event_hooks={'request': [observe]}) as http:
            transport = streamable_http_client(ORIGIN + '/mcp', http_client=http)
            async with sdk.Client(transport) as external:
                assert external.server_info.name == 'louxiang'
                listed = await external.list_tools()
                assert any(t.name == 'save_plan' for t in listed.tools)
                saved = await external.call_tool('save_plan', plan_args('official-sdk-v2'))
                assert saved.is_error is False
                return saved.structured_content['plan']['id']
    plan_id = anyio.run(check)
    assert seen_methods[0] == 'server/discover'
    assert 'initialize' not in seen_methods
    assert client.get('/api/projects/p1/operations/plans').json()[0]['id'] == plan_id


def test_revision_stable_on_read_foreign_writes_and_job_changes_but_tracks_own_external_job(env):
    client, db, _ = env
    from app.db import utcnow
    from app.job_models import Job
    headers = token(client, ['louxiang:read', 'louxiang:write', 'louxiang:production'])
    before = revision(client)
    rpc(client, headers, 'tools/call', {'name': 'list_projects', 'arguments': {}})
    assert revision(client) == before
    db.add(Job(id='private-chat', tenant_id='t1', property_id='p1', user_id='u1', kind='agent',
               status='queued', payload={'message': '私聊'}))
    db.add(Job(id='foreign-job', tenant_id='t2', property_id='p3', user_id='u2', kind='content_skill', status='queued',
               payload={'external_agent': {'grant_id': 'not-this-project'}}))
    db.commit()
    assert revision(client) == before
    response = rpc(client, headers, 'tools/call', {'name': 'run_skill', 'arguments': {
        'project_id': 'p1', 'idempotency_key': 'template', 'skill_id': 'native:template-library',
        'input': '查看模板', 'parameters': {'action': 'read'}}})
    assert response.json()['result']['isError'] is False, response.text
    jid = response.json()['result']['structuredContent']['job_id']
    queued = revision(client)
    assert queued != before
    job = db.get(Job, jid)
    job.status = 'succeeded'
    job.updated_at = utcnow() + timedelta(seconds=1)
    db.commit()
    assert revision(client) != queued
    assert client.get('/api/projects/p3/agent-changes').status_code == 404


def test_revision_tracks_recent_tagged_production_after_newer_plan_writes(env):
    client, db, _ = env
    from app.db import utcnow
    from app.job_models import Job
    from app.external_agent_models import AgentOAuthGrant
    token(client)
    grant_id = db.scalar(select(AgentOAuthGrant.id))
    db.add(Job(id='tagged-native', tenant_id='t1', property_id='p1', user_id='u1', kind='content_skill',
        status='queued', payload={'external_agent': {'project_id': 'p1', 'grant_id': grant_id}}))
    db.commit()
    before = revision(client)
    job = db.get(Job, 'tagged-native')
    job.status = 'succeeded'
    job.updated_at = utcnow() + timedelta(seconds=1)
    db.commit()
    assert revision(client) != before


def test_public_downloads_only_serve_allowlisted_existing_distribution_files(env, tmp_path, monkeypatch):
    client, _, module = env
    assert module is not None
    monkeypatch.setattr(module, 'DISTRIBUTION_DIR', tmp_path)
    (tmp_path/'louxiang.pyz').write_bytes(b'public-cli')
    (tmp_path/'secret.env').write_text('private-value')
    response = client.get('/api/agent/downloads/louxiang.pyz')
    assert response.status_code == 200
    assert response.content == b'public-cli'
    for path in ['secret.env', 'manifest.json', '%2e%2e%2fsecret.env', '.runtime/secret.env']:
        response = client.get('/api/agent/downloads/' + path)
        assert response.status_code == 404
        assert 'private-value' not in response.text


def test_artifact_download_checks_current_grant_project_and_canonical_sha(env, tmp_path, monkeypatch):
    client, db, _ = env
    from app import native_skills
    from app.external_agent_models import AgentOAuthGrant
    from app.job_models import Job
    headers = token(client)
    payload = b'original-research-video'
    db.add(Job(id='native-job', tenant_id='t1', property_id='p1', user_id='u1', kind='content_skill', status='succeeded',
        payload={}, result={'artifacts': [{'name': 'research.mp4', 'sha256': hashlib.sha256(payload).hexdigest(),
                                         'mime_type': 'video/mp4'}]}))
    db.commit()
    monkeypatch.setattr(native_skills, '_read_artifact', lambda job, name: payload)
    path = '/api/agent/projects/p1/jobs/native-job/artifacts/research.mp4'
    assert client.get(path).status_code == 401
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert response.content == payload
    assert client.get(path.replace('/p1/', '/p3/'), headers=headers).status_code == 404
    monkeypatch.setattr(native_skills, '_read_artifact', lambda job, name: b'changed')
    assert client.get(path, headers=headers).status_code == 409
    from app.db import utcnow
    db.scalar(select(AgentOAuthGrant)).revoked_at = utcnow()
    db.commit()
    assert client.get(path, headers=headers).status_code == 401


def test_approved_delivery_never_bypasses_the_canonical_release_gate(env, monkeypatch):
    client, _, module = env
    headers = token(client)
    path = '/api/agent/projects/p1/contents/content/delivery'
    calls = []
    def blocked(db, ctx, pid, cid):
        calls.append((ctx.user_id, pid, cid))
        raise HTTPException(409, '没有有效审批')
    monkeypatch.setattr(module.business, 'export_content', blocked)
    assert client.get(path, headers=headers).status_code == 409
    assert calls == [('u1', 'p1', 'content')]


def test_delivery_file_rechecks_release_and_rights_after_metadata_was_issued(env, tmp_path, monkeypatch):
    client, db, _ = env
    from app import business
    from app.business_models import ContentVersion
    from app.config import settings
    from app.core_models import Asset, Fact, Property
    from app.external_agent_auth import authenticate_agent
    headers = token(client)
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    actor = authenticate_agent(db, headers['Authorization'])
    project = db.get(Property, 'p1')
    project.marketing_confirmed = True
    project.marketing_evidence = 'isolated test fixture permission'
    db.add(Fact(id='verified', tenant_id='t1', property_id='p1', semantic_key='area', label='面积', value='504',
                unit='㎡', source='isolated measured fixture', status='verified', required=True, revision=1))
    image = Asset(id='source', tenant_id='t1', property_id='p1', name='空间.jpg', kind='image', mime_type='image/jpeg',
        storage_key='source.jpg', sha256='a'*64, byte_size=3, rights='authorized', rights_evidence='test source permission', origin_type='actual')
    content = ContentVersion(id='approved', tenant_id='t1', property_id='p1', created_by='u1', script='504㎡空间',
        fact_refs=[{'id': 'verified', 'revision': 1}], storyboard=[{'asset_id': 'source', 'duration': 3, 'caption': '504㎡空间'}])
    db.add_all([image, content])
    db.flush()
    payload = b'opaque rendered fixture; media decode has separate coverage'
    (tmp_path/'render.mp4').write_bytes(payload)
    render = Asset(id='render', tenant_id='t1', property_id='p1', name='交付.mp4', kind='video', mime_type='video/mp4',
        storage_key='render.mp4', sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload), rights='authorized',
        rights_evidence='fixture rendering permission', origin_type='actual', metadata_json={'renderer': 'ffmpeg',
            'content_version_id': content.id, 'content_snapshot_sha256': business.content_snapshot_hash(db, content)})
    db.add(render)
    content.render_asset_id = render.id
    db.flush()
    business.approve_content(db, actor.ctx, 'p1', content.id, {'confirmed': True, 'note': 'isolated approval fixture'})
    db.commit()
    response = client.get('/api/agent/projects/p1/contents/approved/delivery', headers=headers)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['delivery_state'] == 'approved_current_release'
    assert value['render_url'] == ORIGIN + '/api/agent/projects/p1/contents/approved/delivery/file'
    assert client.get(value['render_url']).status_code == 401
    downloaded = client.get(value['render_url'], headers=headers)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == payload
    image.rights = 'revoked'
    db.commit()
    assert client.get(value['render_url'], headers=headers).status_code == 409


def native_archive_receipt(env, tmp_path, monkeypatch):
    from app.config import settings
    from app.job_models import Job
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    db = env[1]
    output = tmp_path/'t1'/'p1'/'skill_outputs'/'archive-job'
    output.mkdir(parents=True)
    raw = b'Original project production document'
    (output/'study.txt').write_bytes(raw)
    db.add(Job(id='archive-job', tenant_id='t1', property_id='p1', user_id='u1',
               kind='content_skill', status='succeeded', payload={'asset_ids': []},
               result={'execution_engine': 'housignal_native', 'artifacts': [{
                   'name': 'study.txt', 'sha256': hashlib.sha256(raw).hexdigest(),
                   'byte_size': len(raw), 'mime_type': 'text/plain',
                   'url': '/api/projects/p1/operations/skills/artifacts/archive-job/study.txt'}]}))
    db.commit()
    return raw


def test_mcp_archive_populates_same_web_asset_and_keeps_successful_file_after_later_error(env, tmp_path, monkeypatch):
    from app.core import router as core_router
    client, db, _ = env
    client.app.include_router(core_router)
    raw = native_archive_receipt(env, tmp_path, monkeypatch)
    headers = token(client, ['louxiang:read', 'louxiang:production'])
    before = revision(client)
    args = {'project_id': 'p1', 'job_id': 'archive-job', 'idempotency_key': 'mcp-archive'}
    response = rpc(client, headers, 'tools/call', {'name': 'archive_production_artifacts', 'arguments': args})
    result = response.json()['result']
    assert result['isError'] is False, response.text
    asset = result['structuredContent']['data']['assets'][0]
    web = client.get('/api/projects/p1/assets')
    assert web.status_code == 200
    assert web.json()[0]['id'] == asset['id']
    assert client.get(asset['file_url']).content == raw
    assert asset['metadata_json']['model_processing_authorized'] is False
    assert revision(client) != before
    assert db.info.get('created_private_files', []) == []
    failed = rpc(client, headers, 'tools/call', {'name': 'archive_production_artifacts',
        'arguments': args | {'job_id': 'missing', 'idempotency_key': 'mcp-archive-failed'}})
    assert failed.json()['result']['isError'] is True
    assert client.get(asset['file_url']).content == raw
    repeated = rpc(client, headers, 'tools/call', {'name': 'archive_production_artifacts', 'arguments': args})
    assert repeated.json()['result']['structuredContent']['receipt']['changed'] is False
    assert len(client.get('/api/projects/p1/assets').json()) == 1


def test_archive_commit_failure_rolls_back_receipt_asset_and_copied_file(env, tmp_path, monkeypatch):
    from sqlalchemy import event
    from app.core_models import Asset
    client, db, _ = env
    native_archive_receipt(env, tmp_path, monkeypatch)
    headers = token(client, ['louxiang:read', 'louxiang:production'])
    def reject_outer_commit(session):
        if not session.in_nested_transaction():
            raise HTTPException(409, 'Controlled transaction failure')
    event.listen(db, 'before_commit', reject_outer_commit)
    try:
        response = rpc(client, headers, 'tools/call', {'name': 'archive_production_artifacts', 'arguments': {
            'project_id': 'p1', 'job_id': 'archive-job', 'idempotency_key': 'rollback-archive'}})
    finally:
        event.remove(db, 'before_commit', reject_outer_commit)
    assert response.json()['result']['isError'] is True
    assert response.json()['result']['structuredContent']['error']['status'] == 409
    assert db.scalar(select(func.count()).select_from(Asset)) == 0
    assert db.scalar(select(func.count()).select_from(AgentInvocation)) == 0
    assert not list(tmp_path.glob('t1/p1/generated/*'))
    assert db.info.get('created_private_files', []) == []
    assert (tmp_path/'t1'/'p1'/'skill_outputs'/'archive-job'/'study.txt').exists()


def test_mcp_and_json_validation_errors_include_safe_declared_path_without_private_value(env):
    client, _, _ = env
    headers = token(client)
    args = plan_args('explain-422')
    args['plan']['planning']['target_seconds'] = 'PRIVATE_INVALID_VALUE'
    args['plan']['PRIVATE_EXTRA_KEY'] = 'PRIVATE_INVALID_VALUE'
    called = rpc(client, headers, 'tools/call', {'name': 'save_plan', 'arguments': args})
    error = called.json()['result']['structuredContent']['error']
    assert error['status'] == 422
    assert isinstance(error['detail'], dict)
    assert any(item['path'] == 'plan.planning.target_seconds' and item['reason']
               for item in error['detail']['errors'])
    plain = client.post('/api/agent/tools/call', headers=headers, json={'name': 'save_plan', 'arguments': args})
    assert plain.status_code == 422
    assert plain.json()['detail'] == error['detail']
    assert 'PRIVATE_INVALID_VALUE' not in called.text + plain.text
    assert 'PRIVATE_EXTRA_KEY' not in called.text + plain.text
