from astrbot.api import logger

SCHEMA_VERSION = 2

SQL_CREATE_TABLES = r"""

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    points INTEGER NOT NULL DEFAULT 0,
    total_earned INTEGER NOT NULL DEFAULT 0,
    last_sign_date TEXT,
    consecutive_days INTEGER NOT NULL DEFAULT 0,
    max_consecutive_days INTEGER NOT NULL DEFAULT 0,
    total_sign_days INTEGER NOT NULL DEFAULT 0,
    birthday TEXT,
    birthday_year INTEGER,
    birthday_bonus_claimed INTEGER NOT NULL DEFAULT 0,
    lucky_pity INTEGER NOT NULL DEFAULT 0,
    unlucky_pity INTEGER NOT NULL DEFAULT 0,
    negative_title_id INTEGER,
    negative_title_prev_card TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(qq, group_id)
);

CREATE INDEX IF NOT EXISTS idx_users_group_points ON users(group_id, points DESC);
CREATE INDEX IF NOT EXISTS idx_users_qq ON users(qq);

CREATE TABLE IF NOT EXISTS sign_in_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_sign_in_date ON sign_in_log(group_id, sign_date);

CREATE TABLE IF NOT EXISTS lottery_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    cost INTEGER NOT NULL,
    reward_amount INTEGER NOT NULL,
    is_win INTEGER NOT NULL DEFAULT 0,
    tier_label TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_lottery_group ON lottery_record(group_id, created_at DESC);

CREATE TABLE IF NOT EXISTS point_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ref_id INTEGER,
    admin_qq TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_pt_qq_group ON point_transactions(qq, group_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pt_reason ON point_transactions(reason);

CREATE TABLE IF NOT EXISTS redeem_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    cost INTEGER NOT NULL,
    stock INTEGER NOT NULL DEFAULT -1,
    discount_price INTEGER,
    discount_end_time TEXT,
    image_url TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS redeem_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_no TEXT NOT NULL UNIQUE,
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    item_id INTEGER NOT NULL REFERENCES redeem_items(id),
    item_name TEXT NOT NULL,
    item_cost INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    admin_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    verified_at TEXT,
    verified_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_redeem_records_qq ON redeem_records(qq);
CREATE INDEX IF NOT EXISTS idx_redeem_records_status ON redeem_records(status);
CREATE INDEX IF NOT EXISTS idx_redeem_records_group ON redeem_records(group_id);

CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL,
    group_id TEXT,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(qq, group_id)
);

CREATE TABLE IF NOT EXISTS date_rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_date TEXT NOT NULL,
    end_date TEXT,
    keyword TEXT NOT NULL,
    points INTEGER NOT NULL,
    probability REAL NOT NULL DEFAULT 1.0,
    description TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS easter_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    probability REAL NOT NULL,
    points_min INTEGER NOT NULL,
    points_max INTEGER NOT NULL,
    pity_count INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS birthday_announce_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    announce_date TEXT NOT NULL,
    announced_qqs TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(group_id, announce_date)
);

CREATE TABLE IF NOT EXISTS daily_keyword (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    points INTEGER NOT NULL,
    set_by TEXT NOT NULL,
    set_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(group_id, set_date)
);

CREATE TABLE IF NOT EXISTS daily_keyword_claim (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kw_id INTEGER NOT NULL REFERENCES daily_keyword(id),
    qq TEXT NOT NULL,
    group_id TEXT NOT NULL,
    points_earned INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(kw_id, qq)
);

CREATE INDEX IF NOT EXISTS idx_dk_claim_group ON daily_keyword_claim(group_id, qq);

CREATE TABLE IF NOT EXISTS plugin_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

"""


async def init_schema(db_manager):
    db = db_manager.conn
    async with db_manager.lock:
        await db.executescript(SQL_CREATE_TABLES)
        await db.commit()

    cur = await db.execute("SELECT value FROM plugin_config WHERE key='schema_version'")
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        await db.execute(
            "INSERT INTO plugin_config (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        await db.commit()
        await _seed_default_easter_events(db)
        logger.info("Database schema initialized (version %d).", SCHEMA_VERSION)
    else:
        try:
            current = int(row["value"])
        except (ValueError, TypeError):
            logger.warning(
                "Invalid schema_version value %r, rewriting to %d.",
                row["value"],
                SCHEMA_VERSION,
            )
            await db.execute(
                "UPDATE plugin_config SET value=?, updated_at=datetime('now','localtime') WHERE key='schema_version'",
                (str(SCHEMA_VERSION),),
            )
            await db.commit()
            current = SCHEMA_VERSION
        if current < SCHEMA_VERSION:
            await _migrate(db, current)
            await db.execute(
                "UPDATE plugin_config SET value=?, updated_at=datetime('now','localtime') WHERE key='schema_version'",
                (str(SCHEMA_VERSION),),
            )
            await db.commit()
            logger.info("Database schema migrated %d -> %d.", current, SCHEMA_VERSION)


async def _table_columns(db, table: str) -> set:
    cur = await db.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    return {r["name"] for r in rows}


async def _migrate(db, current_version: int):
    """增量迁移：仅在目标列缺失时执行，保证可重复运行。"""
    if current_version < 2:
        cols = await _table_columns(db, "users")
        if "negative_title_prev_card" not in cols:
            await db.execute("ALTER TABLE users ADD COLUMN negative_title_prev_card TEXT")
        cols = await _table_columns(db, "point_transactions")
        if "admin_qq" not in cols:
            await db.execute("ALTER TABLE point_transactions ADD COLUMN admin_qq TEXT")
        await db.commit()


async def _seed_default_easter_events(db):
    events = [
        ("lucky", "\u6b27\u7687\u964d\u4e34", "\u7b7e\u5230\u89e6\u53d1\u6b27\u7687\u4e8b\u4ef6\uff0c\u83b7\u5f97\u5927\u91cf\u79ef\u5206\uff01", 0.02, 50, 200, 10),
        ("unlucky", "\u975e\u8457\u9644\u4f53", "\u7b7e\u5230\u89e6\u53d1\u975e\u8457\u4e8b\u4ef6\uff0c\u4e22\u5931\u5927\u91cf\u79ef\u5206\u2026", 0.03, -200, -50, 15),
    ]
    for ev in events:
        await db.execute(
            "INSERT OR IGNORE INTO easter_events (event_type, name, description, probability, points_min, points_max, pity_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ev,
        )
    await db.commit()
