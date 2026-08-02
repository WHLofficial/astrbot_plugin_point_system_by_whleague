"""S10 用户侧 Handler 层 + /流水 + main 路由（wake 跳过、cmd_redeem 路由、cron 注册）。"""

import types
from unittest import mock

from .common import FakeContext, FakeEvent, TempDB, base_cfg, collect


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
        signin_first_bonus=0,
        signin_day_first_bonus=0,
        signin_consecutive_max=30,
        signin_consecutive_bonus_per_day=0,
        signin_weekly_bonus=0,
        birthday_bonus_points=0,
    )
    return SignInService(
        t.db,
        t.dao,
        PointService(t.db, t.dao),
        EasterService(t.dao),
        DateRewardService(t.dao),
        cfg,
    )


async def test_signin_handler_basic():
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        from astrbot_plugin_point_system_by_whleague.handlers.sign_in import (
            SignInHandler,
        )

        handler = SignInHandler(
            types.SimpleNamespace(sign_in_service=await _signin_svc(t))
        )
        # 非群聊拒绝
        ev = FakeEvent("u1", None, msg="签到")
        msgs = await collect(handler.handle(ev))
        assert any("仅支持群聊" in m for m in msgs)
        # 正常签到 + 已签
        ev = FakeEvent("u1", "G1", msg="签到")
        msgs = await collect(handler.handle(ev))
        assert any("签到成功" in m for m in msgs)
        ev = FakeEvent("u1", "G1", msg="签到")
        msgs = await collect(handler.handle(ev))
        assert any("今天已经签到" in m for m in msgs)
    return "签到 handler：非群拒绝/成功/去重提示"


async def test_lottery_handler_paths():
    async with TempDB() as t:
        import json

        from astrbot_plugin_point_system_by_whleague.handlers.lottery import (
            LotteryHandler,
        )
        from astrbot_plugin_point_system_by_whleague.services.lottery_service import (
            LotteryService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

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
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',1000)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('u1','G1')"
        )
        handler = LotteryHandler(
            types.SimpleNamespace(
                config_cache=cfg,
                lottery_service=LotteryService(
                    t.db, t.dao, PointService(t.db, t.dao), cfg
                ),
            )
        )
        # 非群聊
        msgs = await collect(handler.handle(FakeEvent("u1", None, msg="whl 抽奖")))
        assert any("仅支持群聊" in m for m in msgs)
        # 非抽奖消息：无输出
        msgs = await collect(handler.handle(FakeEvent("u1", "G1", msg="今天天气不错")))
        assert msgs == []
        # 无口令的消息：无输出
        msgs = await collect(handler.handle(FakeEvent("u1", "G1", msg="我想抽奖")))
        assert msgs == []
        # 口令+关键词触发
        msgs = await collect(handler.handle(FakeEvent("u1", "G1", msg="whl 抽奖")))
        assert any("参与奖" in m for m in msgs)
    return "抽奖 handler：非群/非抽奖/无口令/正常触发"


async def test_ranking_handler_display():
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
        # 空榜
        msgs = await collect(handler.handle(FakeEvent("u1", "G1", msg="/排行")))
        assert any("暂无排行数据" in m for m in msgs)
        # 3 人本群榜（无 bot 时昵称回退 QQ）
        for i in range(3):
            await t.db.execute(
                "INSERT INTO accounts (qq, points) VALUES (?,?)",
                (f"u{i}", 30 - i * 10),
            )
            await t.db.execute(
                "INSERT INTO users (qq, group_id) VALUES (?,?)", (f"u{i}", "G1")
            )
        msgs = await collect(handler.handle(FakeEvent("u1", "G1", msg="/排行")))
        text = "\n".join(msgs)
        assert "本群排行" in text and "u0" in text and "30 积分" in text
        # 少于 3 人回退全局
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('g1',5)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('g1','G2')"
        )
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('g2',4)"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('g2','G2')"
        )
        msgs = await collect(handler.handle(FakeEvent("u1", "G2", msg="/排行")))
        text = "\n".join(msgs)
        assert "全局排行" in text and "群G1" in text
        # 负分/0 分用户不参与排行
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('neg',-5)"
        )
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('zero',0)"
        )
        msgs = await collect(handler.handle(FakeEvent("u1", "G1", msg="/排行")))
        text = "\n".join(msgs)
        assert "neg" not in text and "zero" not in text
    return "排行 handler：空榜/本群/回退全局/排除非正分"


async def test_stats_handler_full():
    async with TempDB() as t:
        await t.db.execute("UPDATE easter_events SET is_active=0")
        from astrbot_plugin_point_system_by_whleague.handlers.ranking import (
            RankingHandler,
        )

        plugin = types.SimpleNamespace(sign_in_service=await _signin_svc(t))
        handler = RankingHandler(plugin)
        # 空群统计
        msgs = await collect(handler.stats(FakeEvent("u1", "G1", msg="/签到统计")))
        text = "\n".join(msgs)
        assert "签到率: 0%" in text and "今日首签" not in text
        # 有签到数据
        svc = plugin.sign_in_service
        await svc.sign_in("u1", "G1", "aiocqhttp", "签到")
        await svc.sign_in("u2", "G1", "aiocqhttp", "签到")
        await t.db.execute(
            "UPDATE sign_in_log SET created_at='2026-01-01 00:00:00' WHERE qq='u1'"
        )
        msgs = await collect(handler.stats(FakeEvent("u1", "G1", msg="/签到统计")))
        text = "\n".join(msgs)
        assert "总注册用户: 2" in text and "已签到: 2" in text
        assert "今日首签: u1" in text and "连签王" in text
    return "统计 handler：空群/签到率/首签/连签王"


async def test_redeem_handler_items():
    async with TempDB() as t:
        from datetime import datetime, timedelta

        from astrbot_plugin_point_system_by_whleague.handlers.redeem import (
            RedeemHandler,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )
        from astrbot_plugin_point_system_by_whleague.services.redeem_service import (
            RedeemService,
        )

        ps = PointService(t.db, t.dao)
        handler = RedeemHandler(
            types.SimpleNamespace(
                dao=t.dao,
                redeem_service=RedeemService(t.db, t.dao, ps),
            )
        )
        # 空列表
        msgs = await collect(handler.list_items(FakeEvent("u1", "G1")))
        assert any("没有可兑换的物品" in m for m in msgs)
        # 有物品 + 折扣 + 无限库存展示
        item_id = await t.dao.add_item("限量商品", 100, -1)
        end = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        await t.dao.update_item_field(item_id, "discount_price", 50)
        await t.dao.update_item_field(item_id, "discount_end_time", end)
        msgs = await collect(handler.list_items(FakeEvent("u1", "G1")))
        text = "\n".join(msgs)
        assert "限量商品" in text and "限时折扣" in text and "∞" in text
        # 兑换参数错误
        msgs = await collect(handler.do_redeem(FakeEvent("u1", "G1"), "0"))
        assert any("below minimum" in m for m in msgs)
        # 数量超上限 999 被拒
        msgs = await collect(handler.do_redeem(FakeEvent("u1", "G1"), "1", "1000"))
        assert any("exceeds maximum" in m for m in msgs)
        # 物品不存在
        msgs = await collect(handler.do_redeem(FakeEvent("u1", "G1"), "999"))
        assert any("物品不存在或已下架" in m for m in msgs)
        # 非群聊
        msgs = await collect(handler.do_redeem(FakeEvent("u1", None), "1"))
        assert any("仅支持群聊" in m for m in msgs)
    return "兑换 handler：列表/折扣/∞库存/参数错误/数量上限/不存在"


async def test_redeem_handler_records():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.handlers.redeem import (
            RedeemHandler,
        )

        handler = RedeemHandler(types.SimpleNamespace(dao=t.dao))
        item_id = await t.dao.add_item("商品", 10, 5)
        await t.db.execute(
            "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost, quantity) "
            "VALUES ('R20260101-0001','u1','G1',?,'物品1',10,1),('R20260101-0002','u2','G2',?,'物品2',20,1)",
            (item_id, item_id),
        )
        # 查看自己的记录详情
        msgs = await collect(
            handler.list_records(FakeEvent("u1", "G1"), "R20260101-0001", "1")
        )
        text = "\n".join(msgs)
        assert "R20260101-0001" in text and "物品1" in text and "未核销" in text
        # 记录不存在
        msgs = await collect(
            handler.list_records(FakeEvent("u1", "G1"), "R99999999-0001", "1")
        )
        assert any("不存在" in m for m in msgs)
        # 自己的列表（普通用户）
        msgs = await collect(handler.list_records(FakeEvent("u1", "G1"), None, "1"))
        assert any("R20260101-0001" in m for m in msgs)
        # 普通成员查 all/pending：不越权，仅落回自己的列表（无他人记录）
        msgs = await collect(handler.list_records(FakeEvent("u1", "G1"), "all", "1"))
        text = "\n".join(msgs)
        assert "R20260101-0001" in text and "R20260101-0002" not in text, text
        msgs = await collect(
            handler.list_records(FakeEvent("u1", "G1"), "pending", "1")
        )
        assert any("R20260101-0001" in m for m in msgs)
        assert not any("R20260101-0002" in m for m in msgs)
        # 核销：普通成员拒绝
        msgs = await collect(
            handler.toggle_verify(FakeEvent("u1", "G1"), "R20260101-0001", "")
        )
        assert any("没有权限" in m for m in msgs)
        # 核销：群管理成功 + 备注落库
        await t.dao.add_admin("admin", "owner", "G1")
        ev = FakeEvent("admin", "G1", is_admin=False, msg="/核销 R20260101-0001 已发货")
        msgs = await collect(handler.toggle_verify(ev, "R20260101-0001", "已发货"))
        assert any("已核销" in m for m in msgs)
        rec = await t.dao.get_redeem_record("R20260101-0001")
        assert rec["status"] == "verified" and rec["admin_note"] == "已发货"
        assert rec["verified_by"] == "admin"
        # 跨群核销拒绝
        msgs = await collect(handler.toggle_verify(ev, "R20260101-0002", ""))
        assert any("无权核销其他群" in m for m in msgs)
    return "兑换记录：详情/不存在/成员拒绝/群管核销+备注/跨群拒绝"


async def test_transactions_command():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin

        obj = PointSystemPlugin.__new__(PointSystemPlugin)
        obj.dao = t.dao
        for i in range(12):
            await t.db.execute(
                "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) "
                "VALUES ('u1','G1',?,?, '签到')",
                (i, i),
            )
        await t.db.execute(
            "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) VALUES ('u2','G1',5,5,'签到')"
        )
        # 自己看自己
        msgs = await collect(obj.cmd_transactions(FakeEvent("u1", "G1", msg="/流水")))
        assert len(msgs) == 1 and "积分流水" in msgs[0] and "第1页" in msgs[0]
        # 翻页
        msgs = await collect(obj.cmd_transactions(FakeEvent("u1", "G1", msg="/流水 2")))
        assert len(msgs) == 1 and "第2页" in msgs[0]
        # 成员查 all 被拒
        msgs = await collect(
            obj.cmd_transactions(FakeEvent("u1", "G1", msg="/流水 all"))
        )
        assert any("没有权限" in m for m in msgs)
        # 中文别名 全部 → all（成员仍被拒）
        msgs = await collect(
            obj.cmd_transactions(FakeEvent("u1", "G1", msg="/流水 全部"))
        )
        assert any("没有权限" in m for m in msgs)
        # 成员查他人被拒
        msgs = await collect(
            obj.cmd_transactions(FakeEvent("u1", "G1", msg="/流水 @10002"))
        )
        assert any("没有权限" in m for m in msgs)
        # 全局管理员查 all
        msgs = await collect(
            obj.cmd_transactions(
                FakeEvent("root", "G1", is_admin=True, msg="/流水 all")
            )
        )
        assert len(msgs) == 1 and "第1页" in msgs[0]
        # 全局管理员查 全部（中文别名）
        msgs = await collect(
            obj.cmd_transactions(
                FakeEvent("root", "G1", is_admin=True, msg="/流水 全部 2")
            )
        )
        assert len(msgs) == 1 and "第2页" in msgs[0]
        # 参数错误
        msgs = await collect(
            obj.cmd_transactions(FakeEvent("u1", "G1", msg="/流水 abc"))
        )
        assert any("参数错误" in m for m in msgs)
        # 私聊（group_id=None）：仅查自己、不越权
        msgs = await collect(obj.cmd_transactions(FakeEvent("u1", None, msg="/流水")))
        assert len(msgs) == 1 and "积分流水" in msgs[0], msgs
        assert not any("u2" in m for m in msgs)
    return "/流水：自己/翻页/all|全部 权限/他人权限/参数错误/私聊"


async def test_main_routes():
    async with TempDB():
        from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin

        class _SignInHandler:
            def __init__(self):
                self.calls = []

            async def handle(self, event):
                self.calls.append(event)
                yield event.plain_result("签到了")

        class _RedeemHandler:
            def __init__(self):
                self.calls = []

            async def list_items(self, event):
                self.calls.append("list")
                yield event.plain_result("列表")

            async def do_redeem(self, event, item_id, quantity="1"):
                self.calls.append(("redeem", item_id, quantity))
                yield event.plain_result(f"兑换 {item_id} x{quantity}")

            async def list_records(self, event, target, page="1"):
                self.calls.append(("records", target, page))
                yield event.plain_result("记录")

        obj = PointSystemPlugin.__new__(PointSystemPlugin)
        obj.config_cache = base_cfg()
        # on_sign_in：非触发消息（含"流水"）跳过签到
        obj.sign_in_handler = _SignInHandler()
        msgs = await collect(
            obj.on_sign_in(FakeEvent("u1", "G1", msg="流水 1", at_wake=True))
        )
        assert msgs == [] and obj.sign_in_handler.calls == []
        # 带附加文本的签到消息不触发（严格匹配）
        msgs = await collect(
            obj.on_sign_in(FakeEvent("u1", "G1", msg="我想签到", at_wake=True))
        )
        assert msgs == [] and obj.sign_in_handler.calls == []
        # 非唤醒命令：严格触发才走 handler
        msgs = await collect(obj.on_sign_in(FakeEvent("u1", "G1", msg="签到")))
        assert msgs == ["签到了"] and len(obj.sign_in_handler.calls) == 1
        # cmd_redeem 参数路由
        obj.redeem_handler = _RedeemHandler()
        await collect(obj.cmd_redeem(FakeEvent("u1", "G1", msg="/兑换")))
        await collect(obj.cmd_redeem(FakeEvent("u1", "G1", msg="/兑换 1")))
        await collect(obj.cmd_redeem(FakeEvent("u1", "G1", msg="/兑换 1 2")))
        assert obj.redeem_handler.calls == [
            "list",
            ("redeem", "1", "1"),
            ("redeem", "1", "2"),
        ]
        # cmd_redeem_records：纯数字视为页码，all/pending/R 前缀保持原语义，中文别名归一化
        await collect(obj.cmd_redeem_records(FakeEvent("u1", "G1", msg="/兑换记录 2")))
        await collect(obj.cmd_redeem_records(FakeEvent("u1", "G1", msg="/兑换记录 all 3")))
        await collect(obj.cmd_redeem_records(FakeEvent("u1", "G1", msg="/兑换记录 全部")))
        await collect(
            obj.cmd_redeem_records(FakeEvent("u1", "G1", msg="/兑换记录 未核销 2"))
        )
        await collect(
            obj.cmd_redeem_records(FakeEvent("u1", "G1", msg="/兑换记录 R20260101-0001"))
        )
        assert obj.redeem_handler.calls[-5:] == [
            ("records", None, "2"),
            ("records", "all", "3"),
            ("records", "all", "1"),
            ("records", "pending", "2"),
            ("records", "R20260101-0001", "1"),
        ]
        # _start_cron_jobs：备份+生日两个任务
        obj.config_cache = base_cfg(
            backup_enabled=True, backup_time="04:00", birthday_announce_time="08:00"
        )
        obj.context = FakeContext()
        await obj._start_cron_jobs()
        assert {j["name"] for j in obj.context.cron_jobs} == {
            "points_backup",
            "birthday_announce",
        }
        # 停用备份后仅生日任务
        obj2 = PointSystemPlugin.__new__(PointSystemPlugin)
        obj2.config_cache = base_cfg(
            backup_enabled=False, backup_time="04:00", birthday_announce_time="08:00"
        )
        obj2.context = FakeContext()
        await obj2._start_cron_jobs()
        assert {j["name"] for j in obj2.context.cron_jobs} == {"birthday_announce"}
        # 非法备份时间回退默认
        obj3 = PointSystemPlugin.__new__(PointSystemPlugin)
        obj3.config_cache = base_cfg(
            backup_enabled=True, backup_time="99:99", birthday_announce_time="08:00"
        )
        obj3.context = FakeContext()
        await obj3._start_cron_jobs()
        assert {j["name"] for j in obj3.context.cron_jobs} == {
            "points_backup",
            "birthday_announce",
        }
        # 非法播报时间同样回退默认（不再中断整个 cron 注册）
        obj4 = PointSystemPlugin.__new__(PointSystemPlugin)
        obj4.config_cache = base_cfg(
            backup_enabled=True, backup_time="04:00", birthday_announce_time="99:99"
        )
        obj4.context = FakeContext()
        await obj4._start_cron_jobs()
        assert {j["name"] for j in obj4.context.cron_jobs} == {
            "points_backup",
            "birthday_announce",
        }
        # reschedule：先移除旧任务再重建
        obj5 = PointSystemPlugin.__new__(PointSystemPlugin)
        obj5.config_cache = base_cfg(
            backup_enabled=True, backup_time="04:00", birthday_announce_time="08:00"
        )
        obj5.context = FakeContext()
        await obj5._start_cron_jobs()
        obj5._backup_job = types.SimpleNamespace(
            name="points_backup", remove=lambda: obj5.context.cron_jobs.clear()
        )
        obj5._birthday_job = types.SimpleNamespace(
            name="birthday_announce", remove=lambda: None
        )
        await obj5.reschedule_cron_jobs()
        assert {j["name"] for j in obj5.context.cron_jobs} == {
            "points_backup",
            "birthday_announce",
        }
        # on_lottery / on_ranking 处理结束后 stop_event
        class _StopRecorder(FakeEvent):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.stopped = 0

            def stop_event(self):
                self.stopped += 1

        class _SimpleHandler:
            async def handle(self, event):
                yield event.plain_result("ok")

        obj.lottery_handler = _SimpleHandler()
        ev = _StopRecorder("u1", "G1", msg="抽奖")
        msgs = await collect(obj.on_lottery(ev))
        assert msgs == ["ok"]
        assert ev.stopped == 1
        obj.ranking_handler = _SimpleHandler()
        ev2 = _StopRecorder("u1", "G1", msg="排行")
        await collect(obj.on_ranking(ev2))
        assert ev2.stopped == 1
        # cmd_verify：无参数 → 用法提示；有参数 → 委托 toggle_verify
        class _VerifyRecorder:
            def __init__(self):
                self.calls = []

            async def toggle_verify(self, event, record_no, note=""):
                self.calls.append((record_no, note))
                yield event.plain_result(f"核销 {record_no} {note}")

        obj.redeem_handler = _VerifyRecorder()
        msgs = await collect(obj.cmd_verify(FakeEvent("u1", "G1", msg="/核销")))
        assert any("用法" in m for m in msgs)
        assert obj.redeem_handler.calls == []
        msgs = await collect(
            obj.cmd_verify(FakeEvent("u1", "G1", msg="/核销 R20260101-0001 已发货"))
        )
        assert msgs == ["核销 R20260101-0001 已发货"]
        assert obj.redeem_handler.calls == [("R20260101-0001", "已发货")]
        # on_group_message 委托 active_reward_handler
        class _ActiveRecorder:
            def __init__(self):
                self.calls = []

            async def handle(self, event):
                self.calls.append(event)
                return None

        obj.active_reward_handler = _ActiveRecorder()
        ev3 = FakeEvent("u1", "G1", msg="普通群消息")
        await obj.on_group_message(ev3)
        assert obj.active_reward_handler.calls == [ev3]
        # _cron_backup 委托 backup_service.run_backup
        class _BackupRecorder:
            def __init__(self):
                self.calls = 0

            async def run_backup(self):
                self.calls += 1

        obj.backup_service = _BackupRecorder()
        await obj._cron_backup()
        assert obj.backup_service.calls == 1
        # terminate：移除 cron 任务 + 取消 sweep 任务 + 关闭 db
        import asyncio as _asyncio

        class _SweepTask:
            def __init__(self):
                self.cancelled = 0

            def cancel(self):
                self.cancelled += 1

            def __await__(self):
                return _asyncio.sleep(0).__await__()

        removed = []
        sweep = _SweepTask()
        obj._cache_sweep_task = sweep
        obj._backup_job = types.SimpleNamespace(
            name="points_backup", remove=lambda: removed.append("backup")
        )
        obj._birthday_job = types.SimpleNamespace(
            name="birthday_announce", remove=lambda: removed.append("birthday")
        )
        obj.db = types.SimpleNamespace(close=mock.AsyncMock())
        await obj.terminate()
        assert sweep.cancelled == 1, sweep.cancelled
        assert set(removed) == {"backup", "birthday"}, removed
        assert obj.db.close.called
    return "main 路由：wake 跳过/兑换参数路由/兑换记录页码/cron 注册与重建/stop_event/核销委托/群消息委托/备份回调/terminate"


TESTS = [
    ("signin_handler", test_signin_handler_basic),
    ("lottery_handler", test_lottery_handler_paths),
    ("ranking_handler", test_ranking_handler_display),
    ("stats_handler", test_stats_handler_full),
    ("redeem_items", test_redeem_handler_items),
    ("redeem_records", test_redeem_handler_records),
    ("transactions_cmd", test_transactions_command),
    ("main_routes", test_main_routes),
]
