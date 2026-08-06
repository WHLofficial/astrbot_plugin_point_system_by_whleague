import asyncio
from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import MessageChain, MessageEventResult
from astrbot.api.platform import MessageType

from ..utils.group_info import fetch_member_info
from ..utils.security import clean_display_name, parse_int, sanitize_text


class RedeemHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def _fetch_names(
        self, bot, pairs, fallback_group_id: str | None = None
    ) -> list[str]:
        """批量解析兑换者昵称：优先记录所在群，其次管理员当前群，最后回退 QQ。

        Args:
            bot: 平台 bot；None 时直接回退 QQ。
            pairs: [(qq, group_id), ...]，group_id 为兑换发生时的群。
            fallback_group_id: 记录群查不到成员时的兜底群（管理员当前群）。

        Returns:
            与 pairs 等长的昵称列表。
        """
        if bot is None or not pairs:
            return [qq for qq, _ in pairs]

        async def _one(qq, gid):
            info = await fetch_member_info(bot, qq, gid)
            if info is None and fallback_group_id and fallback_group_id != gid:
                info = await fetch_member_info(bot, qq, fallback_group_id)
            if info:
                # 控制字符清洗防注入（与排行/查生日一致，card/nickname 均清洗）
                name = clean_display_name(
                    info.get("card") or info.get("nickname") or ""
                )
                return name or qq
            return qq

        try:
            return await asyncio.gather(*(_one(q, g) for q, g in pairs))
        except Exception:
            return [qq for qq, _ in pairs]

    async def list_items(self, event) -> AsyncGenerator[MessageEventResult, None]:
        try:
            items = await self._plugin.redeem_service.list_items()
            if not items:
                yield event.plain_result(
                    "\u6ca1\u6709\u53ef\u5151\u6362\u7684\u7269\u54c1"
                )
                return
            lines = ["\U0001f4e6 \u53ef\u5151\u6362\u7269\u54c1"]
            for it in items:
                stock_str = "\u221e" if it["stock"] == -1 else str(it["stock"])
                price = f"{it['cost']} \u79ef\u5206"
                if it["discount_label"]:
                    price += f" (\u539f{it['original_cost']}, {it['discount_label']})"
                lines.append(
                    f"{it['id']}. {it['name']}  {price}  \u5e93\u5b58: {stock_str}"
                )
                if it["description"]:
                    lines.append(f"   {it['description']}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"List items error: {e}")
            yield event.plain_result(
                "\u67e5\u8be2\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef"
            )

    async def do_redeem(
        self, event, item_id_str: str, quantity_str: str = "1"
    ) -> AsyncGenerator[MessageEventResult, None]:
        try:
            qq = event.get_sender_id()
            group_id = event.get_group_id()
            if not group_id:
                yield event.plain_result("\u5151\u6362\u4ec5\u652f\u6301\u7fa4\u804a")
                return
            item_id = parse_int(item_id_str, min_val=1)
            quantity = parse_int(quantity_str, min_val=1, max_val=999)
            result = await self._plugin.redeem_service.redeem(
                qq, group_id, item_id, quantity, bot=getattr(event, "bot", None)
            )
            yield event.plain_result(result["msg"])
        except ValueError as e:
            yield event.plain_result(str(e))
        except Exception as e:
            logger.error(f"Redeem error: {e}")
            yield event.plain_result(
                "\u5151\u6362\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef"
            )

    async def list_records(
        self, event, target: str | None = None, page_str: str = "1"
    ) -> AsyncGenerator[MessageEventResult, None]:
        try:
            qq = event.get_sender_id()
            group_id = event.get_group_id()
            is_admin = await self._check_admin(event)
            page = parse_int(page_str, min_val=1)
            offset = (page - 1) * 10
            limit = 10

            if target and target.startswith("R"):
                record = await self._plugin.dao.get_redeem_record(target)
                if not record:
                    yield event.plain_result(
                        f"\u8bb0\u5f55 {target} \u4e0d\u5b58\u5728"
                    )
                    return
                if record["group_id"] != event.get_group_id() and not (
                    await self._is_global_admin(event)
                ):
                    yield event.plain_result(
                        "\u4f60\u65e0\u6743\u67e5\u770b\u5176\u4ed6\u7fa4\u7684\u8bb0\u5f55"
                    )
                    return
                if record["qq"] != qq and not is_admin:
                    yield event.plain_result(
                        "\u4f60\u65e0\u6743\u67e5\u770b\u8be5\u8bb0\u5f55"
                    )
                    return
                status_text = (
                    "\u2714 \u5df2\u6838\u9500"
                    if record["status"] == "verified"
                    else (
                        "\u274c \u5df2\u9a73\u56de"
                        if record["status"] == "rejected"
                        else "\u23f3 \u672a\u6838\u9500"
                    )
                )
                names = await self._fetch_names(
                    getattr(event, "bot", None),
                    [(record["qq"], record["group_id"])],
                    fallback_group_id=group_id,
                )
                redeemer = names[0]
                redeemer_text = (
                    f"{redeemer}({record['qq']})"
                    if redeemer != record["qq"]
                    else record["qq"]
                )
                lines = [
                    f"\U0001f4cb \u5151\u6362\u8bb0\u5f55 {record['record_no']}",
                    f"\u7269\u54c1: {record['item_name']}",
                    f"\u6570\u91cf: {record['quantity']}",
                    f"\u6d88\u8017: {record['item_cost']} \u79ef\u5206",
                    f"\u72b6\u6001: {status_text}",
                    f"\u5151\u6362\u8005: {redeemer_text}",
                ]
                if record["admin_note"]:
                    lines.append(f"\u5907\u6ce8: {record['admin_note']}")
                lines.append(f"\u65f6\u95f4: {record['created_at']}")
                yield event.plain_result("\n".join(lines))
                return

            is_admin_list = (target == "all" or target == "pending") and is_admin
            if is_admin_list:
                status_filter = "pending" if target == "pending" else None
                if event.is_admin():
                    records = await self._plugin.dao.get_redeem_records_all(
                        status=status_filter, limit=limit, offset=offset
                    )
                else:
                    records = await self._plugin.dao.get_redeem_records_all(
                        status=status_filter,
                        group_id=group_id,
                        limit=limit,
                        offset=offset,
                    )
            else:
                records = await self._plugin.dao.get_redeem_records_by_user(
                    qq, group_id=group_id, limit=limit, offset=offset
                )

            if not records:
                yield event.plain_result("\u6ca1\u6709\u8bb0\u5f55")
                return

            name_map = {}
            if is_admin_list:
                names = await self._fetch_names(
                    getattr(event, "bot", None),
                    [(r["qq"], r["group_id"]) for r in records],
                    fallback_group_id=group_id,
                )
                name_map = dict(zip([r["qq"] for r in records], names))

            lines = [f"\U0001f4cb \u5151\u6362\u8bb0\u5f55 (\u7b2c{page}\u9875)"]
            for r in records:
                if r["status"] == "verified":
                    status_icon = "\u2714"
                elif r["status"] == "rejected":
                    status_icon = "\u274c"
                else:
                    status_icon = "\u23f3"
                line = (
                    f"{status_icon} {r['record_no']} {r['item_name']}x{r['quantity']} {r['item_cost']}\u79ef\u5206"
                )
                if is_admin_list:
                    name = name_map.get(r["qq"], r["qq"])
                    line += f" {name}({r['qq']})" if name != r["qq"] else f" {r['qq']}"
                lines.append(line)
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"List records error: {e}")
            yield event.plain_result(
                "\u67e5\u8be2\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef"
            )

    async def verify_record(
        self, event, record_no: str, action: str, note: str = ""
    ) -> AsyncGenerator[MessageEventResult, None]:
        try:
            qq = event.get_sender_id()
            if not await self._check_admin(event):
                yield event.plain_result(
                    "\u4f60\u6ca1\u6709\u6743\u9650\u6267\u884c\u6b64\u64cd\u4f5c"
                )
                return

            note = sanitize_text(note)
            record = await self._plugin.dao.get_redeem_record(record_no)
            if not record:
                yield event.plain_result(f"\u8bb0\u5f55 {record_no} \u4e0d\u5b58\u5728")
                return
            if record["group_id"] != event.get_group_id() and not (
                await self._is_global_admin(event)
            ):
                yield event.plain_result(
                    "\u4f60\u65e0\u6743\u5904\u7406\u5176\u4ed6\u7fa4\u7684\u8bb0\u5f55"
                )
                return

            result = await self._plugin.redeem_service.set_record_status(
                record_no,
                action,
                qq,
                event.get_group_id(),
                note,
            )
            yield event.plain_result(result["msg"])
            if not (result["success"] and result["changed"]):
                return

            # 状态变更成功：按配置渠道通知兑换者；发送失败时向当前会话发警告
            cost = record["item_cost"]
            if result["status"] == "verified":
                note_text = f"\uff08\u5907\u6ce8\uff1a{note}\uff09" if note else ""
                notify = (
                    f"\u2705 \u4f60\u7684\u5151\u6362\u8ba2\u5355 {record_no}"
                    f"\uff08{record['item_name']} x{record['quantity']}\uff09"
                    f"\u5df2\u901a\u8fc7\u6838\u9500{note_text}"
                )
                warn = f"\u26a0\ufe0f \u8ba2\u5355 {record_no} \u5df2\u901a\u8fc7\u6838\u9500\uff0c\u4f46\u901a\u77e5\u5151\u6362\u8005\u5931\u8d25\uff08\u65e0\u6cd5\u9001\u8fbe\u539f\u5151\u6362\u7fa4/\u79c1\u4fe1\uff09\uff0c\u8bf7\u7ebf\u4e0b\u8054\u7cfb"
            else:
                reason = f"\uff08\u7406\u7531\uff1a{note}\uff09" if note else "\uff08\u7ba1\u7406\u5458\u9a73\u56de\uff09"
                notify = (
                    f"\u274c \u4f60\u7684\u5151\u6362\u8ba2\u5355 {record_no}"
                    f"\uff08{record['item_name']} x{record['quantity']}\uff09"
                    f"\u5df2\u88ab\u9a73\u56de\uff0c\u6d88\u8017\u7684 {cost} \u79ef\u5206\u5df2\u9000\u56de{reason}"
                )
                warn = f"\u26a0\ufe0f \u8ba2\u5355 {record_no} \u5df2\u88ab\u9a73\u56de\uff0c\u4f46\u901a\u77e5\u5151\u6362\u8005\u5931\u8d25\uff08\u65e0\u6cd5\u9001\u8fbe\u539f\u5151\u6362\u7fa4/\u79c1\u4fe1\uff09\uff0c\u8bf7\u7ebf\u4e0b\u8054\u7cfb"
            target_qq = record["qq"]
            try:
                await self._send_notice(event, record, target_qq, notify)
            except Exception as e:
                logger.warning(
                    f"Notify redeemer {target_qq} for {record_no} failed: {e}"
                )
                try:
                    await event.send(MessageChain().message(warn))
                except Exception as we:
                    logger.warning(f"Send warning for {record_no} failed: {we}")

            # 驳回退分回正 / 改回通过扣负：联动负分头衔
            await self._plugin.point_service.ensure_negative_title(
                target_qq,
                record["group_id"],
                bot=getattr(event, "bot", None),
            )
        except Exception as e:
            logger.error(f"Verify record error: {e}")
            yield event.plain_result(
                "\u64cd\u4f5c\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef"
            )

    async def _is_global_admin(self, event) -> bool:
        """AstrBot 全局管理员或 admins 表中 group_id 为空的全局管理员。"""
        if event.is_admin():
            return True
        qq = event.get_sender_id()
        return await self._plugin.dao.is_admin(qq, None)

    async def _send_notice(self, event, record: dict, target_qq: str, notify: str) -> None:
        """按配置渠道通知兑换者（group=原兑换群 @通知；private=私信）。

        跨群/私信依赖插件 context 主动发送；发送失败抛异常由调用方兜底。
        """
        channel = self._plugin.config_cache.get("redeem_notify_channel", "group")
        if channel == "private":
            await self._send_private(event, record, target_qq, notify)
        else:
            await self._send_group(event, record, target_qq, notify)

    async def _send_group(self, event, record, target_qq: str, notify: str) -> None:
        chain = MessageChain().at(target_qq, target_qq).message(notify)
        if record["group_id"] == event.get_group_id():
            await event.send(chain)
            return
        context = getattr(self._plugin, "context", None)
        if context is None:
            raise RuntimeError("plugin context unavailable")
        origin = (
            f"{self._bot_name(event)}:"
            f"{MessageType.GROUP_MESSAGE.value}:{record['group_id']}"
        )
        await context.send_message(origin, chain)

    async def _send_private(self, event, record, target_qq: str, notify: str) -> None:
        context = getattr(self._plugin, "context", None)
        if context is None:
            raise RuntimeError("plugin context unavailable")
        origin = (
            f"{self._bot_name(event)}:"
            f"{MessageType.FRIEND_MESSAGE.value}:{target_qq}"
        )
        await context.send_message(origin, MessageChain().message(notify))

    def _bot_name(self, event) -> str:
        """机器人名称 = 平台适配器实例 id（session 首段，context.send_message 匹配依据）。

        AstrBot 中事件会话的 platform_name 即 platform_meta.id（机器人名称），
        context.send_message 仅当 session 首段 == platform.meta().id 时才找到平台实例。
        优先取当前事件的实例 id；缺失时回退事件 unified_msg_origin 首段，最后兜底平台类型名。
        """
        get_platform_id = getattr(event, "get_platform_id", None)
        if callable(get_platform_id):
            try:
                pid = get_platform_id()
                if pid:
                    return str(pid)
            except Exception:
                pass
        umo = getattr(event, "unified_msg_origin", "") or ""
        first = umo.split(":", 1)[0]
        if first:
            return first
        return getattr(event, "get_platform_name", lambda: "")() or ""

    async def _check_admin(self, event) -> bool:
        if event.is_admin():
            return True
        qq = event.get_sender_id()
        group_id = event.get_group_id()
        return await self._plugin.dao.is_admin(qq, group_id)
