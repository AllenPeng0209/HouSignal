"""Acceptance aggregation is fail-closed over local temporary evidence only."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/skill_acceptance/merge_report.py'


def merger():
    spec = importlib.util.spec_from_file_location('skill_acceptance_merge_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, 'build_report'), 'merger needs a pure local build_report entry point'
    return module


def save(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


@pytest.fixture
def evidence(tmp_path):
    definition = {'name': 'sample', 'title': '验收方法', 'engine': 'media'}
    case = {'case_id': 'sample-primary', 'skill_name': 'sample', 'input': '明确临时验收输入',
            'parameters': {}, 'asset_fixture_names': [], 'commercial_scope': 'base',
            'quality': {'min_text_chars': 5, 'required_artifact_suffixes': ['.json'], 'required_fields': ['delivered']}}
    save(tmp_path, 'apps/api/app/content_skills/definitions.json', {'skills': [definition]})
    save(tmp_path, '.runtime/skill-acceptance-fixtures.json', {'project_id': 'test-project',
         'accounts': {'primary': 'test-account'}, 'assets': {'audio': 'audio-id'}})
    save(tmp_path, 'scripts/skill_acceptance/media_cases.json', {'schema_version': 1, 'group': 'media', 'cases': [case]})
    filename = 'reports/skill-acceptance/artifacts/sample-primary/job-1/report.json'
    save(tmp_path, filename, {'delivered': True})
    payload = (tmp_path / filename).read_bytes()
    record = {'case_id': case['case_id'], 'skill_name': 'sample', 'job_id': 'job-1',
              'status': 'passed', 'job_status': 'succeeded', 'actual_attempt': True,
              'response': '这是一份具体的临时验收交付。', 'commercial_scope': 'base',
              'semantic_review_required': False, 'completed_at': '2026-10-14T09:00:00+08:00',
              'request': {'input': case['input'], 'parameters': {}, 'asset_ids': []},
              'artifacts': [{'file': filename, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}]}
    save(tmp_path, 'reports/skill-acceptance/media-live.json', {'cases': [record]})
    return {'root': tmp_path, 'definition': definition, 'case': case, 'record': record}


def review(evidence, **overrides):
    row = {'case_id': evidence['case']['case_id'], 'job_id': evidence['record']['job_id'],
           'semantic_verdict': 'passed_for_requested_operation',
           'commercial_verdict': 'passed_for_requested_operation', 'technical_verdict': 'passed'}
    row.update(overrides)
    save(evidence['root'], 'reports/skill-acceptance/semantic-review.json', {'cases': [row]})


def method(evidence):
    result = merger().build_report(evidence['root'])
    assert result['all_methods_commercially_accepted'] is False
    return result['methods'][0]


def test_missing_semantic_review_never_becomes_a_positive_commercial_state(evidence):
    row = method(evidence)
    assert row['commercial_status'] == 'pending_semantic_review'
    assert row['actual_execution'] == 'passed'


@pytest.mark.parametrize('overrides, expected', [
    ({'semantic_verdict': 'needs_evidence', 'commercial_verdict': 'needs_fix'}, 'not_accepted_needs_fix'),
    ({'semantic_verdict': 'passed', 'commercial_verdict': 'needs_fix'}, 'not_accepted_needs_fix'),
    ({'technical_verdict': 'needs_fix'}, 'not_accepted_needs_fix'),
    ({'semantic_verdict': 'needs_evidence', 'commercial_verdict': 'needs_evidence'}, 'pending_semantic_review'),
    ({'semantic_verdict': 'not_delivered'}, 'not_accepted_needs_fix'),
    ({'semantic_verdict': 'passed_content_needs_typography_fix'}, 'not_accepted_needs_fix'),
    ({'semantic_verdict': 'passed_for_invented_future_scope'}, 'pending_semantic_review'),
    ({'commercial_verdict': 'passed_for_base_scope_requires_human_audiovisual_review'}, 'pending_human_review'),
])
def test_review_verdicts_are_closed_and_negative_wins(evidence, overrides, expected):
    review(evidence, **overrides)
    assert method(evidence)['commercial_status'] == expected


def test_known_scoped_positive_is_accepted_even_when_semantic_review_is_required(evidence):
    evidence['record']['semantic_review_required'] = True
    save(evidence['root'], 'reports/skill-acceptance/media-live.json', {'cases': [evidence['record']]})
    review(evidence)
    row = method(evidence)
    assert row['commercial_status'] == 'controlled_input_path_verified'
    assert row['commercial_scope'] == 'base'


@pytest.mark.parametrize('overrides', [{'case_id': 'another-case'}, {'job_id': 'another-job'}])
def test_semantic_review_requires_both_case_and_job_identity(evidence, overrides):
    review(evidence, **overrides)
    assert method(evidence)['commercial_status'] == 'pending_semantic_review'


def test_changed_manifest_request_cannot_reuse_old_pass(evidence):
    case = deepcopy(evidence['case'])
    case['parameters'] = {'beat_mode': 'auto', 'max_duration': 3}
    save(evidence['root'], 'scripts/skill_acceptance/media_cases.json', {'group': 'media', 'cases': [case]})
    review(evidence)
    row = method(evidence)
    assert row['actual_execution'] == 'stale_manifest'
    assert row['commercial_status'] == 'not_accepted'
    assert 'parameters' in row['cases'][0]['request_mismatch_fields']


def test_unresolved_new_fixture_is_not_a_current_operation_pass(evidence):
    case = deepcopy(evidence['case'])
    case['asset_fixture_names'] = ['noisy_audio']
    save(evidence['root'], 'scripts/skill_acceptance/media_cases.json', {'group': 'media', 'cases': [case]})
    review(evidence)
    row = method(evidence)
    assert row['commercial_status'] == 'not_accepted'
    assert row['cases'][0]['unresolved_fixtures'] == ['noisy_audio']


def test_latest_failure_is_preserved_instead_of_selecting_old_success(evidence):
    latest = deepcopy(evidence['record'])
    latest.update(job_id='job-2', status='failed', job_status='failed',
                  completed_at='2026-10-14T09:05:00+08:00', error='当前请求失败')
    save(evidence['root'], 'reports/skill-acceptance/media-live.json', {'cases': [evidence['record'], latest]})
    review(evidence)
    row = method(evidence)
    assert row['actual_execution'] == 'failed'
    assert row['commercial_status'] == 'not_accepted'
    assert row['cases'][0]['job_id'] == 'job-2'
    assert row['cases'][0]['history'][0]['job_id'] == 'job-1'


def test_multiple_operations_aggregate_as_partial_and_keep_each_current_result(evidence):
    other = deepcopy(evidence['case'])
    other.update(case_id='sample-second', input='第二个明确操作')
    save(evidence['root'], 'scripts/skill_acceptance/media_cases.json', {'group': 'media', 'cases': [evidence['case'], other]})
    failed = deepcopy(evidence['record'])
    failed.update(case_id=other['case_id'], job_id='job-2', status='failed', job_status='failed',
                  request={'input': other['input'], 'parameters': {}, 'asset_ids': []},
                  completed_at='2026-10-14T09:05:00+08:00')
    save(evidence['root'], 'reports/skill-acceptance/media-live.json', {'cases': [evidence['record'], failed]})
    review(evidence)
    row = method(evidence)
    assert row['actual_execution'] == 'partial'
    assert row['commercial_status'] == 'partial_not_accepted'
    assert {case['actual_execution'] for case in row['cases']} == {'passed', 'failed'}


def test_project_api_semantic_proof_binds_actual_case_job_and_artifact(evidence):
    row = {'case_id': evidence['case']['case_id'], 'job_id': 'job-1', 'job_status': 'succeeded',
           'execution_engine': 'housignal_native', 'semantic_status': 'actual_api_semantics_pass',
           'api_scope_verified': True, 'artifacts': evidence['record']['artifacts'],
           'semantic_checks': {'job_succeeded': True, 'real_artifacts_downloaded': True, 'report_matches_project': True}}
    save(evidence['root'], 'reports/skill-acceptance/project-audit.json', {'api_semantic_review': {'cases': [row]}})
    assert method(evidence)['commercial_status'] == 'controlled_input_path_verified'
    row['artifacts'][0]['sha256'] = '0' * 64
    save(evidence['root'], 'reports/skill-acceptance/project-audit.json', {'api_semantic_review': {'cases': [row]}})
    assert method(evidence)['commercial_status'] == 'pending_semantic_review'


def test_artifact_corruption_invalidates_a_previous_pass(evidence):
    review(evidence)
    (evidence['root'] / evidence['record']['artifacts'][0]['file']).write_text('changed artifact')
    row = method(evidence)
    assert row['commercial_status'] == 'not_accepted'
    assert row['actual_execution'] == 'needs_fix'


def test_response_hash_binding_prevents_reusing_review_for_changed_content(evidence):
    review(evidence, response_sha256='0' * 64)
    assert method(evidence)['commercial_status'] == 'pending_semantic_review'


def test_platform_audit_does_not_permanently_override_new_actual_execution(evidence):
    review(evidence)
    save(evidence['root'], 'reports/skill-acceptance/platform-audit.json', {'methods': [
        {'skill_name': 'sample', 'blockers': ['旧账号条件缺失']} ]})
    assert method(evidence)['commercial_status'] == 'controlled_input_path_verified'


def test_invalid_report_is_not_silently_counted_as_missing_evidence(evidence):
    path = evidence['root'] / 'reports/skill-acceptance/media-live.json'
    path.write_text('{broken json')
    with pytest.raises(ValueError):
        merger().build_report(evidence['root'])


def test_missing_live_case_remains_visible_in_coverage(evidence):
    (evidence['root'] / 'reports/skill-acceptance/media-live.json').unlink()
    result = merger().build_report(evidence['root'])
    assert result['coverage']['cases_without_live_result'] == ['sample-primary']
    assert result['methods'][0]['actual_execution'] == 'not_run'


def test_submission_rejection_without_job_is_not_an_actual_execution(evidence):
    record = deepcopy(evidence['record'])
    record.update(status='failed', actual_attempt=False, submission_attempted=True,
                  job_id=None, job_status=None, error='HTTP 429: 验收预算门槛')
    save(evidence['root'], 'reports/skill-acceptance/media-live.json', {'cases': [record]})
    row = method(evidence)
    assert row['actual_execution'] == 'blocked'
    assert row['cases'][0]['actual_attempt'] is False
    assert row['cases'][0]['submission_attempted'] is True


def test_platform_unknown_audit_blocks_even_if_an_unqualified_pass_row_exists(evidence):
    definition = deepcopy(evidence['definition'])
    definition['engine'] = 'platform'
    save(evidence['root'], 'apps/api/app/content_skills/definitions.json', {'skills': [definition]})
    review(evidence)
    save(evidence['root'], 'reports/skill-acceptance/platform-audit.json', {'methods': [
        {'skill_name': 'sample', 'blockers': ['真实登录unknown，指标未刷新']} ]})
    assert method(evidence)['actual_execution'] == 'blocked'


def test_concurrent_manifest_change_aborts_instead_of_certifying_a_mixed_snapshot(evidence, monkeypatch):
    module = merger()
    original = module.quality_errors
    def changed(root, record, case):
        newer = deepcopy(evidence['case'])
        newer['parameters'] = {'new_operation': True}
        save(root, 'scripts/skill_acceptance/media_cases.json', {'group': 'media', 'cases': [newer]})
        return original(root, record, case)
    monkeypatch.setattr(module, 'quality_errors', changed)
    with pytest.raises(ValueError, match='快照'):
        module.build_report(evidence['root'])


def test_single_case_report_keeps_job_link_compatibility(evidence):
    review(evidence)
    row = method(evidence)
    assert row['job_id'] == 'job-1'
    assert row['job_ids'] == ['job-1']


def test_previous_attempts_remain_history_even_when_old_attempt_was_positive(evidence):
    current = deepcopy(evidence['record'])
    current.update(status='failed', job_status='failed', job_id='job-2',
                   previous_attempts=[evidence['record']], completed_at='2026-10-14T09:10:00+08:00')
    save(evidence['root'], 'reports/skill-acceptance/media-live.json', {'cases': [current]})
    review(evidence)
    row = method(evidence)
    assert row['actual_execution'] == 'failed'
    assert row['cases'][0]['history'][0]['status'] == 'passed'
    assert row['cases'][0]['history'][0]['job_id'] == 'job-1'


def test_other_project_report_cannot_certify_same_case_and_request(evidence):
    review(evidence)
    save(evidence['root'], 'reports/skill-acceptance/media-live.json',
         {'project_id': 'different-project', 'cases': [evidence['record']]})
    row = method(evidence)
    assert row['commercial_status'] == 'not_accepted'
    assert 'project_id' in row['cases'][0]['request_mismatch_fields']


def test_platform_live_is_loaded_when_manifest_uses_filename_group_fallback(evidence):
    root=evidence['root']
    (root/'scripts/skill_acceptance/media_cases.json').unlink()
    (root/'reports/skill-acceptance/media-live.json').unlink()
    save(root, 'scripts/skill_acceptance/platform_cases.json', {'cases': [evidence['case']]})
    save(root, 'reports/skill-acceptance/platform-live.json', {'cases': [evidence['record']]})
    review(evidence)
    row=method(evidence)
    assert row['commercial_status']=='controlled_input_path_verified'
    assert 'platform-live.json' in row['evidence']


def test_filename_receipts_cover_ten_blocked_platform_cases_without_actual_attempts(tmp_path):
    definitions = [{'name': f'platform-{index}', 'title': '平台发布', 'engine': 'platform'}
                   for index in range(10)]
    cases = [{'case_id': f'platform-{index}-commercial', 'skill_name': row['name'],
              'input': '', 'parameters': {}, 'expected_outcome': 'blocked'}
             for index, row in enumerate(definitions)]
    records = [{'case_id': row['case_id'], 'skill_name': row['skill_name'],
                'status': 'blocked', 'actual_attempt': False, 'submission_attempted': False,
                'blockers': ['真实账号和发布授权缺失']} for row in cases]
    save(tmp_path, 'apps/api/app/content_skills/definitions.json', {'skills': definitions})
    save(tmp_path, '.runtime/skill-acceptance-fixtures.json', {'project_id': 'test-project'})
    save(tmp_path, 'scripts/skill_acceptance/platform_cases.json', {'cases': cases})
    save(tmp_path, 'reports/skill-acceptance/platform_cases-live.json',
         {'project_id': 'test-project', 'cases': records})
    result = merger().build_report(tmp_path)
    assert result['coverage']['cases_without_live_result'] == []
    assert result['actual_execution_summary'] == {'blocked': 10}
    assert result['commercial_summary'] == {'not_accepted': 10}
    assert result['all_methods_commercially_accepted'] is False
    assert result['input_fingerprints']['reports/skill-acceptance/platform_cases-live.json']
    for row in result['methods']:
        assert row['evidence'] == ['platform_cases-live.json']
        assert row['job_ids'] == []
        assert row['cases'][0]['actual_attempt'] is False
        assert row['cases'][0]['submission_attempted'] is False


def test_canonical_group_receipt_takes_precedence_over_filename_alias(evidence):
    root = evidence['root']
    (root / 'scripts/skill_acceptance/media_cases.json').unlink()
    (root / 'reports/skill-acceptance/media-live.json').unlink()
    save(root, 'scripts/skill_acceptance/platform_cases.json', {'cases': [evidence['case']]})
    save(root, 'reports/skill-acceptance/platform_cases-live.json', {'cases': [evidence['record']]})
    blocked = {'case_id': evidence['case']['case_id'], 'skill_name': 'sample',
               'status': 'blocked', 'actual_attempt': False, 'blockers': ['当前执行受阻']}
    save(root, 'reports/skill-acceptance/platform-live.json', {'cases': [blocked]})
    review(evidence)
    result = merger().build_report(root)
    assert result['coverage']['cases_without_live_result'] == []
    assert result['methods'][0]['actual_execution'] == 'blocked'
    assert result['methods'][0]['commercial_status'] == 'not_accepted'
    assert result['methods'][0]['evidence'] == ['platform-live.json']


def test_new_canonical_receipt_aborts_a_filename_fallback_snapshot(evidence, monkeypatch):
    root = evidence['root']
    (root / 'scripts/skill_acceptance/media_cases.json').unlink()
    (root / 'reports/skill-acceptance/media-live.json').unlink()
    save(root, 'scripts/skill_acceptance/platform_cases.json', {'cases': [evidence['case']]})
    save(root, 'reports/skill-acceptance/platform_cases-live.json', {'cases': [evidence['record']]})
    module = merger()
    original = module.quality_errors

    def add_canonical_receipt(root, record, case):
        save(root, 'reports/skill-acceptance/platform-live.json', {'cases': []})
        return original(root, record, case)

    monkeypatch.setattr(module, 'quality_errors', add_canonical_receipt)
    with pytest.raises(ValueError, match='快照'):
        module.build_report(root)


def test_receipt_change_between_name_selection_and_consumption_aborts(evidence, monkeypatch):
    root = evidence['root']
    (root / 'scripts/skill_acceptance/media_cases.json').unlink()
    (root / 'reports/skill-acceptance/media-live.json').unlink()
    save(root, 'scripts/skill_acceptance/platform_cases.json', {'cases': [evidence['case']]})
    receipt = root / 'reports/skill-acceptance/platform-live.json'
    save(root, 'reports/skill-acceptance/platform-live.json', {'cases': [evidence['record']]})
    module = merger()
    original = module.stable_json
    changed = False

    def change_after_selection(path, optional=True):
        nonlocal changed
        result = original(path, optional=optional)
        if path == receipt and not changed:
            changed = True
            save(root, 'reports/skill-acceptance/platform-live.json', {'cases': []})
        return result

    monkeypatch.setattr(module, 'stable_json', change_after_selection)
    with pytest.raises(ValueError, match='快照'):
        module.build_report(root)
