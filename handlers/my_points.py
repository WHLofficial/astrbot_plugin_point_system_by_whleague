from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import MessageEventResult

from ..utils.group_info import fetch_member_info
from ..utils.helpers import today_str
from ..utils.security import clean_display_name


class MyPointsHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def handle(self, event) -> AsyncGenerator[MessageEventResult, None]:
        try:
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("我的积分仅支持群聊")
                return

            qq = event.get_sender_id()
            account = await self._plugin.dao.get_account(qq)
            if not account:
                yield event.plain_result("你还没有积分记录，先发送「签到」吧")
                return

            info = await fetch_member_info(getattr(event, "bot", None), qq, group_id)
            name = ""
            if info:
                name = clean_display_name(
                    info.get("card") or info.get("nickname") or ""
                )
            display = f"{name} ({qq})" if name else qq

            rank = await self._plugin.dao.get_rank_in_group(qq, group_id)

            lines = [f"💰 {display}"]
            lines.append(f"· 当前积分: {account['points']}")
            lines.append(f"· 累计签到: {account['total_sign_days']} 天")
            lines.append(f"· 连签: 第 {account['consecutive_days']} 天")
            signed = (
                "✅ 已签到" if account["last_sign_date"] == today_str() else "❌ 未签到"
            )
            lines.append(f"· 今日签到: {signed}")
            if rank:
                lines.append(f"· 本群排名: 第 {rank} 名")

            records = await self._plugin.dao.get_transactions(
                qq=qq, group_id=group_id, limit=5
            )
            if records:
                lines.append("")
                lines.append("📊 最近流水")
                for r in records:
                    icon = "\U0001f7e2" if r["amount"] >= 0 else "\U0001f534"
                    lines.append(
                        f"{icon} {r['amount']:+d}  {r['reason']}  {r['created_at'][:16]}"
                    )
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"My points error for {event.get_sender_id()}: {e}")
            yield event.plain_result("查询失败，已记录错误")
