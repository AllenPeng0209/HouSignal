"""Project-owned native content methods and stable product capability mapping."""
import json
from pathlib import Path

from fastapi import HTTPException


DEFINITIONS_PATH = Path(__file__).with_name('definitions.json')
ENGINES = frozenset({'text', 'research', 'media', 'project', 'platform'})


def definitions():
    try:
        document = json.loads(DEFINITIONS_PATH.read_text(encoding='utf-8'))
        rows = document['skills']
        if document.get('schema_version') != 1 or not isinstance(rows, list):
            raise ValueError('invalid definitions')
        names = set()
        for row in rows:
            if (not isinstance(row, dict) or row.get('engine') not in ENGINES
                    or not isinstance(row.get('name'), str) or row['name'] in names
                    or not all(isinstance(row.get(key), str) and row[key] for key in ('title', 'description', 'layer', 'target'))
                    or not all(isinstance(row.get(key), list) and row[key]
                               and all(isinstance(value, str) and value for value in row[key])
                               for key in ('instructions', 'output_requirements'))
                    or not isinstance(row.get('input_schema'), dict)):
                raise ValueError('invalid native method')
            names.add(row['name'])
        return rows
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, '内容方法配置不可读取，请联系管理员') from None


def definition(skill_name):
    row = next((row for row in definitions() if row['name'] == skill_name), None)
    if row is None:
        raise HTTPException(404, '内容技能不存在')
    return row


def catalog_entries(existing=()):
    """Return all native entries, preserving existing product IDs and aliases."""
    methods = {row['name']: row for row in definitions()}
    represented, result = set(), []
    for item in existing:
        name = item.get('skill_name') or item.get('easel_skill')
        method = methods.get(name)
        if method is None:
            continue
        represented.add(name)
        result.append({**method, **item, 'skill_name': name, 'easel_skill': name,
                       'engine': method['engine'], 'input_schema': method['input_schema'],
                       'execution_engine': f"housignal_{method['engine']}", 'mode': 'native'})
    for name, method in methods.items():
        if name not in represented:
            result.append({**method, 'id': f'native:{name}', 'skill_name': name, 'easel_skill': name,
                           'execution_engine': f"housignal_{method['engine']}", 'mode': 'native'})
    return result
