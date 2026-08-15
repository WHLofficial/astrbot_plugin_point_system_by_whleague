"""S11 服务边界 + 积分不变量 + DAO 全方法：抽奖空档位/不限次数、兑换无限库存/下架/折扣过期、total_earned 排除表、负分头衔与群名片、DAO 查询过滤。"""

import json

from .common import FakeBot, TempDB, base_cfg, patch_random


def _lottery_cfg(**over):
    cfg = base_cfg(
        lottery_enabled=True,
        lottery_cost=10,
        lottery_daily_limit=5,
        lottery_passphrase="whl",
        lottery_tiers=json.dumps(
            {
                "tiers": [
                    {
                        "label": "参与奖",
                        "weight": 1,
                        "points_min": 0,
                        "points_max": 0,
                        "emoji": "",
                    }
                ]
            }
        ),
        negative_disable_lottery=True,
    )
    cfg.update(over)
    return cfg


async def _lottery(t, cfg):
    from astrbot_plugin_point_system_by_whleague.services.lottery_service import (
        LotteryService,
    )
    from astrbot_plugin_point_system_by_whleague.services.point_service import (
        PointService,
    )

    return LotteryService(t.db, t.dao, PointService(t.db, t.dao), cfg)


async def test_lottery_edge_configs():
    async with TempDB() as t:
        svc = await _lottery(t, _lottery_cfg(lottery_tiers='{"tiers":[]}'))
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',1000)"
        )
        r = await svc.draw("u1", "G1")
        assert not r["success"] and "档位未配置" in r["msg"]
        assert (await t.dao.get_account("u1"))["points"] == 1000  # 未扣费
        # 无 accounts 行：余额视为 0 → 积分不足
        r = await svc.draw("nobody", "G1")
        assert not r["success"] and "积分不足" in r["msg"]
    return "抽奖：空档位拒绝不扣费、未注册用户积分不足"


async def test_lottery_unlimited_and_accounting():
    async with TempDB() as t:
        win_cfg = _lottery_cfg(
            lottery_daily_limit=0,
            lottery_cost=10,
            lottery_tiers=json.dumps(
                {
                    "tiers": [
                        {
                            "label": "中奖",
                            "weight": 1,
                            "points_min": 10,
                            "points_max": 10,
                            "emoji": "🎉",
                        }
                    ]
                }
            ),
        )
        svc = await _lottery(t, win_cfg)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',1000)"
        )
        with patch_random(randint=10):
            r = await svc.draw("u1", "G1")
        assert r["success"] and r["is_win"] and r["reward"] == 10 and "🎉" in r["msg"]
        # v0.2.2：反馈含消耗/积分变化/当前积分
        assert "消耗: 10 积分" in r["msg"], r["msg"]
        assert "获得: +10 积分" in r["msg"], r["msg"]
        assert "积分变化: +0" in r["msg"], r["msg"]
        assert "当前积分: 1000" in r["msg"], r["msg"]
        assert r["balance"] == 1000, r
        row = await t.dao.get_account("u1")
        assert row["points"] == 1000  # -10 成本 +10 奖励
        assert row["total_earned"] == 10  # 中奖计入累计获得
        txns = await t.db.fetchall(
            "SELECT amount, reason FROM point_transactions WHERE qq='u1' ORDER BY id"
        )
        assert [t["reason"] for t in txns] == ["lottery_cost", "lottery_reward"]
        assert txns[0]["amount"] == -10 and txns[1]["amount"] == 10
        rec = await t.db.fetchone(
            "SELECT is_win, tier_label FROM lottery_record WHERE qq='u1'"
        )
        assert rec["is_win"] == 1 and rec["tier_label"] == "中奖"
        # 不限次数：连续 10 次全部成功
        for _ in range(10):
            r = await svc.draw("u1", "G1")
            assert r["success"]
        assert await t.count("lottery_record") == 11
        # 未中奖：仅扣成本、不计累计（此前 11 次中奖各 +10 → total_earned=110）
        lose_cfg = _lottery_cfg(
            lottery_daily_limit=0,
            lottery_cost=10,
            lottery_tiers=json.dumps(
                {
                    "tiers": [
                        {
                            "label": "谢谢参与",
                            "weight": 1,
                            "points_min": 0,
                            "points_max": 0,
                            "emoji": "",
                        }
                    ]
                }
            ),
        )
        svc2 = await _lottery(t, lose_cfg)
        with patch_random(randint=0):
            r = await svc2.draw("u1", "G1")
        assert r["success"] and not r["is_win"]
        assert "未中奖" in r["msg"] and "积分变化: -10" in r["msg"], r["msg"]
        assert "当前积分: 990" in r["msg"], r["msg"]
        row = await t.dao.get_account("u1")
        assert row["points"] == 990 and row["total_earned"] == 110  # 未中奖不计累计
        # 每日限额按业务日（period_start_str）判定：历史记录不计入今日
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u2',100)"
        )
        await t.db.execute(
            "INSERT INTO lottery_record (qq, group_id, cost, reward_amount, is_win, tier_label, created_at) "
            "VALUES ('u2','G1',1,0,0,'x','2000-01-01 04:00:00')"
        )
        cfg3 = _lottery_cfg(lottery_daily_limit=1, lottery_cost=1)
        svc3 = await _lottery(t, cfg3)
        r = await svc3.draw("u2", "G1")
        assert r["success"]  # 旧记录不影响今日
        r = await svc3.draw("u2", "G1")
        assert not r["success"] and "上限" in r["msg"]
    return "抽奖：中奖入账/未中奖只扣费/不限次数/历史记录不计日限"


async def test_redeem_edge_stocks():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )
        from astrbot_plugin_point_system_by_whleague.services.redeem_service import (
            RedeemService,
        )

        svc = RedeemService(t.db, t.dao, PointService(t.db, t.dao))
        # 无限库存（-1）多次兑换
        item_id = await t.dao.add_item("无限", 10, -1)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',1000)"
        )
        for _ in range(3):
            r = await svc.redeem("u1", "G1", item_id, 1)
            assert r["success"], r
            # 无限库存反馈：剩余库存显示 ∞
            assert "剩余库存: ∞" in r["msg"], r["msg"]
            assert "联系管理员核销" in r["msg"], r["msg"]
        item = await t.dao.get_item(item_id)
        assert item["stock"] == -1
        # 余额恰好相等
        await t.db.execute("UPDATE accounts SET points=10 WHERE qq='u1'")
        r = await svc.redeem("u1", "G1", item_id, 1)
        assert r["success"]
        # 订单号格式与积分余额
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

        assert r["record_no"].startswith(f"R{today_str().replace('-', '')}-"), r
        assert "积分余额: 0" in r["msg"], r["msg"]
        assert (await t.dao.get_account("u1"))["points"] == 0
        r = await svc.redeem("u1", "G1", item_id, 1)  # 现在不足
        assert not r["success"] and "积分不足" in r["msg"]
        # 数量×单价扣费正确
        item2 = await t.dao.add_item("多件", 50, 5)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u2',500)"
        )
        r = await svc.redeem("u2", "G1", item2, 2)
        assert r["success"]
        rec = await t.db.fetchone(
            "SELECT item_cost, quantity FROM redeem_records WHERE qq='u2'"
        )
        assert rec["item_cost"] == 100 and rec["quantity"] == 2
        assert (await t.dao.get_account("u2"))["points"] == 400
        # 下架物品
        await t.dao.soft_delete_item(item2)
        r = await svc.redeem("u2", "G1", item2, 1)
        assert not r["success"] and "已下架" in r["msg"]
        # 不存在物品
        r = await svc.redeem("u2", "G1", 99999, 1)
        assert not r["success"] and "不存在" in r["msg"]
    return "兑换：∞库存/余额恰等/多件扣费/下架/不存在"


async def test_redeem_discount_expired():
    async with TempDB() as t:
        from datetime import datetime, timedelta

        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )
        from astrbot_plugin_point_system_by_whleague.services.redeem_service import (
            RedeemService,
        )

        svc = RedeemService(t.db, t.dao, PointService(t.db, t.dao))
        item_id = await t.dao.add_item("商品", 100, 5)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',1000)"
        )
        # 折扣有效期内按折扣价
        future = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        await svc.set_discount(item_id, 50, future)
        r = await svc.redeem("u1", "G1", item_id, 1)
        assert r["success"]
        rec = await t.db.fetchone("SELECT item_cost FROM redeem_records WHERE qq='u1'")
        assert rec["item_cost"] == 50
        # 折扣过期后按原价
        past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        await svc.set_discount(item_id, 50, past)
        r = await svc.redeem("u1", "G1", item_id, 1)
        assert r["success"]
        rec = await t.db.fetchone(
            "SELECT item_cost FROM redeem_records WHERE qq='u1' ORDER BY id DESC"
        )
        assert rec["item_cost"] == 100
    return "兑换：折扣有效按折价、过期按原价"


async def test_point_earned_exclusion():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        for i, reason in enumerate(
            ("admin_sub", "lottery_cost", "redeem_cost", "easter_unlucky")
        ):
            await ps.add("u1", "G1", 10, reason)
            row = await t.dao.get_account("u1")
            assert row["points"] == (i + 1) * 10 and row["total_earned"] == 0, (
                reason,
                dict(row),
            )
        await ps.add("u1", "G1", 10, "签到")
        row = await t.dao.get_account("u1")
        assert row["points"] == 50 and row["total_earned"] == 10
        await ps.add("u1", "G1", 10, "active_reward")
        row = await t.dao.get_account("u1")
        assert row["total_earned"] == 20
    return "积分：扣减型 reason 不计累计获得、奖励型计入"


async def test_point_invalid_and_overdraw():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        for bad in (0, -1):
            try:
                await ps.add("u1", "G1", bad, "test")
                raise AssertionError("add <=0 应拒绝")
            except ValueError:
                pass
        try:
            await ps.subtract("u1", "G1", -1, "test_sub")
            raise AssertionError("subtract <=0 应拒绝")
        except ValueError:
            pass
        # 超额扣分：允许扣成负数（惩罚场景），流水如实记录、自动补负分头衔
        await ps.add("u1", "G1", 5, "test")
        r = await ps.subtract("u1", "G1", 6, "test_sub")
        assert r["balance"] == -1
        row = await t.dao.get_account("u1")
        assert row["points"] == -1
        assert (await t.dao.get_user("u1", "G1"))["negative_title_id"] == 1
        txn = await t.db.fetchone(
            "SELECT amount, balance_after FROM point_transactions WHERE qq='u1' AND reason='test_sub'"
        )
        assert txn["amount"] == -6 and txn["balance_after"] == -1
        # 未注册用户自动建行
        await ps.add("nobody", "G2", 7, "test")
        row = await t.dao.get_account("nobody")
        assert row["points"] == 7
        assert (await t.dao.get_user("nobody", "G2")) is not None
        # 未注册用户扣分：自动建行并如实扣成负数（不再假成功/静默丢失）
        r2 = await ps.subtract("ghost", "G3", 8, "admin_sub")
        assert r2["balance"] == -8
        row = await t.dao.get_account("ghost")
        assert row is not None and row["points"] == -8
        assert (await t.dao.get_user("ghost", "G3"))["negative_title_id"] == 1  # 扣负后自动补头衔
        txn = await t.db.fetchone(
            "SELECT amount, balance_after FROM point_transactions WHERE qq='ghost' AND reason='admin_sub'"
        )
        assert txn["amount"] == -8 and txn["balance_after"] == -8
    return "积分：非正数拒绝、超额扣分可成负数+头衔+流水、自动建行（含扣分建行）"


async def test_point_ref_admin_trace():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        await ps.add(
            "u1", "G1", 5, "admin_add", ref_id=7, admin_qq="a9", admin_override=True
        )
        txn = await t.db.fetchone(
            "SELECT ref_id, admin_qq FROM point_transactions WHERE qq='u1'"
        )
        assert txn["ref_id"] == 7 and txn["admin_qq"] == "a9"
    return "积分：ref_id/admin_qq 落库"


async def test_negative_title_with_bot_card():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        bot = FakeBot(member_card="旧名片")
        # 负余额来源：签到非酋彩蛋（subtract 不允许扣成负数，直接置负）
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('1001',-10)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('1001','1')"
        )
        await ps.ensure_negative_title("1001", "1", bot=bot)
        row = await t.dao.get_user("1001", "1")
        assert row["negative_title_id"] == 1
        assert row["negative_title_prev_card"] == "旧名片"
        # 取成员信息 + 设置头衔名片
        actions = [a for a, _ in bot.calls]
        assert "get_group_member_info" in actions
        card_calls = [c for a, c in bot.calls if a == "set_group_card"]
        assert any(c["card"] == "群女仆1号" for c in card_calls), bot.calls
        # 回正：恢复原名片
        await ps.add("1001", "1", 10, "admin_add", admin_override=True, bot=bot)
        row = await t.dao.get_account("1001")
        assert row["points"] == 0
        assert (await t.dao.get_user("1001", "1"))["negative_title_id"] is None
        card_calls = [c for a, c in bot.calls if a == "set_group_card"]
        assert card_calls[-1]["card"] == "旧名片"
        # 无 bot 时仅维护 DB 状态（编号复用为最小可用 1）
        await t.db.execute("UPDATE accounts SET points=-5 WHERE qq='1001'")
        n_cards = len([a for a, _ in bot.calls if a == "set_group_card"])
        await ps.ensure_negative_title("1001", "1", bot=None)
        row = await t.dao.get_user("1001", "1")
        assert row["negative_title_id"] == 1
        assert len([a for a, _ in bot.calls if a == "set_group_card"]) == n_cards
    return "负分头衔：取原名片/设头衔/回正恢复/无 bot 仅维护 DB"


async def test_negative_title_id_reuse():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('1001',-5),('1002',-5)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('1001','1'),('1002','1')"
        )
        await ps.ensure_negative_title("1001", "1", bot=None)
        assert (await t.dao.get_user("1001", "1"))["negative_title_id"] == 1
        # 回正释放编号
        await t.db.execute("UPDATE accounts SET points=5 WHERE qq='1001'")
        await ps.ensure_negative_title("1001", "1", bot=None)
        assert (await t.dao.get_user("1001", "1"))["negative_title_id"] is None
        # 新用户复用最小可用编号
        await ps.ensure_negative_title("1002", "1", bot=None)
        assert (await t.dao.get_user("1002", "1"))["negative_title_id"] == 1
    return "负分头衔：编号回收复用"


async def test_dao_transaction_queries():
    async with TempDB() as t:
        for i in range(15):
            await t.db.execute(
                "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) VALUES (?,?,?,?,?)",
                ("u1", "G1", i, i, f"r{i}"),
            )
        await t.db.execute(
            "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) VALUES ('u2','G2',1,1,'x')"
        )
        rows = await t.dao.get_transactions(qq="u1", group_id="G1", limit=10, offset=0)
        assert len(rows) == 10
        rows2 = await t.dao.get_transactions(
            qq="u1", group_id="G1", limit=10, offset=10
        )
        assert len(rows2) == 5
        # 两页合集覆盖全部 15 条（同秒插入时排序稳定由 id 兜底）
        all_amounts = {r["amount"] for r in rows} | {r["amount"] for r in rows2}
        assert all_amounts == set(range(15))
        rows = await t.dao.get_transactions(qq="u1", group_id="G2")
        assert rows == []  # 组合过滤无匹配
        rows = await t.dao.get_transactions(group_id="G2")
        assert len(rows) == 1
    return "DAO：流水分页/组合过滤"


async def test_dao_redeem_record_queries():
    async with TempDB() as t:
        await t.dao.add_item("商品", 10, 5)
        for i, (qq, gid, status) in enumerate(
            [
                ("u1", "G1", "pending"),
                ("u1", "G1", "verified"),
                ("u2", "G1", "pending"),
                ("u3", "G2", "pending"),
            ]
        ):
            await t.db.execute(
                "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost, quantity, status) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (f"R20260101-{i:04d}", qq, gid, 1, "商品", 10, 1, status),
            )
        assert len(await t.dao.get_redeem_records_by_user("u1", group_id="G1")) == 2
        assert (
            len(
                await t.dao.get_redeem_records_all(
                    status="pending", group_id="G1", limit=10, offset=0
                )
            )
            == 2
        )
        assert len(await t.dao.get_redeem_records_all(status="pending")) == 3
        assert len(await t.dao.get_redeem_records_all(limit=2)) == 2
        assert await t.dao.get_redeem_record("R20260101-0003") is not None
        assert await t.dao.get_redeem_record("R99999999-0001") is None
    return "DAO：兑换记录按用户/状态/群过滤与分页"


async def test_dao_redeem_status_machine():
    async with TempDB() as t:
        await t.dao.add_item("商品", 10, 5)
        await t.db.execute(
            "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost, quantity) "
            "VALUES ('R20260101-0001','u1','G1',1,'商品',10,1)"
        )
        # 通过：写 verified 审计
        assert (
            await t.dao.set_redeem_status("R20260101-0001", "verified", "admin")
            == "verified"
        )
        rec = await t.dao.get_redeem_record("R20260101-0001")
        assert rec["status"] == "verified"
        assert rec["verified_by"] == "admin" and rec["verified_at"]
        assert rec["rejected_by"] is None and rec["rejected_at"] is None
        # 驳回：清空 verified 审计、写 rejected 审计
        assert (
            await t.dao.set_redeem_status("R20260101-0001", "rejected", "admin2", "无货")
            == "rejected"
        )
        rec = await t.dao.get_redeem_record("R20260101-0001")
        assert rec["status"] == "rejected"
        assert rec["rejected_by"] == "admin2" and rec["rejected_at"]
        assert rec["verified_by"] is None and rec["verified_at"] is None
        assert rec["admin_note"] == "无货"
        # 不存在 → None
        assert (
            await t.dao.set_redeem_status("R99999999-0001", "rejected", "admin")
            is None
        )
        # 恢复库存：有限库存增加，无限库存（-1）不变
        await t.dao.restore_stock(1, 2)
        row = await t.db.fetchone("SELECT stock FROM redeem_items WHERE id=1")
        assert row["stock"] == 7
        await t.db.execute("UPDATE redeem_items SET stock=-1 WHERE id=1")
        await t.dao.restore_stock(1, 3)
        row = await t.db.fetchone("SELECT stock FROM redeem_items WHERE id=1")
        assert row["stock"] == -1
    return "DAO：核销/驳回状态机与审计列互斥、库存恢复"


async def test_dao_admin_global_scope():
    async with TempDB() as t:
        await t.dao.add_admin("gadmin", "owner", "G1")
        assert await t.dao.is_admin("gadmin", "G1")
        assert not await t.dao.is_admin("gadmin", "G2")  # 群管理仅限本群
        # 全局管理（group_id NULL）
        await t.dao.add_admin("root", "owner", None)
        assert await t.dao.is_admin("root", "任意群")
        await t.dao.remove_admin("gadmin", "G1")
        assert not await t.dao.is_admin("gadmin", "G1")
        await t.dao.remove_admin("root", None)
        assert not await t.dao.is_admin("root", "任意群")
    return "DAO：群管理/全局管理作用域与删除"


async def test_dao_keyword_upsert_keeps_claims():
    async with TempDB() as t:
        await t.dao.set_daily_keyword("G1", "红包", 10, "admin")
        kw = await t.dao.get_daily_keyword("G1", t.dao._today_str())
        await t.db.execute(
            "INSERT INTO daily_keyword_claim (kw_id, qq, group_id, points_earned) VALUES (?,?,?,?)",
            (kw["id"], "u1", "G1", 10),
        )
        # 再次设置（UPSERT）：不删除旧行，领取记录保留
        await t.dao.set_daily_keyword("G1", "新口令", 20, "admin")
        kw = await t.dao.get_daily_keyword("G1", t.dao._today_str())
        assert kw["keyword"] == "新口令" and kw["points"] == 20
        assert await t.count("daily_keyword_claim") == 1
        assert await t.dao.has_claimed_daily_keyword(kw["id"], "u1")
        # 清除口令级联清领取记录
        await t.dao.clear_daily_keyword("G1")
        assert await t.count("daily_keyword") == 0
        assert await t.count("daily_keyword_claim") == 0
    return "DAO：口令 UPSERT 保留领取记录、清除级联"


async def test_dao_misc():
    async with TempDB() as t:
        # 播报幂等
        await t.dao.mark_birthday_announced("G1", "2026-01-01", '["a"]')
        await t.dao.mark_birthday_announced("G1", "2026-01-01", '["b"]')
        assert await t.count("birthday_announce_log") == 1
        assert await t.dao.was_birthday_announced("G1", "2026-01-01")
        assert not await t.dao.was_birthday_announced("G1", "2026-01-02")
        # update_item_field 字段白名单
        item_id = await t.dao.add_item("商品", 10, 5)
        await t.dao.update_item_field(item_id, "description", "新描述")
        assert (await t.dao.get_item(item_id))["description"] == "新描述"
        for bad in ("points", "id", "is_active; DROP TABLE users"):
            try:
                await t.dao.update_item_field(item_id, bad, 1)
                raise AssertionError(f"字段 {bad} 应被白名单拒绝")
            except ValueError:
                pass
        # 排行 min_points 过滤（0 分与负分不参与）
        for qq, pts in (("a", 10), ("b", 0), ("c", -5)):
            await t.db.execute(
                "INSERT INTO accounts (qq, points) VALUES (?,?)", (qq, pts)
            )
            await t.db.execute(
                "INSERT INTO users (qq, group_id) VALUES (?,?)", (qq, "G1")
            )
        rows = await t.dao.get_top_n_by_group("G1", 10)
        assert [r["qq"] for r in rows] == ["a"]
        # 生日用户查询
        await t.db.execute(
            "INSERT INTO accounts (qq, birthday) VALUES ('d','12-25')"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('d','G1')"
        )
        assert [r["qq"] for r in await t.dao.get_birthday_users("G1", "12-25")] == ["d"]
        assert await t.dao.count_users_in_group("G1") == 4
        # get_rank_global 全局排名（与全局榜口径一致：accounts 全局 + 最近活跃群）
        await t.db.execute("INSERT INTO accounts (qq, points) VALUES ('e',10)")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('e','G1')")
        assert await t.dao.get_rank_global("a") == (1, 10, "G1")  # 10 分全局最高
        assert await t.dao.get_rank_global("e") == (1, 10, "G1")  # 同分同名次
        assert await t.dao.get_rank_global("b") is None  # 0 分未上榜
        assert await t.dao.get_rank_global("c") is None  # 负分未上榜
        assert await t.dao.get_rank_global("nobody") is None  # 无账户
    return "DAO：播报幂等/字段白名单/排行过滤/生日查询"


async def test_point_guard_earned_amount():
    """守卫分支（change_balance guard_balance）下显式 earned_amount 也计入 total_earned。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            InsufficientPointsError,
            PointService,
        )

        ps = PointService(t.db, t.dao)
        await ps.add("u1", "G1", 100, "test")

        async def _tx(conn):
            return await PointService.change_balance(
                conn,
                "u1",
                "G1",
                -30,
                "guarded_cost",
                earned_amount=10,
                guard_balance=30,
            )

        balance = await t.db.execute_transaction(_tx)
        row = await t.dao.get_account("u1")
        assert balance == 70
        assert row["points"] == 70
        # 守卫扣分 + 显式 earned_amount：total_earned 正确累计（100 + 10）
        assert row["total_earned"] == 110
        # 守卫失败路径：余额(70) < 守卫值(71) 时抛错且整体回滚
        async def _bad_tx(conn):
            return await PointService.change_balance(
                conn, "u1", "G1", -1, "guarded_cost", guard_balance=71
            )

        try:
            await t.db.execute_transaction(_bad_tx)
            raise AssertionError("余额不足应抛 InsufficientPointsError")
        except InsufficientPointsError:
            pass
        row = await t.dao.get_account("u1")
        assert row["points"] == 70 and row["total_earned"] == 110
    return "积分：守卫分支显式 earned_amount 累计正确、守卫失败原子回滚"


TESTS = [
    ("lottery_edge_configs", test_lottery_edge_configs),
    ("lottery_unlimited_accounting", test_lottery_unlimited_and_accounting),
    ("redeem_edge_stocks", test_redeem_edge_stocks),
    ("redeem_discount_expired", test_redeem_discount_expired),
    ("point_earned_exclusion", test_point_earned_exclusion),
    ("point_guard_earned_amount", test_point_guard_earned_amount),
    ("point_invalid_overdraw", test_point_invalid_and_overdraw),
    ("point_ref_admin_trace", test_point_ref_admin_trace),
    ("negative_title_bot_card", test_negative_title_with_bot_card),
    ("negative_title_id_reuse", test_negative_title_id_reuse),
    ("dao_transaction_queries", test_dao_transaction_queries),
    ("dao_redeem_record_queries", test_dao_redeem_record_queries),
    ("dao_redeem_status_machine", test_dao_redeem_status_machine),
    ("dao_admin_global_scope", test_dao_admin_global_scope),
    ("dao_keyword_upsert", test_dao_keyword_upsert_keeps_claims),
    ("dao_misc", test_dao_misc),
]
