"""竞猜系统同步业务层：入账幂等、对账汇总。

契约（docs/astrbot-sync-api.md）：
- POST /sync/credit：按 payout_id 幂等入账；amount 正为发奖、负为冲正；
  与 sync_ledger 写入同事务，失败整体回滚。
- GET /sync/summary：按东八区日期汇总各 QQ 净额（含冲正负数）。

HTTP 层（handlers/sync.py）负责验签与本层异常到状态码的映射：
- SyncValidationError → 400
- SyncConflictError  → 409
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from astrbot.api import logger

from .point_service import InsufficientPointsError, PointService

# 竞猜入账在流水/群成员关系里记的伪群号（审计维度，非真实群）
GUESS_GROUP_ID = "guess"
# 竞猜入账流水 reason 前缀（不在 _EARNED_EXCLUDED_REASONS 中 → 发奖计入累计获得）
REASON_REWARD = "guess_reward"
REASON_REVERSAL = "guess_reversal"
# 单笔金额绝对值上限，防异常数据打穿积分体系
MAX_AMOUNT_ABS = 1_000_000
VALID_TYPES = frozenset({"reward", "reversal", "reconciliation"})

_SHANGHAI_TZ = timezone(timedelta(hours=8))

# 事务回调内部的「重复单」哨兵
_DUPLICATE = object()


class SyncValidationError(Exception):
    """请求体校验失败（HTTP 层映射为 400）。"""


class SyncConflictError(Exception):
    """入账冲突（如冲正时余额不足，HTTP 层映射为 409）。"""


def shanghai_today() -> str:
    """当前东八区日期 YYYY-MM-DD。"""
    return datetime.now(_SHANGHAI_TZ).strftime("%Y-%m-%d")


class SyncService:
    def __init__(self, db, sync_dao, point_service: PointService):
        self._db = db
        self._sync_dao = sync_dao
        self._point_service = point_service

    # ─── POST /sync/credit ────────────────────────────────

    async def handle_credit(self, body) -> dict:
        """幂等入账。返回 {ok, duplicate, balance}。

        Raises:
            SyncValidationError: 请求体不合法。
            SyncConflictError: 冲正余额不足等业务冲突。
        """
        payout_id, qq_id, amount, type_, event_id = self._validate_credit(body)

        async def _tx(conn):
            try:
                await self._sync_dao.insert_credited(
                    conn, payout_id, qq_id, amount, type_, event_id
                )
            except sqlite3.IntegrityError:
                # payout_id 已存在：不再入账，整体只提交这条空事务
                return _DUPLICATE
            reason = REASON_REWARD if amount > 0 else REASON_REVERSAL
            kwargs = {"guard_balance": -amount} if amount < 0 else {}
            return await PointService.change_balance(
                conn, qq_id, GUESS_GROUP_ID, amount, reason, ref_id=event_id, **kwargs
            )

        try:
            result = await self._db.execute_transaction(_tx)
        except InsufficientPointsError as e:
            raise SyncConflictError(str(e)) from e

        if result is _DUPLICATE:
            balance = await self._point_service.get_balance(qq_id)
            logger.info(
                f"[sync] duplicate credit ignored: payout={payout_id} qq={qq_id}"
            )
            return {"ok": True, "duplicate": True, "balance": balance}

        logger.info(
            f"[sync] credited: payout={payout_id} qq={qq_id} amount={amount} "
            f"type={type_} event={event_id} balance={result}"
        )
        return {"ok": True, "duplicate": False, "balance": result}

    @staticmethod
    def _validate_credit(body) -> tuple[str, str, int, str, int | None]:
        if not isinstance(body, dict):
            raise SyncValidationError("bad body")
        payout_id = body.get("payout_id")
        qq_id = body.get("qq_id")
        amount = body.get("amount")
        type_ = body.get("type", "reward")
        event_id = body.get("event_id")
        if not isinstance(payout_id, str) or not payout_id.strip():
            raise SyncValidationError("bad body")
        if not isinstance(qq_id, str) or not qq_id.strip():
            raise SyncValidationError("bad body")
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise SyncValidationError("bad body")
        if amount == 0 or abs(amount) > MAX_AMOUNT_ABS:
            raise SyncValidationError("bad body")
        if type_ not in VALID_TYPES:
            raise SyncValidationError("bad body")
        if event_id is not None and (
            isinstance(event_id, bool) or not isinstance(event_id, int)
        ):
            raise SyncValidationError("bad body")
        return payout_id.strip(), qq_id.strip(), amount, type_, event_id

    # ─── GET /sync/summary ────────────────────────────────

    async def handle_summary(self, date_str: str | None) -> dict:
        """东八区日期净额汇总。date_str 缺省取当天；格式非法抛 SyncValidationError。"""
        if not date_str:
            date_str = shanghai_today()
        else:
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError as e:
                raise SyncValidationError("bad date") from e
        rows = await self._sync_dao.summary_by_date(date_str)
        items = [{"qq_id": r["qq_id"], "total": int(r["total"])} for r in rows]
        logger.info(f"[sync] summary date={date_str} users={len(items)}")
        return {"date": date_str, "items": items}
