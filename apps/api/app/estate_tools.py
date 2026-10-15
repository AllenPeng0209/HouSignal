"""Native property/catalog tools; the caller owns the transaction and identity."""
from __future__ import annotations

import json
import math
import re

from fastapi import HTTPException
from sqlalchemy import String, cast, or_, select

from .audit_models import AuditEvent
from .core import EDIT_ROLES, ProjectPatch, _asset, _fact, _record_revision, fact_dict, project_dict, valid_fact
from .core_models import Asset, Fact, FactRevision
from .db import utcnow


def _text(maximum, minimum=0, **extra):
    return {'type': 'string', 'minLength': minimum, 'maxLength': maximum, **extra}


def _tool(name, description, properties=None, required=None):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties or {},
                           'required': required or [], 'additionalProperties': False}}}


_ID = _text(64, 1)
_REVISION = {'type': 'integer', 'minimum': 1, 'maximum': 2_147_483_647}
_PROPERTY_FIELDS = {'name': _text(200, 1), 'city': _text(100, 1), 'property_type': _text(100, 1),
                    'owner_name': _text(100), 'audience': _text(3000),
                    'sales_owner_name': _text(100), 'channel': _text(100)}

ESTATE_TOOLS = [
    _tool('get_property_details', '读取当前楼盘基础资料及全部事实（含待核验内容、事实ID、版本和最近修订记录）；先读后改。'),
    _tool('update_property', '按用户明确提供的信息修改当前楼盘基础资料；不确认营销授权，不自动核验事实。', _PROPERTY_FIELDS),
    _tool('update_property_fact', '修改工具已查得的楼盘事实，修改后必定恢复待核验并保留修订历史；建议传入刚读取的expected_revision以防覆盖。',
          {'fact_id': _ID, 'value': _text(5000), 'unit': _text(50), 'source': _text(5000),
           'expected_revision': _REVISION}, ['fact_id']),
    _tool('create_property_fact', '按用户明确提供的资料新增楼盘事实，必须保留实际来源，新增内容为待核验；同语义事实应先读取再修改。',
          {'semantic_key': _text(100, 1, pattern=r'^[a-z][a-z0-9_]*$'), 'label': _text(200, 1),
           'value': _text(5000, 1), 'source': _text(5000, 1), 'unit': _text(50)},
          ['semantic_key', 'label', 'value', 'source']),
    _tool('search_assets', '查询当前素材库名称、描述、标签及用户自定义资料，返回素材ID；不读取文件正文，也不改变文件授权。',
          {'query': _text(120), 'kind': _text(30, 1, enum=['image', 'video', 'audio', 'document']),
           'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}),
    _tool('update_asset', '修改已查得的素材名称、描述、标签、自定义资料；metadata_patch合并到user_metadata。保留原文件、授权及来源，建议传expected_revision。',
          {'asset_id': _ID, 'name': _text(255, 1), 'description': _text(5000),
           'tags': {'type': 'array', 'items': _text(80, 1), 'maxItems': 30},
           'metadata_patch': {'type': 'object', 'additionalProperties': True, 'maxProperties': 30},
           'expected_revision': _REVISION}, ['asset_id']),
]
ESTATE_NAMES = frozenset(tool['function']['name'] for tool in ESTATE_TOOLS)
_SCHEMAS = {tool['function']['name']: tool['function']['parameters'] for tool in ESTATE_TOOLS}
_PROTECTED_META = {'model_processing_authorized', 'rights', 'rights_evidence', 'rights_revision',
                   'origin_type', 'usage_scope', 'storage_key', 'sha256', 'byte_size', 'mime_type',
                   'text_pages', 'extracted_text', 'extraction_status', 'extraction_verified',
                   'fact_candidates', 'extracted_at', 'content_version_id', 'renderer', 'catalog_revision',
                   'description', 'tags', 'user_metadata'}


def _validate(value, schema):
    kind = schema.get('type')
    if kind == 'object':
        if not isinstance(value, dict) or len(value) > schema.get('maxProperties', 40):
            raise ValueError('工具对象参数无效')
        properties = schema.get('properties', {})
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ValueError('工具参数包含不支持的字段')
        if any(key not in value for key in schema.get('required', [])):
            raise ValueError('工具参数不完整')
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key])
    elif kind == 'string':
        if not isinstance(value, str) or not schema.get('minLength', 0) <= len(value) <= schema['maxLength']:
            raise ValueError('工具文本参数无效')
        if schema.get('minLength', 0) and not value.strip():
            raise ValueError('工具文本参数不能为空')
        if schema.get('pattern') and not re.fullmatch(schema['pattern'], value):
            raise ValueError('事实语义标识格式无效')
    elif kind == 'integer':
        if isinstance(value, bool) or not isinstance(value, int) or not schema['minimum'] <= value <= schema['maximum']:
            raise ValueError('工具整数参数无效')
    elif kind == 'array':
        if not isinstance(value, list) or len(value) > schema['maxItems']:
            raise ValueError('工具列表参数无效')
        for item in value:
            _validate(item, schema['items'])
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('工具参数取值无效')


def _metadata_patch(value):
    """User fields live in their own namespace, away from workflow evidence."""
    def check(item, depth=0):
        if depth > 4:
            raise ValueError('自定义资料层级过深')
        if isinstance(item, dict):
            if len(item) > 30:
                raise ValueError('自定义资料字段过多')
            for key, child in item.items():
                if (not isinstance(key, str) or not key.strip() or len(key) > 80
                        or key.lower() in _PROTECTED_META or key.lower().startswith(('model_processing_', 'extraction_'))):
                    raise ValueError('自定义资料不能修改授权、文件或核验字段')
                check(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 30:
                raise ValueError('自定义资料列表过长')
            for child in item:
                check(child, depth + 1)
        elif isinstance(item, str):
            if len(item) > 2000:
                raise ValueError('自定义资料文本过长')
        elif item is not None and not isinstance(item, (bool, int, float)):
            raise ValueError('自定义资料需要JSON数据')
        elif isinstance(item, (int, float)) and not isinstance(item, bool) and not math.isfinite(item):
            raise ValueError('自定义资料数字无效')
    check(value)
    if len(json.dumps(value, ensure_ascii=False)) > 10_000:
        raise ValueError('自定义资料过大')
    return value


def _source(kind, item_id, label, **extra):
    return {'kind': kind, 'id': item_id, 'label': label, 'observed_at': utcnow().isoformat(), **extra}


def _fact_source(fact):
    return _source('fact', fact.id, fact.label, revision=fact.revision, status=fact.status, valid=valid_fact(fact))


def _catalog(asset):
    metadata = asset.metadata_json or {}
    return {'id': asset.id, 'name': asset.name, 'kind': asset.kind, 'mime_type': asset.mime_type,
            'description': metadata.get('description', ''), 'tags': metadata.get('tags', []),
            'user_metadata': metadata.get('user_metadata', {}), 'revision': metadata.get('catalog_revision', 1),
            'rights': asset.rights, 'origin_type': asset.origin_type,
            'created_at': asset.created_at.isoformat()}


def _asset_source(asset):
    return _source('asset', asset.id, asset.name, metadata_only=True, page='assets',
                   revision=(asset.metadata_json or {}).get('catalog_revision', 1))


def _audit(db, ctx, pid, name, entity_id, fields, **extra):
    db.add(AuditEvent(tenant_id=ctx.tenant_id, property_id=pid, actor_id=ctx.user_id,
                     action='agent.' + name,
                     details={'entity_id': entity_id, 'changed_fields': sorted(fields), **extra}))


def execute_estate_tool(db, ctx, pid, name, args, *, raw_text=''):
    """Apply only an explicit bounded operation; never commit the caller's work."""
    from .security import require_project

    schema = _SCHEMAS.get(name)
    if schema is None:
        raise ValueError('不支持的楼盘或素材工具')
    _validate(args, schema)
    mutating = name.startswith(('update_', 'create_'))
    prop = require_project(db, ctx, pid, EDIT_ROLES if mutating else None)
    sources = []
    if name == 'get_property_details':
        facts = db.scalars(select(Fact).where(Fact.tenant_id == ctx.tenant_id, Fact.property_id == pid)
                           .order_by(Fact.required.desc(), Fact.semantic_key)).all()
        rows = []
        for fact in facts:
            revisions = db.scalars(select(FactRevision).where(FactRevision.tenant_id == ctx.tenant_id,
                                  FactRevision.property_id == pid, FactRevision.fact_id == fact.id)
                                  .order_by(FactRevision.revision.desc()).limit(10)).all()
            rows.append({**fact_dict(fact), 'revisions': [revision.payload for revision in reversed(revisions)]})
        data = {'project': project_dict(db, ctx, prop), 'facts': rows}
        sources = [_fact_source(fact) for fact in facts]
        sources.append(_source('page', pid, prop.name + ' · 楼盘资料', page='property'))
    elif name == 'update_property':
        if not args:
            raise ValueError('请提供需要修改的楼盘字段')
        updates = ProjectPatch(**args).model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(prop, key, value.strip())
        _audit(db, ctx, pid, name, pid, updates)
        db.flush()
        data = {'project': project_dict(db, ctx, prop), 'changed_fields': sorted(updates)}
        sources = [_source('page', pid, prop.name + ' · 楼盘资料', page='property')]
    elif name == 'update_property_fact':
        updates = {key: value.strip() for key, value in args.items() if key in {'value', 'unit', 'source'}}
        if not updates:
            raise ValueError('请提供需要修改的事实内容')
        fact = _fact(db, ctx, pid, args['fact_id'])
        db.refresh(fact, with_for_update=True)
        if args.get('expected_revision', fact.revision) != fact.revision:
            raise HTTPException(409, '楼盘事实已被修改，请重新读取后再修改')
        before_revision = fact.revision
        _record_revision(db, fact)
        for key, value in updates.items():
            setattr(fact, key, value)
        fact.status = 'unverified'
        fact.revision += 1
        fact.updated_at = utcnow()
        _record_revision(db, fact)
        _audit(db, ctx, pid, name, fact.id, updates, before_revision=before_revision, after_revision=fact.revision)
        db.flush()
        data, sources = {'fact': fact_dict(fact)}, [_fact_source(fact)]
    elif name == 'create_property_fact':
        # Serialise same-project creates before checking the unique semantic key.
        db.refresh(prop, with_for_update=True)
        existing = db.scalar(select(Fact.id).where(Fact.tenant_id == ctx.tenant_id,
                             Fact.property_id == pid, Fact.semantic_key == args['semantic_key']))
        if existing:
            raise HTTPException(409, '同类楼盘事实已存在，请先读取并修改现有记录')
        fact = Fact(tenant_id=ctx.tenant_id, property_id=pid,
                    **{key: value.strip() for key, value in args.items()},
                    status='unverified', revision=1, required=False)
        db.add(fact)
        db.flush()
        _record_revision(db, fact)
        _audit(db, ctx, pid, name, fact.id, args, after_revision=1)
        db.flush()
        data, sources = {'fact': fact_dict(fact)}, [_fact_source(fact)]
    elif name == 'search_assets':
        statement = select(Asset).where(Asset.tenant_id == ctx.tenant_id, Asset.property_id == pid)
        if args.get('kind'):
            statement = statement.where(Asset.kind == args['kind'])
        query = args.get('query', '').strip()
        if query:
            def pattern(text):
                return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            literal = pattern(query)
            # SQLite retains JSON unicode escapes inside arrays/objects; PostgreSQL
            # JSONB emits literal characters. Match both safe catalog encodings.
            catalog_patterns = {literal, pattern(json.dumps(query, ensure_ascii=True)[1:-1])}
            clauses = [Asset.name.ilike(literal, escape='\\'),
                       Asset.metadata_json['description'].as_string().ilike(literal, escape='\\')]
            for candidate in catalog_patterns:
                clauses.extend([cast(Asset.metadata_json['tags'], String).ilike(candidate, escape='\\'),
                                cast(Asset.metadata_json['user_metadata'], String).ilike(candidate, escape='\\')])
            statement = statement.where(or_(*clauses))
        limit = args.get('limit', 20)
        assets = db.scalars(statement.order_by(Asset.created_at.desc(), Asset.id).limit(limit + 1)).all()
        data = {'assets': [_catalog(asset) for asset in assets[:limit]], 'has_more': len(assets) > limit}
        sources = [_asset_source(asset) for asset in assets[:limit]]
    elif name == 'update_asset':
        fields = set(args) - {'asset_id', 'expected_revision'}
        if not fields:
            raise ValueError('请提供需要修改的素材字段')
        patch = _metadata_patch(args['metadata_patch']) if 'metadata_patch' in args else None
        asset = _asset(db, ctx, pid, args['asset_id'])
        db.refresh(asset, with_for_update=True)
        metadata = dict(asset.metadata_json or {})
        revision = metadata.get('catalog_revision', 1)
        if args.get('expected_revision', revision) != revision:
            raise HTTPException(409, '素材资料已被修改，请重新读取后再修改')
        if 'name' in args:
            asset.name = args['name'].strip()
        if 'description' in args:
            metadata['description'] = args['description'].strip()
        if 'tags' in args:
            metadata['tags'] = [tag.strip() for tag in args['tags']]
        if patch is not None:
            metadata['user_metadata'] = {**metadata.get('user_metadata', {}), **patch}
        metadata['catalog_revision'] = revision + 1
        asset.metadata_json = metadata
        _audit(db, ctx, pid, name, asset.id, fields, before_revision=revision, after_revision=revision + 1)
        db.flush()
        data, sources = {'asset': _catalog(asset)}, [_asset_source(asset)]
    return {'data': data, 'sources': sources, 'observed_at': utcnow().isoformat()}
