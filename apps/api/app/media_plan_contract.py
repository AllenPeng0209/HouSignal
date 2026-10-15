"""Pure preflight for native video plans, using the execution asset order.

No filesystem probes or model calls happen here. Asset authorization and file
integrity stay in native_skills._assets; actual codec duration stays in AV.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math

from fastapi import HTTPException


MEDIA_PLAN_SKILLS = frozenset({'auto-short-video', 'slideshow-video'})


def media_agent_contract(skill_name):
    if skill_name not in MEDIA_PLAN_SKILLS:
        return None
    properties = {
        'per': {'type': 'number', 'minimum': 0.1, 'maximum': 600, 'default': 3},
        'captions': {'type': 'array', 'items': {'type': 'string'},
                     'description': '按画面顺序提供真实字幕文字，不是true/false开关；可省略。'},
        'bgm_index': {'type': 'integer', 'minimum': 0,
                      'description': 'asset_ids原始完整列表中audio素材的零起始索引，不是UUID、路径或仅音频列表索引。'},
    }
    example = {'size': '1080x1920', 'bgm_index': 3}
    if skill_name == 'auto-short-video':
        properties['storyboard'] = {'type': 'array', 'minItems': 1, 'items': {
            'type': 'object', 'required': ['asset_index'], 'additionalProperties': False,
            'properties': {'asset_index': {'type': 'integer', 'minimum': 0,
                'description': 'asset_ids原始完整列表中image/video素材的零起始索引，必须填写。'},
                'duration': {'type': 'number', 'minimum': 0.1, 'maximum': 600,
                             'description': '秒；省略时使用per，默认3秒。'},
                'start': {'type': 'number', 'minimum': 0, 'maximum': 21600, 'default': 0},
                'caption': {'type': ['string', 'null']}}}}
        example['storyboard'] = [
            {'asset_index': 0, 'duration': 10, 'caption': '客厅'},
            {'asset_index': 1, 'duration': 10, 'caption': '餐厅'},
            {'asset_index': 2, 'duration': 10, 'caption': '露台'}]
    else:
        example.update(per=3, captions=['客厅', '餐厅', '露台'])
    return {'schema_version': 1, 'asset_order': 'asset_ids_exact_order_zero_based',
        'instructions': ['所有索引均按本次asset_ids完整原始顺序计算，不过滤、不重排，索引从0开始。',
            '画面只能选image/video（相册模式只接受image）；bgm_index必须指向audio。',
            'bgm不接受素材UUID或文件路径；请使用bgm_index。省略时原生执行器会选第一条音频。',
            'storyboard非空时每镜必须指定asset_index；不提供分镜或表单默认[]时使用per/captions模式。',
            '真实视频start必须小于原片时长，worker会再核验；不能以模型推测代替媒体时长。'],
        'parameters': {'type': 'object', 'properties': properties, 'additionalProperties': True},
        'example': {'asset_ids': ['image_0_id', 'image_1_id', 'image_2_id', 'audio_3_id'], 'parameters': example}}


def media_input_schema(skill_name, schema):
    """Expose native captions' real list form through the shared API parser."""
    if skill_name not in MEDIA_PLAN_SKILLS:
        return schema
    result = deepcopy(schema)
    for spec in result.get('parameters', []):
        if spec['key'] == 'captions':
            spec.update(type='text', format='json')
    return result


def validate_media_plan(skill_name, parameters, assets):
    """Validate/parse supplied data without inventing indices or reordering assets."""
    if skill_name not in MEDIA_PLAN_SKILLS:
        return parameters
    from .native_skills import _public_text

    names = '；'.join(f'{index}: {asset.get("kind", "unknown")} {_public_text(asset.get("name"), 160)}'
                     for index, asset in enumerate(assets)) or '（尚未选择素材）'
    issues = []
    def reject(message):
        issues.append(message)
    def numeric(value, label, low, high):
        if isinstance(value, bool):
            reject(f'{label}必须是{low}至{high}之间的数值')
            return
        try:
            parsed = float(value)
        except (ValueError, TypeError, OverflowError):
            reject(f'{label}必须是{low}至{high}之间的数值')
            return
        if not math.isfinite(parsed) or not low <= parsed <= high:
            reject(f'{label}必须是{low}至{high}之间的数值')
        return parsed
    def index(value, label, kinds):
        if type(value) is not int or not 0 <= value < len(assets):
            reject(f'{label}必须是asset_ids完整列表中的有效整数索引')
            return
        if assets[value].get('kind') not in kinds:
            reject(f'{label}={value}的素材类型不适合；需要{"/".join(sorted(kinds))}')
    result = dict(parameters)
    storyboard = result.get('storyboard')
    if isinstance(storyboard, str):
        try:
            storyboard = json.loads(storyboard) if storyboard.strip() else []
        except (TypeError, ValueError):
            reject('storyboard必须是有效JSON分镜数组')
            storyboard = None
        result['storyboard'] = storyboard
    if storyboard is not None:
        if not isinstance(storyboard, list):
            reject('storyboard必须是分镜数组')
            storyboard = []
        for scene_no, scene in enumerate(storyboard):
            label = f'storyboard[{scene_no}]'
            if not isinstance(scene, dict) or 'asset_index' not in scene:
                reject(f'{label}.asset_index缺失；每镜须明确指定画面素材索引')
                continue
            index(scene['asset_index'], f'{label}.asset_index', {'image', 'video'} if skill_name == 'auto-short-video' else {'image'})
            if set(scene) - {'asset_index', 'duration', 'start', 'caption'}:
                reject(f'{label}只支持asset_index、duration、start、caption')
            if 'duration' in scene:
                numeric(scene['duration'], f'{label}.duration', 0.1, 600)
            if 'start' in scene:
                start = scene['start']
                if isinstance(start, str) and ':' in start:
                    from .content_skills.tools.av import _time
                    try:
                        start = _time(start, 'scene start')
                    except (ValueError, TypeError):
                        reject(f'{label}.start时间格式无效')
                        start = None
                numeric(start, f'{label}.start', 0, 21600)
            if scene.get('caption') is not None and not isinstance(scene['caption'], str):
                reject(f'{label}.caption必须为字幕文字')
    if 'per' in result:
        numeric(result['per'], 'per', 0.1, 600)
    if result.get('bgm') not in (None, ''):
        reject('bgm不接受素材UUID或文件路径，请改用指向audio素材的bgm_index')
    if 'bgm_index' in result:
        index(result['bgm_index'], 'bgm_index', {'audio'})
    if 'captions' in result:
        captions = result['captions']
        if isinstance(captions, str):
            stripped = captions.strip()
            if stripped.lower() in {'true', 'false', 'null'}:
                reject('captions必须是字幕文字数组，不是true/false开关')
            if stripped.startswith(('[', '{')):
                try:
                    captions = json.loads(stripped)
                except (TypeError, ValueError):
                    reject('captions必须是有效JSON文字数组')
                    captions = None
            else:
                captions = captions.split('|') if captions else []
        if not isinstance(captions, list) or any(not isinstance(caption, str) for caption in captions):
            reject('captions必须是字幕文字数组')
        result['captions'] = captions
    if issues:
        raise HTTPException(422, '；'.join(issues) + f'。可用素材索引（asset_ids原始顺序，0开始）：{names}。'
            '请修正索引/参数后重试；不要填写文件路径或素材UUID代替索引。')
    return result
