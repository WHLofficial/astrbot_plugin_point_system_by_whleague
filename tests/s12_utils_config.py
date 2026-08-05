"""S12 工具函数与配置解析边界：parse_keyword_list、业务日分界、today/period 一致性、跨年区间、兑换编号、安全解析、运势文案。"""

import types
from datetime import datetime, timedelta

from .common import TempDB, restore_day_boundary, snapshot_day_boundary


async def test_parse_keyword_list():
    from astrbot_plugin_point_system_by_whleague.config.defaults import (
        parse_keyword_list,
    )

    assert parse_keyword_list(["签到", "sign"]) == ["签到", "sign"]
    assert parse_keyword_list(("a", "", "b")) == ["a", "b"]
    assert parse_keyword_list('["a","b"]') == ["a", "b"]
    assert parse_keyword_list("a,b,c") == ["a", "b", "c"]
    assert parse_keyword_list(" a , b ") == ["a", "b"]  # 逗号分隔去空格
    assert parse_keyword_list("") == []
    assert parse_keyword_list(None) == []
    assert parse_keyword_list(123) == []
    assert parse_keyword_list("not json but comma,separated") == [
        "not json but comma",
        "separated",
    ]
    return "parse_keyword_list：list/元组/JSON/逗号/空/非法输入"


async def test_day_boundary_parse():
    from astrbot_plugin_point_system_by_whleague.utils.helpers import (
        get_day_boundary,
        set_day_boundary,
    )

    saved = snapshot_day_boundary()
    try:
        assert set_day_boundary("04:00") == (4, 0)
        assert set_day_boundary("4:00") == (4, 0)  # 单数字小时规范化
        assert set_day_boundary("23:59") == (23, 59)
        assert set_day_boundary("25:00") == (4, 0)  # 非法回退默认
        assert set_day_boundary("abc") == (4, 0)
        assert set_day_boundary("") == (4, 0)
        assert get_day_boundary() == (4, 0)
    finally:
        restore_day_boundary(saved)
    return "业务日分界：解析/单数字/非法回退默认"


async def test_today_period_consistency():
    from astrbot_plugin_point_system_by_whleague.utils.helpers import (
        period_start_str,
        set_day_boundary,
        today_mmdd,
        today_str,
    )

    saved = snapshot_day_boundary()
    try:
        for value in ("00:00", "04:00", "12:30", "23:59"):
            set_day_boundary(value)
            h, m = value.split(":")
            h, m = int(h), int(m)
            shifted = datetime.now() - timedelta(hours=h, minutes=m)
            assert today_str() == shifted.date().isoformat(), value
            assert today_mmdd() == shifted.date().strftime("%m-%d"), value
            day = (datetime.now() - timedelta(hours=h, minutes=m)).date()
            assert (
                period_start_str() == day.strftime("%Y-%m-%d") + f" {h:02d}:{m:02d}:00"
            ), value
    finally:
        restore_day_boundary(saved)
    return "today_str/today_mmdd/period_start_str 与分界一致"


async def test_is_date_in_range():
    from astrbot_plugin_point_system_by_whleague.utils.helpers import is_date_in_range

    assert is_date_in_range("01-01", "01-01") is True  # 单日
    assert is_date_in_range("01-02", "01-01") is False
    assert is_date_in_range("01-05", "01-01", "01-31") is True
    assert is_date_in_range("01-01", "01-01", "01-31") is True  # 起点包含
    assert is_date_in_range("01-31", "01-01", "01-31") is True  # 终点包含
    assert is_date_in_range("02-01", "01-01", "01-31") is False
    assert is_date_in_range("12-31", "12-30", "01-02") is True  # 跨年
    assert is_date_in_range("01-01", "12-30", "01-02") is True
    assert is_date_in_range("06-01", "12-30", "01-02") is False
    return "is_date_in_range：单日/区间/端点/跨年包裹"


async def test_generate_record_no():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.utils.helpers import (
            generate_record_no,
        )

        async def gen(prefix=None):
            async def _tx(conn):
                return await generate_record_no(conn, prefix)

            return await t.db.execute_transaction(_tx)

        assert await gen() == f"R{today_nodash()}-0001"
        item_id = await t.dao.add_item("商品", 10, 5)
        await t.db.execute(
            "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost) "
            "VALUES (?, 'u1','G1',?,'x',1)",
            (f"R{today_nodash()}-0001", item_id),
        )
        assert await gen() == f"R{today_nodash()}-0002"
        assert await gen("20260101") == "R20260101-0001"  # 自定义日期前缀独立计数
    return "兑换编号：按日期前缀递增、自定义前缀"


async def test_security_parsers():
    from astrbot_plugin_point_system_by_whleague.utils.security import (
        parse_int,
        parse_qq,
        parse_qq_arg,
        sanitize_text,
    )

    # parse_qq
    assert parse_qq("12345") == "12345"
    assert parse_qq("@12345") == "12345"
    assert parse_qq(" 12345 ") == "12345"
    for bad in ("abc", "12a", ""):
        try:
            parse_qq(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    # parse_qq_arg 各形态
    assert parse_qq_arg("[CQ:at,qq=123]") == "123"
    assert parse_qq_arg("@昵称(456)") == "456"
    assert parse_qq_arg("昵称(789)") == "789"
    assert parse_qq_arg("@456") == "456"
    assert parse_qq_arg("456") is None  # 纯数字按页码处理
    assert parse_qq_arg("@u2") is None
    assert parse_qq_arg("") is None
    # parse_int 边界
    assert parse_int("42") == 42
    assert parse_int(" 7 ") == 7
    for bad in ("abc", "1.5", ""):
        try:
            parse_int(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    try:
        parse_int("5", min_val=10)
        raise AssertionError("min_val 应拒绝")
    except ValueError:
        pass
    # sanitize_text
    assert sanitize_text("  abc  ") == "abc"
    assert sanitize_text("") == ""
    assert sanitize_text("x" * 500) == "x" * 200
    assert sanitize_text(None) == ""
    return "安全解析：QQ/at 形态/整数边界/截断"


async def test_fortune_format():
    from astrbot_plugin_point_system_by_whleague.utils.fortune import (
        format_fortune,
        get_fortune,
    )

    f = get_fortune("123", "2026-08-01")
    assert set(f.keys()) == {"level", "lucky_number", "advice"}
    assert 1 <= f["lucky_number"] <= 99
    text = format_fortune("123", "2026-08-01", "小明")
    assert "小明" in text and f["level"] in text and f["advice"] in text
    assert "幸运数字" in text and str(f["lucky_number"]) in text
    # 昵称含换行/控制字符：控制字符被剥离，无法通过昵称构造额外的伪造消息行
    evil = "小明\r\n⚠️ 群公告：全体禁言\x00"
    text2 = format_fortune("123", "2026-08-01", evil)
    assert "\r" not in text2 and "\x00" not in text2
    assert text2.count("\n") == 4  # 仅保留正常行分隔，昵称未引入新行
    return "运势：字段结构/文案包含姓名/等级/建议/幸运数字/控制字符剥离"


async def test_clean_display_name():
    from astrbot_plugin_point_system_by_whleague.utils.security import (
        clean_display_name,
    )

    assert clean_display_name("正常昵称") == "正常昵称"
    assert clean_display_name(" 带空格 ") == "带空格"
    assert clean_display_name("恶意\r\n伪造\x00") == "恶意伪造"
    assert clean_display_name("a\x1fb") == "ab"
    assert clean_display_name("") == ""
    assert clean_display_name(None) == ""
    assert clean_display_name("tab\t分隔") == "tab分隔"
    return "clean_display_name：正常/空白/控制字符/空值/None"


async def test_fetch_member_info():
    from astrbot_plugin_point_system_by_whleague.utils.group_info import (
        fetch_member_info,
    )

    async def _mk(result, raise_err=False):
        async def call_action(action, **kwargs):
            if raise_err:
                raise RuntimeError("boom")
            return result

        return types.SimpleNamespace(call_action=call_action)

    # 无 bot → None
    assert await fetch_member_info(None, "10001", "100000") is None
    # 无 call_action → None
    assert await fetch_member_info(object(), "10001", "100000") is None
    # 正常 dict
    bot = await _mk({"card": "名片", "nickname": "昵称"})
    info = await fetch_member_info(bot, "10001", "100000")
    assert info and info["card"] == "名片"
    # 非 dict 返回（列表/字符串）→ None
    for bad in ([1, 2], "str", 42):
        bot2 = await _mk(bad)
        assert await fetch_member_info(bot2, "10001", "100000") is None, bad
    # 调用异常 → None（静默回退）
    bot3 = await _mk(None, raise_err=True)
    assert await fetch_member_info(bot3, "10001", "100000") is None
    return "fetch_member_info：无bot/无call_action/正常/非dict/异常"


def today_nodash() -> str:
    from astrbot_plugin_point_system_by_whleague.utils.helpers import today_str

    return today_str().replace("-", "")


async def test_keyword_matcher_strict():
    """v0.2.1 严格匹配：仅纯触发词形态触发，附加文本/标点/大小写边界。"""
    from astrbot_plugin_point_system_by_whleague.utils.keyword_matcher import (
        is_lottery_message,
        is_my_points_message,
        is_ranking_message,
        is_signin_message,
    )

    sign = ["签到", "sign", "打卡"]
    # 严格相等触发（大小写不敏感、空白压缩）
    assert is_signin_message("签到", sign)
    assert is_signin_message(" 签到 ", sign)
    assert is_signin_message("SIGN", sign)
    assert is_signin_message("打卡", sign)
    # 附加文本/标点/组合词不触发
    for bad in ("我要签到", "签到打卡", "签到！", "签到 一下", "签到一下", "sign in", "打卡啦"):
        assert not is_signin_message(bad, sign), bad
    # 抽奖：关键词 / 口令+关键词 / 关键词+口令 三形态
    lottery = ["抽奖"]
    assert is_lottery_message("抽奖", "whl", lottery)
    assert is_lottery_message("whl抽奖", "whl", lottery)
    assert is_lottery_message("抽奖whl", "whl", lottery)
    assert is_lottery_message("whl 抽奖", "whl", lottery)  # 空白压缩
    assert is_lottery_message("WHL抽奖", "whl", lottery)  # 大小写
    for bad in ("我要抽奖", "whl 今天抽奖", "抽奖whl哈哈", "抽奖！", "whl", "抽奖 抽奖"):
        assert not is_lottery_message(bad, "whl", lottery), bad
    # 无口令时仅关键词形态
    assert is_lottery_message("抽奖", "", lottery)
    assert not is_lottery_message("whl抽奖", "", lottery)
    # 排行
    assert is_ranking_message("排行")
    assert is_ranking_message("排名")
    assert is_ranking_message("积分榜")
    assert is_ranking_message(" 排行 ")
    for bad in ("排行榜", "我要排行", "积分榜！"):
        assert not is_ranking_message(bad), bad
    # 我的积分
    assert is_my_points_message("我的积分")
    assert is_my_points_message("积分查询")
    assert is_my_points_message(" 我的积分 ")
    for bad in ("查我的积分", "我的积分！", "积分查询一下", "查积分", "积分", "my points"):
        assert not is_my_points_message(bad), bad
    return "关键词严格匹配：四功能边界/大小写/空白/附加文本全部正确"


async def test_lottery_tiers_fallback_and_limits():
    """lottery_tiers 非法回退默认档位；整数配置业务上限拒绝。"""
    from astrbot_plugin_point_system_by_whleague.config.defaults import (
        DEFAULT_CONFIG,
        validate_and_cast,
    )
    from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin

    # 非法 JSON / 缺 tiers / 非字符串 → 回退默认档位
    for bad in ("not json", '{"tiers":[]}', '{"tiers":"x"}', 42, None):
        out = PointSystemPlugin._sanitize_lottery_tiers(bad)
        assert out == DEFAULT_CONFIG["lottery_tiers"], bad
    # 合法档位保持原样
    ok = '{"tiers":[{"label":"x","weight":2,"points_min":1,"points_max":5}]}'
    assert PointSystemPlugin._sanitize_lottery_tiers(ok) == ok

    # 整数上限：超出拒绝、边界值与默认值通过
    assert validate_and_cast("lottery_cost", "1000000") == 1000000
    assert validate_and_cast("lottery_daily_limit", "0") == 0
    assert validate_and_cast("cmd_map_cache_ttl_hours", "87600") == 87600
    assert validate_and_cast("backup_keep_count", "30") == 30
    assert validate_and_cast("backup_keep_count", "0") == 0
    for key, bad in (
        ("lottery_cost", "1000001"),
        ("lottery_daily_limit", "10001"),
        ("signin_weekly_bonus", "1000001"),
        ("active_reward_cooldown", "86401"),
        ("cmd_map_cache_ttl_hours", "87601"),
        ("backup_keep_count", "10001"),
    ):
        try:
            validate_and_cast(key, bad)
            raise AssertionError((key, bad))
        except ValueError:
            pass
    # 新配置键已进入 DEFAULT_CONFIG（schema 同步）
    assert "backup_keep_count" in DEFAULT_CONFIG
    # redeem_notify_channel：枚举归一化与非法值拒绝
    assert validate_and_cast("redeem_notify_channel", "group") == "group"
    assert validate_and_cast("redeem_notify_channel", "群") == "group"
    assert validate_and_cast("redeem_notify_channel", "private") == "private"
    assert validate_and_cast("redeem_notify_channel", "私信") == "private"
    assert validate_and_cast("redeem_notify_channel", "私聊") == "private"
    for bad in ("email", "", "群聊", "  "):
        try:
            validate_and_cast("redeem_notify_channel", bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert DEFAULT_CONFIG["redeem_notify_channel"] == "group"
    return "lottery_tiers：非法回退/合法保持；整数上限：拒绝/边界通过/默认兼容；通知渠道枚举"


async def test_lottery_draw_bad_tiers_runtime():
    """运行时 lottery_tiers 解析失败返回明确错误而非 KeyError。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.lottery_service import (
            LotteryService,
        )
        from astrbot_plugin_point_system_by_whleague.services.point_service import (
            PointService,
        )

        cfg = {"lottery_enabled": True, "lottery_cost": 1, "lottery_tiers": "broken json",
               "lottery_daily_limit": 0, "negative_disable_lottery": False}
        svc = LotteryService(t.db, t.dao, PointService(t.db, t.dao), cfg)
        await t.dao.ensure_account("u1")
        await t.db.execute("UPDATE accounts SET points=10 WHERE qq='u1'")
        r = await svc.draw("u1", "G1")
        assert not r["success"]
        assert "配置异常" in r["msg"], r
    return "抽奖：坏 tiers 配置返回明确错误消息"


TESTS = [
    ("parse_keyword_list", test_parse_keyword_list),
    ("day_boundary_parse", test_day_boundary_parse),
    ("today_period_consistency", test_today_period_consistency),
    ("is_date_in_range", test_is_date_in_range),
    ("generate_record_no", test_generate_record_no),
    ("security_parsers", test_security_parsers),
    ("fortune_format", test_fortune_format),
    ("clean_display_name", test_clean_display_name),
    ("fetch_member_info", test_fetch_member_info),
    ("keyword_matcher_strict", test_keyword_matcher_strict),
    ("lottery_tiers_fallback_limits", test_lottery_tiers_fallback_and_limits),
    ("lottery_draw_bad_tiers", test_lottery_draw_bad_tiers_runtime),
]
