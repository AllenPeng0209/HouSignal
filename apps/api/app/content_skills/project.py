"""Native project records and numeric content analysis.

The statistical formulas and lunar calendar table are adapted from Easel's
Apache-2.0 shared/statistical tools. No skill files, global profiles, service,
or external account directory are consulted. Writes use HouSignal domain
services and stay in the caller's transaction.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import date, datetime, timedelta, timezone
import io
import ipaddress
import json
import math
from pathlib import Path
import re
import socket
import statistics
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from ..config import settings
from ..business_models import FollowUp, Lead, Publication
from ..core import asset_dict, fact_dict
from ..core_models import Asset, Fact
from ..operations import (account_dict, create_account, create_plan, list_accounts,
                          list_plans, update_account, update_plan)
from ..operations_models import ContentAccount, ContentPlan
from ..security import context_for_user, require_project


PROJECT_SKILLS = frozenset({
    'asset-manager', 'template-library', 'skill-content-calendar',
    'skill-content-calendar-log', 'skill-event-calendar', 'skill-profile-builder',
    'skill-profile-manager', 'skill-publish-scheduler', 'skill-publish-log',
    'roi-calculator', 'skill-comment-insights', 'skill-content-postmortem',
    'skill-data-tracker', 'skill-post-scorer', 'skill-publish-analytics',
    'skill-social-performance-review', 'skill-my-account', 'skill-short-link',
    'skill-publish-notify',
})
_ANALYTICS = frozenset({'roi-calculator', 'skill-comment-insights',
    'skill-content-postmortem', 'skill-data-tracker', 'skill-post-scorer',
    'skill-publish-analytics', 'skill-social-performance-review'})
_PROFILE = frozenset({'skill-profile-builder', 'skill-profile-manager'})
_CALENDAR = frozenset({'skill-content-calendar', 'skill-content-calendar-log', 'skill-publish-scheduler'})
_ROI_FIELDS = ('ad_spend', 'impressions', 'clicks', 'engagements', 'conversions',
               'avg_order_value', 'production_cost')
_COUNTS = ('views', 'likes', 'comments', 'saves', 'shares', 'inquiries', 'qualified')
_TITLES = {
    'asset-manager': '项目素材清单', 'template-library': '内容模板库',
    'skill-content-calendar': '项目内容日历', 'skill-content-calendar-log': '内容日历记录',
    'skill-event-calendar': '营销节点日历', 'skill-profile-builder': '项目账号画像',
    'skill-profile-manager': '项目画像管理', 'skill-publish-scheduler': '项目排期队列',
    'skill-publish-log': '本项目发布记录', 'roi-calculator': '内容投放 ROI',
    'skill-comment-insights': '评论量化洞察', 'skill-content-postmortem': '内容数值复盘',
    'skill-data-tracker': '内容指标趋势', 'skill-post-scorer': '历史互动评分',
    'skill-publish-analytics': '发布数据分析', 'skill-social-performance-review': '社媒效果复盘',
    'skill-my-account': '本项目账号', 'skill-short-link': '内容链接追踪',
    'skill-publish-notify': '发布通知',
}
_TEMPLATES = (
    {'id': 'property-tour', 'name': '实拍看房脚本', 'kind': 'video',
     'sections': ['开场：已核验空间事实', '逐镜实拍展示', '适用客群与使用场景', '咨询引导'],
     'required_inputs': ['核验事实', '已授权实拍素材', '账号画像'], 'topic_id': 'tour'},
    {'id': 'space-card', 'name': '空间知识卡片', 'kind': 'cards',
     'sections': ['封面主题', '一页一个空间观察', '图注与事实来源', '咨询引导'],
     'required_inputs': ['空间事实', '项目图片', '目标平台'], 'topic_id': 'space'},
    {'id': 'release-review', 'name': '发布效果复盘', 'kind': 'report',
     'sections': ['已核验发布记录', 'D7 与 D30 指标', '已观察差异', '下一轮待验证假设'],
     'required_inputs': ['发布回执', '已观察指标'], 'topic_id': 'tour'},
)


def _parameter_schema(skill_name: str) -> dict:
    """Internal parameter allowlist; the product exposes UI controls below."""
    if skill_name not in PROJECT_SKILLS:
        raise HTTPException(404, '项目技能不存在')
    properties = {'action': {'type': 'string', 'enum': ['read', 'export'], 'default': 'read'}}
    if skill_name in _PROFILE or skill_name in _CALENDAR:
        properties.update(action={'type': 'string', 'enum': ['read', 'export', 'create', 'update'], 'default': 'read'},
            account_id={'type': 'string'}, target_account_id={'type': 'string'}, plan_id={'type': 'string'},
            target_plan_id={'type': 'string'}, record={'type': 'object'},
            start_date={'type': 'string', 'format': 'date'}, end_date={'type': 'string', 'format': 'date'},
            due_only={'type': 'boolean', 'default': False}, today={'type': 'string', 'format': 'date'})
    elif skill_name in _ANALYTICS:
        properties.update(records={'type': 'array', 'maxItems': 5000}, asset_id={'type': 'string'},
            metric_window={'type': 'string', 'enum': ['D7', 'D30'], 'default': 'D7'},
            column={'type': 'string', 'default': 'content'},
            top_percent={'type': 'number', 'minimum': 0.1, 'maximum': 100, 'default': 10},
            top={'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20})
    elif skill_name == 'asset-manager':
        properties.update(keyword={'type': 'string'}, kind={'type': 'string'})
    elif skill_name == 'template-library':
        properties.update(action={'type': 'string', 'enum': ['read', 'export', 'apply'], 'default': 'read'},
                          template_id={'type': 'string', 'enum': [row['id'] for row in _TEMPLATES]},
                          record={'type': 'object'})
    elif skill_name == 'skill-event-calendar':
        properties.update(year={'type': 'integer', 'minimum': 1901, 'maximum': 2100}, festival={'type': 'string'})
    elif skill_name == 'skill-publish-log':
        properties['publication_id'] = {'type': 'string'}
    elif skill_name == 'skill-my-account':
        properties.update(action={'type': 'string', 'enum': ['read', 'export', 'verify'], 'default': 'read'},
                          account_id={'type': 'string'}, platform={'type': 'string'})
    elif skill_name == 'skill-short-link':
        properties.update(action={'type': 'string', 'enum': ['read', 'export', 'shorten'], 'default': 'read'},
                          url={'type': 'string'}, source={'type': 'string'}, medium={'type': 'string'},
                          campaign={'type': 'string'}, content={'type': 'string'}, term={'type': 'string'})
    elif skill_name == 'skill-publish-notify':
        properties.update(action={'type': 'string', 'enum': ['draft', 'read', 'export', 'send'], 'default': 'draft'},
                          publication_id={'type': 'string'}, confirmed={'type': 'boolean', 'default': False})
    return {'type': 'object', 'properties': properties, 'additionalProperties': False}


def definition(skill_name: str) -> dict:
    labels = {'action': '操作', 'account_id': '本项目账号编号', 'target_account_id': '要修改的账号编号',
              'plan_id': '本项目计划编号', 'target_plan_id': '要修改的计划编号', 'record': '记录字段 JSON',
              'records': '分析数据 JSON 数组', 'asset_id': '所选数据素材编号', 'metric_window': '观察窗口',
              'column': '评论字段', 'top_percent': '高互动样本比例（%）', 'top': '高频词数量',
              'start_date': '开始日期', 'end_date': '结束日期', 'today': '到期比较日期',
              'due_only': '只看已到期候选计划', 'keyword': '素材检索词', 'kind': '素材类型',
              'template_id': '标准模板', 'year': '年份', 'festival': '节日名称',
              'publication_id': '本项目发布记录编号', 'platform': '平台', 'url': '原内容链接',
              'source': 'UTM 来源', 'medium': 'UTM 媒介', 'campaign': 'UTM 活动',
              'content': 'UTM 内容', 'term': 'UTM 关键词',
              'confirmed': '确认发送至本项目已配置通知目标'}
    action_labels = {'read': '查看项目记录', 'export': '导出报告', 'create': '创建候选记录',
                     'update': '修改项目记录', 'draft': '生成通知草稿',
                     'apply': '将模板套用为候选计划', 'shorten': '提交至已配置短链服务',
                     'send': '发送至已配置通知目标', 'verify': '校验已登录的平台身份'}
    parameters = []
    for name, schema in _parameter_schema(skill_name)['properties'].items():
        control = {'key': name, 'label': labels.get(name, name), 'required': False}
        if 'enum' in schema:
            control.update(type='select', options=[{'value': value, 'label': action_labels.get(value, value)} for value in schema['enum']])
        elif schema['type'] in ('integer', 'number'):
            control['type'] = 'number'
        elif schema['type'] == 'boolean':
            control['type'] = 'boolean'
        else:
            control['type'] = 'text'
            if schema['type'] in ('array', 'object'):
                control['format'] = 'json'
            elif schema.get('format') == 'date':
                control.update(format='date', placeholder='YYYY-MM-DD')
        for key in ('default', 'minimum', 'maximum'):
            if key in schema:
                control[{'minimum': 'min', 'maximum': 'max'}.get(key, key)] = schema[key]
        parameters.append(control)
    return {'name': skill_name, 'engine': 'project', 'input_schema': {
        'accepts_assets': skill_name in _ANALYTICS, 'requires_model_authorization': False,
        'input_required': False, 'parameters': parameters}}


def readiness(skill_name: str | None = None) -> dict:
    actions = {'read': 'ready', 'export': 'ready', 'draft': 'ready', 'send': 'setup', 'shorten': 'setup'}
    for kind, action in (('notification', 'send'), ('short_link', 'shorten')):
        if skill_name is None or skill_name == {'notification': 'skill-publish-notify', 'short_link': 'skill-short-link'}[kind]:
            try:
                _provider_endpoint(kind, resolve=False)
                actions[action] = 'ready'
            except HTTPException:
                pass
    return {'status': 'ready', 'execution_engine': 'housignal_project',
            'reason': '默认本地计算、导出或通知草稿；短链与发送动作须有明确配置与授权',
            'actions': actions, 'network_required': False}


def _query(model, ctx, pid):
    return select(model).where(model.tenant_id == ctx.tenant_id, model.property_id == pid)


def _rows(db, model, ctx, pid):
    timestamp = getattr(model, 'created_at', None)
    if timestamp is None:
        timestamp = model.updated_at
    return list(db.scalars(_query(model, ctx, pid).order_by(timestamp, model.id).limit(5000)).all())


def _record(db, model, ctx, pid, rid):
    row = db.scalar(_query(model, ctx, pid).where(model.id == rid))
    if row is None:
        raise HTTPException(404, '记录不存在或无权访问')
    return row


def _date(value, default=None):
    if value is None or value == '':
        return default
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, '日期请填写有效 YYYY-MM-DD') from None


def _publications(db, ctx, pid):
    return [{'id': row.id, 'title': row.title, 'channel': row.channel, 'url': row.url,
             'platform_state': row.platform_state, 'receipt_status': row.receipt_status,
             'metrics': row.metrics or {'D7': None, 'D30': None},
             'created_at': row.created_at.isoformat()} for row in _rows(db, Publication, ctx, pid)]


def _number(value, label):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise HTTPException(422, f'{label}必须是非负有限数值')
    try:
        result = float(value.replace(',', '').strip()) if isinstance(value, str) else float(value)
    except (ValueError, TypeError):
        raise HTTPException(422, f'{label}必须是非负有限数值') from None
    if not math.isfinite(result) or result < 0:
        raise HTTPException(422, f'{label}必须是非负有限数值')
    return result


def _ratio(numerator, denominator, scale=1):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * scale


def _sum_observed(values):
    observed = [value for value in values if value is not None]
    return sum(observed) if observed else None


def _alias(row, *keys):
    for key in keys:
        if key in row and row[key] is not None and row[key] != '':
            return _number(row[key], key)
    return None


def _unwrap(value, skill):
    if isinstance(value, dict):
        for key in ('records', 'campaigns', 'posts', 'items', 'comments', 'data'):
            if key in value:
                value = value[key]
                break
        else:
            value = [value]
    if not isinstance(value, list) or not value or len(value) > 5000:
        raise HTTPException(422, '请输入1到5000条非空数据记录；空数据不能生成完整分析交付')
    if not all(isinstance(row, dict) or (skill == 'skill-comment-insights' and isinstance(row, str)) for row in value):
        raise HTTPException(422, '每条数据必须是对象；评论也可以是字符串')
    return value


def _input_records(db, ctx, pid, skill, parameters, assets, input_text):
    if 'records' in parameters:
        return _unwrap(parameters['records'], skill), [{'type': 'user_input', 'label': '本次提供的数据，待核验'}]
    asset_id = parameters.get('asset_id')
    if asset_id:
        record = _record(db, Asset, ctx, pid, asset_id)
        selected = next((row for row in assets if isinstance(row, dict) and row.get('id') == asset_id), None)
        if selected is None or not selected.get('path'):
            raise HTTPException(422, '请选择已核验的本项目数据素材')
        if record.rights != 'authorized' or not record.rights_evidence:
            raise HTTPException(409, '数据素材尚未确认使用授权')
        path = Path(selected['path'])
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            raise HTTPException(422, '数据素材不存在或超过 5 MiB')
        try:
            raw = path.read_text(encoding='utf-8-sig')
            if path.suffix.lower() == '.json':
                value = json.loads(raw)
            elif path.suffix.lower() == '.csv':
                value = list(csv.DictReader(io.StringIO(raw)))
            elif path.suffix.lower() == '.txt' and skill == 'skill-comment-insights':
                value = [line.strip() for line in raw.splitlines() if line.strip()]
            else:
                raise HTTPException(422, '统计素材请使用 JSON、CSV；评论也支持 TXT')
        except (UnicodeError, ValueError, csv.Error, OSError):
            raise HTTPException(422, '数据素材不是有效 UTF-8 JSON、CSV 或评论文本') from None
        return _unwrap(value, skill), [{'type': 'asset', 'id': record.id, 'label': record.name}]
    if input_text.strip():
        try:
            value = json.loads(input_text)
        except ValueError:
            if skill != 'skill-comment-insights':
                raise HTTPException(422, '请输入 JSON 数据或选择当前项目 CSV/JSON 素材') from None
            value = [line.strip() for line in input_text.splitlines() if line.strip()]
        return _unwrap(value, skill), [{'type': 'user_input', 'label': '本次提供的数据，待核验'}]
    window = parameters.get('metric_window', 'D7')
    publications = _publications(db, ctx, pid)
    records = []
    for row in publications:
        metrics = row['metrics'].get(window)
        records.append({'id': row['id'], 'title': row['title'], 'channel': row['channel'],
                        'created_at': row['created_at'], 'metrics_observed': isinstance(metrics, dict),
                        **(metrics if isinstance(metrics, dict) else {})})
    if not records and skill != 'skill-data-tracker':
        raise HTTPException(409, '当前项目没有可分析的发布记录，请提供数据或选择已授权的数据素材')
    return records, [{'type': 'publication', 'id': row['id'], 'url': row['url']} for row in publications]


def _roi(records):
    results = []
    for index, row in enumerate(records):
        values = {key: _number(row.get(key), key) for key in _ROI_FIELDS}
        spend, impressions, clicks, engagements, conversions, price, production = [values[key] for key in _ROI_FIELDS]
        revenue = conversions * price if conversions is not None and price is not None else None
        cost = (spend or 0) + (production or 0)
        profit = revenue - cost if revenue is not None else None
        paid = spend is not None and spend > 0
        metrics = {'ctr_pct': _ratio(clicks, impressions, 100),
                   'engagement_rate_pct': _ratio(engagements, impressions, 100),
                   'conversion_rate_pct': _ratio(conversions, clicks, 100),
                   'cpc': _ratio(spend, clicks) if paid else None,
                   'cpm': _ratio(spend, impressions, 1000) if paid else None,
                   'cpe': _ratio(spend, engagements) if paid else None,
                   'cpa': _ratio(spend, conversions) if paid else None,
                   'revenue': revenue if paid else None, 'total_cost': (cost or None) if paid else None,
                   'profit': profit if paid else None, 'roas': _ratio(revenue, spend) if paid else None,
                   'roi_pct': _ratio(profit, cost, 100) if paid else None}
        warnings = []
        if clicks is not None and impressions is not None and clicks > impressions:
            warnings.append('点击量大于曝光量，请核对数据口径')
        if conversions is not None and clicks is not None and conversions > clicks:
            warnings.append('转化数大于点击量，请核对数据口径')
        results.append({'name': str(row.get('name') or row.get('title') or f'活动 {index + 1}'),
                        'mode': 'paid' if paid else 'organic', 'inputs': values,
                        'metrics': metrics, 'warnings': warnings})
    results.sort(key=lambda row: (row['metrics']['roi_pct'] is None, -(row['metrics']['roi_pct'] or 0)))
    observed = []
    for index, row in enumerate(results, 1):
        row['roi_rank'] = index
        if row['metrics']['roi_pct'] is not None:
            observed.append(row['metrics']['roi_pct'])
    observed_records = sum(any(row['metrics'][key] is not None for key in
                           ('ctr_pct', 'engagement_rate_pct', 'conversion_rate_pct', 'roi_pct')) for row in results)
    return {'summary': {'campaign_count': len(results), 'avg_roi_pct': statistics.mean(observed) if observed else None},
            'ranking': results,
            'data_quality': {'status': 'calculated' if observed_records else 'insufficient_data',
                             'observed_records': observed_records,
                             'note': '成本或活动名称本身不足以证明ROI；缺失分子分母保持未知'},
            'limitations': ['营收基于转化数乘客单价，不等于已收款流水；有机模式不计算付费成本指标']}


def _metric_analysis(records, skill, parameters):
    scored = []
    for row in records:
        values = {key: _number(row.get(key), key) for key in _COUNTS}
        values['views'] = _alias(row, 'views', 'impressions', '播放', '曝光')
        values['likes'] = _alias(row, 'likes', 'reactions', '点赞')
        values['comments'] = _alias(row, 'comments', '评论')
        engagements = _alias(row, 'engagements', '互动')
        if engagements is None:
            engagements = _sum_observed(values[key] for key in ('likes', 'comments', 'saves', 'shares'))
        score = None if values['likes'] is None and values['comments'] is None else (values['likes'] or 0) + (values['comments'] or 0) * 3
        scored.append({'id': row.get('id'), 'title': str(row.get('title') or row.get('name') or ''),
                       'channel': str(row.get('channel') or row.get('platform') or ''),
                       'created_at': row.get('created_at') or row.get('date'), **values,
                       'engagements': engagements, 'engagement_score': score,
                       'engagement_rate_pct': _ratio(engagements, values['views'], 100),
                       'observed_fields': [key for key in _COUNTS if values[key] is not None]})
    summary = {key: _sum_observed(row[key] for row in scored) for key in (*_COUNTS, 'engagements')}
    comparable = [row for row in scored if row['engagements'] is not None and row['views'] is not None]
    summary.update(publication_count=len(scored), metrics_available=sum(bool(row['observed_fields']) for row in scored),
                   rate_sample_count=len(comparable),
                   engagement_rate_pct=_ratio(_sum_observed(row['engagements'] for row in comparable),
                                              _sum_observed(row['views'] for row in comparable), 100))
    limitations = ['未观察指标保留为 null；只基于提供的数据，不推断未公开播放量、收入或真实客户转化',
                   '跨作品求和不代表去重用户；不同观察窗口不混算；汇总互动率只使用播放量与互动量均已观察的同批样本']
    if skill == 'skill-post-scorer':
        ranking = sorted([row for row in scored if row['engagement_score'] is not None], key=lambda row: -row['engagement_score'])
        top_percent = _number(parameters.get('top_percent', 10), 'top_percent')
        if top_percent is None or not 0 < top_percent <= 100:
            raise HTTPException(422, 'top_percent须大于0且不超过100')
        top_count = max(1, round(len(ranking) * top_percent / 100)) if ranking else 0
        scores = [row['engagement_score'] for row in ranking]
        summary.update(top_percent=top_percent, top_count=top_count,
                       top_threshold_score=ranking[top_count - 1]['engagement_score'] if top_count else None,
                       avg_score=statistics.mean(scores) if scores else None,
                       median_score=statistics.median(scores) if scores else None,
                       engagement_formula='点赞 + 评论×3')
        limitations.append('这是历史互动评分；草稿的定性预测需要文字模型与人工判断')
        return {'summary': summary, 'top_posts': ranking[:top_count],
                'data_quality': {'status': 'calculated' if ranking else 'insufficient_data',
                                 'observed_records': len(ranking)}, 'limitations': limitations}
    groups = {}
    for row in scored:
        group = groups.setdefault(row['channel'] or '未注明平台', [])
        group.append(row)
    by_channel = [{'channel': channel, 'count': len(rows),
                   'views': _sum_observed(row['views'] for row in rows),
                   'engagements': _sum_observed(row['engagements'] for row in rows)} for channel, rows in groups.items()]
    ordered = sorted(scored, key=lambda row: str(row['created_at'] or ''))
    trends = []
    for before, after in zip(ordered, ordered[1:]):
        old, new = before['views'], after['views']
        trends.append({'previous_id': before['id'], 'current_id': after['id'],
                       'views_change': new - old if old is not None and new is not None else None,
                       'views_change_pct': _ratio(new - old, old, 100) if old is not None and new is not None else None,
                       'comparison': '相邻作品观察值差异，不代表同一作品或账号的增长'})
    result = {'summary': summary, 'rows': scored, 'by_channel': by_channel,
              'comparisons': trends, 'metric_window': parameters.get('metric_window', 'D7'),
              'limitations': limitations}
    if skill == 'skill-content-postmortem':
        observed_scores = [row['engagement_score'] for row in scored if row['engagement_score'] is not None]
        median = statistics.median(observed_scores) if observed_scores else None
        result['cohort'] = {'observed_count': len(observed_scores), 'median_engagement_score': median,
                            'high_performer_rule': '互动分达到样本中位数3倍，且严格高于中位数'}
        result['high_performers'] = [row for row in scored if median is not None and row['engagement_score'] is not None
                                     and row['engagement_score'] >= median * 3 and row['engagement_score'] > median]
        limitations.append('高互动只能证明观察值差异；标题、选题与时间的因果解释需进一步研究')
    elif skill == 'skill-data-tracker':
        series = []
        for row in records:
            if 'followers' in row or 'fans' in row:
                observed_date = _date(row.get('date') or str(row.get('created_at') or '')[:10])
                if observed_date is None:
                    raise HTTPException(422, '粉丝快照须提供date观察日期')
                series.append({'date': observed_date.isoformat(),
                               'account_id': str(row.get('account_id') or row.get('account') or ''),
                               'followers': _alias(row, 'followers', 'fans')})
        series.sort(key=lambda row: (row['account_id'], row['date']))
        changes, previous = [], {}
        for row in series:
            if row['followers'] is None:
                continue
            before = previous.get(row['account_id'])
            if before:
                delta = row['followers'] - before['followers']
                changes.append({'account_id': row['account_id'], 'from_date': before['date'], 'to_date': row['date'],
                                'delta': delta, 'growth_pct': _ratio(delta, before['followers'], 100),
                                'elapsed_days': (date.fromisoformat(row['date']) - date.fromisoformat(before['date'])).days})
            previous[row['account_id']] = row
        result.update(follower_series=series, follower_changes=changes)
        limitations.append('粉丝增量只比较同账号已有快照；缺失日不填零也不插值')
    elif skill == 'skill-social-performance-review':
        result['review_items'] = [{'channel': row['channel'], 'observed_publications': row['count'],
                                  'views': row['views'], 'engagements': row['engagements'],
                                  'next_step': '补齐缺失观察窗口' if row['views'] is None else '对照同平台历史样本复核内容差异'} for row in by_channel]
    observed_records = sum(bool(row['observed_fields']) or row['engagements'] is not None for row in scored)
    if skill == 'skill-data-tracker':
        observed_records = max(observed_records, sum(row['followers'] is not None for row in result['follower_series']))
    result['data_quality'] = {'status': 'calculated' if observed_records else 'insufficient_data',
                              'observed_records': observed_records,
                              'note': '只证明已有数据的计算；不证明未采集的平台数据或因果结论'}
    return result


_POSITIVE = set('喜欢 爱了 绝了 绝绝子 yyds 推荐 种草 好用 值得 赞 太棒 厉害 优秀 完美 真香 可爱 好看 划算 满意 惊喜 惊艳 治愈 舒服 期待 支持 感谢 谢谢 强推 好评 靠谱 良心 实用 干货 收藏 有用 nice 温柔 心动'.split())
_NEGATIVE = set('差 垃圾 退货 坑 避雷 踩雷 翻车 失望 难用 难看 贵 骗人 假货 烂 无语 恶心 后悔 浪费 拉胯 别买 劝退 敷衍 套路 割韭菜 智商税 差评 一般般 糊弄 坑爹 反感 讨厌 bug 退款 态度差 不值 慎买 难受 抵制'.split())
_DEMAND = re.compile(r'求|想要|想买|想了解|在哪买|哪里买|链接|多少钱|价格|怎么买|能不能|可不可以|教程|怎么做|求推荐|求同款')
_COMPLAINT = re.compile(r'差|垃圾|退|坑|避雷|翻车|失望|贵|骗|假|烂|后悔|浪费|劝退|套路|退款')
_QUESTION = re.compile(r'[?？]|哪里|在哪|怎么|是否|多少|能不能|可以吗')
_STOP = set('这个 那个 什么 怎么 哪里 位置 我们 你们 他们 一个 真的 非常 感觉 觉得 可以 没有 不是 这么 那么 现在 已经 应该 可能 大家'.split())


def _comment_analysis(records, parameters):
    column = parameters.get('column', 'content')
    comments = [str(row if isinstance(row, str) else row.get(column) or row.get('text') or row.get('comment') or '').strip() for row in records]
    comments = [value[:10000] for value in comments if value]
    if not comments:
        raise HTTPException(422, '请提供评论文本；播放等指标不能替代评论内容')
    buckets = {'positive': [], 'neutral': [], 'negative': []}
    words = Counter()
    phrases = Counter()
    tokenizer = None
    try:
        import jieba.posseg as tokenizer
    except ImportError:
        pass
    for text in comments:
        score = sum(word in text.lower() for word in _POSITIVE) - sum(word in text.lower() for word in _NEGATIVE)
        buckets['positive' if score > 0 else 'negative' if score < 0 else 'neutral'].append(text)
        if tokenizer:
            tokens = [word for word, flag in tokenizer.cut(text) if len(word) >= 2 and word not in _STOP and flag.startswith(('n', 'v', 'a', 'l', 'i'))]
        else:
            tokens = re.findall(r'[a-zA-Z]{2,}|[\u4e00-\u9fff]{2,}', text)
            tokens = [word for word in tokens if word not in _STOP]
        words.update(tokens)
        phrases.update(tokens[index] + tokens[index + 1] for index in range(len(tokens) - 1))
    total = len(comments)
    top = parameters.get('top', 20)
    if type(top) is not int or not 1 <= top <= 100:
        raise HTTPException(422, 'top须为1到100的整数')
    result = {'total': total, 'sentiment': {key: {'count': len(values), 'ratio': len(values) / total, 'examples': values[:3]} for key, values in buckets.items()},
              'keywords': [{'word': word, 'count': count} for word, count in words.most_common(top)],
              'phrases': [{'phrase': phrase, 'count': count} for phrase, count in phrases.most_common(top) if count >= 2],
              'intents': {}, 'method': '本地社媒情感词典规则基线；情感判断需要人工复核',
              'keyword_method': 'jieba词性分词' if tokenizer else '文本片段词频（未安装中文分词器）'}
    for key, pattern in (('demand', _DEMAND), ('complaint', _COMPLAINT), ('question', _QUESTION)):
        matches = [text for text in comments if pattern.search(text)]
        result['intents'][key] = {'count': len(matches), 'examples': matches[:5]}
    return result


# Standard 1900–2100 lunarInfo table, migrated from Easel's lunar.py. The table
# lineage is jjonline/calendar.js and Lunar-Solar-Calendar-Converter. The module
# exposes data operations rather than the original CLI or skill instructions.
_LUNAR_INFO = (
    0x04bd8,0x04ae0,0x0a570,0x054d5,0x0d260,0x0d950,0x16554,0x056a0,0x09ad0,0x055d2,
    0x04ae0,0x0a5b6,0x0a4d0,0x0d250,0x1d255,0x0b540,0x0d6a0,0x0ada2,0x095b0,0x14977,
    0x04970,0x0a4b0,0x0b4b5,0x06a50,0x06d40,0x1ab54,0x02b60,0x09570,0x052f2,0x04970,
    0x06566,0x0d4a0,0x0ea50,0x06e95,0x05ad0,0x02b60,0x186e3,0x092e0,0x1c8d7,0x0c950,
    0x0d4a0,0x1d8a6,0x0b550,0x056a0,0x1a5b4,0x025d0,0x092d0,0x0d2b2,0x0a950,0x0b557,
    0x06ca0,0x0b550,0x15355,0x04da0,0x0a5b0,0x14573,0x052b0,0x0a9a8,0x0e950,0x06aa0,
    0x0aea6,0x0ab50,0x04b60,0x0aae4,0x0a570,0x05260,0x0f263,0x0d950,0x05b57,0x056a0,
    0x096d0,0x04dd5,0x04ad0,0x0a4d0,0x0d4d4,0x0d250,0x0d558,0x0b540,0x0b6a0,0x195a6,
    0x095b0,0x049b0,0x0a974,0x0a4b0,0x0b27a,0x06a50,0x06d40,0x0af46,0x0ab60,0x09570,
    0x04af5,0x04970,0x064b0,0x074a3,0x0ea50,0x06b58,0x055c0,0x0ab60,0x096d5,0x092e0,
    0x0c960,0x0d954,0x0d4a0,0x0da50,0x07552,0x056a0,0x0abb7,0x025d0,0x092d0,0x0cab5,
    0x0a950,0x0b4a0,0x0baa4,0x0ad50,0x055d9,0x04ba0,0x0a5b0,0x15176,0x052b0,0x0a930,
    0x07954,0x06aa0,0x0ad50,0x05b52,0x04b60,0x0a6e6,0x0a4e0,0x0d260,0x0ea65,0x0d530,
    0x05aa0,0x076a3,0x096d0,0x04afb,0x04ad0,0x0a4d0,0x1d0b6,0x0d250,0x0d520,0x0dd45,
    0x0b5a0,0x056d0,0x055b2,0x049b0,0x0a577,0x0a4b0,0x0aa50,0x1b255,0x06d20,0x0ada0,
    0x14b63,0x09370,0x049f8,0x04970,0x064b0,0x168a6,0x0ea50,0x06b20,0x1a6c4,0x0aae0,
    0x0a2e0,0x0d2e3,0x0c960,0x0d557,0x0d4a0,0x0da50,0x05d55,0x056a0,0x0a6d0,0x055d4,
    0x052d0,0x0a9b8,0x0a950,0x0b4a0,0x0b6a6,0x0ad50,0x055a0,0x0aba4,0x0a5b0,0x052b0,
    0x0b273,0x06930,0x07337,0x06aa0,0x0ad50,0x14b55,0x04b60,0x0a570,0x054e4,0x0d160,
    0x0e968,0x0d520,0x0daa0,0x16aa6,0x056d0,0x04ae0,0x0a9d4,0x0a2d0,0x0d150,0x0f252,
    0x0d520,
)


def _lunar_months(year):
    if type(year) is not int or not 1900 <= year <= 2100:
        raise HTTPException(422, '农历年份支持1900到2100')
    info = _LUNAR_INFO[year - 1900]
    result = []
    for month in range(1, 13):
        result.append((month, False, 30 if info & (0x10000 >> month) else 29))
        if month == info & 0xf:
            result.append((month, True, 30 if info & 0x10000 else 29))
    return result


def _lunar_to_solar(year, month, day):
    offset = sum(sum(length for _, _, length in _lunar_months(previous)) for previous in range(1900, year))
    for current, leap, length in _lunar_months(year):
        if current == month and not leap:
            if not 1 <= day <= length:
                raise HTTPException(422, '农历日期不存在')
            return date(1900, 1, 31) + timedelta(days=offset + day - 1)
        offset += length
    raise HTTPException(422, '农历月份不存在')


def _events(parameters):
    year = parameters.get('year', datetime.now(ZoneInfo('Asia/Shanghai')).year)
    if type(year) is not int or not 1901 <= year <= 2100:
        raise HTTPException(422, '营销日历年份支持1901到2100')
    lunar = {'春节': (1, 1, 0), '元宵': (1, 15, 0), '龙抬头': (2, 2, 0),
             '端午': (5, 5, 0), '七夕': (7, 7, 0), '中元': (7, 15, 0),
             '中秋': (8, 15, 0), '重阳': (9, 9, 0), '腊八': (12, 8, -1), '小年': (12, 23, -1)}
    entries = [{'name': name, 'date': _lunar_to_solar(year + offset, month, day).isoformat(),
                'calendar': 'lunar', 'status': 'candidate'} for name, (month, day, offset) in lunar.items()]
    entries.append({'name': '除夕', 'date': (_lunar_to_solar(year, 1, 1) - timedelta(days=1)).isoformat(), 'calendar': 'lunar', 'status': 'candidate'})
    for name, month, day in (('元旦', 1, 1), ('国际妇女节', 3, 8), ('劳动节', 5, 1),
                             ('儿童节', 6, 1), ('教师节', 9, 10), ('国庆节', 10, 1),
                             ('双十一', 11, 11), ('双十二', 12, 12)):
        entries.append({'name': name, 'date': date(year, month, day).isoformat(), 'calendar': 'solar', 'status': 'candidate'})
    if parameters.get('festival'):
        entries = [row for row in entries if row['name'] == parameters['festival']]
        if not entries:
            raise HTTPException(422, '不支持该节日名称')
    return {'year': year, 'events': sorted(entries, key=lambda row: row['date']),
            'note': '离线日期换算；候选内容节点不是政府调休安排或已确认平台活动，不自动写入发布队列'}


def _calendar(db, ctx, pid, parameters):
    plans = list_plans(db, ctx, pid)
    start, end = _date(parameters.get('start_date')), _date(parameters.get('end_date'))
    if start and end and end < start:
        raise HTTPException(422, '结束日期不能早于开始日期')
    today = _date(parameters.get('today'), datetime.now(ZoneInfo('Asia/Shanghai')).date())
    selected = []
    for row in plans:
        day = _date(row['scheduled_for'])
        if (start or end or parameters.get('due_only')) and day is None:
            continue
        if start and day < start or end and day > end:
            continue
        if parameters.get('due_only') and (day > today or row['status'] == 'completed'):
            continue
        selected.append(row)
    selected.sort(key=lambda row: (row['scheduled_for'] or '9999-12-31', row['id']))
    return {'plans': selected, 'publications': _publications(db, ctx, pid),
            'today': today.isoformat(), 'note': '项目内容计划；真实发布须在账号页使用通过审核的发布包'}


def _write_domain(db, ctx, pid, skill, parameters):
    action = parameters.get('action', 'read')
    record = parameters.get('record')
    if not isinstance(record, dict):
        raise HTTPException(422, '创建或修改请填写 record 对象')
    if skill in _PROFILE:
        if action == 'create':
            result = create_account(db, ctx, pid, record)
        else:
            aid = parameters.get('target_account_id') or parameters.get('account_id')
            if not aid:
                raise HTTPException(422, '修改画像请先选择本项目账号')
            result = update_account(db, ctx, pid, aid, record)
        return {'record': result, 'changed_account_id': result['id']}, {'changed_account_id': result['id']}
    body = dict(record)
    if body.get('status') not in (None, 'draft'):
        raise HTTPException(422, '技能保存候选计划为draft；真实发布和完成状态请走发布流程')
    body['status'] = 'draft'
    if action == 'create':
        _ensure_outer_transaction(db)
        result = create_plan(db, ctx, pid, body)
    else:
        plan_id = parameters.get('target_plan_id') or parameters.get('plan_id')
        if not plan_id:
            raise HTTPException(422, '修改日历请先选择本项目计划')
        result = update_plan(db, ctx, pid, plan_id, body)
    return {'record': result, 'changed_plan_id': result['id'], 'note': '已保存候选计划，等待人工核验'}, {'changed_plan_id': result['id']}


def _ensure_outer_transaction(db):
    """SQLite's deferred BEGIN must precede a domain service SAVEPOINT.

    Releasing SQLite's first savepoint otherwise commits the insert before the
    worker's cancellation/permission fence can commit or roll it back.
    """
    if db.get_bind().dialect.name == 'sqlite':
        connection = db.connection()
        driver = connection.connection.driver_connection
        if not driver.in_transaction:
            connection.exec_driver_sql('BEGIN')


def _apply_template(db, ctx, pid, project, template, parameters):
    record = parameters.get('record', {})
    if not isinstance(record, dict):
        raise HTTPException(422, '模板套用的record必须是对象')
    body = dict(record)
    if body.get('status') not in (None, 'draft'):
        raise HTTPException(422, '模板只能生成候选计划，不能跳过内容审核或自动发布')
    body.setdefault('title', f'{project.name}｜{template["name"]}')
    body.setdefault('channel', project.channel)
    body.setdefault('topic_id', template['topic_id'])
    body['status'] = 'draft'
    sections = '\n'.join(f'{index}. {section}' for index, section in enumerate(template['sections'], 1))
    original = body.get('brief', '')
    if not isinstance(original, str):
        raise HTTPException(422, 'brief必须是文字')
    body['brief'] = f'原生模板：{template["name"]}\n{sections}\n\n所需输入：{"、".join(template["required_inputs"])}\n{original}'.strip()
    _ensure_outer_transaction(db)
    return create_plan(db, ctx, pid, body)


_PRIVATE_QUERY = re.compile(r'^(?:.*[_-])?(?:token|password|passwd|secret|key|apikey|authorization|cookie|session|signature|sign|email|phone|mobile|contact|code)(?:[_-].*)?$', re.I)
_SECRET_ASSIGNMENT = re.compile(r'(?:api[_ -]?key|access[_ -]?key|token|secret|password|cookie|authorization|密码|密钥|凭证)\s*[:=：]\s*[^\s,;，；]+', re.I)
_PRIVATE_PATH = re.compile(r'(?<![\w:/])/(?:Users|private|tmp|var|home|mnt|Volumes|opt|runtime|etc|root)(?:/[^\s,;，；)]+)+')


def _secret(value):
    if hasattr(value, 'get_secret_value'):
        value = value.get_secret_value()
    return str(value or '')


def _known_secrets():
    return [_secret(getattr(settings, name, '')) for name in
            ('native_short_link_api_key', 'native_notification_key', 'native_notification_webhook_url')
            if _secret(getattr(settings, name, ''))]


def _public_host(host):
    lowered = host.lower()
    if (not lowered or '.' not in lowered or lowered.endswith(('.local', '.internal', '.localhost', '.localdomain', '.lan', '.home.arpa'))
            or lowered in ('localhost', 'localhost.localdomain') or lowered.isdecimal()
            or lowered.startswith('0x')):
        return False
    try:
        return ipaddress.ip_address(lowered).is_global
    except ValueError:
        return bool(re.fullmatch(r'[a-z0-9.-]+', lowered))


def _public_link(value):
    """Public content URLs exclude login tokens and private query data."""
    if not isinstance(value, str) or len(value) > 4000 or any(character.isspace() or ord(character) < 32 for character in value):
        return None
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('https', 'http') and _public_host(parsed.hostname or '')
                 and not parsed.username and not parsed.password and parsed.port in (None, 80, 443))
    except ValueError:
        return None
    if not valid or any(secret in value for secret in _known_secrets()):
        return None
    for key, data in parse_qsl(parsed.query, keep_blank_values=True):
        key, data = unquote(unquote(key)), unquote(unquote(data))
        if _PRIVATE_QUERY.fullmatch(key) or re.search(r'(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\bsk-[A-Za-z0-9_-]{12,}', data):
            return None
    if _SECRET_ASSIGNMENT.search(unquote(parsed.fragment)):
        return None
    return value


def _outbound_text(value, limit=8000):
    from ..agent import sanitize
    text = sanitize(str(value or ''))
    for secret in _known_secrets():
        text = text.replace(secret, '［凭证已隐藏］')
    text = _SECRET_ASSIGNMENT.sub('［凭证已隐藏］', text)
    text = _PRIVATE_PATH.sub('［本地路径已隐藏］', text)
    text = re.sub(r'https?://[^\s｜|<>]+', lambda match: match.group(0) if _public_link(match.group(0)) else '［私密链接已隐藏］', text)
    return text[:limit]


def _tracking_link(parameters, input_text):
    value = str(parameters.get('url') or input_text).strip()
    if not _public_link(value):
        raise HTTPException(422, '请填写公开HTTP(S)内容链接，不能包含登录凭证、私密参数或内网地址')
    parsed = urlsplit(value)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key in ('source', 'medium', 'campaign', 'content', 'term'):
        if parameters.get(key):
            query['utm_' + key] = str(parameters[key])[:200]
    tracked = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    if not _public_link(tracked):
        raise HTTPException(422, '追踪参数不能包含凭证或私密联系信息')
    return {'original_url': value, 'tracked_url': tracked, 'shortened': False,
            'note': '已在本地生成UTM链接，没有向第三方提交链接'}


def _provider_endpoint(kind, *, resolve=True):
    if kind == 'short_link':
        url = str(getattr(settings, 'native_short_link_base_url', '') or '').strip()
        key = _secret(getattr(settings, 'native_short_link_api_key', ''))
        allowed = getattr(settings, 'native_short_link_allowed_hosts', [])
    else:
        url = str(getattr(settings, 'native_notification_webhook_url', '') or '').strip()
        key = _secret(getattr(settings, 'native_notification_key', ''))
        allowed = getattr(settings, 'native_notification_allowed_hosts', [])
    if not url or not allowed or (kind == 'short_link' and not key):
        raise HTTPException(409, '原生服务地址、可信目标白名单或凭证未配置完整')
    try:
        parsed = urlsplit(url)
        hosts = {str(host).lower().rstrip('.') for host in allowed}
        valid = (parsed.scheme == 'https' and _public_host(parsed.hostname or '')
                 and parsed.hostname.lower().rstrip('.') in hosts and parsed.port in (None, 443)
                 and not parsed.username and not parsed.password and not parsed.fragment
                 and not any(character.isspace() or ord(character) < 32 for character in url)
                 and (kind != 'short_link' or not parsed.query))
        if not valid:
            raise ValueError
        if resolve:
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or not all(ipaddress.ip_address(row[4][0]).is_global for row in addresses):
                raise ValueError
    except (ValueError, TypeError, AttributeError, OSError):
        raise HTTPException(409, '原生服务地址或可信目标配置无效') from None
    if kind == 'short_link' and not parsed.path.rstrip('/').endswith(('/create', '/shorten')):
        url = url.rstrip('/') + '/create'
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    return url, headers


def _receipt_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    result = str(value)
    if (not re.fullmatch(r'[A-Za-z0-9._:-]{1,120}', result)
            or any(secret in result for secret in _known_secrets())):
        return None
    return result


def _external_permission(db, ctx, pid):
    if callable(db.info.get('job_cancelled')) and db.info['job_cancelled']():
        raise HTTPException(409, '任务已取消，未提交外部服务')
    db.expire_all()
    current = context_for_user(db, ctx.user_id, ctx.tenant_id)
    require_project(db, current, pid, roles=('owner', 'admin', 'content'))


def _post_provider(db, ctx, pid, kind, payload):
    endpoint, headers = _provider_endpoint(kind)
    _external_permission(db, ctx, pid)
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False, trust_env=False) as client:
            response = client.post(endpoint, json=payload, headers=headers)
        if not 200 <= response.status_code < 300 or len(response.content) > 128 * 1024:
            raise ValueError
        try:
            body = response.json()
        except ValueError:
            if response.text.strip().lower() not in ('', 'ok', 'success', 'accepted'):
                raise ValueError
            body = {}
        if not isinstance(body, dict) or body.get('errors'):
            raise ValueError
        for indicator in ('ok', 'success'):
            if indicator in body and body[indicator] is not True:
                raise ValueError
        for code in ('errcode', 'code', 'StatusCode'):
            if isinstance(body.get(code), bool) or body.get(code) not in (None, 0, 200, '0', '200'):
                raise ValueError
        if str(body.get('status') or '').lower() in ('error', 'failed', 'failure'):
            raise ValueError
        nested = body.get('data') if isinstance(body.get('data'), dict) else body
        receipt = {'status': 'accepted', 'http_status': response.status_code}
        message_id = _receipt_id(nested.get('message_id') or nested.get('id') or body.get('message_id'))
        request_id = _receipt_id(response.headers.get('X-Request-ID'))
        if message_id:
            receipt['message_id'] = message_id
        if request_id:
            receipt['request_id'] = request_id
        if kind == 'short_link':
            short_url = nested.get('tiny_url') or nested.get('short_url') or nested.get('shorturl')
            if not _public_link(short_url):
                raise ValueError
            return {'short_url': short_url, 'receipt': receipt}
        return {'receipt': receipt}
    except Exception:
        # HTTP libraries and third-party parsers may include a credential URL
        # in any exception class. None of that text is a worker-safe error.
        raise HTTPException(503, '原生外部服务未返回有效回执，请稍后核对后重试') from None


def _notification_data(project, rows):
    clean = [{key: (_outbound_text(value, 2000) if isinstance(value, str) else value)
              for key, value in row.items() if key != 'metrics'} for row in rows]
    message = '\n'.join(f'{row["title"]}｜{row["channel"]}｜平台状态 {row["platform_state"]}｜回执 {row["receipt_status"]}｜{row["url"]}' for row in clean) or '本项目尚无发布回执。'
    return {'status': 'draft', 'sent': False,
            'message': _outbound_text(f'{project.name} 发布通知\n{message}'),
            'publications': clean, 'note': '本次仅生成通知草稿，没有调用外部通知服务'}


def _platform_key(value):
    """Accept a platform key or its product label, never paths or commands."""
    from ..platform_runtime import PLATFORMS
    if not value:
        return None
    for key, metadata in PLATFORMS.items():
        if value in (key, metadata['name']):
            return key
    raise HTTPException(422, '平台不存在，请选择本项目支持的平台')


def _cached_platform_metadata(db, ctx, pid, platform=None):
    """Read scoped login and analytics caches; never probe or refresh a session."""
    # Imports are local because the account router uses native skill output
    # sanitizers. Its public helpers enforce project scope and session freshness.
    from ..platform_accounts import account_rows
    from ..platform_analytics import analytics_rows
    require_project(db, ctx, pid)
    data = {'metadata_read_at': datetime.now(timezone.utc).isoformat(),
            'data_refreshed': False, 'platform_accounts': [], 'operational_snapshots': [],
            'platform_accounts_status': 'available', 'operational_snapshots_status': 'not_refreshed'}
    for key, reader in (('platform_accounts', account_rows), ('operational_snapshots', analytics_rows)):
        try:
            rows = reader(db, ctx, pid)
            data[key] = [row for row in rows if platform is None or row['platform'] == platform]
        except HTTPException as exc:
            if exc.status_code not in (409, 503):
                raise
            data[key + '_status'] = 'unavailable'
        except (OSError, RuntimeError):
            data[key + '_status'] = 'unavailable'
    if data['operational_snapshots_status'] != 'unavailable' and any(
            row.get('cached') is True for row in data['operational_snapshots']):
        data['operational_snapshots_status'] = 'cached'
    return data


def _verify_project_identity(db, ctx, pid, platform):
    """Use the existing project account adapter; never initiate a login."""
    if not platform:
        raise HTTPException(422, '身份校验请先选择本项目平台')
    _external_permission(db, ctx, pid)
    from ..platform_runtime import runtime
    try:
        known = runtime.login_status(ctx.tenant_id, pid, platform)
        if known.get('loggedIn') is not True:
            raise HTTPException(409, '本项目平台账号未登录，请在当前项目账号页扫码后再校验')
        verified = runtime.whoami(ctx.tenant_id, pid, platform)
        if verified.get('loggedIn') is not True or verified.get('verified') is not True or not verified.get('name'):
            raise HTTPException(409, '平台没有返回已校验的真实身份，不能以账号画像冒充登录身份')
        return {'platform': platform, 'identity_name': _outbound_text(verified['name'], 80),
                'loggedIn': True, 'verified': True, 'observed_at': datetime.now(timezone.utc).isoformat()}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(409, '平台身份校验未取得有效结果，请查看本项目账号页后重试') from None


def _cached_follower_points(snapshots):
    """Each current session supplies at most one point, with no inferred growth."""
    points = []
    for row in snapshots:
        identity = row.get('account') or {}
        observed = row.get('observed_at')
        if row.get('status') not in ('success', 'partial') or not identity.get('session_id') or not observed:
            continue
        observed_date = _date(str(observed)[:10])
        if observed_date is None:
            continue
        points.append({'date': observed_date.isoformat(), 'observed_at': observed,
                       'source_observed_at': row.get('source_observed_at'),
                       'account_id': f'{row["platform"]}:{identity["session_id"]}',
                       'account_name': identity.get('name', ''), 'platform': row['platform'],
                       'followers': (row.get('metrics') or {}).get('followers'),
                       'source': 'platform_analytics_cache', 'cached': row.get('cached') is True})
    return points


def _platform_cache_refs(data, pid):
    refs = [{'type': 'platform_account_cache', 'project_id': pid, 'platform': row['platform'],
             'observed_at': data['metadata_read_at']} for row in data['platform_accounts']]
    refs.extend({'type': 'platform_analytics_cache', 'project_id': pid, 'platform': row['platform'],
                 'observed_at': row.get('observed_at'), 'source_observed_at': row.get('source_observed_at'),
                 'status': row.get('status'), 'cached': row.get('cached') is True}
                for row in data['operational_snapshots'])
    return refs


def _render_report(skill, project_name, data):
    title = _TITLES[skill]
    summary = data.get('summary')
    lines = [f'# {title}', '', f'项目：{project_name}', '']
    if data.get('data_quality', {}).get('status') == 'insufficient_data':
        lines += ['数据不足：这些记录不能构成完整分析交付，请补齐已观察数据。', '']
    if summary:
        lines += ['| 指标 | 观察值 |', '| --- | --- |']
        for key, value in summary.items():
            if isinstance(value, (int, float)) or value is None:
                lines.append(f'| {key} | {"未观察" if value is None else format(value, ".6g")} |')
        lines.append('')
    if skill == 'roi-calculator':
        for row in data['ranking']:
            metrics = row['metrics']
            roi = metrics['roi_pct']
            lines.append(f'- {row["name"]}：ROI {"数据不足" if roi is None else f"{roi:.2f}%"}；模式 {row["mode"]}')
    elif skill == 'skill-comment-insights':
        lines.append(f'评论共 {data["total"]} 条；正向 {data["sentiment"]["positive"]["count"]}，中性 {data["sentiment"]["neutral"]["count"]}，负向 {data["sentiment"]["negative"]["count"]}。')
        lines.append(data['method'])
    elif skill == 'skill-event-calendar':
        lines += ['| 节点 | 日期 |', '| --- | --- |']
        lines += [f'| {row["name"]} | {row["date"]} |' for row in data['events']]
    elif skill == 'skill-short-link':
        lines.append(f'[查看追踪链接]({data["tracked_url"]})')
        if data.get('short_url'):
            lines.append(f'[查看服务返回的短链]({data["short_url"]})')
    elif skill == 'skill-publish-notify':
        lines += [data['message'], '', '通知状态：已发送，服务已受理。' if data.get('sent') else '通知状态：草稿，未发送。']
    elif skill in _PROFILE:
        rows = [data['record']] if 'record' in data else data.get('accounts', [])
        for row in rows[:100]:
            lines += [f'## {row["name"]}', '', f'- 平台：{row["platform"]}',
                      f'- 管理状态：{"正在管理" if row["status"] == "managed" else "暂停"}',
                      f'- 定位：{row.get("positioning") or "待补充"}',
                      f'- 受众：{row.get("audience") or "待补充"}',
                      f'- 文风：{row.get("voice") or "待补充"}',
                      f'- 禁区：{row.get("red_lines") or "待补充"}', '']
        if not rows:
            lines += ['本项目还没有账号画像，请提供平台、账号名称、定位和受众后创建。', '']
    elif skill in _CALENDAR:
        rows = [data['record']] if 'record' in data else data.get('plans', [])
        lines += ['| 内容计划 | 渠道 | 计划日期 | 状态 |', '| --- | --- | --- | --- |']
        statuses = {'draft': '候选草稿', 'scheduled': '已设置计划日期', 'completed': '计划已完成'}
        for row in rows[:100]:
            cells = [row['title'], row['channel'], row.get('scheduled_for') or '未设置', statuses.get(row['status'], row['status'])]
            lines.append('| ' + ' | '.join(str(value).replace('|', '\\|').replace('\n', ' ') for value in cells) + ' |')
        if not rows:
            lines.append('| 尚无符合筛选的本项目计划 | — | — | — |')
        lines += ['', '计划日期用于内容组织；候选草稿尚未完成审核，不代表平台已经发布或已创建平台定时任务。']
    elif skill == 'template-library':
        for row in data.get('templates', []):
            lines += [f'## {row["name"]}', '', *[f'{index}. {section}' for index, section in enumerate(row['sections'], 1)],
                      '', '所需输入：' + '、'.join(row['required_inputs']), '']
        if data.get('record'):
            record = data['record']
            lines += [f'已套用为候选计划：{record["title"]}',
                      f'渠道：{record["channel"]}；计划日期：{record.get("scheduled_for") or "未设置"}；状态：候选草稿。']
    else:
        rows = data.get('accounts') or data.get('plans') or data.get('publications') or data.get('assets') or data.get('templates') or []
        if 'record' in data:
            rows = [data['record']]
        for row in rows[:100]:
            lines.append(f'- {str(row.get("name") or row.get("title") or row.get("id") or "记录")}')
        if not rows and not summary:
            lines.append('本项目当前没有对应记录。')
    if skill in ('skill-my-account', 'skill-data-tracker') and 'platform_accounts' in data:
        connection_note = ('平台账号连接（本次校验已登录身份；平台指标仍为最近缓存，未同步指标）：'
                           if data.get('verified_identity') else '平台账号连接（本次只读取缓存，未启动登录或刷新平台数据）：')
        lines += ['', connection_note]
        if data.get('platform_accounts_status') == 'unavailable':
            lines.append('- 连接状态暂不可用；账号定位资料不代表已完成真实登录。')
        for row in data['platform_accounts']:
            state = '已登录并校验' if row.get('loggedIn') and row.get('verified') else (
                '登录会话待校验' if row.get('loggedIn') else '尚无已确认的登录会话')
            lines.append(f'- {row["name"]}：{row.get("identity_name") or "身份待确认"}，{state}。')
        for row in data.get('operational_snapshots', []):
            metric = (row.get('metrics') or {}).get('followers')
            value = '未获取' if metric is None else str(metric)
            lines.append(f'- {row["name"]}数据：{row.get("reason") or "状态待确认"}；粉丝 {value}；'
                         f'采集时间 {row.get("observed_at") or "尚无采集记录"}。')
    for text in data.get('limitations', []):
        lines += ['', str(text)]
    if data.get('note'):
        lines += ['', data['note']]
    return '\n'.join(lines) + '\n'


def execute(db, ctx, pid, *, skill_name, input_text, parameters, assets, context, output_dir):
    """Project operations; only explicit configured shorten/send use HTTP."""
    project = require_project(db, ctx, pid)
    if skill_name not in PROJECT_SKILLS:
        raise HTTPException(404, '项目技能不存在')
    if not isinstance(parameters, dict):
        raise HTTPException(422, 'parameters必须是对象')
    schema = _parameter_schema(skill_name)
    if set(parameters) - set(schema['properties']):
        raise HTTPException(422, '包含不支持的项目技能参数')
    action = parameters.get('action', schema['properties']['action']['default'])
    if action not in schema['properties']['action']['enum']:
        raise HTTPException(422, '此技能动作不可用；通知发送须先配置项目通知渠道并授权')
    mutation = {}
    refs = []
    if action in ('create', 'update'):
        data, mutation = _write_domain(db, ctx, pid, skill_name, parameters)
    elif skill_name in _ANALYTICS:
        if parameters.get('metric_window', 'D7') not in ('D7', 'D30'):
            raise HTTPException(422, '指标窗口只支持D7或D30')
        records, refs = _input_records(db, ctx, pid, skill_name, parameters, assets, input_text)
        if skill_name == 'roi-calculator':
            data = _roi(records)
        elif skill_name == 'skill-comment-insights':
            data = _comment_analysis(records, parameters)
        else:
            data = _metric_analysis(records, skill_name, parameters)
        if skill_name == 'skill-data-tracker' and not (
                'records' in parameters or parameters.get('asset_id') or input_text.strip()):
            data.update(_cached_platform_metadata(db, ctx, pid))
            data['follower_series'] = _cached_follower_points(data['operational_snapshots'])
            data['follower_changes'] = []
            data['limitations'].append('平台账号累计快照与项目作品指标分别展示；单个缓存快照不能证明粉丝增长，本次没有刷新平台数据。')
            refs.extend(_platform_cache_refs(data, pid))
    elif skill_name in _PROFILE:
        accounts = list_accounts(db, ctx, pid)
        aid = parameters.get('account_id') or parameters.get('target_account_id')
        if aid:
            accounts = [account_dict(_record(db, ContentAccount, ctx, pid, aid))]
        data = {'accounts': accounts, 'facts': [fact_dict(row) for row in _rows(db, Fact, ctx, pid)],
                'note': '项目账号画像与平台登录状态分别管理；画像修改使用本项目账号编号'}
        refs = [{'type': 'content_account', 'id': row['id']} for row in accounts]
    elif skill_name in _CALENDAR:
        data = _calendar(db, ctx, pid, parameters)
        if ctx.role in ('owner', 'admin', 'sales'):
            followup_query = _query(FollowUp, ctx, pid)
            if ctx.role == 'sales':
                followup_query = followup_query.join(Lead, FollowUp.lead_id == Lead.id).where(Lead.owner_id == ctx.user_id)
            data['followup_count'] = len(db.scalars(followup_query.limit(5000)).all())
        refs = [{'type': 'content_plan', 'id': row['id']} for row in data['plans']]
    elif skill_name == 'skill-event-calendar':
        data = _events(parameters)
        refs = [{'type': 'calendar_algorithm', 'label': '本地农历年表换算1900—2100'}]
    elif skill_name == 'asset-manager':
        rows = _rows(db, Asset, ctx, pid)
        if parameters.get('keyword'):
            needle = str(parameters['keyword']).casefold()
            rows = [row for row in rows if needle in row.name.casefold() or needle in (row.extracted_text or '').casefold()]
        if parameters.get('kind'):
            rows = [row for row in rows if row.kind == parameters['kind']]
        data = {'assets': [asset_dict(row) for row in rows], 'total_bytes': sum(row.byte_size for row in rows),
                'note': '清单只含本项目素材，使用授权和核验状态以项目素材页为准'}
        refs = [{'type': 'asset', 'id': row.id} for row in rows]
    elif skill_name == 'template-library':
        rows = [dict(row) for row in _TEMPLATES if not parameters.get('template_id') or parameters['template_id'] == row['id']]
        if not rows:
            raise HTTPException(422, '模板不存在')
        data = {'templates': rows, 'note': '楼响原生标准模板；填入核验事实和已授权素材后生成待审核内容'}
        if action == 'apply':
            if len(rows) != 1:
                raise HTTPException(422, '请先选择一个标准模板再套用')
            record = _apply_template(db, ctx, pid, project, rows[0], parameters)
            data.update(record=record, changed_plan_id=record['id'],
                        note='模板结构已保存到本项目候选内容计划；继续填写核验事实与授权素材后制作审核')
            mutation = {'changed_plan_id': record['id']}
            refs = [{'type': 'content_plan', 'id': record['id']}, {'type': 'native_template', 'id': rows[0]['id']}]
    elif skill_name == 'skill-publish-log':
        rows = _publications(db, ctx, pid)
        if parameters.get('publication_id'):
            rid = parameters['publication_id']
            _record(db, Publication, ctx, pid, rid)
            rows = [row for row in rows if row['id'] == rid]
        if not rows:
            raise HTTPException(409, '当前项目没有发布记录，空清单不能作为发布日志交付；请先取得本项目真实发布回执')
        data = {'publications': rows, 'note': '回执未核验或平台未live的记录不能视为已公开发布'}
        refs = [{'type': 'publication', 'id': row['id'], 'url': row['url']} for row in rows]
    elif skill_name == 'skill-my-account':
        accounts = list_accounts(db, ctx, pid)
        platform = _platform_key(parameters.get('platform'))
        if parameters.get('account_id'):
            accounts = [account_dict(_record(db, ContentAccount, ctx, pid, parameters['account_id']))]
        if platform:
            accounts = [row for row in accounts if _platform_key(row['platform']) == platform]
        data = {'accounts': accounts, 'platform_accounts_url': f'/api/projects/{pid}/operations/platform-accounts',
                'strategy_identity_bound': False,
                'note': '账号定位资料与真实平台身份分别展示；定位记录没有绑定登录身份。每项目每平台使用一个实际运营会话；平台指标为同会话最近缓存，缺失保持为空，采集时间以快照为准。本次未启动浏览器、登录或数据同步。'}
        data.update(_cached_platform_metadata(db, ctx, pid, platform))
        if action == 'verify':
            data['verified_identity'] = _verify_project_identity(db, ctx, pid, platform)
            data.update(_cached_platform_metadata(db, ctx, pid, platform))
            data['note'] = '已通过本项目会话向平台校验真实身份；作品与粉丝指标仍取同一会话的最近采集快照，缺失指标不编造。'
        refs = [{'type': 'content_account', 'id': row['id']} for row in accounts]
        refs.extend(_platform_cache_refs(data, pid))
    elif skill_name == 'skill-short-link':
        data = _tracking_link(parameters, input_text)
        if action == 'shorten':
            result = _post_provider(db, ctx, pid, 'short_link', {'url': data['tracked_url']})
            data.update(result, shortened=True, note='短链由已配置服务实际返回；不推断点击量或转化')
            refs = [{'type': 'native_short_link_receipt', 'short_url': result['short_url'], 'receipt': result['receipt']}]
    elif skill_name == 'skill-publish-notify':
        if action == 'send' and parameters.get('confirmed') is not True:
            raise HTTPException(422, '发送通知前请明确确认发送至本项目已配置通知目标')
        rows = _publications(db, ctx, pid)
        if parameters.get('publication_id'):
            row = _record(db, Publication, ctx, pid, parameters['publication_id'])
            rows = [value for value in rows if value['id'] == row.id]
        data = _notification_data(project, rows)
        if action == 'send':
            result = _post_provider(db, ctx, pid, 'notification', {'text': data['message']})
            data.update(result, status='sent', sent=True, note='通知已提交至本项目配置目标；服务已受理，未声称收件人已阅读')
            refs = [{'type': 'native_notification_receipt', 'receipt': result['receipt']}]
    else:
        raise HTTPException(404, '项目技能不存在')
    data.update(project_id=pid, skill_name=skill_name, action=action)
    response = _render_report(skill_name, project.name, data)
    directory = Path(output_dir)
    if directory.is_symlink():
        raise HTTPException(422, '产物目录无效')
    directory.mkdir(parents=True, exist_ok=True)
    report_path, markdown_path = directory / 'report.json', directory / 'report.md'
    if report_path.is_symlink() or markdown_path.is_symlink():
        raise HTTPException(422, '产物路径无效')
    report_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    markdown_path.write_text(response, encoding='utf-8')
    return {'response': response, 'artifact_paths': [report_path, markdown_path],
            'source_refs': refs, 'mutation_refs': mutation, **mutation}
