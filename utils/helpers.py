from datetime import date, datetime, timedelta

_DEFAULT_BOUNDARY = (4, 0)
_day_boundary = _DEFAULT_BOUNDARY
"""业务日分界时刻 (hour, minute)：一天区间为该时刻至次日同一时刻。"""


def set_day_boundary(value: str) -> tuple[int, int]:
    """设置每日刷新时刻，非法输入回退到默认 04:00。"""
    global _day_boundary
    try:
        hour, minute = value.strip().split(":", 1)
        parsed = (int(hour), int(minute))
        if not (0 <= parsed[0] <= 23 and 0 <= parsed[1] <= 59):
            raise ValueError
        _day_boundary = parsed
    except (AttributeError, ValueError):
        _day_boundary = _DEFAULT_BOUNDARY
    return _day_boundary


def get_day_boundary() -> tuple[int, int]:
    return _day_boundary


def _shifted_now() -> datetime:
    h, m = _day_boundary
    return datetime.now() - timedelta(hours=h, minutes=m)


def today_str() -> str:
    return _shifted_now().date().isoformat()


def today_mmdd() -> str:
    return _shifted_now().date().strftime("%m-%d")


def period_start_str() -> str:
    """当前业务日区间的起点时间（YYYY-MM-DD HH:MM:SS，本地时区）。"""
    now = datetime.now()
    h, m = _day_boundary
    day = (now - timedelta(hours=h, minutes=m)).date()
    return day.strftime("%Y-%m-%d") + f" {h:02d}:{m:02d}:00"


def is_date_in_range(target_mmdd: str, start: str, end: str | None = None) -> bool:
    if end is None:
        return target_mmdd == start
    if start <= end:
        return start <= target_mmdd <= end
    return target_mmdd >= start or target_mmdd <= end


async def generate_record_no(conn, date_prefix: str | None = None) -> str:
    """在事务内生成兑换记录编号（并发安全：随事务持锁计数）。"""
    if date_prefix is None:
        date_prefix = today_str().replace("-", "")
    prefix = f"R{date_prefix}-"
    async with conn.execute(
        "SELECT COUNT(*) AS cnt FROM redeem_records WHERE record_no LIKE ?",
        (f"{prefix}%",),
    ) as cur:
        row = await cur.fetchone()
    count = row[0] if row else 0
    return f"{prefix}{count + 1:04d}"


def week_start_str() -> str:
    """当前业务日所在周（周一为起点）的周一，YYYY-MM-DD。"""
    day = _shifted_now().date()
    return (day - timedelta(days=day.weekday())).isoformat()


def month_bounds(offset: int = 0) -> tuple[str, str]:
    """当前业务日所在自然月的首末业务日，YYYY-MM-DD。

    offset=0 为本月，-1 为上月，依此类推（可跨年）。
    """
    day = _shifted_now().date()
    year, month = day.year, day.month + offset
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    first = date(year, month, 1)
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first.isoformat(), (nxt - timedelta(days=1)).isoformat()


def consecutive_day_streak(date_strs, end_date: str) -> int:
    """从 end_date 起往回数的连续日历天数。

    end_date 当天没有记录时从次日往回算起——当天还没过完，连续不应被判为中断。
    """
    days = set(date_strs)
    cur = date.fromisoformat(end_date)
    if cur.isoformat() not in days:
        cur -= timedelta(days=1)
    streak = 0
    while cur.isoformat() in days:
        streak += 1
        cur -= timedelta(days=1)
    return streak
