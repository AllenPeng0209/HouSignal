"""Real chapter generation and narrated rendering in the existing example project.

Receipts are resumable; manual approval and external publication remain pending.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import zipfile

import httpx
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'reports/content-longform-20261015'
RECEIPT = OUTPUT / 'live-flow.json'
PROJECT_ID = '63212d46-b1b9-50d0-a2c2-bcea5f4d5038'
REFERENCE_ID = 'bcef1a19-8e1e-49c0-acaa-012e297b3cd0'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--script-only', action='store_true', help='Persist the real draft for source-card production before rendering')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required for configured model and actual speech synthesis')
    state = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {
        'project_id': PROJECT_ID, 'checks': {}, 'started_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'example project; actual model, Edge narration and FFmpeg; no human approval or external publication'}

    def save(stage):
        state.update(stage=stage, updated_at=datetime.now(timezone.utc).isoformat())
        OUTPUT.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        print(stage, flush=True)

    with httpx.Client(base_url='http://127.0.0.1:8017', headers={'Origin': 'http://127.0.0.1:8017'}, timeout=30) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                raise RuntimeError(f'HTTP {response.status_code}: ' + str(response.json().get('detail')))
            return response.json()

        def wait(jid):
            until, previous = time.monotonic() + 2700, None
            while time.monotonic() < until:
                job = request('GET', '/api/jobs/' + jid)
                progress = (job['status'], job.get('phase'), (job.get('result') or {}).get('longform_progress'))
                if progress != previous:
                    print(json.dumps({'job': jid, 'status': progress[0], 'phase': progress[1], 'chapters': progress[2]}, ensure_ascii=False), flush=True)
                    previous = progress
                if job['status'] == 'succeeded':
                    return job
                if job['status'] in ('failed', 'cancelled'):
                    state.setdefault('failed_attempts', []).append({'job_id': jid, 'kind': job['kind'], 'error': job.get('error')})
                    save('job_failed_receipt_preserved')
                    raise RuntimeError(job.get('error') or '任务未完成')
                time.sleep(2)
            raise RuntimeError('任务仍在运行；回执可恢复，不能重复提交')

        def queue_once(key, path, body=None):
            if state.get(key):
                old = request('GET', '/api/jobs/' + state[key])
                if old['status'] in ('failed', 'cancelled'):
                    state.pop(key)
            if not state.get(key):
                state[key] = request('POST', path, **({'json': body} if body is not None else {}))['job_id']
                save(key + '_queued')
            return wait(state[key])

        request('POST', '/api/auth/login', json={'email': settings.bootstrap_email, 'password': settings.bootstrap_password})
        try:
            project = next(p for p in request('GET', '/api/projects') if p['id'] == PROJECT_ID)
            assert project['is_test'] is True and project['name'] == '云境·壹号'
            base = '/api/projects/' + PROJECT_ID
            if not state.get('plan_id'):
                planning = {'content_type': 'trust', 'subject': '买房顾虑', 'target_seconds': 360,
                    'skill_ids': ['topic-matrix', 'titles-and-hooks', 'topic-evaluation', 'series-planning'],
                    'hook': '看到329㎡、8.2米开间和4房同层，先别急着下结论。一起把家庭问题变成可以核对的看房清单。',
                    'outline': [
                        '开场用家庭成员的不同需求提出问题，说明示例资料与空间示意的边界，承诺得到一份可执行核对清单',
                        '建筑面积329㎡应怎样读：数字可确认的资料含义、不能推导的实际使用体验；演示记录来源、口径、待核对项的做法',
                        '客厅开间8.2米如何核对：把沙发、家庭活动、走动三类需求放到同一张清单，分别问测量位置和家具方案，不由示意图推断舒适结论',
                        '4房同层与家庭分工：给有孩子、长辈、居家工作三种条件性场景，分别核对私密边界、噪声、储物；不声称本项目已经满足',
                        '厨房与餐厅相邻如何验证家务过程：按取物、备餐、上桌、收拾分别记录动线和待确认细节，比较不同习惯的取舍',
                        '把面积、开间、布局、厨餐关系合并为看房检查与选择清单：分别记录资料依据、待核对问题和家庭优先级，结尾约定下一步核对与预约'],
                    'cta': '把自家优先级和待核对清单带去问销售；看房预约需销售逐次确认。'}
                plan = request('POST', base + '/operations/plans', json={
                    'title': '改善家庭看房前：从空间数字到核对清单', 'angle': '六章解释、场景取舍与看房核对方法，使用示例资料和授权示意图',
                    'channel': project['channel'], 'account_id': None, 'reference_id': REFERENCE_ID, 'topic_id': 'tour',
                    'status': 'draft', 'scheduled_for': None, 'planning': planning,
                    'brief': '长片示例验收。参考仅有标题，没有原片分析；参考段落只作为明确标注的表达假设。'
                        '全片以资料解释和条件性看房方法提供独立信息，使用当前授权示意图搭配数据卡、对比卡和检查清单。'
                        '不能声称现场拍摄、实测、真实样板间、具体采光或成交价值。'
                        '每章输出新的问题、判断操作、不同需求的取舍与可带走的答案；避免重复面积数字或免责声明凑时长。'})
                state['plan_id'] = plan['id']
                state['planning'] = planning
                save('six_chapter_plan_and_reference_saved')
            plan = next(p for p in request('GET', base + '/operations/plans') if p['id'] == state['plan_id'])
            generated = queue_once('script_job_id', base + '/contents/generate', {
                'topic_id': plan['topic_id'], 'plan_id': plan['id'], 'channel': plan['channel']})
            cid = state.get('production_content_id') or generated['result']['content_version_id']
            state['content_id'] = cid
            content = next(c for c in request('GET', base + '/contents') if c['id'] == cid)
            plan = next(p for p in request('GET', base + '/operations/plans') if p['id'] == state['plan_id'])
            board = content['storyboard']; shots, chapters = board['shots'], board['chapters']
            assert board['schema_version'] == 2 and len(chapters) >= 6 and len(shots) >= 30
            assert content['script'] == '\n'.join(s['voiceover'] for s in shots)
            assert plan['content_version_id'] == cid and board['planning_context']['plan_id'] == plan['id']
            assert board['planning_context']['reference']['reference_id'] == REFERENCE_ID
            assert all(s['plan_section'] and s['reference_beat_ids'] for s in shots)
            expected = {'hook', 'cta'} | {f'outline-{i}' for i in range(1, len(state['planning']['outline']) + 1)}
            assert {s['plan_section'] for s in shots} == expected
            state.update(chapter_count=len(chapters), shot_count=len(shots), narration_characters=len(re.findall(r'[\u4e00-\u9fff]', content['script'])))
            state['storyboard'] = board
            state['checks']['same_plan_reference_and_narration'] = True
            (OUTPUT / 'script.txt').write_text(content['script'] + '\n')
            (OUTPUT / 'storyboard.json').write_text(json.dumps(board, ensure_ascii=False, indent=2) + '\n')
            save('real_script_and_many_shots_verified')
            if args.script_only:
                return
            assert all(s['production_status'] == 'ready' and s['asset_id'] for s in shots), 'Some shots still need source material; bind it in the editor before rendering'
            rendered = queue_once('render_job_id', base + '/contents/' + cid + '/render')
            content = next(c for c in request('GET', base + '/contents') if c['id'] == cid)
            assert content['render_asset_id'] == rendered['result']['render_asset_id']
            assets = {a['id']: a for a in request('GET', base + '/assets')}
            metadata = assets[content['render_asset_id']]['metadata_json']
            state['render_metadata'] = metadata
            assert metadata['shot_count'] == len(shots) and metadata['speech_coverage'] >= .9
            assert metadata['voiceover_audio_sha256'] and metadata['decoded_voice_seconds'] >= 300
            video = client.get(base + '/assets/' + content['render_asset_id'] + '/file'); video.raise_for_status()
            path = OUTPUT / 'example-longform.mp4'; path.write_bytes(video.content)
            probe = subprocess.run([settings.ffprobe_binary, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], capture_output=True, text=True, check=True)
            info = json.loads(probe.stdout); duration = float(info['format']['duration'])
            assert 300 <= duration <= 600
            assert any(s['codec_type'] == 'audio' for s in info['streams'])
            subprocess.run([settings.ffmpeg_binary, '-v', 'error', '-i', str(path), '-f', 'null', '-'], capture_output=True, check=True)
            state.update(actual_duration_seconds=duration, render_asset_id=content['render_asset_id'], video_sha256=hashlib.sha256(video.content).hexdigest())
            state['checks']['actual_narrated_long_mp4_full_decode'] = True
            files = {'video.mp4': video.content, 'script.txt': content['script'].encode(), 'storyboard.json': (OUTPUT / 'storyboard.json').read_bytes()}
            for key, filename in (('cover_asset_id', 'cover.jpg'), ('subtitle_asset_id', 'subtitles.srt'),
                    ('voiceover_audio_asset_id', 'voiceover.wav'), ('chapters_asset_id', 'actual-chapters.json')):
                aid = rendered['result'].get(key) or metadata.get(key)
                assert aid, key
                file = client.get(base + '/assets/' + aid + '/file'); file.raise_for_status()
                files[filename] = file.content; (OUTPUT / filename).write_bytes(file.content)
                if key == 'voiceover_audio_asset_id':
                    assert hashlib.sha256(file.content).hexdigest() == metadata['voiceover_audio_sha256']
            actual_chapters = json.loads(files['actual-chapters.json'])
            assert len(actual_chapters['shots']) == len(shots) and len(actual_chapters['chapters']) == len(chapters)
            assert abs(actual_chapters['chapters'][-1]['end'] - duration) < .75
            for shot, measured in zip(shots, actual_chapters['shots']):
                assert measured['voiceover_text_sha256'] == hashlib.sha256(shot['voiceover'].encode()).hexdigest()
                assert 1 <= measured['decoded_audio_seconds'] <= 30
            subtitles = files['subtitles.srt'].decode()
            assert len(re.findall('-->', subtitles)) >= len(shots)
            state['checks']['actual_voice_subtitle_and_chapter_receipts'] = True
            preview = {'project_id': PROJECT_ID, 'plan_id': state['plan_id'], 'content_version_id': cid,
                'render_asset_id': content['render_asset_id'], 'approval_state': 'pending_human_review',
                'publication_state': 'not_published', 'reference_evidence': board['planning_context']['reference']['evidence_basis'],
                'video_sha256': state['video_sha256'], 'actual_duration_seconds': duration}
            files['preview.json'] = json.dumps(preview, ensure_ascii=False, indent=2).encode()
            with zipfile.ZipFile(OUTPUT / 'review-preview.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items(): archive.writestr(name, data)
            assert content.get('approval_valid') is False and not content.get('release_id')
            assert not request('GET', base + '/publications')
            state['checks']['no_fabricated_approval_or_publication'] = True
            save('longform_video_and_review_preview_ready')
        finally:
            try:
                request('POST', '/api/auth/logout')
            except httpx.HTTPError:
                print('Local API unavailable during session cleanup; durable task receipt retained', flush=True)


if __name__ == '__main__':
    main()
