"""S2 并发/竞态：BEGIN IMMEDIATE 串行化、UNIQUE 约束、令牌单次性。"""

import asyncio
import json

from .common import FakeEvent, TempDB, base_cfg, collect


async def _signin_svc(t, cfg=None):
    from astrbot_plugin_point_system_by_whleague.services.date_reward_service import (
        DateRewardService,
    )
    from astrbot_plugin_point_system_by_whleague.services.easter_service import (
        EasterService,
    )
    from astrbot_plugin_point_system_by_whleague.services.point_service import (
        PointService,
    )
    from astrbot_plugin_point_system_by_whleague.services.sign_in_service import (
        SignInService,
    )

    cfg = cfg or base_cfg(
        signin_fixed_mode=True,
        signin_fixed_points=10,
        signin_first_bonus=50,
        signin_day_first_bonus=30,
        signin_consecutive_max=30,
        signin_consecutive_bonus_per_day=5,
        signin_weekly_bonus=100,
        birthday_bonus_points=0,
    )
    ps = PointService(t.db, t.dao)
    return SignInService(
        t.db, t.dao, ps, EasterService(t.dao), DateRewardService(t.dao), cfg
    )


async def test_concurrent_signin_same_user():
    """100 并发同用户签到：恰好 1 次成功、1 条记录。"""
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        svc = await _signin_svc(t)
        results = await asyncio.gather(
            *[svc.sign_in("u1", "G1", "aiocqhttp", "签到") for _ in range(100)]
        )
        ok = [r for r in results if not r["already_signed"]]
        assert len(ok) == 1, len(ok)
        assert await t.count("sign_in_log") == 1
        row = await t.dao.get_account("u1")
        assert row["total_sign_days"] == 1 and row["points"] == 10 + 50 + 30 + 0
    return "并发签到同用户：仅 1 次成功"


async def test_concurrent_signin_distinct_users():
    """100 并发异用户签到：每日首签奖励恰 1 人。"""
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        svc = await _signin_svc(t)
        await asyncio.gather(
            *[svc.sign_in(f"u{i}", "G1", "aiocqhttp", "签到") for i in range(100)]
        )
        assert await t.count("sign_in_log") == 100
        rows = await t.db.fetchall(
            "SELECT COUNT(*) AS c FROM sign_in_log WHERE bonus_day_first>0"
        )
        assert rows[0]["c"] == 1
    return "并发签到异用户：首签奖励恰 1 人"


async def test_concurrent_lottery_daily_limit():
    """50 并发抽奖（日限 10）：恰 10 次成功。"""
    async with TempDB() as t:
        cfg = {
            "lottery_enabled": True,
            "lottery_cost": 10,
            "lottery_daily_limit": 10,
            "lottery_passphrase": "whl",
            "negative_disable_lottery": True,
            "lottery_tiers": json.dumps(
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
        }
        from astrbot_plugin_point_system_by_whleague.services.lottery_service import (
            LotteryService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        svc = LotteryService(t.db, t.dao, ps, cfg)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',10000)"
        )
        results = await asyncio.gather(*[svc.draw("u1", "G1") for _ in range(50)])
        ok = [r for r in results if r["success"]]
        assert len(ok) == 10, len(ok)
        assert await t.count("lottery_record") == 10
        row = await t.dao.get_account("u1")
        assert row["points"] == 10000 - 100
    return "并发抽奖：每日限额恰 10 次"


async def test_concurrent_redeem_last_stock():
    """30 并发抢最后 1 库存：恰 1 成功，库存不为负。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )
        from astrbot_plugin_point_system_by_whleague.services.redeem_service import (
            RedeemService,
        )

        ps = PointService(t.db, t.dao)
        svc = RedeemService(t.db, t.dao, ps)
        item_id = await t.dao.add_item("限量", 10, 1)
        for i in range(30):
            await t.db.execute(
                "INSERT INTO accounts (qq, points) VALUES (?,1000)", (f"u{i}",)
            )
            await t.db.execute(
                "INSERT INTO users (qq, group_id) VALUES (?,?)", (f"u{i}", "G1")
            )
        results = await asyncio.gather(
            *[svc.redeem(f"u{i}", "G1", item_id, 1) for i in range(30)]
        )
        ok = [r for r in results if r["success"]]
        assert len(ok) == 1, len(ok)
        row = await t.db.fetchone(
            "SELECT stock FROM redeem_items WHERE id=?", (item_id,)
        )
        assert row["stock"] == 0
        assert await t.count("redeem_records") == 1
    return "并发兑换：库存原子扣减恰 1 成功"


async def test_concurrent_daily_keyword_claim():
    """50 并发口令领取同用户：恰 1 次加分。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.daily_keyword_service import (
            DailyKeywordService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        svc = DailyKeywordService(t.db, t.dao, ps)
        await t.dao.set_daily_keyword("G1", "红包", 10, "admin")
        results = await asyncio.gather(
            *[svc.check_and_claim("u1", "G1", "抢到红包") for _ in range(50)]
        )
        claimed = [r for r in results if r.get("claimed")]
        assert len(claimed) == 1, len(claimed)
        assert await t.count("daily_keyword_claim") == 1
    return "并发口令：UNIQUE 约束恰 1 次"


async def test_concurrent_points_reconcile():
    """200 次并发加减分：余额 == 初始 + Σ流水。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',10000)"
        )

        async def add():
            await ps.add("u1", "G1", 10, "test_add")

        async def sub():
            await ps.subtract("u1", "G1", 3, "test_sub")

        tasks = [add() if i % 2 == 0 else sub() for i in range(200)]
        await asyncio.gather(*tasks)

        row = await t.dao.get_account("u1")
        flows = await t.db.fetchone(
            "SELECT SUM(amount) AS s FROM point_transactions WHERE qq='u1'"
        )
        assert row["points"] == 10000 + flows["s"], (row["points"], flows["s"])
        assert flows["s"] == 100 * 10 - 100 * 3
    return "并发加减分：余额与流水严格对账"


async def test_concurrent_negative_title_unique():
    """10 用户同时转负：头衔编号唯一无冲突。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        for i in range(10):
            await t.db.execute(
                "INSERT INTO accounts (qq, points) VALUES (?,?)", (f"n{i}", -1)
            )
            await t.db.execute(
                "INSERT INTO users (qq, group_id, negative_title_prev_card) VALUES (?,?,?)",
                (f"n{i}", "G1", f"卡片{i}"),
            )
        ids = await asyncio.gather(
            *[ps.ensure_negative_title(f"n{i}", "G1", bot=None) for i in range(10)]
        )
        assert sorted(ids) == list(range(1, 11)), ids
    return "并发负分头衔：编号 1..10 唯一"


async def test_read_while_write_no_block():
    """读写串行化正确性：写事务进行中，读被锁串行等待，但无脏读、无数据丢失。"""
    async with TempDB() as t:
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',1)"
        )

        async def slow_tx():
            async def _tx(conn):
                await conn.execute(
                    "INSERT INTO accounts (qq, points) VALUES ('u2',2)"
                )
                await asyncio.sleep(0.4)  # 模拟慢事务

            await t.db.execute_transaction(_tx)

        async def reader():
            await asyncio.sleep(0.1)  # 事务已开始
            started = asyncio.get_event_loop().time()
            rows = await t.db.fetchall("SELECT qq FROM accounts")
            elapsed = asyncio.get_event_loop().time() - started
            return elapsed, {r["qq"] for r in rows}

        tx_task = asyncio.create_task(slow_tx())
        try:
            await asyncio.sleep(0.05)
            read_elapsed, qqs = await reader()
            # 单连接串行化：读等待写锁释放；正确性由数据断言保证（读到提交后完整数据）
            assert read_elapsed >= 0.15, read_elapsed
            assert qqs == {"u1", "u2"}, qqs  # 无脏读、无丢失
        finally:
            await tx_task
    return "读写串行化：等待写锁、无脏读、无丢失"


async def test_concurrent_clear_confirm_single_use():
    """两个并发确认清空（同一令牌）：仅 1 个执行。"""
    async with TempDB() as t:
        import types as _t

        from astrbot_plugin_point_system_by_whleague.handlers.admin import AdminHandler
        from astrbot_plugin_point_system_by_whleague.services.backup_service import (
            BackupService,
        )

        backup = BackupService(t.db, {"backup_dirs": []})
        plugin = _t.SimpleNamespace(
            db=t.db,
            dao=t.dao,
            backup_service=backup,
            point_service=_t.SimpleNamespace(_set_group_card=_async_noop),
        )
        handler = AdminHandler(plugin)
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',1)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('u1','G1')"
        )

        ev = FakeEvent("admin", "G1", is_admin=True)
        await collect(handler.clear_data(ev, "group"))
        token = handler._pending_clears["admin"]["token"]

        async def confirm():
            e = FakeEvent("admin", "G1", is_admin=True, msg=f"/确认清空 {token}")
            return await collect(handler.confirm_clear(e))

        results = await asyncio.gather(confirm(), confirm())
        executed = [r for r in results if any("已清空本群数据" in m for m in r)]
        assert len(executed) == 1, list(results)
        # 群清空：成员关系保留，积分归零
        assert await t.count("users") == 1
        row = await t.db.fetchone("SELECT points FROM accounts WHERE qq='u1'")
        assert row["points"] == 0
    return "并发确认清空：令牌单次性保证仅 1 次执行"


async def test_concurrent_redeem_record_no_unique():
    """30 并发兑换无限库存：全部成功且 record_no 唯一。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )
        from astrbot_plugin_point_system_by_whleague.services.redeem_service import (
            RedeemService,
        )

        ps = PointService(t.db, t.dao)
        svc = RedeemService(t.db, t.dao, ps)
        item_id = await t.dao.add_item("无限", 1, -1)
        for i in range(30):
            await t.db.execute(
                "INSERT INTO accounts (qq, points) VALUES (?,1000)", (f"u{i}",)
            )
            await t.db.execute(
                "INSERT INTO users (qq, group_id) VALUES (?,?)", (f"u{i}", "G1")
            )
        results = await asyncio.gather(
            *[svc.redeem(f"u{i}", "G1", item_id, 1) for i in range(30)]
        )
        assert all(r["success"] for r in results)
        rows = await t.db.fetchall("SELECT record_no FROM redeem_records")
        nos = [r["record_no"] for r in rows]
        assert len(nos) == 30 and len(set(nos)) == 30
    return "并发兑换：record_no 全局唯一"


async def test_concurrent_add_admin_single_row():
    """50 并发添加同一管理：UNIQUE 约束下仅 1 行。"""
    async with TempDB() as t:
        await asyncio.gather(*[t.dao.add_admin("a", "x", "G1") for _ in range(50)])
        assert await t.count("admins") == 1
        assert await t.dao.is_admin("a", "G1")
    return "并发添加管理：恰 1 行"


async def test_concurrent_birthday_mark_idempotent():
    """50 并发播报标记：INSERT OR IGNORE 保证恰 1 行。"""
    async with TempDB() as t:
        await asyncio.gather(
            *[
                t.dao.mark_birthday_announced("G1", "2026-08-01", '["a"]')
                for _ in range(50)
            ]
        )
        assert await t.count("birthday_announce_log") == 1
        assert await t.dao.was_birthday_announced("G1", "2026-08-01")
    return "并发播报标记：幂等恰 1 行"


async def _async_noop(*a, **k):
    return None


TESTS = [
    ("concurrent_signin_same_user", test_concurrent_signin_same_user),
    ("concurrent_signin_distinct", test_concurrent_signin_distinct_users),
    ("concurrent_lottery_limit", test_concurrent_lottery_daily_limit),
    ("concurrent_redeem_stock", test_concurrent_redeem_last_stock),
    ("concurrent_keyword_claim", test_concurrent_daily_keyword_claim),
    ("concurrent_points_reconcile", test_concurrent_points_reconcile),
    ("concurrent_negative_title", test_concurrent_negative_title_unique),
    ("read_while_write", test_read_while_write_no_block),
    ("concurrent_clear_confirm", test_concurrent_clear_confirm_single_use),
    ("concurrent_redeem_record_no", test_concurrent_redeem_record_no_unique),
    ("concurrent_add_admin", test_concurrent_add_admin_single_row),
    ("concurrent_birthday_mark", test_concurrent_birthday_mark_idempotent),
]
