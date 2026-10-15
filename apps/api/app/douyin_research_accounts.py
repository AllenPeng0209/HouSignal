"""Project-authorized login to Douyin's public research surface only."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .db import get_db
from .douyin_research_runtime import research_runtime
from .security import Context, get_context, require_project

router = APIRouter(prefix='/douyin-account', tags=['抖音内容研究账号'])
ROLES = ('owner', 'admin', 'content')
_FIELDS = {'state', 'message', 'qrReady', 'qrTs', 'loggedIn', 'verified', 'ready'}


def _call(method, ctx, pid):
    try:
        return getattr(research_runtime, method)(ctx.tenant_id, pid)
    except ValueError:
        raise HTTPException(422, '研究账号参数无效') from None
    except (RuntimeError, OSError):
        raise HTTPException(409, '研究会话正在占用或暂不可用，请稍后重试') from None


def _safe(value):
    if not isinstance(value, dict):
        raise HTTPException(409, '研究会话状态暂不可用')
    result = {key:value[key] for key in _FIELDS if key in value}
    for key in ('state', 'message'):
        result[key] = ''.join(char for char in str(result.get(key, '')) if ord(char) >= 32)[:300]
    for key in ('qrReady', 'loggedIn', 'verified', 'ready'):
        result[key] = result.get(key) is True
    if type(result.get('qrTs')) not in (int, float):
        result['qrTs'] = None
    result['scope'] = 'research'
    return result


@router.get('')
@router.get('/status')
def status(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid)
    return _safe(_call('login_status', ctx, pid))


@router.post('/login')
def login(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid, ROLES)
    return _safe(_call('start_login', ctx, pid))


@router.post('/verify')
def verify(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid, ROLES)
    return _safe(_call('verify', ctx, pid))


@router.post('/disconnect')
def disconnect(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid, ROLES)
    return _safe(_call('disconnect', ctx, pid))


@router.get('/qr')
def qr(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid, ROLES)
    path = _call('qr_path', ctx, pid)
    if path is None:
        raise HTTPException(404, '官方二维码尚未生成或已失效')
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file() or not 8 <= path.stat().st_size <= 2_000_000:
            raise OSError()
        with path.open('rb') as source:
            if source.read(8) != b'\x89PNG\r\n\x1a\n':
                raise OSError()
    except OSError:
        raise HTTPException(404, '官方二维码尚未生成或已失效') from None
    return FileResponse(path, media_type='image/png', headers={'Cache-Control':'no-store',
                        'X-Content-Type-Options':'nosniff'})
