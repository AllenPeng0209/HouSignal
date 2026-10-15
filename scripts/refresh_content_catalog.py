"""Queue five content categories for one explicitly authorized tenant project.

Run from the repository root with PYTHONPATH=apps/api. The existing worker reads
public source results; this command only enqueues jobs and never searches inline.
"""
import argparse
import json
import sys
from pathlib import Path

from fastapi import HTTPException

from app.content_catalog import enqueue_catalog_refresh, import_catalog_index
from app.db import SessionLocal
from app.security import context_for_user


def main(argv=None):
    parser = argparse.ArgumentParser(description='后台整理一个项目的五类房地产内容')
    parser.add_argument('--tenant-id', required=True, help='明确的组织编号')
    parser.add_argument('--project-id', required=True, help='明确的项目编号')
    parser.add_argument('--user-id', required=True, help='该组织中有项目编辑权限的用户编号')
    parser.add_argument('--platforms', default='douyin,bilibili', help='逗号分隔：douyin,bilibili,xiaohongshu')
    parser.add_argument('--days', type=int, default=365, choices=(7, 30, 90, 365))
    parser.add_argument('--index-file', help='导入已核对的公开来源JSON数组；不会在线搜索或新建搜索任务')
    options = parser.parse_args(argv)
    platforms = list(dict.fromkeys(value.strip() for value in options.platforms.split(',')))
    if not platforms or any(value not in ('douyin', 'bilibili', 'xiaohongshu') for value in platforms):
        parser.error('--platforms 仅支持 douyin,bilibili,xiaohongshu')
    try:
        with SessionLocal() as db:
            # Establish PostgreSQL RLS context before any project read.
            ctx = context_for_user(db, options.user_id, options.tenant_id)
            if options.index_file:
                result = import_catalog_index(db, ctx, options.project_id,
                    json.loads(Path(options.index_file).read_text(encoding='utf-8')))
            else:
                jobs = enqueue_catalog_refresh(db, ctx, options.project_id, platforms=platforms, days=options.days)
                result = {'jobs': jobs, 'collection_state': 'collecting'}
            db.commit()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except HTTPException as exc:
        print(exc.detail if isinstance(exc.detail, str) else '后台采集参数或权限无效', file=sys.stderr)
        return 1
    except Exception:
        print('后台采集排队失败，请查看服务日志', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
