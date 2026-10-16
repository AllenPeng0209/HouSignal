"""The deployed application exposes MCP without relaxing Web CSRF controls."""
from fastapi.testclient import TestClient
import app.main as main


def test_application_publishes_oauth_discovery_and_mcp_challenge():
    with TestClient(main.app) as client:
        discovery = client.get('/.well-known/oauth-protected-resource/mcp')
        assert discovery.status_code == 200
        assert discovery.json()['resource'].endswith('/mcp')
        response = client.post('/mcp', json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
        assert response.status_code == 401
        assert 'resource_metadata=' in response.headers['www-authenticate']


def test_external_transport_origin_does_not_bypass_web_cookie_csrf():
    with TestClient(main.app) as client:
        response = client.post('/mcp', headers={'Origin':'https://chatgpt.com'},
                               json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
        assert response.status_code == 401
        client.cookies.set('hs_session', 'not-a-valid-session')
        assert client.post('/mcp', json={}).status_code == 403
        assert client.post('/api/projects', headers={'Origin':'https://chatgpt.com'},
                           json={'name':'Unauthorized','city':'Shanghai'}).status_code == 403


def test_external_migrations_preserve_existing_project_and_match_models():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import Session
    from app.db import Base
    from app.core_models import Property, Tenant

    engine = create_engine('sqlite://')
    new_tables = {table.name for table in Base.metadata.sorted_tables if table.name.startswith('external_agent_')}
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables if table.name not in new_tables])
    with Session(engine) as db:
        db.add(Tenant(id='kept-tenant', name='Existing organization'))
        db.flush()
        db.add(Property(id='kept-project', tenant_id='kept-tenant', name='Existing project', city='Shanghai'))
        db.commit()
    modules = []
    folder = Path(__file__).parents[1] / 'migrations' / 'versions'
    for filename in ('c24101500003_external_agents.py', 'c24101500004_external_invocations.py'):
        spec = importlib.util.spec_from_file_location('migration_' + filename[:-3], folder / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for module in modules:
                module.upgrade()
    inspector = inspect(engine)
    for table in new_tables:
        assert {column['name'] for column in inspector.get_columns(table)} == set(Base.metadata.tables[table].columns.keys())
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for module in reversed(modules):
                module.downgrade()
    with Session(engine) as db:
        assert db.get(Property, 'kept-project').name == 'Existing project'
    engine.dispose()


def test_source_only_build_serves_downloads_with_matching_manifest(tmp_path, monkeypatch):
    """A fresh source checkout must produce usable public clients without local dist files."""
    import hashlib
    import io
    import json
    from pathlib import Path
    import shutil
    import subprocess
    import sys
    import zipfile
    import app.external_agent_api as external_agent_api

    root = Path(__file__).resolve().parents[3]
    source = tmp_path / 'source' / 'integrations' / 'louxiang-agent'
    shutil.copytree(root / 'integrations/louxiang-agent', source,
                    ignore=shutil.ignore_patterns('dist', '__pycache__'))
    distribution = source / 'dist'
    monkeypatch.setattr(external_agent_api, 'DISTRIBUTION_DIR', distribution)
    with TestClient(main.app) as client:
        assert client.get('/api/agent/downloads/manifest.json').status_code == 404
        subprocess.run([sys.executable, str(source / 'build.py')],
                       cwd=tmp_path / 'source', check=True, capture_output=True, text=True)
        response = client.get('/api/agent/downloads/manifest.json')
        assert response.status_code == 200
        manifest = response.json()
        assert set(manifest) == {'louxiang.pyz', 'louxiang_agent-0.1.0-py3-none-any.whl', 'louxiang-plugin.zip'}
        for name, metadata in manifest.items():
            download = client.get('/api/agent/downloads/' + name)
            assert download.status_code == 200
            assert len(download.content) == metadata['bytes']
            assert hashlib.sha256(download.content).hexdigest() == metadata['sha256']
        portable = tmp_path / 'downloaded-client.pyz'
        portable.write_bytes(client.get('/api/agent/downloads/louxiang.pyz').content)
        help_result = subprocess.run([sys.executable, str(portable), '--help'],
                                     check=True, capture_output=True, text=True)
        assert 'login' in help_result.stdout and 'call' in help_result.stdout
        with zipfile.ZipFile(io.BytesIO(client.get('/api/agent/downloads/louxiang-plugin.zip').content)) as plugin:
            marketplace = json.loads(plugin.read('louxiang-tools/.agents/plugins/marketplace.json'))
            assert marketplace['plugins'][0]['source']['path'] == './plugin'
            assert 'louxiang-tools/plugin/.codex-plugin/plugin.json' in plugin.namelist()
            assert 'louxiang-tools/plugin/.mcp.json' in plugin.namelist()
