"""S15 迁移：schema v1→v2 加列迁移与幂等、legacy 配置迁移（_load_config_cache first_deploy）。"""

import os
import tempfile

from .common import TempDB, base_cfg


async def test_schema_v1_to_v2_migration():
    tmp = tempfile.mkdtemp(prefix="points_mig_")
    path = os.path.join(tmp, "mig.db")
    from astrbot_plugin_point_system_by_whleague.db.connection import DatabaseManager
    from astrbot_plugin_point_system_by_whleague.db.schema import (
        SCHEMA_VERSION,
        init_schema,
    )

    db = DatabaseManager(path)
    await db.init()
    try:
        # 构造 v1 schema：users 缺 negative_title_prev_card、point_transactions 缺 admin_qq
        await db.conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qq TEXT NOT NULL, group_id TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT '',
                points INTEGER NOT NULL DEFAULT 0,
                total_earned INTEGER NOT NULL DEFAULT 0,
                last_sign_date TEXT,
                consecutive_days INTEGER NOT NULL DEFAULT 0,
                max_consecutive_days INTEGER NOT NULL DEFAULT 0,
                total_sign_days INTEGER NOT NULL DEFAULT 0,
                birthday TEXT, birthday_year INTEGER,
                birthday_bonus_claimed INTEGER NOT NULL DEFAULT 0,
                lucky_pity INTEGER NOT NULL DEFAULT 0,
                unlucky_pity INTEGER NOT NULL DEFAULT 0,
                negative_title_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE(qq, group_id)
            );
            CREATE TABLE point_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qq TEXT NOT NULL, group_id TEXT NOT NULL,
                amount INTEGER NOT NULL, balance_after INTEGER NOT NULL,
                reason TEXT NOT NULL, ref_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE plugin_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            INSERT INTO plugin_config (key, value) VALUES ('schema_version', '1');
            INSERT INTO users (qq, group_id, points) VALUES ('u1','G1',5);
            """
        )
        await db.conn.commit()
        await init_schema(db)
        # 迁移后：版本号升级、目标列存在、数据保留
        row = await db.fetchone(
            "SELECT value FROM plugin_config WHERE key='schema_version'"
        )
        assert row["value"] == str(SCHEMA_VERSION)
        for table, col in (
            ("users", "negative_title_prev_card"),
            ("point_transactions", "admin_qq"),
        ):
            cols = await db.fetchall(f"PRAGMA table_info({table})")
            assert col in {c["name"] for c in cols}, (table, col)
        row = await db.fetchone("SELECT points FROM users WHERE qq='u1'")
        assert row["points"] == 5
    finally:
        await db.close()
    return "schema 迁移：v1→v2 加列、版本升级、数据保留"


async def test_schema_migration_idempotent():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.db.schema import (
            SCHEMA_VERSION,
            init_schema,
        )

        await init_schema(t.db)  # 重复执行不报错
        await init_schema(t.db)
        row = await t.db.fetchone(
            "SELECT value FROM plugin_config WHERE key='schema_version'"
        )
        assert row["value"] == str(SCHEMA_VERSION)
    return "schema 迁移：重复 init 幂等"


async def test_legacy_config_migration():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.main import PointSystemPlugin

        class _FakeConfig(dict):
            first_deploy = True

            def __init__(self):
                super().__init__(dict(base_cfg()))
                self.save_called = 0

            def save_config(self):
                self.save_called += 1

        # 旧版 DB 配置（plugin_config 表）
        await t.dao.set_config("signin_fixed_points", "15")
        await t.dao.set_config("signin_fixed_mode", "true")
        await t.dao.set_config("active_reward_probability", "0.2")
        await t.dao.set_config("keyword_sign", '["a","b"]')
        await t.dao.set_config("signin_random_min", "oops")  # 非法值跳过
        await t.dao.set_config("unknown_key", "x")  # 未知键跳过

        cfg = _FakeConfig()
        obj = PointSystemPlugin.__new__(PointSystemPlugin)
        obj.config = cfg
        obj.dao = t.dao
        cache = await obj._load_config_cache()
        assert cache["signin_fixed_points"] == 15
        assert cache["signin_fixed_mode"] is True
        assert cache["active_reward_probability"] == 0.2
        assert cache["keyword_sign"] == ["a", "b"]
        assert (
            cache["signin_random_min"] == base_cfg()["signin_random_min"]
        )  # 非法值保留默认
        assert cfg["signin_fixed_points"] == 15  # 同步回托管配置
        assert cfg.save_called == 1
        assert await t.count("plugin_config") == 0  # 迁移后清空旧表
        # config 为 None：直接返回默认缓存
        obj2 = PointSystemPlugin.__new__(PointSystemPlugin)
        obj2.config = None
        obj2.dao = t.dao
        cache2 = await obj2._load_config_cache()
        assert isinstance(cache2["keyword_sign"], list)
        assert cache2["signin_fixed_points"] == base_cfg()["signin_fixed_points"]
    return "legacy 配置迁移：类型转换/非法跳过/清空旧表/None 路径"


TESTS = [
    ("schema_v1_to_v2", test_schema_v1_to_v2_migration),
    ("schema_idempotent", test_schema_migration_idempotent),
    ("legacy_config_migration", test_legacy_config_migration),
]
