from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import MessageEventResult

from ..utils.group_info import fetch_member_info
from ..utils.helpers import (
    consecutive_day_streak,
    month_bounds,
    today_str,
    week_start_str,
)
from ..utils.security import clean_display_name
from ..utils.speak_titles import resolve_title


def _group_stats(rows, total: int) -> tuple[int, int, int, int]:
    """从本群排行推导统计量，同分同名次（与积分排行口径一致）。

    Returns:
        (名次, 群参与人数, 群总发言量, 比我少的人数)
    """
    members = len(rows)
    group_total = sum(int(r["total"]) for r in rows)
    greater = sum(1 for r in rows if int(r["total"]) > total)
    less = sum(1 for r in rows if int(r["total"]) < total)
    return greater + 1, members, group_total, less


class SpeakStatHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def handle(self, event) -> None:
        """静默记一条发言（挂群消息钩子）。

        异常一律吞掉：钩子里紧跟着还有活跃奖励，这里抛出去会让它整体不执行。
        """
        qq = ""
        try:
            if not self._plugin.config_cache.get("speak_stat_enabled"):
                return
            qq = event.get_sender_id()
            group_id = event.get_group_id()
            if not group_id or not qq:
                return
            # 真空消息：图片/表情也算发言，但 notice/request 类协议事件要挡掉
            if not event.get_messages():
                return
            if str(qq) == str(event.get_self_id()):
                return
            await self._plugin.speak_dao.record(str(qq), str(group_id), today_str())
        except Exception as e:
            logger.error(f"Speak stat error for {qq}: {e}")

    async def handle_query(self, event) -> AsyncGenerator[MessageEventResult, None]:
        # 先取值再进 try：异常若就出在取 id 处，兜底日志才不会二次抛错
        qq = ""
        try:
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("我的发言仅支持群聊")
                return

            qq = str(event.get_sender_id())
            dao = self._plugin.speak_dao
            total = await dao.get_total(qq, group_id)
            if total <= 0:
                yield event.plain_result("你还没有发言记录")
                return

            info = await fetch_member_info(getattr(event, "bot", None), qq, group_id)
            name = ""
            if info:
                name = clean_display_name(
                    info.get("card") or info.get("nickname") or ""
                )
            display = f"{name} ({qq})" if name else qq

            today = today_str()
            week_from = week_start_str()
            month_from, month_to = month_bounds(0)
            last_month_from, last_month_to = month_bounds(-1)
            week_count = await dao.get_period_total(qq, group_id, week_from, today)
            month_count = await dao.get_period_total(qq, group_id, month_from, month_to)
            last_month_count = await dao.get_period_total(
                qq, group_id, last_month_from, last_month_to
            )
            today_count = await dao.get_period_total(qq, group_id, today, today)

            lines = [f"💬 {display}"]
            lines.append(f"· 今日发言: {today_count} 条")
            lines.append(f"· 本周发言: {week_count} 条")
            lines.append(f"· 本月发言: {month_count} 条")
            lines.append(f"· 上月发言: {last_month_count} 条")

            rows = await dao.get_group_ranking(group_id)
            rank, members, group_total, less = _group_stats(rows, total)
            lines.append(f"· 累计发言: {total} 条 · 本群第 {rank} 名")

            titles = self._plugin.config_cache.get("speak_stat_titles")
            lines.append(f"· 发言称号: {resolve_title(total, titles)}")

            if group_total > 0:
                share = total / group_total * 100
                percentile = round(less / members * 100) if members else 0
                lines.append(
                    f"· 活跃画像: 占本群 {share:.1f}%，超过 {percentile}% 的群友"
                )

            daily = await dao.get_daily_rows(qq, group_id)
            days = [r["stat_date"] for r in daily if int(r["msg_count"]) > 0]
            if days:
                last_at = max(r["updated_at"] for r in daily)[5:16]
                best = max(int(r["msg_count"]) for r in daily)
                streak = consecutive_day_streak(days, today)
                lines.append(
                    f"· 活跃轨迹: 最近 {last_at} · 活跃 {len(days)} 天"
                    f" · 连续 {streak} 天 · 最高单日 {best} 条"
                )
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"Speak stat query error for {qq}: {e}")
            yield event.plain_result("查询失败，已记录错误")
