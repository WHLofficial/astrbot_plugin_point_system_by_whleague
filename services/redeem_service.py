from datetime import datetime

from astrbot.api import logger

from ..utils.helpers import generate_record_no
from .point_service import InsufficientPointsError, PointService


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
            result.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "description": r["description"],
                    "cost": effective_price,
                    "original_cost": r["cost"] if discount_label else None,
                    "stock": r["stock"],
                    "discount_label": discount_label,
                    "image_url": r["image_url"],
                }
            )
        return result

    async def redeem(
        self, qq: str, group_id: str, item_id: int, quantity: int = 1, bot=None
    ) -> dict:
        item = await self._dao.get_item(item_id)
        if not item or not item["is_active"]:
            return {
                "success": False,
                "msg": "\u7269\u54c1\u4e0d\u5b58\u5728\u6216\u5df2\u4e0b\u67b6",
            }

        effective_price = item["cost"]
        if item["discount_price"] is not None and item["discount_end_time"]:
            try:
                end = datetime.strptime(item["discount_end_time"], "%Y-%m-%d %H:%M")
                if datetime.now() < end:
                    effective_price = item["discount_price"]
            except (ValueError, TypeError):
                pass

        total_cost = effective_price * quantity

        balance = await self._point.get_balance(qq)
        if balance < total_cost:
            return {
                "success": False,
                "msg": f"积分不足，需要 {total_cost} 积分，当前 {balance}",
            }

        async def _tx(conn):
            async with conn.execute(
                "UPDATE redeem_items SET stock=CASE WHEN stock=-1 THEN -1 ELSE stock-? END WHERE id=? AND (stock=-1 OR stock>=?)",
                (quantity, item_id, quantity),
            ) as cur:
                if cur.rowcount == 0:
                    raise ValueError("库存不足")
            # 余额守卫在事务内生效，防止并发兑换透支
            try:
                balance = await PointService.change_balance(
                    conn,
                    qq,
                    group_id,
                    -total_cost,
                    "redeem_cost",
                    earned_amount=0,
                    guard_balance=total_cost,
                )
            except InsufficientPointsError:
                raise ValueError(f"积分不足，需要 {total_cost} 积分")
            # 事务内读取扣减后剩余库存（同事务视图，并发安全）
            async with conn.execute(
                "SELECT stock FROM redeem_items WHERE id=?", (item_id,)
            ) as cur:
                stock_row = await cur.fetchone()
            remaining_stock = stock_row[0] if stock_row else 0
            record_no = await generate_record_no(conn)
            await conn.execute(
                "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost, quantity) VALUES (?,?,?,?,?,?,?)",
                (record_no, qq, group_id, item_id, item["name"], total_cost, quantity),
            )
            return record_no, remaining_stock, balance

        try:
            record_no, remaining_stock, balance = await self._db.execute_transaction(
                _tx
            )
        except ValueError as e:
            return {"success": False, "msg": str(e)}

        await self._point.ensure_negative_title(qq, group_id, bot=bot)

        logger.info(
            f"Redeem {qq}@{group_id}: {item['name']}x{quantity} for {total_cost}"
        )
        stock_text = "∞ (无限)" if remaining_stock == -1 else str(remaining_stock)
        msg = (
            f"兑换成功！获得 {item['name']} x{quantity}，消耗 {total_cost} 积分\n"
            f"  · 订单号: {record_no}\n"
            f"  · 剩余库存: {stock_text}\n"
            f"  · 积分余额: {balance}\n"
            f"  · 请联系管理员核销"
        )
        return {
            "success": True,
            "record_no": record_no,
            "remaining_stock": remaining_stock,
            "balance": balance,
            "msg": msg,
        }

    async def set_record_status(
        self,
        record_no: str,
        action: str,
        admin_qq: str,
        group_id: str,
        note: str = "",
    ) -> dict:
        """核销/驳回兑换订单（三态：pending/verified/rejected，可互切）。

        驳回：事务内退回兑换消耗积分（reason=redeem_refund，不计累计获得）并恢复库存；
        驳回→通过：重新扣除积分（允许余额为负）并扣减库存（有限库存不足则操作失败）。

        Args:
            record_no: 记录编号。
            action: 目标状态，verified（通过）或 rejected（驳回）。
            admin_qq: 操作管理员 QQ。
            group_id: 操作所在群（流水审计维度）。
            note: 备注（驳回原因等）。

        Returns:
            dict：success / msg / changed / status / record。
        """
        record = await self._dao.get_redeem_record(record_no)
        if not record:
            return {"success": False, "msg": f"记录 {record_no} 不存在"}

        if action not in ("verified", "rejected"):
            return {"success": False, "msg": f"无效操作: {action}"}

        target = action
        if record["status"] == target:
            text = "已是通过状态" if target == "verified" else "已是驳回状态"
            return {"success": True, "msg": f"记录 {record_no} {text}", "changed": False}

        item_cost = record["item_cost"]
        quantity = record["quantity"]
        qq = record["qq"]
        item_id = record["item_id"]
        record_id = record["id"]
        refund_amount = item_cost

        async def _tx(conn):
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 条件状态迁移：仅当仍处于预读状态时生效，防止并发重复处理
            if target == "rejected":
                async with conn.execute(
                    "UPDATE redeem_records SET status='rejected', rejected_at=?, rejected_by=?, "
                    "verified_at=NULL, verified_by=NULL, admin_note=? WHERE record_no=? AND status=?",
                    (now, admin_qq, note, record_no, record["status"]),
                ) as cur:
                    if cur.rowcount == 0:
                        raise ValueError("记录状态已变更，请刷新后重试")
                # 通过 → 驳回：退回积分 + 恢复库存
                await PointService.change_balance(
                    conn,
                    qq,
                    group_id,
                    refund_amount,
                    "redeem_refund",
                    earned_amount=0,
                    ref_id=record_id,
                    admin_qq=admin_qq,
                )
                await conn.execute(
                    "UPDATE redeem_items SET stock=CASE WHEN stock=-1 THEN -1 ELSE stock+? END "
                    "WHERE id=?",
                    (quantity, item_id),
                )
            else:
                async with conn.execute(
                    "UPDATE redeem_records SET status='verified', verified_at=?, verified_by=?, "
                    "rejected_at=NULL, rejected_by=NULL, admin_note=? WHERE record_no=? AND status=?",
                    (now, admin_qq, note, record_no, record["status"]),
                ) as cur:
                    if cur.rowcount == 0:
                        raise ValueError("记录状态已变更，请刷新后重试")
                if record["status"] == "rejected":
                    # 驳回 → 通过：库存守卫（不足失败）后重新扣分（允许余额为负）
                    async with conn.execute(
                        "UPDATE redeem_items SET stock=CASE WHEN stock=-1 THEN -1 ELSE stock-? END "
                        "WHERE id=? AND (stock=-1 OR stock>=?)",
                        (quantity, item_id, quantity),
                    ) as cur:
                        if cur.rowcount == 0:
                            raise ValueError("库存不足，无法改回通过")
                    await PointService.change_balance(
                        conn,
                        qq,
                        group_id,
                        -refund_amount,
                        "redeem_cost",
                        earned_amount=0,
                        ref_id=record_id,
                        admin_qq=admin_qq,
                    )

        try:
            await self._db.execute_transaction(_tx)
        except ValueError as e:
            return {"success": False, "msg": str(e)}

        status_text = "✅ 已核销" if target == "verified" else "❌ 已驳回"
        note_text = f"（{note}）" if note else ""
        logger.info(
            f"Redeem record {record_no} {record['status']} -> {target} by {admin_qq}"
        )
        return {
            "success": True,
            "msg": f"记录 {record_no} 状态已修改为: {status_text}{note_text}",
            "changed": True,
            "status": target,
            "record": record,
        }

    async def set_discount(
        self, item_id: int, discount_price: int, end_time: str
    ) -> dict:
        item = await self._dao.get_item(item_id)
        if not item:
            return {"success": False, "msg": "\u7269\u54c1\u4e0d\u5b58\u5728"}
        if discount_price >= item["cost"]:
            return {
                "success": False,
                "msg": "\u6298\u6263\u4ef7\u5e94\u4f4e\u4e8e\u539f\u4ef7",
            }
        try:
            datetime.strptime(end_time, "%Y-%m-%d %H:%M")
        except ValueError:
            return {
                "success": False,
                "msg": "\u622a\u6b62\u65f6\u95f4\u9700\u4e3a YYYY-MM-DD HH:MM \u683c\u5f0f",
            }
        await self._dao.update_item_field(item_id, "discount_price", discount_price)
        await self._dao.update_item_field(item_id, "discount_end_time", end_time)
        return {
            "success": True,
            "msg": f"\u5df2\u8bbe\u7f6e{item['name']}\u6298\u6263\u4ef7 {discount_price} \u79ef\u5206\uff0c\u622a\u6b62 {end_time}",
        }

    async def clear_discount(self, item_id: int) -> dict:
        item = await self._dao.get_item(item_id)
        if not item:
            return {"success": False, "msg": "\u7269\u54c1\u4e0d\u5b58\u5728"}
        await self._dao.update_item_field(item_id, "discount_price", None)
        await self._dao.update_item_field(item_id, "discount_end_time", None)
        return {
            "success": True,
            "msg": f"\u5df2\u6e05\u9664{item['name']}\u7684\u6298\u6263",
        }
