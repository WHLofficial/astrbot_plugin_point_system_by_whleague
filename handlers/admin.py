from astrbot.api import logger
from utils.security import parse_int, parse_qq, sanitize_text


class AdminHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def _is_admin(self, event) -> bool:
        if event.is_admin():
            return True
        qq = event.get_sender_id()
        group_id = event.get_group_id()
        return await self._plugin.dao.is_admin(qq, group_id)

    async def _require_admin(self, event):
        if not await self._is_admin(event):
            yield event.plain_result("\u4f60\u6ca1\u6709\u6743\u9650\u6267\u884c\u6b64\u64cd\u4f5c")
            return False
        return True

    async def adjust_points(self, event, action: str):
        if not await self._require_admin(event):
            return
        try:
            msg = event.get_message_str()
            parts = msg.split()
            if len(parts) < 3:
                yield event.plain_result(f"\u7528\u6cd5: /{action} @\u7528\u6237/Q\u53f7 <\u5206\u503c>")
                return
            target = parse_qq(parts[1])
            amount = parse_int(parts[2], min_val=1, max_val=1000000)
            group_id = event.get_group_id()
            admin_qq = event.get_sender_id()

            reason = "admin_add" if action == "\u52a0\u5206" else "admin_sub"
            if action == "\u52a0\u5206":
                r = await self._plugin.point_service.add(target, group_id, amount, reason, admin_override=True)
                yield event.plain_result(f"\u5df2\u7ed9 {target} \u52a0 {amount} \u79ef\u5206\uff0c\u5f53\u524d\u4f59\u989d: {r['balance']}")
            else:
                try:
                    r = await self._plugin.point_service.subtract(target, group_id, amount, reason)
                    yield event.plain_result(f"\u5df2\u7ed9 {target} \u6263 {amount} \u79ef\u5206\uff0c\u5f53\u524d\u4f59\u989d: {r['balance']}")
                except ValueError as e:
                    yield event.plain_result(str(e))
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"\u53c2\u6570\u9519\u8bef: {e}")
        except Exception as e:
            logger.error(f"Admin adjust points error: {e}")
            yield event.plain_result("\u64cd\u4f5c\u5931\u8d25\uff0c\u5df2\u8bb0\u5f55\u9519\u8bef")

    async def add_item(self, event):
        if not await self._require_admin(event):
            return
        try:
            msg = event.get_message_str()
            parts = msg.split(maxsplit=3)
            if len(parts) < 3:
                yield event.plain_result("\u7528\u6cd5: /\u6dfb\u52a0\u5151\u6362 <\u540d\u79f0> <\u6d88\u8017> [\u5e93\u5b58]")
                return
            name = sanitize_text(parts[1])
            cost = parse_int(parts[2], min_val=1)
            stock = -1
            if len(parts) >= 4:
                stock = parse_int(parts[3], min_val=-1)
            item_id = await self._plugin.dao.add_item(name, cost, stock)
            yield event.plain_result(f"\u5df2\u6dfb\u52a0\u5151\u6362\u7269\u54c1: {name} (\u6d88\u8017{cost}\u79ef\u5206, \u5e93\u5b58{stock})")
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"\u53c2\u6570\u9519\u8bef: {e}")
        except Exception as e:
            logger.error(f"Add item error: {e}")
            yield event.plain_result("\u64cd\u4f5c\u5931\u8d25")

    async def delete_item(self, event):
        if not await self._require_admin(event):
            return
        try:
            msg = event.get_message_str()
            parts = msg.split()
            if len(parts) < 2:
                yield event.plain_result("\u7528\u6cd5: /\u5220\u9664\u5151\u6362 <\u7269\u54c1ID>")
                return
            item_id = parse_int(parts[1], min_val=1)
            await self._plugin.dao.soft_delete_item(item_id)
            yield event.plain_result(f"\u5df2\u5220\u9664\u5151\u6362\u7269\u54c1 (ID: {item_id})")
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"\u53c2\u6570\u9519\u8bef: {e}")
        except Exception as e:
            logger.error(f"Delete item error: {e}")
            yield event.plain_result("\u64cd\u4f5c\u5931\u8d25")

    async def modify_item(self, event):
        if not await self._require_admin(event):
            return
        try:
            msg = event.get_message_str()
            parts = msg.split(maxsplit=3)
            if len(parts) < 4:
                yield event.plain_result("\u7528\u6cd5: /\u4fee\u6539\u5151\u6362 <ID> <\u5b57\u6bb5> <\u503c>")
                return
            item_id = parse_int(parts[1], min_val=1)
            field = parts[2]
            value = sanitize_text(parts[3])
            if field in ("cost", "stock", "discount_price"):
                value = int(value)
            await self._plugin.dao.update_item_field(item_id, field, value)
            yield event.plain_result(f"\u5df2\u4fee\u6539\u7269\u54c1 {item_id} \u7684 {field}")
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"\u53c2\u6570\u9519\u8bef: {e}")
        except Exception as e:
            logger.error(f"Modify item error: {e}")
            yield event.plain_result("\u64cd\u4f5c\u5931\u8d25")

    async def set_daily_kw(self, event):
        if not await self._require_admin(event):
            return
        try:
            msg = event.get_message_str()
            parts = msg.split(maxsplit=2)
            if len(parts) < 3:
                yield event.plain_result("\u7528\u6cd5: /\u8bbe\u7f6e\u4eca\u65e5\u53e3\u4ee4 <\u5173\u952e\u8bcd> <\u79ef\u5206>")
                return
            keyword = sanitize_text(parts[1])
            points = parse_int(parts[2], min_val=1)
            group_id = event.get_group_id()
            admin_qq = event.get_sender_id()
            await self._plugin.dao.set_daily_keyword(group_id, keyword, points, admin_qq)
            yield event.plain_result(f"\u5df2\u8bbe\u7f6e\u4eca\u65e5\u53e3\u4ee4: \"{keyword}\" \u5956\u52b1 {points} \u79ef\u5206")
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"\u53c2\u6570\u9519\u8bef: {e}")
        except Exception as e:
            logger.error(f"Set daily keyword error: {e}")
            yield event.plain_result("\u64cd\u4f5c\u5931\u8d25")

    async def clear_daily_kw(self, event):
        if not await self._require_admin(event):
            return
        try:
            group_id = event.get_group_id()
            await self._plugin.dao.clear_daily_keyword(group_id)
            yield event.plain_result("\u5df2\u6e05\u9664\u4eca\u65e5\u53e3\u4ee4")
        except Exception as e:
            logger.error(f"Clear daily keyword error: {e}")
            yield event.plain_result("\u64cd\u4f5c\u5931\u8d25")

    async def set_config(self, event):
        if not await self._require_admin(event):
            return
        try:
            msg = event.get_message_str()
            parts = msg.split(maxsplit=2)
            if len(parts) < 3:
                yield event.plain_result("\u7528\u6cd5: /\u8bbe\u7f6e <\u914d\u7f6e\u9879> <\u503c>")
                return
            key = parts[1]
            value = parts[2]
            from config.defaults import cast_value
            parsed = cast_value(key, value)
            await self._plugin.dao.set_config(key, str(parsed))
            self._plugin.config_cache[key] = parsed if not isinstance(parsed, str) else parsed
            yield event.plain_result(f"\u5df2\u66f4\u65b0\u914d\u7f6e {key} = {parsed}")
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"\u53c2\u6570\u9519\u8bef: {e}")
        except Exception as e:
            logger.error(f"Set config error: {e}")
            yield event.plain_result("\u64cd\u4f5c\u5931\u8d25")

    async def view_config(self, event):
        if not await self._require_admin(event):
            return
        try:
            rows = await self._plugin.dao.get_all_config()
            if not rows:
                yield event.plain_result("\u6ca1\u6709\u914d\u7f6e\u6570\u636e")
                return
            lines = ["\u2699 \u5f53\u524d\u914d\u7f6e"]
            for r in rows:
                if r["key"] in ("schema_version",):
                    continue
                lines.append(f"{r['key']} = {r['value']}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"View config error: {e}")
            yield event.plain_result("\u67e5\u8be2\u5931\u8d25")

    async def set_discount(self, event):
        if not await self._require_admin(event):
            return
        try:
            msg = event.get_message_str()
            parts = msg.split(maxsplit=3)
            if len(parts) < 4:
                yield event.plain_result("\u7528\u6cd5: /\u8bbe\u7f6e\u6298\u6263 <\u7269\u54c1ID> <\u6298\u6263\u4ef7> <\u622a\u6b62\u65f6\u95f4>")
                return
            item_id = parse_int(parts[1], min_val=1)
            price = parse_int(parts[2], min_val=1)
            end_time = parts[3]
            result = await self._plugin.redeem_service.set_discount(item_id, price, end_time)
            yield event.plain_result(result["msg"])
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"\u53c2\u6570\u9519\u8bef: {e}")
        except Exception as e:
            logger.error(f"Set discount error: {e}")
            yield event.plain_result("\u64cd\u4f5c\u5931\u8d25")

    async def clear_discount(self, event):
        if not await self._require_admin(event):
            return
        try:
            msg = event.get_message_str()
            parts = msg.split()
            if len(parts) < 2:
                yield event.plain_result("\u7528\u6cd5: /\u6e05\u9664\u6298\u6263 <\u7269\u54c1ID>")
                return
            item_id = parse_int(parts[1], min_val=1)
            result = await self._plugin.redeem_service.clear_discount(item_id)
            yield event.plain_result(result["msg"])
        except (ValueError, IndexError) as e:
            yield event.plain_result(f"\u53c2\u6570\u9519\u8bef: {e}")
        except Exception as e:
            logger.error(f"Clear discount error: {e}")
            yield event.plain_result("\u64cd\u4f5c\u5931\u8d25")
