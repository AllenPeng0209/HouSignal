"""Optional daily token quotas; metering is independent of admission.

Zero disables the application quota. A positive configured quota still applies
to the team's cloud or local/PPU lane, without deleting any usage receipts.
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import BigInteger, cast, func, select

from .agent_models import ModelUsage
from .config import settings


def _local_lane(local_generation):
    return local_generation and settings.native_text_backend in ('mlx', 'resident_ppu')


def daily_token_budget(*, local_generation=False):
    """Return the configured quota, or None when the application has no quota."""
    if _local_lane(local_generation):
        value = settings.native_local_text_daily_token_budget
    elif settings.app_env == 'development':
        value = settings.development_token_budget
    else:
        value = settings.daily_token_budget
    return value if value > 0 else None


def used_daily_tokens(db, tenant_id, *, local_generation=False):
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    local_scope = ModelUsage.model.startswith('local:') | ModelUsage.model.startswith('ppu:')
    # PostgreSQL adds INTEGER columns before SUM widens them. Long cumulative
    # receipts need BIGINT operands so a valid pair cannot overflow mid-query.
    tokens = cast(ModelUsage.input_tokens, BigInteger) + cast(ModelUsage.output_tokens, BigInteger)
    # SUM(BIGINT) is NUMERIC on PostgreSQL. Normalize its driver Decimal to an
    # exact Python int before arithmetic/JSON, without a lossy float conversion.
    return int(db.scalar(select(func.coalesce(func.sum(tokens), 0))
        .where(ModelUsage.tenant_id == tenant_id, ModelUsage.created_at >= today,
               local_scope if _local_lane(local_generation) else ~local_scope)) or 0)


def remaining_daily_tokens(db, tenant_id, *, local_generation=False):
    """None means unlimited; zero means a configured quota is exhausted."""
    budget = daily_token_budget(local_generation=local_generation)
    if budget is None:
        return None
    return max(0, budget - used_daily_tokens(db, tenant_id, local_generation=local_generation))


def check_daily_token_budget(db, tenant_id, additional_tokens=0, *, local_generation=False):
    remaining = remaining_daily_tokens(db, tenant_id, local_generation=local_generation)
    if remaining is not None and additional_tokens >= remaining:
        raise HTTPException(429, '团队今日模型用量已达到限制，请明日重试')
