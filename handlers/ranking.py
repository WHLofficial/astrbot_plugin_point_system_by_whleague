import asyncio
from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import MessageEventResult

from ..utils.group_info import fetch_member_info
from ..utils.security import clean_display_name


class RankingHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def _fetch_names(self, bot, pairs) -> list[str]:
        """批量解析群昵称：优先群名片(card)，其次昵称(nickname)，最后回退 QQ。

        Args:
            bot: 平台 bot；None 时直接回退 QQ。
            pairs: [(qq, group_id), ...]，全局榜时 group_id 为各用户最近活跃群。

        Returns:
            与 pairs 等长的昵称列表。
        """
        if bot is None or not pairs:
            return [qq for qq, _ in pairs]

        async def _one(qq, gid):
            info = await fetch_member_info(bot, qq, gid)
            if info:
                # 控制字符清洗防注入（与运势/查生日一致，card/nickname 均清洗）
                name = clean_display_name(
                    info.get("card") or info.get("nickname") or ""
                )
                return name or qq
            return qq

        try:
            return await asyncio.gather(*(_one(q, g) for q, g in pairs))
        except Exception:
            return [qq for qq, _ in pairs]

    async def handle(self, event) -> AsyncGenerator[MessageEventResult, None]:
        try:
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("排行仅支持群聊")
                return

            result = await self._plugin.ranking_service.get_ranking(group_id)
            users = result["users"]
            if not users:
                yield event.plain_result("暂无排行数据")
                return

            pairs = []
            for u in users:
                gid = u["group_id"] if "group_id" in u.keys() else group_id
                pairs.append((u["qq"], gid or group_id))
            names = await self._fetch_names(getattr(event, "bot", None), pairs)

            prefix = "🌍 全局排行" if result["is_global"] else "🏆 本群排行"
            lines = [f"{prefix} (Top {len(users)})"]
            for i, (u, name) in enumerate(zip(users, names), 1):
                if result["is_global"]:
                    lines.append(f"{i}. {name}  {u['points']} 积分 (群{u['group_id']})")
                else:
                    lines.append(f"{i}. {name}  {u['points']} 积分")

            # 触发者自己不在榜上时，末尾追加分隔线与自己的排名（未上榜引导签到）
            qq = event.get_sender_id()
            if all(u["qq"] != qq for u in users):
                lines.append("\u2500" * 15)
                self_rank = await self._plugin.ranking_service.get_self_rank(
                    qq, group_id, result["is_global"]
                )
                if self_rank is None:
                    sign_kw = (
                        self._plugin.config_cache.get("keyword_sign") or ["签到"]
                    )[0]
                    lines.append(
                        f"\u4f60: \u672a\u4e0a\u699c\uff0c\u53d1\u9001\u300c{sign_kw}\u300d"
                        "\u5373\u53ef\u83b7\u5f97\u79ef\u5206"
                    )
                else:
                    rank, points, gid = self_rank
                    self_name = (
                        await self._fetch_names(
                            getattr(event, "bot", None), [(qq, gid or group_id)]
                        )
                    )[0]
                    suffix = f" (\u7fa4{gid})" if result["is_global"] else ""
                    lines.append(
                        f"\u4f60: \u7b2c {rank} \u540d \u00b7 {self_name} \u00b7 {points} \u79ef\u5206{suffix}"
                    )
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"Ranking error: {e}")
            yield event.plain_result("查询失败，已记录错误")

    async def stats(self, event) -> AsyncGenerator[MessageEventResult, None]:
        try:
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("统计仅支持群聊")
                return
            s = await self._plugin.sign_in_service.get_stats(group_id)
            lines = [
                "📊 今日签到统计",
                f"👥 总注册用户: {s['total']}",
                f"✅ 今日已签到: {s['today_count']}",
                f"📈 签到率: {s['rate']}",
            ]
            bot = getattr(event, "bot", None)
            if s["first_signer_qq"] or s["streak_king_qq"]:
                qqs = [q for q in (s["first_signer_qq"], s["streak_king_qq"]) if q]
                names = await self._fetch_names(bot, [(q, group_id) for q in qqs])
                name_map = dict(zip(qqs, names))
            else:
                name_map = {}
            if s["first_signer_qq"]:
                lines.append(
                    f"🥇 今日首签: {name_map.get(s['first_signer_qq'], s['first_signer_qq'])}"
                )
            if s["streak_king_qq"]:
                lines.append(
                    f"🏆 当前连签王: {name_map.get(s['streak_king_qq'], s['streak_king_qq'])} ({s['streak_days']}天)"
                )
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"Stats error: {e}")
            yield event.plain_result("查询失败，已记录错误")
