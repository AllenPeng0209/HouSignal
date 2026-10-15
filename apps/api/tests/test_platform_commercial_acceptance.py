"""Commercial submission evidence cannot be inferred from a clean process exit.

These checks invoke only the receipt parser. They never run a platform adapter,
read a profile, or perform an external write.
"""
import json
import subprocess

import pytest

from app.platform_publish import _parse_submission


@pytest.mark.parametrize('output', ['', 'offline preparation finished',
    json.dumps({'success': True, 'dry_run': True}),
    json.dumps({'success': True}),
    json.dumps({'status': 'published', 'draft_only': True, 'url': 'https://www.douyin.com/video/123456789'}),
    json.dumps({'status': 'blocked', 'error': 'platform verification required'})])
def test_unproven_or_blocked_adapter_exit_requires_reconciliation(output):
    process = subprocess.CompletedProcess(['controlled-adapter'], 0, output, '')
    result = _parse_submission('douyin', process, False)
    assert result['status'] == 'reconciliation_required'
    assert not result.get('url')


@pytest.mark.parametrize('status', ['submitted', 'published', 'posted'])
def test_explicit_platform_submission_receipt_preserves_pending_submission(status):
    process = subprocess.CompletedProcess(['controlled-adapter'], 0, json.dumps({'status': status}), '')
    result = _parse_submission('douyin', process, False)
    assert result['status'] == 'submitted'
    assert not result.get('url')


def test_a_concrete_platform_work_url_can_supply_submission_evidence():
    process = subprocess.CompletedProcess(['controlled-adapter'], 0,
        json.dumps({'url': 'https://www.douyin.com/video/123456789'}), '')
    result = _parse_submission('douyin', process, False)
    assert result['status'] == 'submitted'
    assert result['url'] == 'https://www.douyin.com/video/123456789'


@pytest.mark.parametrize('status', [[], {}, 0, None])
def test_malformed_status_without_platform_receipt_is_safely_uncertain(status):
    process = subprocess.CompletedProcess(['controlled-adapter'], 0, json.dumps({'status': status}), '')
    assert _parse_submission('douyin', process, False)['status'] == 'reconciliation_required'


def test_a_platform_review_receipt_still_does_not_prove_public_visibility():
    process = subprocess.CompletedProcess(['controlled-adapter'], 0,
        json.dumps({'status': 'review_pending'}), '')
    result = _parse_submission('douyin', process, False)
    assert result['status'] == 'review_pending'
    assert not result.get('url')


def test_official_account_draft_receipt_stays_a_draft():
    process = subprocess.CompletedProcess(['controlled-adapter'], 0,
        json.dumps({'success': True, 'media_id': 'controlled-draft-id'}), '')
    result = _parse_submission('wechat-oa', process, True)
    assert result['status'] == 'draft_saved'
    assert result['draft_only'] is True
    assert not result.get('url')
