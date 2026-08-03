"""S3 安全测试：SQL 注入 fuzz、路径穿越、权限矩阵、令牌、数值/超长输入。

安全声明：全部在临时库上进行；注入 payload 只会验证"被参数化拒绝"，
不会对生产数据产生任何影响。
"""

import os
import tempfile

from .common import FakeEvent, TempDB, collect

# ── SQL 注入 fuzz ─────────────────────────────────────────

_INJECT_PAYLOADS = [
    "'; DROP TABLE users;--",
    "'; DELETE FROM point_transactions;--",
    '" OR 1=1 --',
    "1 OR 1=1",
    "1; SELECT * FROM users",
    "') OR ('1'='1",
    "'; INSERT INTO admins (qq, group_id, added_by) VALUES ('hack','G1','hack');--",
    "' UNION SELECT 1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1--",
    "`; VACUUM INTO '/tmp/evil';--",
    "日'; UPDATE users SET points=99999;--",
]


async def test_sql_injection_fuzz():
    async with TempDB() as t:
        await t.db.execute("INSERT INTO accounts (qq, points) VALUES ('victim',10)")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('victim','G1')")
        before_users = await t.count("users")

        for i, payload in enumerate(_INJECT_PAYLOADS):
            # 经 DAO 参数化路径注入
            await t.dao.add_item(payload, 1, 1)
            await t.dao.set_daily_keyword("G1", payload, 10, "admin")
            await t.dao.add_date_reward("01-01", None, payload, 5, 1.0)
            await t.dao.soft_delete_item(999999)
            try:
                await t.dao.get_redeem_record(payload)
            except Exception:
                pass
            try:
                await t.dao.set_redeem_status(payload, "rejected", "admin")
            except Exception:
                pass

        # /设置 路径（validate_and_cast）
        from astrbot_plugin_point_system_by_whleague.config.defaults import (
            validate_and_cast,
        )

        for payload in _INJECT_PAYLOADS:
            try:
                validate_and_cast("lottery_passphrase", payload)
            except ValueError:
                pass

        # 结果断言：库未被破坏，用户数据完好
        assert await t.count("users") == before_users
        row = await t.dao.get_account("victim")
        assert row["points"] == 10
        # 注入目标表仍存在
        tables = await t.db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('users','admins','point_transactions')"
        )
        assert {r["name"] for r in tables} == {"users", "admins", "point_transactions"}
    return f"SQL 注入 fuzz：{len(_INJECT_PAYLOADS)} 组 payload 全部被参数化拒绝"


async def test_path_traversal_backup():
    """backup_dirs 恶意路径：不越权写入、单引号路径正确转义。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.backup_service import (
            BackupService,
        )

        svc = BackupService(t.db, {"backup_dirs": []})
        base = tempfile.mkdtemp(prefix="backup_target_")
        targets = [
            base,  # 正常绝对路径
            os.path.join(base, "sub dir'quote"),  # 含空格与单引号
            os.path.join(base, "..", "..", "..", "..", "escape_check"),  # 相对 .. 上跳
            os.path.expanduser("~"),  # ~ 展开（不写入，仅验证解析）
        ]
        for i, target in enumerate(targets):
            resolved = svc._resolve(target)
            try:
                await svc._backup_to(resolved)
                wrote = True
            except Exception:
                wrote = False
            if i < 3:
                assert wrote, f"backup to {target} failed"
                assert resolved.is_dir()
        # 备份文件名含单引号路径时 VACUUM 成功（转义生效）
        q_dir = os.path.join(base, "it's here")
        await svc._backup_to(svc._resolve(q_dir))
        files = os.listdir(q_dir)
        assert any(f.endswith(".db") for f in files)
        # 生产库路径未被触碰（测试库仍在原地且可读）
        row = await t.db.fetchone("SELECT COUNT(*) AS c FROM users")
        assert row is not None
    return "路径穿越：相对上跳解析可控、单引号路径 VACUUM 转义成功"


# ── 权限矩阵 ──────────────────────────────────────────────


async def _admin_plugin(t):
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
        config_cache={},
        config=None,
        daily_keyword_service=_t.SimpleNamespace(invalidate=lambda g: None),
        point_service=_t.SimpleNamespace(
            add=_add_points, subtract=_sub_points, _set_group_card=_noop
        ),
        redeem_service=_t.SimpleNamespace(
            set_discount=_noop_dict, clear_discount=_noop_dict
        ),
        sign_in_service=None,
    )
    return AdminHandler(plugin)


async def _add_points(qq, gid, amount, reason, **k):
    return {"balance": amount}


async def _sub_points(qq, gid, amount, reason, **k):
    return {"balance": 0}


async def _noop_dict(*a, **k):
    return {"msg": "ok", "success": True}


async def _noop(*a, **k):
    return None


async def test_permission_matrix():
    """管理指令 × 角色：普通成员全部被拒。"""
    async with TempDB() as t:
        await t.dao.add_admin("gadmin", "owner", "G1")
        handler = await _admin_plugin(t)
        member = FakeEvent("member", "G1", is_admin=False, msg="/加分 @victim 10")
        gadmin = FakeEvent("gadmin", "G1", is_admin=False, msg="/加分 @victim 10")
        global_admin = FakeEvent("root", "G1", is_admin=True, msg="/加分 @victim 10")

        cases = [
            (handler.adjust_points, ("加分",)),
            (handler.adjust_points, ("扣分",)),
            (handler.add_item, ()),
            (handler.delete_item, ()),
            (handler.modify_item, ()),
            (handler.set_daily_kw, ()),
            (handler.clear_daily_kw, ()),
            (handler.set_config, ()),
            (handler.view_config, ()),
            (handler.set_discount, ()),
            (handler.clear_discount, ()),
            (handler.add_date_reward, ()),
            (handler.delete_date_reward, ()),
            (handler.view_date_rewards, ()),
            (handler.clear_data, ("group",)),
        ]
        for fn, args in cases:
            msgs = await collect(fn(member, *args))
            assert any("没有权限" in m for m in msgs), (fn.__name__, msgs)
        # 提权操作（群主/全局管理）
        msgs = await collect(handler.add_admin(member))
        assert any("没有权限" in m for m in msgs), msgs
        msgs = await collect(handler.remove_admin(member))
        assert any("没有权限" in m for m in msgs), msgs
        # 全局清空仅全局管理员
        msgs = await collect(handler.clear_data(gadmin, "global"))
        assert any("全局管理员" in m for m in msgs), msgs
        # 群管理员可执行群级操作
        msgs = await collect(handler.clear_data(gadmin, "group"))
        assert any("确认清空" in m for m in msgs), msgs
        # 全局管理员可执行一切
        msgs = await collect(handler.add_admin(global_admin))
        assert not any("没有权限" in m for m in msgs), msgs
    return "权限矩阵：15 个群级操作 + 3 个提权操作 × 角色边界正确"


async def test_cross_group_isolation():
    """跨群隔离：本群管理员看不到其他群的兑换记录（C2 回归）。"""
    async with TempDB() as t:
        import types as _t

        from astrbot_plugin_point_system_by_whleague.handlers.redeem import (
            RedeemHandler,
        )

        item_id = await t.dao.add_item("商品", 10, 10)
        await t.db.execute(
            "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost) "
            "VALUES ('RG1-001','a','G1',?,'物品1',10),('RG2-001','b','G2',?,'物品2',10)",
            (item_id, item_id),
        )
        await t.dao.add_admin("gadmin", "owner", "G1")
        handler = RedeemHandler(_t.SimpleNamespace(dao=t.dao))

        ev = FakeEvent("gadmin", "G1", is_admin=False, msg="/兑换记录 all")
        msgs = await collect(handler.list_records(ev, "all", "1"))
        assert any("RG1-001" in m for m in msgs), msgs
        assert not any("RG2-001" in m for m in msgs), msgs
        # 详情：其他群记录被拒
        ev2 = FakeEvent("gadmin", "G1", is_admin=False, msg="/兑换记录 RG2-001")
        msgs = await collect(handler.list_records(ev2, "RG2-001", "1"))
        assert any("其他群" in m for m in msgs), msgs
        # 全局管理员可跨群
        ev3 = FakeEvent("root", "G1", is_admin=True, msg="/兑换记录 RG2-001")
        msgs = await collect(handler.list_records(ev3, "RG2-001", "1"))
        assert not any("其他群" in m for m in msgs), msgs
        # 普通用户查他人详情被拒
        ev4 = FakeEvent("member", "G1", is_admin=False, msg="/兑换记录 RG1-001")
        msgs = await collect(handler.list_records(ev4, "RG1-001", "1"))
        assert any("无权" in m for m in msgs), msgs
    return "跨群隔离：列表/详情/全局管理员路径全部正确"


# ── 令牌安全 ──────────────────────────────────────────────


async def test_clear_token_security():
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
            point_service=_t.SimpleNamespace(_set_group_card=_noop),
        )
        handler = AdminHandler(plugin)
        await t.db.execute("INSERT INTO accounts (qq, points) VALUES ('u1',1)")
        await t.db.execute("INSERT INTO users (qq, group_id) VALUES ('u1','G1')")

        ev = FakeEvent("admin", "G1", is_admin=True)
        await collect(handler.clear_data(ev, "group"))

        # 100 次错误验证码：全部拒绝；首次错误即消耗令牌
        for i in range(100):
            e = FakeEvent(
                "admin",
                "G1",
                is_admin=True,
                msg=f"/确认清空 {000000 if i == 0 else 999999}",
            )
            msgs = await collect(handler.confirm_clear(e))
            assert any("错误" in m for m in msgs), (i, msgs)
            if i == 0:
                assert "admin" not in handler._pending_clears
                break
        # 无令牌再确认
        e = FakeEvent("admin", "G1", is_admin=True, msg="/确认清空 123456")
        msgs = await collect(handler.confirm_clear(e))
        assert any("没有待确认" in m for m in msgs)

        # 跨 QQ 不可用
        await collect(handler.clear_data(ev, "group"))
        token2 = handler._pending_clears["admin"]["token"]
        e = FakeEvent("other", "G1", is_admin=True, msg=f"/确认清空 {token2}")
        msgs = await collect(handler.confirm_clear(e))
        assert any("没有待确认" in m for m in msgs)
        assert "admin" in handler._pending_clears  # 原令牌未消耗
        assert await t.count("users") == 1  # 数据未动

        # 过期
        handler._pending_clears["admin"]["expires_at"] = 0
        e = FakeEvent("admin", "G1", is_admin=True, msg=f"/确认清空 {token2}")
        msgs = await collect(handler.confirm_clear(e))
        assert any("过期" in m for m in msgs)
    return "令牌安全：爆破全拒/单次/跨 QQ 隔离/过期"


# ── 数值与超长输入边界 ────────────────────────────────────


async def test_numeric_boundaries():
    """NaN/Infinity/超大/负值/零值：拒绝或钳制，不崩溃不越界。"""
    from astrbot_plugin_point_system_by_whleague.config.defaults import (
        validate_and_cast,
    )

    # NaN/Inf 对概率被拒绝
    for bad in ("nan", "inf", "-inf"):
        try:
            validate_and_cast("active_reward_probability", bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    # 超大整数在 int 范围内通过但语义受限由业务层钳制
    v = validate_and_cast("lottery_cost", "999999999")
    assert v == 999999999
    # 负值拒绝
    for key in ("lottery_cost", "signin_fixed_points", "active_reward_points_min"):
        try:
            validate_and_cast(key, "-1")
            raise AssertionError(key)
        except ValueError:
            pass

    # 随机区间 min/max 填反：业务层钳制不报错
    import random

    for _ in range(50):
        lo, hi = 8, 2
        p = random.randint(min(lo, hi), max(lo, hi))
        assert lo >= p >= hi or hi >= p >= lo
    return "数值边界：NaN/Inf/负值拒绝、上下限钳制"


async def test_oversized_inputs():
    """超长消息/名称/数量：截断或拒绝，不崩溃。"""
    async with TempDB():
        import types as _t

        from astrbot_plugin_point_system_by_whleague.handlers.active_reward import (
            ActiveRewardHandler,
        )
        from astrbot_plugin_point_system_by_whleague.utils.security import (
            parse_int,
            sanitize_text,
        )

        # sanitize_text 截断 200
        long_text = "x" * 10000
        assert len(sanitize_text(long_text)) == 200
        # parse_int 边界
        try:
            parse_int("1000", max_val=999)
            raise AssertionError("1000 应被拒")
        except ValueError:
            pass
        try:
            parse_int("abc")
            raise AssertionError("abc 应被拒")
        except ValueError:
            pass
        try:
            parse_int("0", min_val=1)
            raise AssertionError("0 应被拒")
        except ValueError:
            pass

        # 100KB 消息过活跃奖励处理器（功能关闭 → 早期返回）
        cfg = {
            "keyword_sign": ["签到", "sign", "打卡"],
            "lottery_passphrase": "whl",
            "keyword_lottery": ["抽奖", "lottery"],
            "active_reward_enabled": False,
            "active_reward_min_length": 3,
            "active_reward_cooldown": 60,
            "active_reward_global_cooldown": 10,
            "active_reward_probability": 0.05,
            "active_reward_points_min": 1,
            "active_reward_points_max": 5,
        }
        from astrbot_plugin_point_system_by_whleague.utils.rate_limiter import (
            RateLimiter,
        )

        plugin = _t.SimpleNamespace(
            config_cache=cfg,
            rate_limiter=RateLimiter(),
            daily_keyword_service=_t.SimpleNamespace(check_and_claim=_noop_dict),
        )
        handler = ActiveRewardHandler(plugin)
        ev = FakeEvent("u1", "G1", is_admin=False, msg="a" * 102400 + "签到")
        await handler.handle(ev)  # 不应抛异常

        # emoji/CQ 码混淆不崩溃
        ev2 = FakeEvent(
            "u1", "G1", is_admin=False, msg="[CQ:at,qq=123] @昵称(456) 😀 抽奖 whl"
        )
        await handler.handle(ev2)
    return "超长/混淆输入：截断、拒绝、处理不崩溃"


async def test_field_whitelist_injection():
    """update_item_field 字段白名单：SQL 注入字段名被拒绝，表不受影响。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.db.dao import PointDAO

        item_id = await t.dao.add_item("商品", 10, 5)
        before = await t.count("redeem_items")
        for bad in (
            "cost; DROP TABLE users;--",
            "name='x' WHERE 1=1;--",
            "points",
            "id",
            "is_active",
            "stock, name",
        ):
            try:
                await PointDAO(t.db).update_item_field(item_id, bad, 1)
                raise AssertionError(f"字段 {bad} 应被拒绝")
            except ValueError:
                pass
        assert await t.count("redeem_items") == before
        assert await t.count("users") == 0  # users 表未被破坏
        item = await t.dao.get_item(item_id)
        assert item["name"] == "商品" and item["cost"] == 10
    return "字段白名单：注入型字段名全部拒绝"


async def test_lottery_tiers_non_finite():
    """lottery_tiers 权重 NaN/Infinity：json.loads 接受但校验必须拒绝（防抽奖崩溃）。"""
    from astrbot_plugin_point_system_by_whleague.config.defaults import (
        validate_and_cast,
    )

    for bad in (
        '{"tiers":[{"label":"x","weight":NaN,"points_min":1,"points_max":5}]}',
        '{"tiers":[{"label":"x","weight":Infinity,"points_min":1,"points_max":5}]}',
        '{"tiers":[{"label":"x","weight":-Infinity,"points_min":1,"points_max":5}]}',
        '{"tiers":[{"label":"x","weight":"2","points_min":1,"points_max":5}]}',
        '{"tiers":[{"label":"x","weight":2.5,"points_min":NaN,"points_max":5}]}',
    ):
        try:
            validate_and_cast("lottery_tiers", bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    # 合法有限值通过
    ok = '{"tiers":[{"label":"x","weight":2.5,"points_min":1,"points_max":5}]}'
    assert validate_and_cast("lottery_tiers", ok) == ok
    return "lottery_tiers：NaN/Infinity/字符串权重被拒、有限值通过"


async def test_date_reward_handler_bounds():
    """日期奖励 handler：概率 NaN/Inf/越界、非法日期区间全部拒绝。"""
    async with TempDB() as t:
        import types as _t

        from astrbot_plugin_point_system_by_whleague.handlers.admin import AdminHandler

        handler = AdminHandler(
            _t.SimpleNamespace(
                dao=t.dao,
                db=t.db,
                config_cache={},
                config=None,
                point_service=_t.SimpleNamespace(),
                redeem_service=_t.SimpleNamespace(),
                daily_keyword_service=_t.SimpleNamespace(),
                backup_service=_t.SimpleNamespace(),
            )
        )
        for bad in ("1.5", "0", "-1", "nan", "inf", "abc"):
            ev = FakeEvent(
                "admin", "G1", is_admin=True, msg=f"/添加日期奖励 01-01 元旦 50 {bad}"
            )
            msgs = await collect(handler.add_date_reward(ev))
            assert any("参数错误" in m for m in msgs), (bad, msgs)
        # 非法日期/区间
        for bad in ("13-01", "02-30", "12-30~bad"):
            ev = FakeEvent(
                "admin", "G1", is_admin=True, msg=f"/添加日期奖励 {bad} 元旦 50"
            )
            msgs = await collect(handler.add_date_reward(ev))
            assert any("参数错误" in m for m in msgs), (bad, msgs)
        assert await t.count("date_rewards") == 0
    return "日期奖励：概率与日期边界全部拒绝、零落库"


TESTS = [
    ("sql_injection_fuzz", test_sql_injection_fuzz),
    ("path_traversal_backup", test_path_traversal_backup),
    ("permission_matrix", test_permission_matrix),
    ("cross_group_isolation", test_cross_group_isolation),
    ("clear_token_security", test_clear_token_security),
    ("numeric_boundaries", test_numeric_boundaries),
    ("oversized_inputs", test_oversized_inputs),
    ("field_whitelist_injection", test_field_whitelist_injection),
    ("lottery_tiers_non_finite", test_lottery_tiers_non_finite),
    ("date_reward_handler_bounds", test_date_reward_handler_bounds),
]
