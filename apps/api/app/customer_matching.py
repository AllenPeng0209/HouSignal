"""Read-only CRM matching against the selected project's verified resources."""
from __future__ import annotations

import math
import re

from fastapi import HTTPException
from sqlalchemy import select

from .business import source_assets
from .core import fact_dict, valid_fact
from .core_models import Fact
from .db import utcnow
from .secretary import _customer, _safe, profile_data
from .security import require_project


MATCHING_TOOLS = [{'type': 'function', 'function': {
    'name': 'match_customer_resources',
    'description': '按已保存CRM客户预算、面积与偏好，核对当前选中楼盘的已核验事实，并排序可用于制作的授权源素材。只匹配当前项目，不代表全市场推荐；价格或偏好证据缺失会明确待确认。返回真实素材ID供制作使用，不写入资料。',
    'parameters': {'type': 'object', 'properties': {
        'lead_id': {'type': 'string', 'minLength': 1, 'maxLength': 64},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 12}},
        'required': ['lead_id'], 'additionalProperties': False}}}]
MATCHING_NAMES = frozenset({'match_customer_resources'})
_PREFERENCES = {'江景': ('江景',), '安静': ('安静', '静谧', '清静', '低噪'),
                '低楼层': ('低楼层', '低层'), '四房': ('四房', '四居', '4房', '4居')}
_AMOUNT = r'\d+(?:\.\d+)?'
_AREA_UNIT = r'(?:平方米|平米|㎡|m²|m2)'


def _positive(text, term):
    for match in re.finditer(re.escape(term), text, re.I):
        if not re.search(r'(?:不(?:喜欢|要|需|是|靠)?|没有|无|非|排除|避免)[^，,、；;。]{0,3}$',
                         text[max(0, match.start() - 8):match.start()]):
            return True
    return False


def _budget_max(text):
    """Accept bounded currency amounts; never infer a unit or financing model."""
    text = re.sub(r'[\s,，]', '', text).replace('万元', '万').replace('亿元', '亿')
    prefix = r'(?:总价|总预算|预算|最高|上限|不超过|不高于|最多|控制在|为|是|[:：])*'
    match = re.fullmatch(prefix + rf'({_AMOUNT})(万|亿|元)?(?:[-~—至到]({_AMOUNT})(万|亿|元))?(?:以内|以下|封顶)?', text)
    if not match:
        return None
    low, low_unit, high, high_unit = match.groups()
    unit = high_unit if high else low_unit
    if unit is None:
        return None
    multiplier = {'万': 1, '亿': 10000, '元': 0.0001}
    maximum = float(high or low) * multiplier[unit]
    if high and float(low) * multiplier[low_unit or unit] > maximum:
        return None
    return maximum if math.isfinite(maximum) and maximum > 0 else None


def _area_bounds(text):
    number = rf'({_AMOUNT})'
    interval = re.search(number + rf'\s*{_AREA_UNIT}?\s*[-~—至到]\s*' + number + rf'\s*{_AREA_UNIT}', text)
    if interval:
        low, high = map(float, interval.groups())
        if 0 < low <= high:
            return low, high
    lower = re.search(rf'(?:不少于|不低于|至少|大于等于)\s*{number}\s*{_AREA_UNIT}', text)
    lower = lower or re.search(number + rf'\s*{_AREA_UNIT}\s*(?:以上|起)', text)
    upper = re.search(rf'(?:不超过|不高于|最多|小于等于)\s*{number}\s*{_AREA_UNIT}', text)
    upper = upper or re.search(number + rf'\s*{_AREA_UNIT}\s*(?:以内|以下)', text)
    return (float(lower.group(1)) if lower else None, float(upper.group(1)) if upper else None)


def _source(kind, identity, label, **extra):
    return {'kind': kind, 'id': identity, 'label': _safe(label),
            'observed_at': utcnow().isoformat(), **extra}


def execute_matching_tool(db, ctx, pid, name, args, *, raw_text=''):
    if name not in MATCHING_NAMES:
        raise ValueError('不支持的客户匹配工具')
    if (not isinstance(args, dict) or set(args) - {'lead_id', 'limit'}
            or not isinstance(args.get('lead_id'), str) or not 1 <= len(args['lead_id'].strip()) <= 64
            or type(args.get('limit', 6)) is not int or not 1 <= args.get('limit', 6) <= 12):
        raise HTTPException(422, '请提供有效客户编号及1至12条素材数量')
    project = require_project(db, ctx, pid)
    lead = _customer(db, ctx, pid, args['lead_id'])
    profile = profile_data(db, ctx, pid, lead.id)
    budget = _budget_max(profile.get('budget', ''))
    requirement_text = ' '.join((profile.get('requirements', ''), lead.need or ''))
    area_min, area_max = _area_bounds(requirement_text)
    explicit_preferences = profile.get('preferences', '').strip()
    preference_text = ' '.join((explicit_preferences, profile.get('requirements', ''),
                                '' if explicit_preferences else lead.need or ''))
    preferences = [label for label, aliases in _PREFERENCES.items()
                   if any(_positive(preference_text, alias) for alias in aliases)]
    constraints = {'budget_max_wan': budget, 'area_min_sqm': area_min,
                   'area_max_sqm': area_max, 'preferences': preferences}
    facts = [fact for fact in db.scalars(select(Fact).where(Fact.tenant_id == ctx.tenant_id,
             Fact.property_id == pid).order_by(Fact.semantic_key, Fact.id)).all() if valid_fact(fact)]
    reasons, unconfirmed, mismatches = [], [], []
    prices = []
    for fact in facts:
        if fact.semantic_key in {'total_price', 'price'} and fact.unit in {'万元', '亿元', '元'}:
            prices.append(float(fact.value.replace(',', '')) * {'万元': 1, '亿元': 10000, '元': 0.0001}[fact.unit])
    if budget is None:
        unconfirmed.append('CRM预算未明确或无法可靠解析，未推定预算匹配')
    elif not prices:
        unconfirmed.append('当前楼盘缺少已核验总价，无法确认预算匹配')
    elif len(set(prices)) > 1:
        unconfirmed.append('当前楼盘已核验总价不一致，需先确认适用报价')
    elif prices[0] > budget:
        mismatches.append(f'已核验总价{prices[0]:g}万元超过CRM预算上限{budget:g}万元')
    else:
        reasons.append(f'已核验总价{prices[0]:g}万元在CRM预算上限{budget:g}万元内')
    area = next((float(f.value.replace(',', '')) for f in facts if f.semantic_key == 'area'), None)
    if area_min is not None or area_max is not None:
        if area is None:
            unconfirmed.append('缺少已核验建筑面积，无法确认面积要求')
        elif area_min is not None and area < area_min or area_max is not None and area > area_max:
            mismatches.append(f'已核验建筑面积{area:g}平方米不在CRM面积要求内')
        else:
            reasons.append(f'已核验建筑面积{area:g}平方米符合CRM面积要求')
    factual_text = ' '.join(f.label + ' ' + f.value for f in facts)
    for preference in preferences:
        if any(_positive(factual_text, alias) for alias in _PREFERENCES[preference]):
            reasons.append(f'已核验楼盘事实包含偏好：{preference}')
        else:
            unconfirmed.append(f'偏好“{preference}”尚无已核验楼盘事实支持')
    if not preferences and profile.get('preferences', '').strip():
        unconfirmed.append('CRM其他偏好尚未结构化核对')
    if re.search(r'不喜欢|不要|排除|避免', preference_text):
        unconfirmed.append('CRM中明确排除的条件需进一步核对，未推定满足')
    qualification = 'not_match' if mismatches else 'partial' if unconfirmed else 'matches'
    candidates = source_assets(db, ctx, project)
    ranked = []
    for asset in candidates:
        metadata = asset.metadata_json or {}
        description = metadata.get('description', '')
        description = description if isinstance(description, str) else ''
        tags = [tag for tag in metadata.get('tags', []) if isinstance(tag, str)] if isinstance(metadata.get('tags', []), list) else []
        catalog = ' '.join((asset.name, description, ' '.join(tags)))
        matched = [label for label in preferences if any(_positive(catalog, alias) for alias in _PREFERENCES[label])]
        ranked.append({'id': asset.id, 'name': _safe(asset.name), 'kind': asset.kind,
            'description': _safe(description[:2000]), 'tags': [_safe(tag[:80]) for tag in tags[:30]],
            'score': len(matched), 'matched_preferences': matched,
            'scoring_basis': '名称、描述或标签每命中一项CRM已识别偏好计1分；未分析图片或视频画面',
            'is_test': project.is_test or asset.origin_type in {'test', 'template'},
            'origin_type': asset.origin_type, 'model_processing_authorized': metadata.get('model_processing_authorized') is True,
            'catalog_revision': metadata.get('catalog_revision', 1)})
    ranked.sort(key=lambda row: (-row['score'], row['name'], row['id']))
    selected = ranked[:args.get('limit', 6)]
    sources = [_source('lead', lead.id, lead.name),
               _source('page', pid, project.name + ' · 楼盘资料', page='property')]
    sources.extend(_source('fact', fact.id, fact.label, revision=fact.revision,
                          status=fact.status, valid=True) for fact in facts)
    sources.extend(_source('asset', row['id'], row['name'], metadata_only=True, page='assets',
                          revision=row['catalog_revision']) for row in selected)
    return {'data': {'scope': 'current_project', 'lead_id': lead.id, 'constraints': constraints,
        'property_match': {'project_id': pid, 'name': _safe(project.name), 'is_test': project.is_test,
            'qualification': qualification, 'reasons': reasons + mismatches, 'unconfirmed': unconfirmed},
        'verified_facts': [fact_dict(fact) for fact in facts], 'assets': selected,
        'candidate_asset_count': len(candidates),
        'scoring_basis': '仅核对当前项目已核验事实和CRM可解析约束；素材分数来自目录关键词，不是画面真实性判断'},
        'sources': sources, 'observed_at': utcnow().isoformat()}
