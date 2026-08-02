"""S18 跨群共享（v0.2.0 核心）：一号跨群积分、全局限签、跨群负分联动、排行昵称、群清空语义。

说明：FakeEvent 的 bot 为 object()（无 call_action），昵称获取自动回退 QQ 号；
FakeBot 可返回 card 验证昵称路径。
"""

import asyncio
import json
import types

from .common import FakeBot, FakeEvent, TempDB, base_cfg, collect


async def _stack(t, overrides=None):
    from astrbot_plugin_point_system_by_whleague.services.date_reward_service import (
        DateRewardService,
    )
    from astrbot_plugin_point_system_by_whleague.services.easter_service import (
        EasterService,
    )
    from astrbot_plugin_point_system_by_whleague.services.lottery_service import (
        LotteryService,
    )
    from astrbot_plugin_point_system_by_whleague.services.point_service import (
        PointService,
    )
    from astrbot_plugin_point_system_by_whleague.services.redeem_service import (
        RedeemService,
    )
    from astrbot_plugin_point_system_by_whleague.services.sign_in_service import (
        SignInService,
    )

    cfg = base_cfg(
        lottery_enabled=True,
        lottery_cost=10,
        lottery_daily_limit=0,
        lottery_passphrase="whl",
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
        negative_disable_lottery=True,
        signin_fixed_mode=True,
        signin_fixed_points=10,
        signin_first_bonus=0,
        signin_day_first_bonus=30,
        signin_consecutive_max=30,
        signin_consecutive_bonus_per_day=5,
        signin_weekly_bonus=100,
        birthday_bonus_points=0,
    )
    cfg.update(overrides or {})
    ps = PointService(t.db, t.dao)
    easter = EasterService(t.dao)
    dater = DateRewardService(t.dao)
    return {
        "cfg": cfg,
        "point": ps,
        "sign_in": SignInService(t.db, t.dao, ps, easter, dater, cfg),
        "lottery": LotteryService(t.db, t.dao, ps, cfg),
        "redeem": RedeemService(t.db, t.dao, ps),
    }


async def test_shared_balance_across_groups():
    """A 群签到入账 → B 群余额/排行可见；A 群加分 → B 群可兑换。"""
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        s = await _stack(t)
        # A 群签到：基础 10 + 首签0 + 日首签30 = 40（第1天连签0）
        r = await s["sign_in"].sign_in("u1", "100000", "aiocqhttp", "签到")
        assert r["points"] == 40, r["points"]
        # B 群余额共享可见
        assert await s["point"].get_balance("u1") == 40
        # A 群加分 → B 群可兑换
        await s["point"].add("u1", "100000", 60, "admin_add", admin_override=True)
        item_id = await t.dao.add_item("商品", 90, 10)
        r = await s["redeem"].redeem("u1", "200000", item_id, 1)
        assert r["success"], r
        assert await s["point"].get_balance("u1") == 10
        # 兑换流水记录在 G2（发生群），B 群流水可见
        rows = await t.dao.get_transactions(qq="u1", group_id="200000")
        assert any(r2["reason"] == "redeem_cost" for r2 in rows)
    return "跨群共享：签到/加分/兑换余额一致，流水按发生群记录"


async def test_global_once_daily_signin():
    """每天全局限签 1 次：同用户跨群同日签到被拒，连签跨群延续。"""
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        s = await _stack(t)
        r = await s["sign_in"].sign_in("u1", "100000", "aiocqhttp", "签到")
        assert not r["already_signed"]
        # 同日另一群被拒
        r2 = await s["sign_in"].sign_in("u1", "200000", "aiocqhttp", "签到")
        assert r2["already_signed"], r2
        assert await t.count("sign_in_log") == 1
        # 次日：在 G2 签到，连签延续（第2天连签奖励生效）
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str
        from datetime import datetime, timedelta

        yesterday = (
            datetime.strptime(today_str(), "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        await t.db.execute(
            "UPDATE accounts SET last_sign_date=? WHERE qq='u1'", (yesterday,)
        )
        await t.db.execute("DELETE FROM sign_in_log")
        r3 = await s["sign_in"].sign_in("u1", "200000", "aiocqhttp", "签到")
        assert not r3["already_signed"]
        assert r3["consecutive"] == 2, r3
        assert "连签奖励(第2天): +5" in r3["msg"], r3["msg"]
        acct = await t.dao.get_account("u1")
        assert acct["consecutive_days"] == 2
    return "全局限签：跨群同日拒绝、次日连签跨群延续（第2天起奖励）"


async def test_negative_title_cross_group_clear():
    """负分回正后，所有群的负分头衔清除并恢复原名片（跨群联动）。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        ps = PointService(t.db, t.dao)
        await t.db.execute("INSERT INTO accounts (qq, points) VALUES ('1001',-10)")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('1001','100000')")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('1001','200000')")
        bot = FakeBot(member_card="原名片1")
        await ps.ensure_negative_title("1001", "100000", bot=bot)
        # G2 尚无头衔；负分用户不能抽奖/兑换（余额 -10 < 成本 10，拦截）
        assert await ps.is_negative("1001")
        s = await _stack(t)
        r = await s["lottery"].draw("1001", "100000")
        assert not r["success"] and ("积分不足" in r["msg"] or "积分为负" in r["msg"]), r
        # 回正：所有群头衔清除
        await ps.add("1001", "200000", 20, "admin_add", admin_override=True, bot=bot)
        assert (await t.dao.get_user("1001", "100000"))["negative_title_id"] is None
        assert (await t.dao.get_user("1001", "200000"))["negative_title_id"] is None
        cards = [c for a, c in bot.calls if a == "set_group_card"]
        assert cards, bot.calls
        assert cards[0]["card"] == "群女仆1号"  # 负分时设置头衔
        assert cards[-1]["card"] == "原名片1"  # 回正后恢复原名片
    return "负分联动：跨群限制、回正全群清除头衔并恢复名片"


async def test_ranking_display_nickname():
    """排行展示群昵称（card 优先 nickname 其次），失败回退 QQ。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.handlers.ranking import (
            RankingHandler,
        )
        from astrbot_plugin_point_system_by_whleague.services.ranking_service import (
            RankingService,
        )

        handler = RankingHandler(
            types.SimpleNamespace(ranking_service=RankingService(t.dao))
        )
        for i in range(3):
            await t.db.execute(
                "INSERT INTO accounts (qq, points) VALUES (?,?)",
                (f"100{i}", 30 - i * 10),
            )
            await t.db.execute(
                "INSERT INTO users (qq, group_id) VALUES (?,?)", (f"100{i}", "100000")
            )
        # FakeBot 返回 card → 显示昵称
        ev = FakeEvent("1000", "100000", msg="/排行", bot=FakeBot(member_card="群名片A"))
        msgs = await collect(handler.handle(ev))
        text = "\n".join(msgs)
        assert "本群排行" in text and "群名片A" in text and "1000" not in text, text
        # 无 card 时回退 nickname
        ev2 = FakeEvent(
            "1000",
            "100000",
            msg="/排行",
            bot=types.SimpleNamespace(call_action=_member_info_no_card),
        )
        msgs = await collect(handler.handle(ev2))
        text = "\n".join(msgs)
        assert "昵称回退" in text, text
        # 恶意 card 含控制字符：\n 被剥离，无法构造多行伪造消息（v0.2.2）
        ev_evil = FakeEvent(
            "1000",
            "100000",
            msg="/排行",
            bot=FakeBot(member_card="群名片\n⚠️ 伪造公告\x00"),
        )
        msgs = await collect(handler.handle(ev_evil))
        text = "\n".join(msgs)
        assert "\r" not in text and "\x00" not in text, text
        # 控制字符剥离后昵称文字连续（无伪造换行），正常行分隔仅来自排行格式
        assert "群名片⚠️ 伪造公告" in text, text
        # bot 异常 → 回退 QQ
        ev3 = FakeEvent(
            "1000", "100000", msg="/排行", bot=types.SimpleNamespace(call_action=_boom)
        )
        msgs = await collect(handler.handle(ev3))
        text = "\n".join(msgs)
        assert "1000" in text, text
    return "排行昵称：card 优先/昵称回退/恶意昵称剥离/异常回退 QQ"


async def _member_info_no_card(action, **kwargs):
    return {"nickname": "昵称回退", "user_id": kwargs.get("user_id")}


async def _boom(action, **kwargs):
    raise RuntimeError("api down")


async def test_group_clear_semantics():
    """群清空：本群成员共享积分归零、成员关系保留、其他群余额同步归零。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.handlers.admin import AdminHandler
        from astrbot_plugin_point_system_by_whleague.services.backup_service import (
            BackupService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        plugin = types.SimpleNamespace(
            db=t.db,
            dao=t.dao,
            backup_service=BackupService(t.db, {"backup_dirs": []}),
            point_service=PointService(t.db, t.dao),
        )
        handler = AdminHandler(plugin)
        # u1 同时在 G1/G2，余额 50
        await t.db.execute("INSERT INTO accounts (qq, points) VALUES ('u1',50)")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('u1','100000')")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('u1','200000')")
        await t.db.execute("INSERT INTO accounts (qq, points) VALUES ('u2',10)")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('u2','100000')")
        ev = FakeEvent("admin", "100000", is_admin=True)
        await collect(handler.clear_data(ev, "group"))
        token = handler._pending_clears["admin"]["token"]
        ev2 = FakeEvent("admin", "100000", is_admin=True, msg=f"/确认清空 {token}")
        msgs = await collect(handler.confirm_clear(ev2))
        assert any("已清空本群数据" in m for m in msgs), msgs
        # 成员关系保留；本群成员积分归零（跨群共享余额同步归零）
        assert await t.count("users") == 3
        assert (await t.dao.get_account("u1"))["points"] == 0
        assert (await t.dao.get_account("u2"))["points"] == 0
    return "群清空：共享积分清零、成员关系保留"


async def test_admin_subtract_negative_new_user():
    """管理扣分可扣成负数；无玩家信息时自动建行（回归）。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.handlers.admin import AdminHandler
        from astrbot_plugin_point_system_by_whleague.services.backup_service import (
            BackupService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        handler = AdminHandler(
            types.SimpleNamespace(
                db=t.db,
                dao=t.dao,
                backup_service=BackupService(t.db, {"backup_dirs": []}),
                config_cache=base_cfg(),
                config=None,
                point_service=PointService(t.db, t.dao),
                redeem_service=types.SimpleNamespace(),
                daily_keyword_service=types.SimpleNamespace(
                    invalidate=lambda g: None
                ),
            )
        )
        ev = FakeEvent("admin", "100000", is_admin=True, msg="/扣分 @10086 8")
        msgs = await collect(handler.adjust_points(ev, "扣分"))
        assert any("扣 8" in m for m in msgs), msgs
        acct = await t.dao.get_account("10086")
        assert acct["points"] == -8
        assert (await t.dao.get_user("10086", "100000"))["negative_title_id"] == 1
        # 建行后再加分：余额与流水一致
        ev2 = FakeEvent("admin", "100000", is_admin=True, msg="/加分 @10086 3")
        await collect(handler.adjust_points(ev2, "加分"))
        assert (await t.dao.get_account("10086"))["points"] == -5
    return "管理扣分：可扣成负数、无玩家自动建行+头衔、后续加分一致"


async def test_concurrent_cross_group_signin_once():
    """并发跨群签到同一用户：全局限签 1 次成功（全局去重）。"""
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        s = await _stack(t)
        results = await asyncio.gather(
            *[
                s["sign_in"].sign_in("u1", gid, "aiocqhttp", "签到")
                for gid in ("100000", "200000", "300000")
                for _ in range(20)
            ]
        )
        ok = [r for r in results if not r["already_signed"]]
        assert len(ok) == 1, len(ok)
        assert await t.count("sign_in_log") == 1
    return "并发跨群签到：仅 1 次成功（唯一索引 + 事务查重）"


async def test_daily_keyword_global_once():
    """口令全局限领 1 次：A 群领取后 B 群（即使口令不同）拒绝。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.daily_keyword_service import (
            DailyKeywordService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        svc = DailyKeywordService(t.db, t.dao, PointService(t.db, t.dao))
        await t.dao.set_daily_keyword("100000", "红包", 10, "admin")
        await t.dao.set_daily_keyword("200000", "口令", 20, "admin")
        r = await svc.check_and_claim("u1", "100000", "抢到红包")
        assert r.get("claimed") and r["points"] == 10
        # B 群口令不同，同用户当天仍拒绝（全局限领 1 次）
        r2 = await svc.check_and_claim("u1", "200000", "今日口令")
        assert not r2.get("claimed") and r2.get("already"), r2
        # 其他用户不受影响
        r3 = await svc.check_and_claim("u2", "200000", "今日口令")
        assert r3.get("claimed") and r3["points"] == 20
        assert await t.count("daily_keyword_claim") == 2
    return "口令全局限领：跨群（不同口令）同日拒绝、他人不受影响"


async def test_lottery_daily_limit_global():
    """抽奖每日限次按 QQ 全局：两群各抽部分，总计不超过限额。"""
    async with TempDB() as t:
        cfg = base_cfg(
            lottery_enabled=True,
            lottery_cost=1,
            lottery_daily_limit=3,
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
        from astrbot_plugin_point_system_by_whleague.services.lottery_service import (
            LotteryService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        svc = LotteryService(t.db, t.dao, PointService(t.db, t.dao), cfg)
        await t.db.execute("INSERT INTO accounts (qq, points) VALUES ('u1',100)")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('u1','100000')")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('u1','200000')")
        ok = 0
        for gid in ("100000", "200000", "100000"):
            r = await svc.draw("u1", gid)
            assert r["success"], r
            ok += 1
        # 第 4 次（另一个群）超全局限额
        r = await svc.draw("u1", "200000")
        assert not r["success"] and "上限" in r["msg"], r
        assert ok == 3
        assert await t.count("lottery_record") == 3
    return "抽奖全局限次：跨群累计、第 4 次超限"


TESTS = [
    ("shared_balance", test_shared_balance_across_groups),
    ("global_once_daily", test_global_once_daily_signin),
    ("negative_title_cross_group", test_negative_title_cross_group_clear),
    ("ranking_nickname", test_ranking_display_nickname),
    ("group_clear_semantics", test_group_clear_semantics),
    ("admin_subtract_negative", test_admin_subtract_negative_new_user),
    ("concurrent_cross_group", test_concurrent_cross_group_signin_once),
    ("daily_keyword_global_once", test_daily_keyword_global_once),
    ("lottery_limit_global", test_lottery_daily_limit_global),
]
