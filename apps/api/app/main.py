"""Application assembly; migrations and bootstrap are explicit commands."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from .rate_limit import LoginLimiter
from sqlalchemy import text
from .config import settings
from .db import SessionLocal
from .audit_models import AuditEvent
from . import core, business, agent, jobs, operations, native_skills, content_planning, mobile_auth, discovery, platform_accounts, platform_publish, platform_analytics, research_workspace, reference_media, reference_upload, research_outcomes, secretary

login_limiter = LoginLimiter(settings.secret_key)

app = FastAPI(title='楼响 HouSignal', version='0.1.0', docs_url='/api/docs', openapi_url='/api/openapi.json')
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                   allow_credentials=True, allow_methods=['GET', 'POST', 'PATCH', 'DELETE'],
                   allow_headers=['Content-Type', 'Authorization', 'X-Request-ID'])


@app.middleware('http')
async def origin_and_audit(request: Request, call_next):
    if request.method == 'POST' and request.url.path in {'/api/auth/login', '/api/mobile/auth/login'}:
        body = await request.body()
        if len(body) > 8192:
            return JSONResponse({'detail':'登录请求过大'}, status_code=413)
        try:
            payload = await request.json()
            email = str(payload.get('email','')).strip().lower()[:254]
        except (ValueError, AttributeError):
            email = ''
        actor = (request.client.host if request.client else 'unknown') + ':' + email
        if not login_limiter.allow(actor):
            return JSONResponse({'detail':'登录尝试过于频繁，请稍后重试'}, status_code=429, headers={'Retry-After':'60'})
    if request.method in {'POST', 'PATCH', 'PUT', 'DELETE'}:
        origin = request.headers.get('origin')
        if origin and origin not in settings.allowed_origins:
            return JSONResponse({'detail': '请求来源未授权'}, status_code=403)
        if request.cookies.get('hs_session') and not origin:
            return JSONResponse({'detail': '写入请求需要同源校验'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Cache-Control'] = 'no-store'
    ctx = getattr(request.state, 'context', None)
    if ctx and request.method in {'POST', 'PATCH', 'PUT', 'DELETE'}:
        with SessionLocal() as db:
            db.info.update(tenant_id=ctx.tenant_id, user_id=ctx.user_id)
            db.add(AuditEvent(tenant_id=ctx.tenant_id, actor_id=ctx.user_id,
                              action=request.method + ' ' + request.url.path,
                              details={'status': response.status_code}))
            db.commit()
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request, error):
    return JSONResponse({'detail': '请求参数格式无效', 'fields': ['.'.join(map(str, e['loc'])) for e in error.errors()]}, status_code=422)


@app.get('/api/health')
def health():
    with SessionLocal() as db:
        db.execute(text('SELECT 1'))
    return {'status': 'ok', 'database': 'postgresql' if settings.database_url.startswith('postgresql') else 'test',
            'environment': settings.app_env, 'version': '0.1.0'}


for router in (core.router, business.router, agent.router, jobs.router, operations.router, native_skills.router, content_planning.router, mobile_auth.router, discovery.router, platform_accounts.router, platform_publish.router, platform_analytics.router, research_workspace.router, reference_media.router, reference_upload.router, research_outcomes.router, secretary.router):
    app.include_router(router)


WEB_DIST = Path(__file__).resolve().parents[3] / 'apps/web/dist'
if (WEB_DIST / 'index.html').is_file():
    app.mount('/assets', StaticFiles(directory=WEB_DIST / 'assets'), name='web-assets')

    @app.get('/', include_in_schema=False)
    def web_application():
        return FileResponse(WEB_DIST / 'index.html', headers={'Cache-Control':'no-store'})
