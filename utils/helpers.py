from datetime import datetime, date
from typing import Optional


def today_str() -> str:
    return date.today().isoformat()


def today_mmdd() -> str:
    return date.today().strftime("%m-%d")


def is_date_in_range(target_mmdd: str, start: str, end: Optional[str] = None) -> bool:
    if end is None:
        return target_mmdd == start
    if start <= end:
        return start <= target_mmdd <= end
    return target_mmdd >= start or target_mmdd <= end


async def generate_record_no(conn, date_prefix: Optional[str] = None) -> str:
    """在事务内生成兑换记录编号（并发安全：随事务持锁计数）。"""
    if date_prefix is None:
        date_prefix = datetime.now().strftime("%Y%m%d")
    prefix = f"R{date_prefix}-"
    cur = await conn.execute(
        "SELECT COUNT(*) AS cnt FROM redeem_records WHERE record_no LIKE ?",
        (f"{prefix}%",),
    )
    row = await cur.fetchone()
    count = row[0] if row else 0
    return f"{prefix}{count + 1:04d}"
