"""S7 生日：设置/查询/非法格式、播报服务三态、签到生日奖励与年度去重、cron 播报幂等。"""

import json
import types

from .common import FakeBot, FakeEvent, TempDB, base_cfg, collect


async def test_birthday_set_and_query():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.handlers.birthday import (
            BirthdayHandler,
        )

        plugin = types.SimpleNamespace(dao=t.dao, db=t.db)
        handler = BirthdayHandler(plugin)
        # MM-DD 与 MM月DD日 两种格式
        for fmt in ("12-25", "12月25日"):
            ev = FakeEvent("u1", "G1", msg=f"/设置生日 {fmt}")
            msgs = await collect(handler.set_birthday(ev))
            assert any("已设置生日为 12-25" in m for m in msgs)
        row = await t.dao.get_account("u1")
        assert row["birthday"] == "12-25"
        # 查询自己（无 bot：回退 QQ）
        ev = FakeEvent("u1", "G1", msg="/查生日")
        msgs = await collect(handler.query_birthday(ev))
        assert any("12-25" in m and "u1" in m for m in msgs)
        # 查询他人（@QQ 形式 + FakeBot 群名片，群 ID 须为数字供 int() 转换）
        await t.db.execute(
            "INSERT INTO accounts (qq, birthday) VALUES ('10002','02-29')"
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('10002','100001')"
        )
        ev = FakeEvent("u1", "100001", msg="/查生日 @10002", bot=FakeBot(member_card="群名片X"))
        msgs = await collect(handler.query_birthday(ev))
        assert any("群名片X" in m and "02-29" in m for m in msgs), msgs
        # 恶意群名片含控制字符：\n 被剥离，无法构造多行伪造消息
        ev = FakeEvent(
            "u1", "100001", msg="/查生日 @10002", bot=FakeBot(member_card="卡片\n伪造行\x00")
        )
        msgs = await collect(handler.query_birthday(ev))
        text = "\n".join(msgs)
        assert "\r" not in text and "\x00" not in text, text
        assert "\n" not in text, text  # 控制字符剥离后无新增换行
        assert "02-29" in text, text
        # 无 bot（call_action 缺失）回退 QQ
        ev = FakeEvent("u1", "100001", msg="/查生日 @10002")
        msgs = await collect(handler.query_birthday(ev))
        assert any("10002" in m and "02-29" in m for m in msgs)
        # 私聊（group_id=None）：昵称获取静默失败回退 QQ，仍可查询
        ev = FakeEvent("u1", None, msg="/查生日")
        msgs = await collect(handler.query_birthday(ev))
        assert any("u1" in m and "12-25" in m for m in msgs)
        # 闰日合法（2000 年为闰年）
        ev = FakeEvent("u3", "G1", msg="/设置生日 2月29日")
        msgs = await collect(handler.set_birthday(ev))
        assert any("02-29" in m for m in msgs)
    return "生日设置：两种格式/查询自己与他人（群昵称+防注入）/闰日合法"


async def test_birthday_invalid_inputs():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.handlers.birthday import (
            BirthdayHandler,
        )

        handler = BirthdayHandler(types.SimpleNamespace(dao=t.dao, db=t.db))
        for bad in ("13-01", "00-05", "02-30", "abcd", "1月32日", "31-01"):
            ev = FakeEvent("u1", "G1", msg=f"/设置生日 {bad}")
            msgs = await collect(handler.set_birthday(ev))
            assert any("Invalid" in m for m in msgs), (bad, msgs)
            assert await t.dao.get_user("u1", "G1") is None  # 未建行
        # 未设置生日查询
        ev = FakeEvent("u9", "G1", msg="/查生日")
        msgs = await collect(handler.query_birthday(ev))
        assert any("还没有设置生日" in m for m in msgs)
    return "生日设置：非法日期全部拒绝且不落库、未设置查询提示"


async def test_birthday_announce_service():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.birthday_service import (
            BirthdayService,
        )
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_mmdd

        svc = BirthdayService(t.dao)
        # 无寿星
        r = await svc.announce_birthdays("G1")
        assert r == {"announced": False, "reason": "no_birthdays"}
        # 有寿星（含跨群隔离：仅本群用户）
        await t.db.execute(
            "INSERT INTO accounts (qq, birthday) VALUES ('a',?),('b',?)",
            (today_mmdd(), today_mmdd()),
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('a','G1'),('b','G2')"
        )
        r = await svc.announce_birthdays("G1")
        assert r["announced"] is True and r["users"] == ["a"]
        # 已播报
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

        await t.dao.mark_birthday_announced("G1", today_str(), '["a"]')
        r = await svc.announce_birthdays("G1")
        assert r == {"announced": False, "reason": "already_done"}
    return "生日播报服务：无寿星/本群寿星/已播报三态"


async def test_birthday_signin_bonus_and_year_dedup():
    async with TempDB() as t:
        from datetime import datetime, timedelta

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
        from astrbot_plugin_point_system_by_whleague.utils.helpers import (
            today_mmdd,
            today_str,
        )

        await t.db.execute("UPDATE easter_events SET is_active=0")
        await t.db.execute(
            "INSERT INTO accounts (qq, birthday) VALUES ('u1',?)",
            (today_mmdd(),),
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('u1','G1')"
        )
        cfg = base_cfg(
            signin_fixed_mode=True,
            signin_fixed_points=10,
            signin_first_bonus=0,
            signin_day_first_bonus=0,
            signin_consecutive_max=30,
            signin_consecutive_bonus_per_day=0,
            signin_weekly_bonus=0,
            birthday_bonus_points=100,
        )
        svc = SignInService(
            t.db,
            t.dao,
            PointService(t.db, t.dao),
            EasterService(t.dao),
            DateRewardService(t.dao),
            cfg,
        )
        r = await svc.sign_in("u1", "G1", "aiocqhttp", "签到")
        assert r["points"] == 10 + 100, r["points"]  # 生日奖励
        row = await t.dao.get_account("u1")
        assert row["birthday_year"] == int(today_str()[:4])
        # 模拟下一年再签：同年不再发
        await t.db.execute("DELETE FROM sign_in_log WHERE qq='u1'")
        yesterday = (
            datetime.strptime(today_str(), "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        await t.db.execute(
            "UPDATE accounts SET last_sign_date=?, consecutive_days=1, total_sign_days=1 WHERE qq='u1'",
            (yesterday,),
        )
        r = await svc.sign_in("u1", "G1", "aiocqhttp", "签到")
        assert r["points"] == 10, r["points"]  # 无生日奖励
        log = await t.db.fetchone("SELECT points_earned FROM sign_in_log WHERE qq='u1'")
        assert log["points_earned"] == 10
    return "生日签到奖励：当天发放、同年去重"


async def test_birthday_cron_announce_idempotent():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin
        from astrbot_plugin_point_system_by_whleague.services.birthday_service import (
            BirthdayService,
        )
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_mmdd

        await t.db.execute(
            "INSERT INTO accounts (qq, birthday, platform) VALUES ('a',?,'aiocqhttp'),('b',?,'aiocqhttp')",
            (today_mmdd(), today_mmdd()),
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('a','G1'),('b','G1')"
        )
        obj = PointSystemPlugin.__new__(PointSystemPlugin)

        class _Ctx:
            def __init__(self):
                self.sent = []
                self.platform_manager = types.SimpleNamespace(
                    platform_insts=[
                        types.SimpleNamespace(meta=lambda: types.SimpleNamespace(id="bot1")),
                    ]
                )

            async def send_message(self, origin, chain):
                self.sent.append((origin, str(chain)))
                return True

        ctx = _Ctx()
        obj.context = ctx
        obj.dao = t.dao
        obj.birthday_service = BirthdayService(t.dao)
        await obj._cron_birthday_announce()
        assert len(ctx.sent) == 1
        origin, text = ctx.sent[0]
        assert origin.startswith("bot1:") and "G1" in origin, origin
        assert "a" in text and "b" in text and "生日快乐" in text
        row = await t.db.fetchone(
            "SELECT announced_qqs FROM birthday_announce_log WHERE group_id='G1'"
        )
        assert row and sorted(json.loads(row["announced_qqs"])) == ["a", "b"]
        # 再次执行：已播报，不重复发送
        await obj._cron_birthday_announce()
        assert len(ctx.sent) == 1
    return "生日报播 cron：发送+标记+幂等"


async def test_birthday_cron_announce_fail_no_mark():
    """全部平台实例发送失败（或实例缺失）→ 不标记已播报，下次可重试。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin
        from astrbot_plugin_point_system_by_whleague.services.birthday_service import (
            BirthdayService,
        )
        from astrbot_plugin_point_system_by_whleague.utils.helpers import today_mmdd

        await t.db.execute(
            "INSERT INTO accounts (qq, birthday, platform) VALUES ('a',?,'aiocqhttp')",
            (today_mmdd(),),
        )
        await t.db.execute(
            "INSERT INTO users (qq, group_id) VALUES ('a','G1')"
        )
        obj = PointSystemPlugin.__new__(PointSystemPlugin)

        class _CtxFail:
            def __init__(self):
                self.calls = []
                self.platform_manager = types.SimpleNamespace(
                    platform_insts=[
                        types.SimpleNamespace(meta=lambda: types.SimpleNamespace(id="bot1")),
                        types.SimpleNamespace(meta=lambda: types.SimpleNamespace(id="bot2")),
                    ]
                )

            async def send_message(self, origin, chain):
                self.calls.append(origin)
                raise RuntimeError("send failed")

        ctx_fail = _CtxFail()
        obj.context = ctx_fail
        obj.dao = t.dao
        obj.birthday_service = BirthdayService(t.dao)
        await obj._cron_birthday_announce()
        assert ctx_fail.calls == ["bot1:GroupMessage:G1", "bot2:GroupMessage:G1"], ctx_fail.calls
        row = await t.db.fetchone(
            "SELECT announced_qqs FROM birthday_announce_log WHERE group_id='G1'"
        )
        assert row is None, "发送失败不应标记已播报"
        # 平台管理器缺失：不发送、不标记
        obj.context = types.SimpleNamespace()
        await obj._cron_birthday_announce()
        assert len(ctx_fail.calls) == 2
    return "生日报播 cron：全部实例失败/实例缺失不标记、可重试"


TESTS = [
    ("birthday_set_query", test_birthday_set_and_query),
    ("birthday_invalid_inputs", test_birthday_invalid_inputs),
    ("birthday_announce_service", test_birthday_announce_service),
    ("birthday_signin_bonus", test_birthday_signin_bonus_and_year_dedup),
    ("birthday_cron_announce", test_birthday_cron_announce_idempotent),
    ("birthday_cron_announce_fail", test_birthday_cron_announce_fail_no_mark),
]
