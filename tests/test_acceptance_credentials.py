"""Live harnesses require configured credentials before any HTTP request."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


@pytest.fixture(params=[
    ('acceptance_secretary_agent.py', 'Acceptance', 'prepare'),
    ('acceptance_secretary_video_chain.py', 'ChainAcceptance', 'login'),
], ids=['secretary-agent', 'secretary-video-chain'])
def harness(request, monkeypatch, tmp_path):
    filename, class_name, method_name = request.param
    script = Path(__file__).resolve().parents[1] / 'scripts' / filename
    spec = importlib.util.spec_from_file_location('credential_test_' + script.stem, script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'STATE_PATH', tmp_path / 'uncreated-state.json')
    monkeypatch.setattr(module.settings, 'bootstrap_email', 'synthetic-owner@example.test')
    monkeypatch.delenv('HOUSIGNAL_ACCEPTANCE_EMAIL', raising=False)
    return module, getattr(module, class_name), method_name


@pytest.mark.parametrize('configured', ['', None, '   '], ids=['empty', 'absent', 'whitespace'])
def test_missing_acceptance_credentials_fail_before_http(harness, monkeypatch, configured):
    module, acceptance_class, method_name = harness
    monkeypatch.delenv('HOUSIGNAL_ACCEPTANCE_PASSWORD', raising=False)
    monkeypatch.setattr(module.settings, 'bootstrap_password', configured)
    attempts = 0
    def transport(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={'detail': 'controlled unauthenticated boundary'})
    caught = None
    with httpx.Client(base_url='http://acceptance.invalid', transport=httpx.MockTransport(transport)) as client:
        run = acceptance_class(client)
        try:
            getattr(run, method_name)()
        except module.AcceptanceFailure as error:
            caught = error
    assert attempts == 0, 'Missing credentials must fail before sending an HTTP request'
    assert caught is not None
    assert 'HOUSIGNAL_ACCEPTANCE_PASSWORD' in str(caught)


@pytest.mark.parametrize('explicit', ['', '   '], ids=['empty', 'whitespace'])
def test_explicit_blank_acceptance_credentials_do_not_send_or_borrow_a_password(
        harness, monkeypatch, explicit):
    module, acceptance_class, method_name = harness
    monkeypatch.setenv('HOUSIGNAL_ACCEPTANCE_PASSWORD', explicit)
    monkeypatch.setattr(module.settings, 'bootstrap_password', 'synthetic-configured-credential-only')
    attempts = 0
    def transport(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={'detail': 'controlled unauthenticated boundary'})
    caught = None
    with httpx.Client(base_url='http://acceptance.invalid', transport=httpx.MockTransport(transport)) as client:
        run = acceptance_class(client)
        try:
            getattr(run, method_name)()
        except module.AcceptanceFailure as error:
            caught = error
    assert attempts == 0, 'An explicitly blank credential must fail before HTTP'
    assert caught is not None
    assert 'HOUSIGNAL_ACCEPTANCE_PASSWORD' in str(caught)


@pytest.mark.parametrize('source', ['configured', 'explicit'])
def test_configured_or_explicit_acceptance_credentials_reach_only_login_boundary(
        harness, monkeypatch, source):
    module, acceptance_class, method_name = harness
    configured = 'synthetic-configured-credential-only'
    explicit = 'synthetic-explicit-credential-only'
    monkeypatch.setattr(module.settings, 'bootstrap_password', configured)
    if source == 'explicit':
        monkeypatch.setenv('HOUSIGNAL_ACCEPTANCE_PASSWORD', explicit)
    else:
        monkeypatch.delenv('HOUSIGNAL_ACCEPTANCE_PASSWORD', raising=False)
    expected = explicit if source == 'explicit' else configured
    observations = []
    def transport(request):
        # Compare only booleans: a regression must never print request credentials.
        body = json.loads(request.content)
        observations.append((request.method == 'POST', request.url.path == '/api/auth/login',
                             body.get('password') == expected,
                             body.get('email') == 'synthetic-owner@example.test'))
        return httpx.Response(401, json={'detail': 'controlled unauthenticated boundary'})
    with httpx.Client(base_url='http://acceptance.invalid', transport=httpx.MockTransport(transport)) as client:
        run = acceptance_class(client)
        with pytest.raises(module.AcceptanceFailure, match='HTTP 401'):
            getattr(run, method_name)()
    assert observations == [(True, True, True, True)]
