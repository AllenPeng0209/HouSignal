"""Recheck persistent external-agent grants at worker side-effect boundaries.

The payload is an association with an OAuth grant, never a permission snapshot.
Web jobs omit that association and retain the existing worker permission path.
"""
from dataclasses import replace
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .core_models import Membership, User
from .db import utcnow
from .external_agent_models import AgentOAuthClient, AgentOAuthGrant
from .security import context_for_user, require_project


class ExternalJobAccessDenied(HTTPException):
    def __init__(self, status_code=403):
        super().__init__(status_code, '外部 Agent 授权已失效，请重新连接后发起制作任务')


def check_external_job_access(db, job):
    """Return the current restricted context, or None for a regular Web job.

    Every identity and permission comes from current database records. Matching
    payload fields only detects a broken association; payload scopes grant no
    rights. A missing migration or inaccessible auth table fails closed.
    """
    payload = job.payload or {}
    if 'external_agent' not in payload:
        return None
    association = payload['external_agent']
    if (not isinstance(association, dict) or job.kind not in {'generate_content', 'render_content', 'content_skill'}
            or not all(isinstance(association.get(key), str) and association[key]
                       for key in ('grant_id', 'client_id', 'project_id', 'user_id'))
            or association['project_id'] != job.property_id or association['user_id'] != job.user_id):
        raise ExternalJobAccessDenied()
    try:
        from .external_agent_auth import public_resource, WRITE_ROLES
        with db.no_autoflush:
            grant = db.scalar(select(AgentOAuthGrant).where(AgentOAuthGrant.id == association['grant_id'])
                              .execution_options(populate_existing=True))
            if (grant is None or grant.organization_id != job.tenant_id or grant.user_id != job.user_id
                    or grant.client_id != association['client_id'] or grant.revoked_at is not None
                    or grant.resource != public_resource()
                    or not {'louxiang:read', 'louxiang:production'} <= set((grant.scope or '').split())
                    or not isinstance(grant.project_ids, list)
                    or job.property_id not in grant.project_ids):
                raise ExternalJobAccessDenied()
            expiry = grant.expires_at
            expiry = expiry.replace(tzinfo=timezone.utc) if expiry.tzinfo is None else expiry
            if expiry <= utcnow() or db.get(AgentOAuthClient, grant.client_id) is None:
                raise ExternalJobAccessDenied()
            # These identities may already be in a long-running handler's
            # identity map. Refresh before canonical context reconstruction.
            db.scalar(select(User).where(User.id == job.user_id).execution_options(populate_existing=True))
            db.scalar(select(Membership).where(Membership.user_id == job.user_id,
                Membership.tenant_id == job.tenant_id).execution_options(populate_existing=True))
            ctx = context_for_user(db, job.user_id, job.tenant_id)
            allowed = [pid for pid in grant.project_ids if ctx.project_ids is None or pid in ctx.project_ids]
            restricted = replace(ctx, project_ids=allowed)
            require_project(db, restricted, job.property_id, roles=tuple(WRITE_ROLES))
            return restricted
    except ExternalJobAccessDenied:
        raise
    except HTTPException:
        raise ExternalJobAccessDenied() from None
    except SQLAlchemyError:
        # Do not expose SQL, table layout, credentials, or provider paths.
        raise ExternalJobAccessDenied(status_code=503) from None


def external_job_access_allowed(db, job):
    """Fail-closed polling adapter used by existing provider abort callbacks."""
    try:
        check_external_job_access(db, job)
        return True
    except HTTPException:
        return False
