"""S10 用户侧 Handler 层 + /流水 + main 路由（wake 跳过、cmd_redeem 路由、cron 注册）。"""

import types
from unittest import mock

from .common import FakeBot, FakeContext, FakeEvent, TempDB, base_cfg, collect


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
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )
        from astrbot_plugin_point_system_by_whleague.services.redeem_service import (
            RedeemService,
        )

        ps = PointService(t.db, t.dao)
        plugin = types.SimpleNamespace(
            dao=t.dao,
            point_service=ps,
            redeem_service=RedeemService(t.db, t.dao, ps),
            config_cache=base_cfg(),
            context=FakeContext(),
        )
        handler = RedeemHandler(plugin)
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
        assert "兑换者: u1" in text, text
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
            handler.verify_record(FakeEvent("u1", "G1"), "R20260101-0001", "verified", "")
        )
        assert any("没有权限" in m for m in msgs)
        # 核销：群管理成功 + 备注落库 + @ 通知兑换者
        await t.dao.add_admin("admin", "owner", "G1")
        # 管理员查看 all/pending：行尾展示兑换者（无 bot 回退 QQ，本群群管仅本群记录）
        ev_admin = FakeEvent("admin", "G1", is_admin=False, msg="/兑换记录 all")
        msgs = await collect(handler.list_records(ev_admin, "all", "1"))
        assert any("R20260101-0001 物品1x1 10积分 u1" in m for m in msgs), msgs
        assert not any("R20260101-0002" in m for m in msgs), msgs
        msgs = await collect(handler.list_records(ev_admin, "pending", "1"))
        assert any("R20260101-0001 物品1x1 10积分 u1" in m for m in msgs), msgs
        # 带 bot：显示群名片昵称 + QQ；详情视图带标签（昵称路径要求数字 QQ/群，走全局管理员跨群视图）
        await t.db.execute(
            "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost, quantity) "
            "VALUES ('R20260101-0007','10001','888',?,'物品7',30,1)",
            (item_id,),
        )
        ev_root_bot = FakeEvent(
            "root", "G1", is_admin=True, bot=FakeBot(member_card="小红")
        )
        msgs = await collect(handler.list_records(ev_root_bot, "all", "1"))
        assert any("R20260101-0007 物品7x1 30积分 小红(10001)" in m for m in msgs), msgs
        msgs = await collect(handler.list_records(ev_root_bot, "R20260101-0007", "1"))
        assert any("兑换者: 小红(10001)" in m for m in msgs), msgs
        ev = FakeEvent("admin", "G1", is_admin=False, msg="/核销 R20260101-0001 已发货")
        msgs = await collect(
            handler.verify_record(ev, "R20260101-0001", "verified", "已发货")
        )
        assert any("已核销" in m for m in msgs)
        rec = await t.dao.get_redeem_record("R20260101-0001")
        assert rec["status"] == "verified" and rec["admin_note"] == "已发货"
        assert rec["verified_by"] == "admin"
        assert ev.sent and "R20260101-0001" in str(ev.sent[0])
        assert "已通过核销" in str(ev.sent[0])
        assert "（备注：已发货）" in str(ev.sent[0])
        # 跨群核销拒绝（本群群管无权处理其他群记录）
        msgs = await collect(
            handler.verify_record(ev, "R20260101-0002", "verified", "")
        )
        assert any("无权处理其他群" in m for m in msgs)
        # 全局管理员跨群核销：通知发往原兑换群 G2
        ev_root = FakeEvent("root", "G1", is_admin=True, msg="/核销 R20260101-0002")
        msgs = await collect(
            handler.verify_record(ev_root, "R20260101-0002", "verified", "")
        )
        assert any("已核销" in m for m in msgs)
        assert plugin.context.sent, "跨群通知应通过 context 发送"
        origin, chain = plugin.context.sent[-1]
        assert origin == "bot1:GroupMessage:G2", origin
        assert "已通过核销" in str(chain)
        rec = await t.dao.get_redeem_record("R20260101-0002")
        assert rec["status"] == "verified" and rec["verified_by"] == "root"
        # 私信渠道：同群核销 → context 发送私信，纯文本无 @
        await t.db.execute(
            "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost, quantity) "
            "VALUES ('R20260101-0003','u1','G1',?,'物品3',10,1)",
            (item_id,),
        )
        plugin.config_cache["redeem_notify_channel"] = "private"
        ev_pri = FakeEvent("admin", "G1", is_admin=False, msg="/核销 R20260101-0003")
        msgs = await collect(
            handler.verify_record(ev_pri, "R20260101-0003", "verified", "")
        )
        assert any("已核销" in m for m in msgs)
        assert ev_pri.sent == [], ev_pri.sent  # 私信渠道不走当前会话
        origin, chain = plugin.context.sent[-1]
        assert origin == "bot1:FriendMessage:u1", origin
        assert "已通过核销" in str(chain) and "u1" not in str(chain)
        plugin.config_cache["redeem_notify_channel"] = "group"
        # 机器人名称缺失时回退 unified_msg_origin 首段（get_platform_id 不可用场景）
        ev_fb = FakeEvent("root", "G1", is_admin=True, platform_id="botx")
        ev_fb.get_platform_id = None
        await t.db.execute(
            "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost, quantity) "
            "VALUES ('R20260101-0006','u5','G2',?,'物品6',10,1)",
            (item_id,),
        )
        msgs = await collect(
            handler.verify_record(ev_fb, "R20260101-0006", "verified", "")
        )
        assert any("已核销" in m for m in msgs)
        origin, chain = plugin.context.sent[-1]
        assert origin == "botx:GroupMessage:G2", origin
        # 跨群核销但插件无 context：通知失败 → 当前会话警告兜底
        plugin_nc = types.SimpleNamespace(
            dao=t.dao,
            point_service=ps,
            redeem_service=RedeemService(t.db, t.dao, ps),
            config_cache=base_cfg(),
        )
        handler_nc = RedeemHandler(plugin_nc)
        await t.db.execute(
            "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost, quantity) "
            "VALUES ('R20260101-0004','u3','G2',?,'物品4',10,1)",
            (item_id,),
        )
        ev_nc = FakeEvent("root", "G1", is_admin=True, msg="/核销 R20260101-0004")
        msgs = await collect(
            handler_nc.verify_record(ev_nc, "R20260101-0004", "verified", "")
        )
        assert any("已核销" in m for m in msgs)
        assert ev_nc.sent and "通知兑换者失败" in str(ev_nc.sent[-1])
        # 表级全局管理员（admins 表 group_id 为空）：可跨群核销 + 详情视图跨群口径一致
        await t.dao.add_admin("gadmin2", "owner", None)
        await t.db.execute(
            "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost, quantity) "
            "VALUES ('R20260101-0005','u4','G2',?,'物品5',10,1)",
            (item_id,),
        )
        ev_g = FakeEvent("gadmin2", "G1", is_admin=False, msg="/核销 R20260101-0005")
        msgs = await collect(
            handler.verify_record(ev_g, "R20260101-0005", "verified", "")
        )
        assert any("已核销" in m for m in msgs)
        msgs = await collect(
            handler.list_records(FakeEvent("gadmin2", "G1", is_admin=False), "R20260101-0005", "1")
        )
        assert any("R20260101-0005" in m for m in msgs)
        assert not any("其他群" in m for m in msgs), msgs
        # 本群群管查看其他群详情仍被拒（口径一致回归）
        msgs = await collect(
            handler.list_records(FakeEvent("admin", "G1", is_admin=False), "R20260101-0005", "1")
        )
        assert any("其他群" in m for m in msgs)
        # 驳回：退分 + 恢复库存 + 原因入通知括号
        msgs = await collect(
            handler.verify_record(ev, "R20260101-0001", "rejected", "无货")
        )
        assert any("已驳回" in m for m in msgs)
        rec = await t.dao.get_redeem_record("R20260101-0001")
        assert rec["status"] == "rejected" and rec["admin_note"] == "无货"
        assert rec["rejected_by"] == "admin"
        assert "已被驳回" in str(ev.sent[-1]) and "（理由：无货）" in str(ev.sent[-1])
        acct = await t.dao.get_account("u1")
        assert acct["points"] == 10  # 退回消耗的 10 积分
        row = await t.db.fetchone("SELECT stock FROM redeem_items WHERE id=?", (item_id,))
        assert row["stock"] == 6  # 库存恢复
        # 驳回 → 通过：扣回积分 + 扣库存
        msgs = await collect(
            handler.verify_record(ev, "R20260101-0001", "verified", "")
        )
        assert any("已核销" in m for m in msgs)
        acct = await t.dao.get_account("u1")
        assert acct["points"] == 0
        row = await t.db.fetchone("SELECT stock FROM redeem_items WHERE id=?", (item_id,))
        assert row["stock"] == 5
        # 幂等：不重复处理、不通知
        ev2 = FakeEvent("admin", "G1", is_admin=False)
        msgs = await collect(
            handler.verify_record(ev2, "R20260101-0001", "verified", "")
        )
        assert any("已是通过状态" in m for m in msgs)
        assert ev2.sent == []
        # 通知发送失败：发警告信息（首次 send 抛异常，警告 send 成功）
        ev3 = FakeEvent("admin", "G1", is_admin=False)
        ev3.send = mock.AsyncMock(side_effect=[RuntimeError("user left"), None])
        msgs = await collect(
            handler.verify_record(ev3, "R20260101-0001", "rejected", "")
        )
        assert any("已驳回" in m for m in msgs)
        assert ev3.send.called
        assert "通知兑换者失败" in str(ev3.send.call_args_list[-1][0][0])
    return "兑换记录：详情/不存在/成员拒绝/群管核销+备注/驳回/通知/幂等/跨群权限/私信渠道/无context兜底"


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
        await collect(obj.cmd_redeem(FakeEvent("u1", "G1", msg="/兑换商品")))
        await collect(obj.cmd_redeem(FakeEvent("u1", "G1", msg="/兑换商品 1")))
        await collect(obj.cmd_redeem(FakeEvent("u1", "G1", msg="/兑换商品 1 2")))
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
        # reschedule：先移除旧任务再重建（经 delete_job 真正移除）
        obj5 = PointSystemPlugin.__new__(PointSystemPlugin)
        obj5.config_cache = base_cfg(
            backup_enabled=True, backup_time="04:00", birthday_announce_time="08:00"
        )
        obj5.context = FakeContext()
        await obj5._start_cron_jobs()
        old_ids = {j["job_id"] for j in obj5.context.cron_jobs}
        assert len(old_ids) == 2
        await obj5.reschedule_cron_jobs()
        assert set(obj5.context.deleted_jobs) == old_ids, obj5.context.deleted_jobs
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
        # cmd_verify：无参数 → 用法提示；旧格式默认通过；动作词归一化委托 verify_record
        class _VerifyRecorder:
            def __init__(self):
                self.calls = []

            async def verify_record(self, event, record_no, action, note=""):
                self.calls.append((record_no, action, note))
                yield event.plain_result(f"核销 {record_no} {action} {note}")

        obj.redeem_handler = _VerifyRecorder()
        msgs = await collect(obj.cmd_verify(FakeEvent("u1", "G1", msg="/核销")))
        assert any("用法" in m for m in msgs)
        assert obj.redeem_handler.calls == []
        # 旧格式：默认通过
        msgs = await collect(
            obj.cmd_verify(FakeEvent("u1", "G1", msg="/核销 R20260101-0001 已发货"))
        )
        assert msgs == ["核销 R20260101-0001 verified 已发货"]
        assert obj.redeem_handler.calls == [("R20260101-0001", "verified", "已发货")]
        # 显式通过/驳回：中英文与大小写归一
        await collect(
            obj.cmd_verify(FakeEvent("u1", "G1", msg="/核销 通过 R20260101-0001"))
        )
        assert obj.redeem_handler.calls[-1] == ("R20260101-0001", "verified", "")
        await collect(
            obj.cmd_verify(FakeEvent("u1", "G1", msg="/核销 REJECT R20260101-0002 无货"))
        )
        assert obj.redeem_handler.calls[-1] == ("R20260101-0002", "rejected", "无货")
        await collect(
            obj.cmd_verify(FakeEvent("u1", "G1", msg="/核销 pass R20260101-0003"))
        )
        assert obj.redeem_handler.calls[-1] == ("R20260101-0003", "verified", "")
        # 动作词后缺编号：用法提示
        msgs = await collect(obj.cmd_verify(FakeEvent("u1", "G1", msg="/核销 驳回")))
        assert any("用法" in m for m in msgs)
        msgs = await collect(obj.cmd_verify(FakeEvent("u1", "G1", msg="/核销 通过")))
        assert any("用法" in m for m in msgs)
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
        # terminate：经 delete_job 移除 cron 任务 + 取消 sweep 任务 + 关闭 db
        import asyncio as _asyncio

        class _SweepTask:
            def __init__(self):
                self.cancelled = 0

            def cancel(self):
                self.cancelled += 1

            def __await__(self):
                return _asyncio.sleep(0).__await__()

        sweep = _SweepTask()
        obj._cache_sweep_task = sweep
        obj.context = FakeContext()
        obj._backup_job = types.SimpleNamespace(name="points_backup", job_id="bk")
        obj._birthday_job = types.SimpleNamespace(
            name="birthday_announce", job_id="bd"
        )
        obj.db = types.SimpleNamespace(close=mock.AsyncMock())
        await obj.terminate()
        assert sweep.cancelled == 1, sweep.cancelled
        assert set(obj.context.deleted_jobs) == {"bk", "bd"}, obj.context.deleted_jobs
        assert obj.db.close.called
        # 兼容兜底：无 job_id 的旧式任务对象回退 remove()
        removed_fallback = []
        obj._backup_job = types.SimpleNamespace(
            name="points_backup", remove=lambda: removed_fallback.append("backup")
        )
        obj._birthday_job = None
        await obj.terminate()
        assert removed_fallback == ["backup"], removed_fallback
    return "main 路由：wake 跳过/兑换参数路由/兑换记录页码/cron 注册与重建/stop_event/核销委托/群消息委托/备份回调/terminate"


async def test_my_points_handler():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.handlers.my_points import (
            MyPointsHandler,
        )
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

        handler = MyPointsHandler(types.SimpleNamespace(dao=t.dao))
        # 非群聊
        msgs = await collect(handler.handle(FakeEvent("u1", None)))
        assert any("仅支持群聊" in m for m in msgs)
        # 未注册
        msgs = await collect(handler.handle(FakeEvent("u1", "G1")))
        assert any("还没有积分记录" in m for m in msgs)
        # 已注册：完整信息 + 群昵称 + 排名（fetch_member_info 要求数字 ID）
        today = today_str()
        await t.db.execute(
            "INSERT INTO accounts (qq, points, total_sign_days, consecutive_days, last_sign_date) VALUES (?,?,?,?,?)",
            ("10001", 50, 45, 3, today),
        )
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('10001','123')")
        await t.db.execute("INSERT INTO accounts (qq, points) VALUES ('10002',100)")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('10002','123')")
        await t.db.execute("INSERT INTO accounts (qq, points) VALUES ('10003',10)")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('10003','123')")
        ev = FakeEvent("10001", "123", bot=FakeBot(member_card="小明"))
        msgs = await collect(handler.handle(ev))
        text = "\n".join(msgs)
        assert "💰 小明 (10001)" in text
        assert "当前积分: 50" in text
        assert "累计签到: 45 天" in text
        assert "连签: 第 3 天" in text
        assert "✅ 已签到" in text
        assert "本群排名: 第 2 名" in text
        # 最近流水：仅本群、恰 5 条、格式与 /流水 一致
        for i in range(7):
            await t.db.execute(
                "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) VALUES (?,?,?,?,?)",
                ("10001", "123", 10 * (i + 1), 0, "签到"),
            )
        await t.db.execute(
            "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason) VALUES ('10001','999',99,99,'抽奖')"
        )
        rows = await t.db.fetchall(
            "SELECT id FROM point_transactions WHERE qq='10001' ORDER BY id"
        )
        for idx, row in enumerate(rows):
            await t.db.execute(
                "UPDATE point_transactions SET created_at=? WHERE id=?",
                (f"2026-08-01 08:{idx:02d}:00", row["id"]),
            )
        msgs = await collect(handler.handle(ev))
        text = "\n".join(msgs)
        assert "📊 最近流水" in text
        assert "🟢 +70  签到  2026-08-01 08:06" in text
        assert "🟢 +30  签到  2026-08-01 08:02" in text
        assert "🟢 +10" not in text  # 最早 3 条被截断
        assert "🟢 +99" not in text  # 跨群流水不显示
        assert "📊 最近流水" in text and len(text.split("📊 最近流水")[1].strip().splitlines()) == 5
        # 未签到 + 0 分未上榜：无排名行
        await t.db.execute(
            "INSERT INTO accounts (qq, points, total_sign_days, consecutive_days, last_sign_date) VALUES ('10004',0,0,0,NULL)"
        )
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('10004','123')")
        msgs = await collect(handler.handle(FakeEvent("10004", "123")))
        text = "\n".join(msgs)
        assert "❌ 未签到" in text
        assert "本群排名" not in text
        assert "最近流水" not in text  # 无流水时不显示区块
        # 无 bot：昵称回退纯 QQ
        msgs = await collect(handler.handle(FakeEvent("10001", "123")))
        text = "\n".join(msgs)
        assert "💰 10001" in text and "10001 (10001)" not in text
    return "我的积分 handler：完整信息/群昵称/排名/最近流水/未注册/非群/未上榜/昵称回退"


async def test_my_points_route():
    from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin

    class _MyPointsHandler:
        def __init__(self):
            self.calls = 0

        async def handle(self, event):
            self.calls += 1
            yield event.plain_result("我的积分")

    obj = PointSystemPlugin.__new__(PointSystemPlugin)
    obj.config_cache = base_cfg()
    obj.my_points_handler = _MyPointsHandler()
    # 非触发消息：跳过
    msgs = await collect(
        obj.on_my_points(FakeEvent("u1", "G1", msg="查我的积分", at_wake=True))
    )
    assert msgs == [] and obj.my_points_handler.calls == 0
    # 严格触发：委托 handler
    msgs = await collect(obj.on_my_points(FakeEvent("u1", "G1", msg="我的积分")))
    assert msgs == ["我的积分"] and obj.my_points_handler.calls == 1
    msgs = await collect(obj.on_my_points(FakeEvent("u1", "G1", msg="积分查询")))
    assert msgs == ["我的积分"] and obj.my_points_handler.calls == 2
    return "我的积分路由：严格匹配触发与委托"


async def test_cleanup_stale_cron_jobs():
    """存量清理只删除本插件的非持久化 basic 任务，不影响其他定时任务。"""
    from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin

    def _job(job_id, name, persistent, job_type="basic"):
        return types.SimpleNamespace(
            job_id=job_id, name=name, persistent=persistent, job_type=job_type
        )

    obj = PointSystemPlugin.__new__(PointSystemPlugin)
    obj.context = FakeContext()
    obj.context.seed_jobs = [
        _job("s1", "points_backup", persistent=False),
        _job("s2", "birthday_announce", persistent=False),
        # 以下均不应被删除
        _job("s3", "points_backup", persistent=True),  # 持久化任务
        _job("s4", "other_plugin_job", persistent=False),  # 其他插件任务
        _job("s5", "points_backup", persistent=False, job_type="active_agent"),
        _job("s6", "birthday_announce", persistent=False, job_type="active_agent"),
    ]
    await obj._cleanup_stale_cron_jobs()
    assert set(obj.context.deleted_jobs) == {"s1", "s2"}, obj.context.deleted_jobs
    # 无 list_jobs（旧版 AstrBot）：静默跳过
    obj.context.cron_manager = types.SimpleNamespace(add_basic_job=lambda *a, **k: None)
    obj.context.deleted_jobs.clear()
    await obj._cleanup_stale_cron_jobs()
    assert obj.context.deleted_jobs == []
    return "存量清理：仅删本插件非持久化 basic 任务，持久化/他插件/active_agent 不受影响"


TESTS = [
    ("signin_handler", test_signin_handler_basic),
    ("lottery_handler", test_lottery_handler_paths),
    ("ranking_handler", test_ranking_handler_display),
    ("stats_handler", test_stats_handler_full),
    ("redeem_items", test_redeem_handler_items),
    ("redeem_records", test_redeem_handler_records),
    ("transactions_cmd", test_transactions_command),
    ("main_routes", test_main_routes),
    ("my_points_handler", test_my_points_handler),
    ("my_points_route", test_my_points_route),
    ("cleanup_stale_cron_jobs", test_cleanup_stale_cron_jobs),
]
