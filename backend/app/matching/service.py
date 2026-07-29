"""反馈 → 学习偏好派生（docs/03 第 5 节 learned_preferences）。

- 增量更新：写反馈 +1、撤回/替换 -1（下限 0），键形如
  ``{"not_interested:salary": 3}``——次数累计，撤回即回退。
- 与 reset-learned（阶段 4 已有接口）兼容：重置会把 active 版本标记
  reset 并新建空版本，此后的增量只作用于新版本，历史反馈不会"复活"。
- 学习偏好只影响评分的用户偏好分项（10 分），绝不覆盖用户显式配置。
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LearnedPreference


def _weight_key(sentiment: str, reason_code: str | None) -> str:
    return f"{sentiment}:{reason_code or 'none'}"


async def _get_or_create_active(db: AsyncSession, user_id: uuid.UUID) -> LearnedPreference:
    active = (
        await db.execute(
            select(LearnedPreference).where(
                LearnedPreference.user_id == user_id,
                LearnedPreference.status == "active",
            )
        )
    ).scalar_one_or_none()
    if active is None:
        max_version = (
            await db.execute(
                select(func.coalesce(func.max(LearnedPreference.version), 0)).where(
                    LearnedPreference.user_id == user_id
                )
            )
        ).scalar_one()
        active = LearnedPreference(
            user_id=user_id,
            version=int(max_version) + 1,
            weights_json={},
            status="active",
            derived_from="feedback",
        )
        db.add(active)
        await db.flush()
    return active


async def apply_feedback_delta(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    sentiment: str,
    reason_code: str | None,
    delta: int,
) -> dict:
    """对 active 学习偏好版本做一次计数增减（下限 0，0 值键移除）。"""
    active = await _get_or_create_active(db, user_id)
    weights = dict(active.weights_json or {})
    key = _weight_key(sentiment, reason_code)
    new_value = max(0, int(weights.get(key, 0)) + delta)
    if new_value == 0:
        weights.pop(key, None)
    else:
        weights[key] = new_value
    active.weights_json = weights  # 重新赋值触发 JSONB 变更检测
    active.derived_from = "feedback"
    await db.flush()
    return weights
