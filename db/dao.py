from datetime import datetime

from ..utils.helpers import today_str


class PointDAO:
    def __init__(self, db_manager):
        self._db = db_manager

    # ─── helpers ──────────────────────────────────────────

    def _today_str(self) -> str:
        return today_str()

    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ─── accounts ─────────────────────────────────────────

    async def get_account(self, qq: str):
        return await self._db.fetchone("SELECT * FROM accounts WHERE qq=?", (qq,))

    async def ensure_account(self, qq: str, platform: str = ""):
        await self._db.execute("INSERT OR IGNORE INTO accounts (qq) VALUES (?)", (qq,))
        if platform:
            await self._db.execute(
                "UPDATE accounts SET platform=?, updated_at=datetime('now','localtime') WHERE qq=? AND platform != ?",
                (platform, qq, platform),
            )
        return await self.get_account(qq)

    async def get_balance(self, qq: str) -> int:
        row = await self._db.fetchone("SELECT points FROM accounts WHERE qq=?", (qq,))
        return row["points"] if row else 0

    async def get_user_groups(self, qq: str) -> list:
        rows = await self._db.fetchall("SELECT group_id FROM users WHERE qq=?", (qq,))
        return [r["group_id"] for r in rows]

    async def set_account_birthday(self, qq: str, birthday: str):
        await self._db.execute(
            "UPDATE accounts SET birthday=?, birthday_year=NULL, updated_at=datetime('now','localtime') WHERE qq=?",
            (birthday, qq),
        )

    # ─── users ────────────────────────────────────────────

    async def get_user(self, qq: str, group_id: str):
        sql = "SELECT * FROM users WHERE qq=? AND group_id=?"
        return await self._db.fetchone(sql, (qq, group_id))

    async def create_user(self, qq: str, group_id: str):
        sql = "INSERT OR IGNORE INTO users (qq, group_id) VALUES (?, ?)"
        cur = await self._db.execute(sql, (qq, group_id))
        try:
            return cur.lastrowid
        finally:
            await cur.close()

    async def ensure_user(self, qq: str, group_id: str, platform: str = ""):
        user = await self.get_user(qq, group_id)
        if not user:
            await self.create_user(qq, group_id)
            user = await self.get_user(qq, group_id)
        await self.ensure_account(qq, platform)
        return user

    async def set_negative_title(
        self,
        qq: str,
        group_id: str,
        title_id: int | None,
        prev_card: str | None = None,
    ):
        if title_id is None:
            await self._db.execute(
                "UPDATE users SET negative_title_id=NULL, negative_title_prev_card=NULL, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
                (qq, group_id),
            )
        else:
            await self._db.execute(
                "UPDATE users SET negative_title_id=?, negative_title_prev_card=?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
                (title_id, prev_card, qq, group_id),
            )

    async def get_top_n_by_group(self, group_id: str, n: int, min_points: int = 1):
        return await self._db.fetchall(
            "SELECT u.qq, a.points, a.total_earned FROM users u "
            "JOIN accounts a ON a.qq=u.qq "
            "WHERE u.group_id=? AND a.points>=? ORDER BY a.points DESC LIMIT ?",
            (group_id, min_points, n),
        )

    async def get_top_n_global(self, n: int, min_points: int = 1):
        return await self._db.fetchall(
            "SELECT a.qq, a.points, a.total_earned, "
            "(SELECT u2.group_id FROM users u2 WHERE u2.qq=a.qq "
            " ORDER BY u2.updated_at DESC, u2.id DESC LIMIT 1) AS group_id "
            "FROM accounts a WHERE a.points>=? ORDER BY a.points DESC LIMIT ?",
            (min_points, n),
        )

    async def count_users_in_group(self, group_id: str) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS cnt FROM users WHERE group_id=?", (group_id,)
        )
        return row["cnt"] if row else 0

    async def get_birthday_users(self, group_id: str, today_mmdd: str):
        return await self._db.fetchall(
            "SELECT u.qq FROM users u JOIN accounts a ON a.qq=u.qq "
            "WHERE u.group_id=? AND a.birthday=?",
            (group_id, today_mmdd),
        )

    async def get_all_group_ids(self):
        rows = await self._db.fetchall("SELECT DISTINCT group_id FROM users")
        return [r["group_id"] for r in rows]

    # ─── sign_in_log ──────────────────────────────────────

    async def count_signins_today(self, group_id: str, sign_date: str) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS cnt FROM sign_in_log WHERE group_id=? AND sign_date=?",
            (group_id, sign_date),
        )
        return row["cnt"] if row else 0

    async def get_first_signer_today(self, group_id: str, sign_date: str):
        return await self._db.fetchone(
            "SELECT qq, MIN(created_at) AS first_time FROM sign_in_log WHERE group_id=? AND sign_date=?",
            (group_id, sign_date),
        )

    async def get_max_streak_today(self, group_id: str):
        return await self._db.fetchone(
            """SELECT u.qq, a.consecutive_days FROM users u
               JOIN accounts a ON a.qq=u.qq
               INNER JOIN sign_in_log s ON s.qq=u.qq AND s.group_id=u.group_id
               WHERE u.group_id=? AND s.sign_date=?
               ORDER BY a.consecutive_days DESC, s.created_at ASC LIMIT 1""",
            (group_id, self._today_str()),
        )

    # ─── point_transactions ────────────────────────────────

    async def get_transactions(
        self,
        qq: str | None = None,
        group_id: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ):
        conditions = []
        params = []
        if qq:
            conditions.append("qq=?")
            params.append(qq)
        if group_id:
            conditions.append("group_id=?")
            params.append(group_id)
        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM point_transactions WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return await self._db.fetchall(sql, params)

    # ─── lottery_record ───────────────────────────────────

    # ─── redeem_items ─────────────────────────────────────

    async def get_active_items(self):
        return await self._db.fetchall(
            "SELECT * FROM redeem_items WHERE is_active=1 ORDER BY id"
        )

    async def get_item(self, item_id: int):
        return await self._db.fetchone(
            "SELECT * FROM redeem_items WHERE id=?", (item_id,)
        )

    async def add_item(self, name: str, cost: int, stock: int, desc: str = ""):
        cur = await self._db.execute(
            "INSERT INTO redeem_items (name, cost, stock, description) VALUES (?, ?, ?, ?)",
            (name, cost, stock, desc),
        )
        try:
            return cur.lastrowid
        finally:
            await cur.close()

    async def soft_delete_item(self, item_id: int):
        await self._db.execute(
            "UPDATE redeem_items SET is_active=0, updated_at=datetime('now','localtime') WHERE id=?",
            (item_id,),
        )

    async def update_item_field(self, item_id: int, field: str, value):
        allowed = {
            "name",
            "cost",
            "stock",
            "description",
            "discount_price",
            "discount_end_time",
        }
        if field not in allowed:
            raise ValueError(f"Field '{field}' not allowed")
        sql = f"UPDATE redeem_items SET {field}=?, updated_at=datetime('now','localtime') WHERE id=?"
        await self._db.execute(sql, (value, item_id))

    # ─── redeem_records ───────────────────────────────────

    async def get_redeem_record(self, record_no: str):
        return await self._db.fetchone(
            "SELECT * FROM redeem_records WHERE record_no=?", (record_no,)
        )

    async def get_redeem_records_by_user(
        self,
        qq: str,
        group_id: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ):
        if group_id:
            return await self._db.fetchall(
                "SELECT * FROM redeem_records WHERE qq=? AND group_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (qq, group_id, limit, offset),
            )
        return await self._db.fetchall(
            "SELECT * FROM redeem_records WHERE qq=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (qq, limit, offset),
        )

    async def get_redeem_records_all(
        self,
        status: str | None = None,
        group_id: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ):
        conditions = []
        params = []
        if status:
            conditions.append("status=?")
            params.append(status)
        if group_id:
            conditions.append("group_id=?")
            params.append(group_id)
        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM redeem_records WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return await self._db.fetchall(sql, params)

    async def toggle_redeem_status(self, record_no: str, admin_qq: str, note: str = ""):
        record = await self.get_redeem_record(record_no)
        if not record:
            return None
        now = self._now_str()
        if record["status"] == "pending":
            await self._db.execute(
                "UPDATE redeem_records SET status='verified', verified_at=?, verified_by=?, admin_note=? WHERE record_no=?",
                (now, admin_qq, note, record_no),
            )
            return "verified"
        await self._db.execute(
            "UPDATE redeem_records SET status='pending', verified_at=NULL, verified_by=NULL, admin_note=? WHERE record_no=?",
            (note, record_no),
        )
        return "pending"

    # ─── admins ───────────────────────────────────────────

    async def is_admin(self, qq: str, group_id: str | None = None) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 FROM admins WHERE qq=? AND (group_id IS NULL OR group_id=?) LIMIT 1",
            (qq, group_id or ""),
        )
        return row is not None

    async def add_admin(self, qq: str, added_by: str, group_id: str | None = None):
        await self._db.execute(
            "INSERT OR IGNORE INTO admins (qq, group_id, added_by) VALUES (?, ?, ?)",
            (qq, group_id, added_by),
        )

    async def remove_admin(self, qq: str, group_id: str | None = None):
        if group_id:
            await self._db.execute(
                "DELETE FROM admins WHERE qq=? AND group_id=?", (qq, group_id)
            )
        else:
            await self._db.execute("DELETE FROM admins WHERE qq=?", (qq,))

    # ─── date_rewards ─────────────────────────────────────

    async def get_active_date_rewards(self):
        return await self._db.fetchall("SELECT * FROM date_rewards WHERE is_active=1")

    async def get_all_date_rewards(self):
        return await self._db.fetchall(
            "SELECT * FROM date_rewards ORDER BY start_date, id"
        )

    async def add_date_reward(
        self, start_date: str, end_date, keyword: str, points: int, probability: float
    ):
        cur = await self._db.execute(
            "INSERT INTO date_rewards (start_date, end_date, keyword, points, probability, description) VALUES (?, ?, ?, ?, ?, '')",
            (start_date, end_date, keyword, points, probability),
        )
        try:
            return cur.lastrowid
        finally:
            await cur.close()

    async def soft_delete_date_reward(self, reward_id: int):
        await self._db.execute(
            "UPDATE date_rewards SET is_active=0 WHERE id=?", (reward_id,)
        )

    # ─── easter_events ────────────────────────────────────

    async def get_active_easter_events(self):
        return await self._db.fetchall("SELECT * FROM easter_events WHERE is_active=1")

    # ─── birthday_announce_log ─────────────────────────────

    async def was_birthday_announced(self, group_id: str, announce_date: str) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 FROM birthday_announce_log WHERE group_id=? AND announce_date=? LIMIT 1",
            (group_id, announce_date),
        )
        return row is not None

    async def mark_birthday_announced(
        self, group_id: str, announce_date: str, qq_list: str
    ):
        await self._db.execute(
            "INSERT OR IGNORE INTO birthday_announce_log (group_id, announce_date, announced_qqs) VALUES (?, ?, ?)",
            (group_id, announce_date, qq_list),
        )

    # ─── daily_keyword ────────────────────────────────────

    async def get_daily_keyword(self, group_id: str, set_date: str):
        return await self._db.fetchone(
            "SELECT * FROM daily_keyword WHERE group_id=? AND set_date=?",
            (group_id, set_date),
        )

    async def set_daily_keyword(
        self, group_id: str, keyword: str, points: int, set_by: str
    ):
        today = self._today_str()
        # 使用 UPSERT 而非 INSERT OR REPLACE：REPLACE 会先 DELETE 旧行，
        # 已被领取的 daily_keyword_claim 存在外键引用会导致约束失败
        await self._db.execute(
            "INSERT INTO daily_keyword (group_id, keyword, points, set_by, set_date) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(group_id, set_date) DO UPDATE SET "
            "keyword=excluded.keyword, points=excluded.points, set_by=excluded.set_by",
            (group_id, keyword, points, set_by, today),
        )

    async def clear_daily_keyword(self, group_id: str):
        today = self._today_str()

        async def _tx(conn):
            # 先删领取记录再删口令，避免外键约束失败；两条 DELETE 同事务，中途失败整体回滚
            await conn.execute(
                "DELETE FROM daily_keyword_claim WHERE kw_id IN "
                "(SELECT id FROM daily_keyword WHERE group_id=? AND set_date=?)",
                (group_id, today),
            )
            await conn.execute(
                "DELETE FROM daily_keyword WHERE group_id=? AND set_date=?",
                (group_id, today),
            )

        await self._db.execute_transaction(_tx)

    async def has_claimed_daily_keyword(self, kw_id: int, qq: str) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 FROM daily_keyword_claim WHERE kw_id=? AND qq=? LIMIT 1",
            (kw_id, qq),
        )
        return row is not None

    # ─── plugin_config ────────────────────────────────────

    async def set_config(self, key: str, value: str):
        await self._db.execute(
            "INSERT OR REPLACE INTO plugin_config (key, value, updated_at) VALUES (?, ?, datetime('now','localtime'))",
            (key, value),
        )

    async def get_all_config(self):
        return await self._db.fetchall("SELECT * FROM plugin_config ORDER BY key")

    async def clear_config(self):
        await self._db.execute("DELETE FROM plugin_config")
