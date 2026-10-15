"""Resume a real planning → adoption → script check in an existing synthetic project."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'reports/content-planning-redesign-20261014/live-flow.json'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required to use configured model capacity')
    state = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else {
        'mode': 'real API, database, worker and configured model; existing synthetic project only',
        'external_publication': False, 'started_at': datetime.now(timezone.utc).isoformat(), 'checks': {}}

    def save(stage):
        state.update(stage=stage, updated_at=datetime.now(timezone.utc).isoformat())
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        print(stage, flush=True)

    with httpx.Client(base_url='http://127.0.0.1:8017', headers={'Origin': 'http://127.0.0.1:8017'}, timeout=30) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                detail = response.json().get('detail')
                raise RuntimeError(f'HTTP {response.status_code}: ' + (detail if isinstance(detail, str) else '请求格式错误'))
            return response.json()

        def wait(jid):
            until = time.monotonic() + 600
            while time.monotonic() < until:
                job = request('GET', '/api/jobs/' + jid)
                if job['status'] == 'succeeded':
                    return job
                if job['status'] in ('failed', 'cancelled'):
                    raise RuntimeError(job.get('error') or '任务未完成')
                time.sleep(1)
            raise RuntimeError('任务仍在运行，验收回执已保留，可继续执行此脚本')

        request('POST', '/api/auth/login', json={'email': settings.bootstrap_email, 'password': settings.bootstrap_password})
        if not state.get('project_id'):
            prior = json.loads((ROOT / 'reports/product-refinement-20261014/live-flow.json').read_text())
            state['project_id'] = prior['project_id']
        pid = state['project_id']
        project = next(p for p in request('GET', '/api/projects') if p['id'] == pid)
        assert project['is_test'] is True and '[合成验收]' in project['name'], 'Only the existing synthetic project is permitted'
        base = '/api/projects/' + pid
        references = request('GET', base + '/operations/research/references')
        eligible = [t for t in request('GET', base + '/topics') if t['eligible']]
        assert eligible, 'Synthetic project needs verified facts'
        if not state.get('suggestion_job_id'):
            reference = next((r for r in references if r['analysis_snapshot'].get('job_id')), None)
            assert reference, 'Synthetic project needs an existing analyzed reference'
            topic = next((t for t in eligible if t['id'] == 'layout'), eligible[0])
            body = {'content_type': 'trust', 'subject': '看房服务', 'topic_id': topic['id'],
                'channel': project['channel'], 'account_id': None, 'reference_id': reference['id'],
                'skill_ids': ['topic-matrix', 'titles-and-hooks', 'topic-evaluation'], 'target_seconds': 30,
                'intent': '为改善家庭策划一次真实看房服务，用生活问题和实拍核对表达专业判断。借鉴参考结构，不复制原片数字。'}
            state['request'] = body
            state['suggestion_job_id'] = request('POST', base + '/operations/planning/suggest', json=body)['job_id']
            save('suggestion_queued')
        job = wait(state['suggestion_job_id'])
        result = job['result']['planning_suggestions']
        assert len(result['candidates']) == 3
        assert result['context'] == state['request']
        assert {m['id'] for m in result['methods_used']} == set(state['request']['skill_ids'])
        state['checks']['real_model_suggestions'] = True
        state['candidates'] = result['candidates']
        state['source_note'] = result['source_note']
        save('three_real_suggestions_ready')
        if not state.get('plan_id'):
            c = result['candidates'][0]
            context = result['context']
            config = {k: context[k] for k in ('content_type', 'subject', 'target_seconds', 'skill_ids')}
            config.update(hook=c['hook'], outline=c['outline'], cta=c['cta'],
                suggestion_job_id=job['id'], candidate_id=c['id'])
            plan = request('POST', base + '/operations/plans', json={
                'title': '[策划验收] ' + c['title'], 'angle': c['angle'], 'channel': context['channel'],
                'account_id': context['account_id'], 'reference_id': context['reference_id'], 'topic_id': c['topic_id'],
                'status': 'draft', 'scheduled_for': None, 'brief': c['why'] + '\n待准备：' + '；'.join(c['missing']), 'planning': config})
            state['plan_id'] = plan['id']
            save('candidate_adopted')
        plan = next(p for p in request('GET', base + '/operations/plans') if p['id'] == state['plan_id'])
        assert plan['reference_id'] == state['request']['reference_id']
        assert plan['planning']['suggestion_job_id'] == job['id']
        assert plan['source']['planning_suggestion']['candidate_id'] == result['candidates'][0]['id']
        state['checks']['durable_planning_context'] = True
        if state.get('script_job_id'):
            previous = request('GET', '/api/jobs/' + state['script_job_id'])
            if previous['status'] in ('failed', 'cancelled'):
                state.setdefault('script_attempts', []).append({'job_id': previous['id'],
                    'status': previous['status'], 'error': previous.get('error')})
                state.pop('script_job_id')
                save('resuming_after_script_failure')
        if not state.get('script_job_id'):
            state['script_job_id'] = request('POST', base + '/contents/generate', json={'topic_id': plan['topic_id'], 'plan_id': plan['id']})['job_id']
            save('script_queued')
        script_job = wait(state['script_job_id'])
        content_id = script_job['result']['content_version_id']
        content = next(c for c in request('GET', base + '/contents') if c['id'] == content_id)
        plan = next(p for p in request('GET', base + '/operations/plans') if p['id'] == state['plan_id'])
        assert plan['content_version_id'] == content_id and content['script'].strip()
        assert content['channel'] == state['request']['channel']
        state['content_id'] = content_id
        state['checks']['real_script_generation'] = True
        state['script_preview'] = content['script'][:1000]
        save('planning_adoption_script_verified')
        request('POST', '/api/auth/logout')


if __name__ == '__main__':
    main()
