"""S15 迁移：schema v1→v3 全链路迁移与幂等、legacy 配置迁移（_load_config_cache first_deploy）。"""

import os
import tempfile

from .common import TempDB, base_cfg


async def test_schema_v1_to_v3_migration():
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
        # 构造 v1 schema：users 缺 negative_title_prev_card、point_transactions 缺 admin_qq，
        # users 含旧积分字段（points 等），sign_in_log 含同 (qq, sign_date) 跨群重复行
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
            CREATE TABLE sign_in_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qq TEXT NOT NULL, group_id TEXT NOT NULL,
                sign_date TEXT NOT NULL,
                points_earned INTEGER NOT NULL DEFAULT 0,
                base_points INTEGER NOT NULL DEFAULT 0,
                bonus_first_sign INTEGER NOT NULL DEFAULT 0,
                bonus_day_first INTEGER NOT NULL DEFAULT 0,
                bonus_consecutive INTEGER NOT NULL DEFAULT 0,
                bonus_weekly INTEGER NOT NULL DEFAULT 0,
                easter_event_type TEXT,
                easter_points INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE(qq, group_id, sign_date)
            );
            CREATE TABLE plugin_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            INSERT INTO plugin_config (key, value) VALUES ('schema_version', '1');
            INSERT INTO users (qq, group_id, points) VALUES
                ('u1','G1',5),('u1','G2',5),('u2','G1',7);
            INSERT INTO sign_in_log (qq, group_id, sign_date, points_earned) VALUES
                ('u1','G1','2026-08-01',10),
                ('u1','G2','2026-08-01',10),
                ('u1','G1','2026-08-02',10);
            """
        )
        await db.conn.commit()
        await init_schema(db)
        # 版本号升级到当前
        row = await db.fetchone(
            "SELECT value FROM plugin_config WHERE key='schema_version'"
        )
        assert row["value"] == str(SCHEMA_VERSION)
        # v2 加列迁移
        for table, col in (
            ("users", "negative_title_prev_card"),
            ("point_transactions", "admin_qq"),
        ):
            cols = await db.fetchall(f"PRAGMA table_info({table})")
            assert col in {c["name"] for c in cols}, (table, col)
        # v3：accounts 回填（多群余额取 MAX，total_earned 原值）
        acct = await db.fetchone(
            "SELECT points, total_earned FROM accounts WHERE qq='u1'"
        )
        assert acct["points"] == 5 and acct["total_earned"] == 0
        # users 旧列已删除（瘦身）
        cols = await db.fetchall("PRAGMA table_info(users)")
        names = {c["name"] for c in cols}
        for dropped in (
            "points", "platform", "total_earned", "last_sign_date",
            "consecutive_days", "max_consecutive_days", "total_sign_days",
            "birthday", "birthday_year", "birthday_bonus_claimed",
            "lucky_pity", "unlucky_pity",
        ):
            assert dropped not in names, dropped
        # 全局唯一索引：同 (qq, sign_date) 重复行被清理（保留最早一条）
        cnt = await db.fetchone("SELECT COUNT(*) AS c FROM sign_in_log")
        assert cnt["c"] == 2, cnt["c"]
        idx = await db.fetchone(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_sign_in_log_qq_date'"
        )
        assert idx is not None
        # 成员关系数据保留
        user = await db.fetchone(
            "SELECT 1 FROM users WHERE qq='u1' AND group_id='G1'"
        )
        assert user is not None
    finally:
        await db.close()
    return "schema 迁移：v1→v3 加列/accounts 回填/users 瘦身/去重索引/数据保留"


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
    ("schema_v1_to_v3", test_schema_v1_to_v3_migration),
    ("schema_idempotent", test_schema_migration_idempotent),
    ("legacy_config_migration", test_legacy_config_migration),
]
