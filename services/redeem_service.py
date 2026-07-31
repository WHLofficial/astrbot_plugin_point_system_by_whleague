from datetime import datetime
from astrbot.api import logger
from ..utils.helpers import generate_record_no


class RedeemService:
    def __init__(self, db, dao, point_svc):
        self._db = db
        self._dao = dao
        self._point = point_svc

    async def list_items(self):
        rows = await self._dao.get_active_items()
        result = []
        for r in rows:
            effective_price = r["cost"]
            discount_label = None
            if r["discount_price"] is not None and r["discount_end_time"]:
                try:
                    end = datetime.strptime(r["discount_end_time"], "%Y-%m-%d %H:%M")
                    if datetime.now() < end:
                        effective_price = r["discount_price"]
                        discount_label = f"\u9650\u65f6\u6298\u6263 {r['discount_price']} \u79ef\u5206"
                except (ValueError, TypeError):
                    pass
            result.append({
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "cost": effective_price,
                "original_cost": r["cost"] if discount_label else None,
                "stock": r["stock"],
                "discount_label": discount_label,
                "image_url": r["image_url"],
            })
        return result

    async def redeem(self, qq: str, group_id: str, item_id: int, quantity: int = 1, bot=None) -> dict:
        item = await self._dao.get_item(item_id)
        if not item or not item["is_active"]:
            return {"success": False, "msg": "\u7269\u54c1\u4e0d\u5b58\u5728\u6216\u5df2\u4e0b\u67b6"}

        effective_price = item["cost"]
        if item["discount_price"] is not None and item["discount_end_time"]:
            try:
                end = datetime.strptime(item["discount_end_time"], "%Y-%m-%d %H:%M")
                if datetime.now() < end:
                    effective_price = item["discount_price"]
            except (ValueError, TypeError):
                pass

        total_cost = effective_price * quantity

        balance = await self._point.get_balance(qq, group_id)
        if balance < total_cost:
            return {"success": False, "msg": f"\u79ef\u5206\u4e0d\u8db3\uff0c\u9700\u8981 {total_cost} \u79ef\u5206\uff0c\u5f53\u524d {balance}"}

        async def _tx(conn):
            cur = await conn.execute(
                "UPDATE redeem_items SET stock=CASE WHEN stock=-1 THEN -1 ELSE stock-? END WHERE id=? AND (stock=-1 OR stock>=?)",
                (quantity, item_id, quantity),
            )
            if cur.rowcount == 0:
                raise ValueError("\u5e93\u5b58\u4e0d\u8db3")
            # 余额守卫在事务内生效，防止并发兑换透支
            cur = await conn.execute("UPDATE users SET points=points-? WHERE qq=? AND group_id=? AND points>=?", (total_cost, qq, group_id, total_cost))
            if cur.rowcount == 0:
                raise ValueError(f"\u79ef\u5206\u4e0d\u8db3\uff0c\u9700\u8981 {total_cost} \u79ef\u5206")
            cur2 = await conn.execute("SELECT points FROM users WHERE qq=? AND group_id=?", (qq, group_id))
            bal = (await cur2.fetchone())[0]
            await conn.execute("INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) VALUES (?,?,?,?,?)", (qq, group_id, -total_cost, bal, "redeem_cost"))
            record_no = await generate_record_no(conn)
            await conn.execute("INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost, quantity) VALUES (?,?,?,?,?,?,?)", (record_no, qq, group_id, item_id, item["name"], total_cost, quantity))

        try:
            await self._db.execute_transaction(_tx)
        except ValueError as e:
            return {"success": False, "msg": str(e)}

        await self._point.ensure_negative_title(qq, group_id, bot=bot)

        logger.info(f"Redeem {qq}@{group_id}: {item['name']}x{quantity} for {total_cost}")
        return {"success": True, "msg": f"\u5151\u6362\u6210\u529f\uff01\u83b7\u5f97 {item['name']} x{quantity}\uff0c\u6d88\u8017 {total_cost} \u79ef\u5206"}

    async def set_discount(self, item_id: int, discount_price: int, end_time: str) -> dict:
        item = await self._dao.get_item(item_id)
        if not item:
            return {"success": False, "msg": "\u7269\u54c1\u4e0d\u5b58\u5728"}
        if discount_price >= item["cost"]:
            return {"success": False, "msg": "\u6298\u6263\u4ef7\u5e94\u4f4e\u4e8e\u539f\u4ef7"}
        try:
            datetime.strptime(end_time, "%Y-%m-%d %H:%M")
        except ValueError:
            return {"success": False, "msg": "\u622a\u6b62\u65f6\u95f4\u9700\u4e3a YYYY-MM-DD HH:MM \u683c\u5f0f"}
        await self._dao.update_item_field(item_id, "discount_price", discount_price)
        await self._dao.update_item_field(item_id, "discount_end_time", end_time)
        return {"success": True, "msg": f"\u5df2\u8bbe\u7f6e{item['name']}\u6298\u6263\u4ef7 {discount_price} \u79ef\u5206\uff0c\u622a\u6b62 {end_time}"}

    async def clear_discount(self, item_id: int) -> dict:
        item = await self._dao.get_item(item_id)
        if not item:
            return {"success": False, "msg": "\u7269\u54c1\u4e0d\u5b58\u5728"}
        await self._dao.update_item_field(item_id, "discount_price", None)
        await self._dao.update_item_field(item_id, "discount_end_time", None)
        return {"success": True, "msg": f"\u5df2\u6e05\u9664{item['name']}\u7684\u6298\u6263"}
