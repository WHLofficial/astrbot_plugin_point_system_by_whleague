from datetime import datetime, date


def today_str() -> str:
    return date.today().isoformat()


def today_mmdd() -> str:
    return date.today().strftime("%m-%d")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_date_in_range(target_mmdd: str, start: str, end: str = None) -> bool:
    if end is None:
        return target_mmdd == start
    if start <= end:
        return start <= target_mmdd <= end
    return target_mmdd >= start or target_mmdd <= end


async def generate_record_no(dao, date_prefix: str = None) -> str:
    if date_prefix is None:
        date_prefix = datetime.now().strftime("%Y%m%d")
    prefix = f"R{date_prefix}-"
    count = await dao.get_record_count_by_prefix(prefix)
    return f"{prefix}{count + 1:04d}"
