"""Current declared cases, bound reviews, and history; no external operations."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'reports/skill-acceptance'
SEMANTIC_POSITIVE = {'passed', 'pass', 'passed_for_base_scope', 'passed_for_requested_scope',
    'passed_for_requested_operation', 'passed_for_provided_text_poster_render'}
COMMERCIAL_POSITIVE = {'passed', 'pass', 'passed_for_base_scope', 'passed_for_requested_scope',
    'passed_for_requested_operation', 'passed_for_public_rss_aggregation', 'passed_for_public_hotword_retrieval',
    'passed_for_pdf_text_and_embedded_figure_extraction', 'passed_for_existing_clip_assembly_only',
    'passed_for_provided_translation_timecode_render', 'passed_for_public_voice_audio_generation_technical',
    'passed_for_actual_asr_chapter_starts', 'passed_for_provided_highlight_ranges',
    'passed_for_requested_clip_production', 'passed_for_static_reframe_only',
    'passed_for_actual_audio_transcription_only', 'passed_for_provided_note_and_three_card_render',
    'passed_for_provided_text_poster_render'}
TECHNICAL_POSITIVE = {'passed', 'pass', 'succeeded', 'passed_for_requested_operation'}
NEGATIVE = {'failed', 'needs_fix', 'requires_regeneration', 'rejected', 'not_delivered',
            'passed_content_needs_typography_fix'}
HUMAN_PENDING = {'passed_for_base_scope_requires_human_audiovisual_review',
                 'pending_human_review', 'requires_human_review'}


def stable_json(path, optional=True):
    for attempt in range(5):
        try:
            before = path.stat(); raw = path.read_bytes(); after = path.stat()
            result = json.loads(raw)
            if (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size):
                return result, hashlib.sha256(raw).hexdigest()
        except FileNotFoundError:
            if optional:
                return {}, None
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        if attempt < 4:
            time.sleep(.05)
    raise ValueError(f'验收输入 {path.name} 无法取得稳定有效JSON快照')


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def completion(record):
    try:
        value = datetime.fromisoformat((record.get('completed_at') or record.get('started_at') or '').replace('Z', '+00:00'))
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    except (TypeError, ValueError, AttributeError):
        return float('-inf')


def resolve(value, state, missing):
    if isinstance(value, str):
        def substitute(match):
            key = match.group(1)
            result = state.get('project_id') if key == 'project_id' else (
                state.get('accounts', {}).get('primary') if key == 'account_id' else (
                state.get('assets', {}).get(key[6:]) if key.startswith('asset:') else None))
            if result is None:
                missing.add(key)
                return match.group(0)
            return str(result)
        return re.sub(r'\{\{([^{}]+)\}\}', substitute, value)
    if isinstance(value, list):
        return [resolve(item, state, missing) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, state, missing) for key, item in value.items()}
    return value


def expected_request(case, state):
    missing = set()
    result = {'input': resolve(case.get('input', ''), state, missing),
              'parameters': resolve(case.get('parameters', {}), state, missing), 'asset_ids': []}
    for name in case.get('asset_fixture_names', []):
        identifier = state.get('assets', {}).get(name)
        if identifier is None:
            missing.add(name)
        result['asset_ids'].append(identifier)
    if case.get('account_fixture_name'):
        name = case['account_fixture_name']; identifier = state.get('accounts', {}).get(name)
        if identifier is None:
            missing.add('account:' + name)
        result['account_id'] = identifier
    return result, sorted(missing)


def receipts(record):
    return {(item.get('sha256'), item.get('bytes')) for item in record.get('artifacts', [])
            if isinstance(item, dict) and item.get('sha256') and type(item.get('bytes')) is int}


def quality_errors(root, record, case):
    quality = case.get('quality', {}); errors = []; suffixes = set(); documents = [record]
    if len(str(record.get('response') or '').strip()) < quality.get('min_text_chars', 0):
        errors.append('当前声明的文字交付长度不足')
    for item in record.get('artifacts', []):
        try:
            path = root / item['file']
            if path.is_symlink() or not path.resolve().is_relative_to((root / 'reports/skill-acceptance').resolve()):
                raise ValueError
            before = path.stat(); raw = path.read_bytes(); after = path.stat()
            if (not raw or (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size)
                    or hashlib.sha256(raw).hexdigest() != item.get('sha256') or len(raw) != item.get('bytes')):
                raise ValueError
            suffixes.add(path.suffix.lower())
            if path.suffix.lower() == '.json':
                value = json.loads(raw)
                if isinstance(value, dict):
                    documents.append(value)
        except (TypeError, KeyError, ValueError, OSError, UnicodeError):
            errors.append('产物缺失、变动或与SHA/大小回执不一致')
    if not set(quality.get('required_artifact_suffixes', [])).issubset(suffixes):
        errors.append('当前声明的交付格式缺失')
    fields = set(quality.get('required_fields', []))
    if fields and not any(fields.issubset(value) for value in documents):
        errors.append('当前声明的结构化字段缺失')
    if quality.get('require_sources') and not (record.get('sources') or re.search(r'https?://', str(record.get('response') or ''))):
        errors.append('当前声明所需来源缺失')
    return errors


def bound(review, record, api=False):
    if review.get('case_id') != record.get('case_id') or review.get('job_id') != record.get('job_id'):
        return False
    if review.get('skill_name') not in (None, record.get('skill_name')):
        return False
    if review.get('response_sha256') and review['response_sha256'] != hashlib.sha256(str(record.get('response') or '').encode()).hexdigest():
        return False
    if review.get('request_sha256') and review['request_sha256'] != canonical_hash(record.get('request')):
        return False
    if review.get('case_snapshot_sha256'):
        clean = {key: value for key, value in record.items() if not key.startswith('_')}
        if review['case_snapshot_sha256'] != canonical_hash(clean):
            return False
    frozen = review.get('central_report_snapshot')
    if isinstance(frozen, dict) and (frozen.get('job_id') != record.get('job_id')
            or frozen.get('case_id') != record.get('case_id') or frozen.get('request') != record.get('request')
            or receipts(frozen) != receipts(record)):
        return False
    if review.get('artifacts') and receipts(review) != receipts(record):
        return False
    if api:
        checks = review.get('semantic_checks')
        return (review.get('semantic_status') == 'actual_api_semantics_pass'
                and review.get('job_status') == 'succeeded' and review.get('execution_engine') == 'housignal_native'
                and review.get('api_scope_verified') is True and isinstance(checks, dict) and bool(checks)
                and all(value is True for value in checks.values())
                and all(checks.get(key) is True for key in ('job_succeeded', 'real_artifacts_downloaded', 'report_matches_project'))
                and bool(receipts(review)) and receipts(review) == receipts(record))
    return True


def semantic_state(record, reviews, api_reviews):
    matching = [row for row in reviews if bound(row, record)]
    matching_api = [row for row in api_reviews if bound(row, record, api=True)]
    sources = [row['_review_source'] for row in matching + matching_api]
    issues = [issue for row in matching for issue in row.get('issues', [])]
    values = [(row.get('semantic_verdict'), row.get('commercial_verdict', row.get('commercial_scope_verdict')),
               row.get('technical_verdict', row.get('technical_status', 'passed'))) for row in matching]
    if any(any(value in NEGATIVE for value in triple) for triple in values):
        return 'not_accepted_needs_fix', 'needs_fix', issues, sources
    if any(any(value in HUMAN_PENDING for value in triple) for triple in values):
        return 'pending_human_review', 'pending_human_review', issues, sources
    if matching:
        if not all(s in SEMANTIC_POSITIVE and c in COMMERCIAL_POSITIVE and t in TECHNICAL_POSITIVE for s,c,t in values):
            return 'pending_semantic_review', 'needs_evidence', issues, sources
        return 'controlled_input_path_verified', 'passed_for_requested_scope', issues, sources
    if matching_api:
        return 'controlled_input_path_verified', 'actual_api_semantics_pass', issues, sources
    return 'pending_semantic_review', 'pending_review', issues, sources


def history_record(record):
    return {key: record.get(key) for key in ('case_id','job_id','status','job_status','actual_attempt',
        'submission_attempted','completed_at','started_at','error','_evidence_report') if key in record}


def evaluate_case(root, case, candidates, state, reviews, api_reviews, blockers):
    row = {'case_id': case['case_id'], 'skill_name': case['skill_name'], 'commercial_scope': case.get('commercial_scope','base'),
           'actual_execution':'not_run', 'commercial_status':'not_accepted', 'semantic_status':'pending_review',
           'evidence':[], 'limitations':[], 'history':[]}
    expected, missing = expected_request(case, state)
    row.update(expected_request_sha256=canonical_hash(expected), unresolved_fixtures=missing)
    if not candidates:
        if blockers or case.get('expected_outcome') == 'blocked':
            row.update(actual_execution='blocked', limitations=blockers or case.get('prerequisites',[]))
        return row
    ordered = sorted(enumerate(candidates), key=lambda value:(completion(value[1]),value[0]))
    current = ordered[-1][1]; history = [history_record(value) for _,value in ordered[:-1]]
    for key in ('previous_attempts','attempt_history','history'):
        for previous in current.get(key,[]) if isinstance(current.get(key),list) else []:
            if isinstance(previous,dict) and previous.get('job_id'):
                history.append(history_record(previous))
    unique = {(value.get('job_id'),value.get('status'),value.get('completed_at')):value for value in history}
    row.update(job_id=current.get('job_id'), completed_at=current.get('completed_at'), reported_status=current.get('status'),
               evidence=[current['_evidence_report']], history=list(unique.values()),
               actual_attempt=current.get('actual_attempt') is True and bool(current.get('job_id')),
               submission_attempted=current.get('submission_attempted') is True,
               technical_status=current.get('job_status',current.get('status')))
    request = current.get('request')
    mismatch = [key for key in expected if not isinstance(request,dict) or request.get(key)!=expected[key]]
    if current.get('skill_name') != case['skill_name']:
        mismatch.append('skill_name')
    if current.get('_project_scope_mismatch'):
        mismatch.append('project_id')
    row['request_mismatch_fields']=mismatch
    # Predeclared blocked prerequisites carry no fabricated request/job. A
    # recorded actual operation, however, must match the current manifest.
    if not row['actual_attempt'] and current.get('status')=='blocked' and not request:
        row.update(actual_execution='blocked',limitations=current.get('blockers') or blockers or case.get('prerequisites',[]))
        return row
    if missing or mismatch:
        row.update(actual_execution='stale_manifest',limitations=['当前请求/fixture不匹配历史回执，不能认证新操作'])
        return row
    if current.get('status')!='passed':
        row.update(actual_execution='failed' if row['actual_attempt'] and current.get('status') in ('failed','cancelled') else 'blocked',
                   limitations=current.get('blockers') or [current.get('error','当前操作尚未交付')])
        return row
    if not row['actual_attempt'] or current.get('job_status')!='succeeded':
        row.update(actual_execution='needs_fix',limitations=['passed记录没有真实attempt、job或succeeded回执'])
        return row
    errors = quality_errors(root,current,case)
    if errors:
        row.update(actual_execution='needs_fix',limitations=errors)
        return row
    commercial,semantic,issues,sources=semantic_state(current,reviews,api_reviews)
    row.update(actual_execution='needs_fix' if commercial=='not_accepted_needs_fix' else 'passed',
               commercial_status=commercial,semantic_status=semantic,limitations=issues,
               evidence=list(dict.fromkeys(row['evidence']+sources)),request_sha256=canonical_hash(request))
    row['limitations'].append('仅此case所声明操作的受控输入证据，不接受全部操作或真实商业发布' if commercial=='controlled_input_path_verified'
                             else '当前商业判定未通过；不得由技术succeeded或旧成功自动放行')
    return row


def build_report(root=ROOT):
    """Pure local evaluator. No model/API/platform calls and no output writes."""
    root=Path(root); folder=root/'reports/skill-acceptance'; fingerprints={}
    def load(path, optional=True):
        document,digest=stable_json(path,optional=optional)
        relative=str(path.relative_to(root))
        if relative in fingerprints and fingerprints[relative] != digest:
            raise ValueError(f'验收输入 {path.name} 在合并期间变化，请重取一致快照')
        fingerprints[relative]=digest
        return document,digest
    definitions,_=load(root/'apps/api/app/content_skills/definitions.json',optional=False)
    definitions=definitions['skills']; names=[row['name'] for row in definitions]
    if len(names)!=len(set(names)):
        raise ValueError('canonical方法名称重复')
    state,_=load(root/'.runtime/skill-acceptance-fixtures.json')
    declared=[]; report_paths=set()
    manifest_paths=sorted((root/'scripts/skill_acceptance').glob('*_cases.json'))
    for path in manifest_paths:
        document,digest=load(path,optional=False); group=document.get('group',path.name.removesuffix('_cases.json'))
        if not re.fullmatch(r'[A-Za-z0-9_]+',str(group)):
            raise ValueError('case分组无效')
        fingerprints[str(path.relative_to(root))]=digest
        report_path=folder/f'{group}-live.json'
        if 'group' not in document and path.stem != group:
            # Older runners used the full manifest stem (platform_cases-live).
            # Prefer the group name, and bind absent candidates too so a new
            # preferred receipt cannot silently change a fallback snapshot.
            _,canonical_digest=load(report_path)
            if canonical_digest is None:
                fallback=folder/f'{path.stem}-live.json'
                _,fallback_digest=load(fallback)
                if fallback_digest is not None: report_path=fallback
        declared.extend(document.get('cases',[])); report_paths.add(report_path)
    if len({row['case_id'] for row in declared})!=len(declared):
        raise ValueError('当前manifest的case_id重复')
    if any(row['skill_name'] not in names for row in declared):
        raise ValueError('当前manifest包含非canonical方法')
    records=[]
    for path in sorted(report_paths):
        document,digest=load(path)
        if digest: fingerprints[str(path.relative_to(root))]=digest
        mismatch=bool(document.get('project_id') and state.get('project_id')
                      and document['project_id']!=state['project_id'])
        records.extend({**row,'_evidence_report':path.name,'_project_scope_mismatch':mismatch}
                       for row in document.get('cases',[]))
    main,digest=load(folder/'semantic-review.json')
    if digest: fingerprints['reports/skill-acceptance/semantic-review.json']=digest
    reviews=[{**row,'_review_source':'semantic-review.json'} for key in ('cases','current_case_verdicts') for row in main.get(key,[])]
    supplement,digest=load(folder/'semantic-media-supplement.json')
    if digest: fingerprints['reports/skill-acceptance/semantic-media-supplement.json']=digest
    reviews.extend({**row,'_review_source':'semantic-media-supplement.json'} for row in supplement.get('cases',[]))
    project,digest=load(folder/'project-audit.json')
    if digest: fingerprints['reports/skill-acceptance/project-audit.json']=digest
    api=[{**row,'_review_source':'project-audit.json'} for row in project.get('api_semantic_review',{}).get('cases',[])]
    platform,_=load(folder/'platform-audit.json'); blockers={row['skill_name']:row.get('blockers',[]) for row in platform.get('methods',[])}
    bycase=defaultdict(list)
    for row in records: bycase[row.get('case_id')].append(row)
    results=[]
    for definition in definitions:
        name=definition['name']; cases=[row for row in declared if row['skill_name']==name]
        evaluated=[evaluate_case(root,case,bycase.get(case['case_id'],[]),state,reviews,api,blockers.get(name,[])) for case in cases]
        if definition['engine']=='platform' and blockers.get(name):
            for row in evaluated:
                row.update(actual_execution='blocked',commercial_status='not_accepted',limitations=blockers[name],
                           evidence=list(dict.fromkeys(row['evidence']+['platform-audit.json'])))
        states=[row['actual_execution'] for row in evaluated]; commercials=[row['commercial_status'] for row in evaluated]
        actual='passed' if states and all(v=='passed' for v in states) else ('partial' if 'passed' in states else
                next((v for v in ('needs_fix','failed','stale_manifest','blocked') if v in states),'not_run'))
        commercial='controlled_input_path_verified' if commercials and all(v=='controlled_input_path_verified' for v in commercials) else (
            'partial_not_accepted' if 'controlled_input_path_verified' in commercials else next((v for v in
                ('not_accepted_needs_fix','pending_human_review','pending_semantic_review') if v in commercials),'not_accepted'))
        scopes=sorted({row['commercial_scope'] for row in evaluated})
        result={'skill_name':name,'title':definition['title'],'engine':definition['engine'],
            'actual_execution':actual,'commercial_status':commercial,'commercial_scope':scopes[0] if len(scopes)==1 else 'mixed' if scopes else None,
            'evidence':sorted({value for row in evaluated for value in row['evidence']}),
            'limitations':list(dict.fromkeys(str(value) for row in evaluated for value in row['limitations'])),
            'cases':evaluated,'declared_case_count':len(evaluated),
            'job_ids':list(dict.fromkeys(row['job_id'] for row in evaluated if row.get('job_id')))}
        if len(evaluated)==1:
            result.update(job_id=evaluated[0].get('job_id'),semantic_status=evaluated[0]['semantic_status'])
        results.append(result)
    ids={row['case_id'] for row in declared}; runtime,_=load(folder/'runtime-acceptance.json')
    if manifest_paths!=sorted((root/'scripts/skill_acceptance').glob('*_cases.json')):
        raise ValueError('验收清单在合并期间变化，请重取一致快照')
    for relative,digest in fingerprints.items():
        _,current_digest=stable_json(root/relative)
        if current_digest!=digest:
            raise ValueError(f'验收输入 {Path(relative).name} 在合并期间变化，请重取一致快照')
    return {'schema_version':2,'verified_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'method_count':len(results),
        'declared_case_count':len(declared),'scope':'当前manifest、最新per-case真实任务与严格绑定语义证据；历史另列',
        'all_methods_commercially_accepted':False,
        'actual_execution_summary':dict(Counter(row['actual_execution'] for row in results)),
        'commercial_summary':dict(Counter(row['commercial_status'] for row in results)),
        'coverage':{'methods_with_cases':len({row['skill_name'] for row in declared}),
            'methods_missing_cases':sorted(set(names)-{row['skill_name'] for row in declared}),
            'cases_without_live_result':sorted(ids-set(bycase)),'orphaned_live_cases':sorted(set(bycase)-ids)},
        'input_fingerprints':fingerprints,'runtime_proof':runtime,'methods':results}


def main():
    result=build_report(ROOT); REPORT.mkdir(parents=True,exist_ok=True)
    fd,path=tempfile.mkstemp(dir=REPORT,prefix='.all-methods-',suffix='.json')
    try:
        with os.fdopen(fd,'w') as file:
            json.dump(result,file,ensure_ascii=False,indent=2); file.write('\n')
        os.replace(path,REPORT/'all-methods-acceptance.json')
    finally:
        if os.path.exists(path):os.unlink(path)
    print(json.dumps({key:result[key] for key in ('method_count','actual_execution_summary',
          'commercial_summary','all_methods_commercially_accepted')},ensure_ascii=False))


if __name__=='__main__':main()
