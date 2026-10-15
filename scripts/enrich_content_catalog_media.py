"""Enrich a project's existing catalog with real public video thumbnails.

Run from the repository root with PYTHONPATH=apps/api. This explicit backend
action does not search for works or download videos. Catalog GET remains local.
"""
import argparse
import json
import sys

from fastapi import HTTPException

from app.content_catalog import enrich_catalog_media
from app.db import SessionLocal
from app.security import context_for_user


def main(argv=None):
    parser = argparse.ArgumentParser(description='后台整理已收录视频的公开封面')
    parser.add_argument('--tenant-id', required=True, help='明确的组织编号')
    parser.add_argument('--project-id', required=True, help='明确的项目编号')
    parser.add_argument('--user-id', required=True, help='该组织中有项目编辑权限的用户编号')
    parser.add_argument('--limit', type=int, default=15, help='本次最多整理1至50条视频，默认15条')
    options = parser.parse_args(argv)
    if not 1 <= options.limit <= 50:
        parser.error('--limit 需为1至50')
    try:
        with SessionLocal() as db:
            # Establish PostgreSQL RLS context before any project read.
            ctx = context_for_user(db, options.user_id, options.tenant_id)
            result = enrich_catalog_media(db, ctx, options.project_id, limit=options.limit)
            db.commit()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except HTTPException as exc:
        print(exc.detail if isinstance(exc.detail, str) else '后台封面整理参数或权限无效', file=sys.stderr)
        return 1
    except Exception:
        print('后台封面整理失败，请查看服务日志', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
