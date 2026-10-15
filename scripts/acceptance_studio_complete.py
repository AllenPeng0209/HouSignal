"""Resume the screenshot's example plan through real script and MP4 production.

Leaves human review and publication state intact. The local ZIP is explicitly
a preview for review, not a server approval or a platform receipt.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import zipfile

import httpx
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'reports/studio-full-flow-20261015'
RECEIPT = OUTPUT / 'live-flow.json'
PROJECT_ID = '63212d46-b1b9-50d0-a2c2-bcea5f4d5038'
PLAN_ID = 'e3768e24-0a01-4699-992b-ec5edf732c4b'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required for the configured model and renderer')
    state = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {
        'project_id': PROJECT_ID, 'plan_id': PLAN_ID, 'checks': {},
        'scope': 'existing example project, real model and FFmpeg; human review and publication remain pending',
        'started_at': datetime.now(timezone.utc).isoformat()}

    def save(stage):
        state.update(stage=stage, updated_at=datetime.now(timezone.utc).isoformat())
        OUTPUT.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        print(stage, flush=True)

    with httpx.Client(base_url='http://127.0.0.1:8017', headers={'Origin': 'http://127.0.0.1:8017'}, timeout=30) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                detail = response.json().get('detail')
                raise RuntimeError(f'HTTP {response.status_code}: ' + (detail if isinstance(detail, str) else '请求格式错误'))
            return response.json()

        def wait(jid):
            end = time.monotonic() + 900
            while time.monotonic() < end:
                job = request('GET', '/api/jobs/' + jid)
                if job['status'] == 'succeeded':
                    return job
                if job['status'] in ('failed', 'cancelled'):
                    raise RuntimeError(job.get('error') or '任务未完成')
                time.sleep(1)
            raise RuntimeError('任务仍在运行，可使用已保留回执继续验收')

        def queue_once(key, path, body=None):
            if state.get(key):
                previous = request('GET', '/api/jobs/' + state[key])
                if previous['status'] in ('failed', 'cancelled'):
                    state.setdefault('attempts', []).append({'job_id': previous['id'], 'kind': previous['kind'],
                        'status': previous['status'], 'error': previous.get('error')})
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
            plan = next(p for p in request('GET', base + '/operations/plans') if p['id'] == PLAN_ID)
            if not state.get('plan_revised'):
                state['original_plan'] = {k: plan[k] for k in ('title', 'angle', 'brief', 'planning')}
                config = dict(plan['planning'])
                config.update(hook='329㎡、8.2米开间、4房同层，用空间示意图一起理解这份项目资料。',
                    outline=['以空间示意图介绍329㎡建筑面积，说明这是示例项目资料讲解',
                        '用核验资料说明8.2米客厅开间，不把示意图称为现场测量',
                        '讲清厨房与餐厅相邻的资料关系',
                        '结合4房同层资料介绍空间安排，画面仅作示意',
                        '结尾说明看房预约需销售逐次确认'],
                    cta='想了解实际房源，可联系销售确认看房时间。')
                plan = request('PATCH', base + '/operations/plans/' + PLAN_ID, json={
                    'title': '329㎡四房同层，客厅开间8.2米，空间示意导览',
                    'angle': '用已核验项目资料和空间示意素材讲解居住需求',
                    'brief': '本次示例流程修订：当前素材为测试或示意图，不能声称实拍、现场记录或实测。\n'
                        '面积、开间、厨房关系与房间布局读取当前核验资料；原参考只借鉴表达结构。\n'
                        '预约需销售逐次确认，不补造报价、交通、学校或效果承诺。', 'planning': config})
                state['plan_revised'] = True
                save('example_plan_aligned_with_source_material')
            generated = queue_once('script_job_id', base + '/contents/generate',
                {'topic_id': plan['topic_id'], 'plan_id': PLAN_ID, 'channel': plan['channel']})
            cid = generated['result']['content_version_id']
            state['content_id'] = cid
            content = next(c for c in request('GET', base + '/contents') if c['id'] == cid)
            plan = next(p for p in request('GET', base + '/operations/plans') if p['id'] == PLAN_ID)
            assert plan['content_version_id'] == cid
            assert content['script'].strip() and any(word in content['script'] for word in ('示意', '合成'))
            source_assets = {a['id']: a for a in request('GET', base + '/assets')}
            shots = content['storyboard'].get('shots', []) if isinstance(content['storyboard'], dict) else content['storyboard']
            assert shots and abs(sum(s['duration'] for s in shots) - 30) <= .1
            for shot in shots:
                asset = source_assets[shot['asset_id']]
                assert asset['origin_type'] in ('test', 'template')
                assert not (asset.get('metadata_json') or {}).get('renderer')
                assert not (asset.get('metadata_json') or {}).get('content_version_id')
            state['script'] = content['script']
            state['cover_title'] = content['cover_title']
            state['checks']['same_plan_real_script'] = True
            save('script_and_source_assets_verified')
            rendered = queue_once('render_job_id', base + '/contents/' + cid + '/render')
            content = next(c for c in request('GET', base + '/contents') if c['id'] == cid)
            assert content['render_asset_id'] == rendered['result']['render_asset_id']
            video = client.get(base + '/assets/' + content['render_asset_id'] + '/file')
            video.raise_for_status()
            video_path = OUTPUT / 'example-tour.mp4'
            video_path.write_bytes(video.content)
            probe = subprocess.run([settings.ffprobe_binary, '-v', 'error', '-show_format', '-show_streams',
                '-of', 'json', str(video_path)], capture_output=True, text=True, check=True)
            info = json.loads(probe.stdout)
            assert abs(float(info['format']['duration']) - 30) <= .12
            v = next(s for s in info['streams'] if s['codec_type'] == 'video')
            assert (v['width'], v['height']) == (720, 1280)
            subprocess.run([settings.ffmpeg_binary, '-v', 'error', '-i', str(video_path), '-f', 'null', '-'],
                capture_output=True, check=True)
            state['render_asset_id'] = content['render_asset_id']
            state['render_bytes'] = len(video.content)
            state['checks']['actual_mp4_full_decode'] = True
            state['checks']['review_preview_same_content_and_render'] = True
            save('mp4_and_review_entry_ready')
            files = {'video.mp4': video.content, 'script.txt': content['script'].encode(),
                'cover-title.txt': content['cover_title'].encode()}
            for key, filename in (('cover_asset_id', 'cover.jpg'), ('subtitle_asset_id', 'subtitles.srt')):
                aid = rendered['result'].get(key)
                if aid:
                    file = client.get(base + '/assets/' + aid + '/file'); file.raise_for_status()
                    files[filename] = file.content
                    (OUTPUT / filename).write_bytes(file.content)
            preview = {'schema_version': 1, 'project_id': PROJECT_ID, 'plan_id': PLAN_ID,
                'content_version_id': cid, 'render_asset_id': content['render_asset_id'],
                'approval_state': 'pending_human_review', 'publication_state': 'not_published',
                'material_basis': 'synthetic_example', 'video_sha256': hashlib.sha256(video.content).hexdigest(),
                'title': content['cover_title']}
            files['preview.json'] = json.dumps(preview, ensure_ascii=False, indent=2).encode()
            files['readme.txt'] = '示例项目审核预览包。视频与图像仅作示意。尚未人工审核、未提交任何平台；正式平台发布包需在应用中审核当前成片后准备。'.encode()
            with zipfile.ZipFile(OUTPUT / 'review-preview.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            assert content.get('approval_valid') is False and not content.get('release_id')
            assert not request('GET', base + '/publications')
            report = request('GET', base + '/reports?window=D7')
            assert not report['publications']
            state['checks']['preview_package_and_unpublished_analytics'] = True
            state['manual_review'] = 'pending; actual checkbox and current render must be confirmed by the user'
            state['platform_publication'] = 'not attempted'
            state['checks']['no_fabricated_approval_or_receipt'] = True
            save('example_script_video_preview_package_complete')
        finally:
            request('POST', '/api/auth/logout')


if __name__ == '__main__':
    main()
