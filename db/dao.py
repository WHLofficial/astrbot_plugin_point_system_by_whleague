from datetime import datetime, date
from typing import Optional


class PointDAO:
    def __init__(self, db_manager):
        self._db = db_manager

    # ─── helpers ──────────────────────────────────────────

    def _today_str(self) -> str:
        return date.today().isoformat()

    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ─── users ────────────────────────────────────────────

    async def get_user(self, qq: str, group_id: str):
        sql = "SELECT * FROM users WHERE qq=? AND group_id=?"
        return await self._db.fetchone(sql, (qq, group_id))

    async def create_user(self, qq: str, group_id: str, platform: str = ""):
        sql = "INSERT OR IGNORE INTO users (qq, group_id, platform) VALUES (?, ?, ?)"
        cur = await self._db.execute(sql, (qq, group_id, platform))
        return cur.lastrowid

    async def ensure_user(self, qq: str, group_id: str, platform: str = ""):
        user = await self.get_user(qq, group_id)
        if user:
            if platform and user["platform"] != platform:
                await self._db.execute(
                    "UPDATE users SET platform=? WHERE qq=? AND group_id=?",
                    (platform, qq, group_id),
                )
            return user
        await self.create_user(qq, group_id, platform)
        return await self.get_user(qq, group_id)

    async def update_points(self, qq: str, group_id: str, delta: int, add_to_earned: bool = False):
        if delta >= 0 and add_to_earned:
            sql = "UPDATE users SET points=points+?, total_earned=total_earned+?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?"
            await self._db.execute(sql, (delta, delta, qq, group_id))
        else:
            sql = "UPDATE users SET points=points+?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?"
            await self._db.execute(sql, (delta, qq, group_id))

    async def get_user_balance(self, qq: str, group_id: str) -> int:
        row = await self._db.fetchone(
            "SELECT points FROM users WHERE qq=? AND group_id=?", (qq, group_id)
        )
        return row["points"] if row else 0

    async def update_signin_state(
        self, qq: str, group_id: str, today: str, consecutive: int, total_days: int
    ):
        sql = """
        UPDATE users SET last_sign_date=?, consecutive_days=?, total_sign_days=?,
            max_consecutive_days = MAX(max_consecutive_days, ?),
            updated_at=datetime('now','localtime')
        WHERE qq=? AND group_id=?
        """
        await self._db.execute(sql, (today, consecutive, total_days, consecutive, qq, group_id))

    async def update_pity(self, qq: str, group_id: str, lucky: int, unlucky: int):
        sql = """
        UPDATE users SET lucky_pity=?, unlucky_pity=?, updated_at=datetime('now','localtime')
        WHERE qq=? AND group_id=?
        """
        await self._db.execute(sql, (lucky, unlucky, qq, group_id))

    async def update_birthday_bonus_claimed(self, qq: str, group_id: str, claimed: int):
        await self._db.execute(
            "UPDATE users SET birthday_bonus_claimed=?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
            (claimed, qq, group_id),
        )

    async def set_negative_title(self, qq: str, group_id: str, title_id: Optional[int]):
        await self._db.execute(
            "UPDATE users SET negative_title_id=?, updated_at=datetime('now','localtime') WHERE qq=? AND group_id=?",
            (title_id, qq, group_id),
        )

    async def get_used_title_ids(self, group_id: str):
        rows = await self._db.fetchall(
            "SELECT negative_title_id FROM users WHERE group_id=? AND negative_title_id IS NOT NULL",
            (group_id,),
        )
        return {r["negative_title_id"] for r in rows}

    async def get_top_n_by_group(self, group_id: str, n: int, min_points: int = 1):
        return await self._db.fetchall(
            "SELECT qq, points, total_earned, consecutive_days FROM users WHERE group_id=? AND points>=? ORDER BY points DESC LIMIT ?",
            (group_id, min_points, n),
        )

    async def get_top_n_global(self, n: int, min_points: int = 1):
        return await self._db.fetchall(
            "SELECT qq, group_id, points, total_earned FROM users WHERE points>=? ORDER BY points DESC LIMIT ?",
            (min_points, n),
        )

    async def count_users_in_group(self, group_id: str) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS cnt FROM users WHERE group_id=?", (group_id,)
        )
        return row["cnt"] if row else 0

    async def get_birthday_users(self, group_id: str, today_mmdd: str):
        return await self._db.fetchall(
            "SELECT qq FROM users WHERE group_id=? AND birthday=?", (group_id, today_mmdd)
        )

    async def get_negative_users_without_title(self):
        return await self._db.fetchall(
            "SELECT qq, group_id FROM users WHERE points<0 AND negative_title_id IS NULL"
        )

    async def get_negative_users_in_group(self, group_id: str):
        return await self._db.fetchall(
            "SELECT qq, points, negative_title_id FROM users WHERE group_id=? AND points<0",
            (group_id,),
        )

    async def get_all_group_ids(self):
        rows = await self._db.fetchall("SELECT DISTINCT group_id FROM users")
        return [r["group_id"] for r in rows]

    # ─── sign_in_log ──────────────────────────────────────

    async def insert_sign_in_log(
        self, qq: str, group_id: str, sign_date: str, base: int,
        bonus_first: int, bonus_day_first: int, bonus_consecutive: int,
        bonus_weekly: int, easter_type: Optional[str], easter_pts: int,
        total: int,
    ):
        sql = """
        INSERT OR IGNORE INTO sign_in_log
            (qq, group_id, sign_date, points_earned, base_points,
             bonus_first_sign, bonus_day_first, bonus_consecutive, bonus_weekly,
             easter_event_type, easter_points)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        await self._db.execute(sql, (
            qq, group_id, sign_date, total, base,
            bonus_first, bonus_day_first, bonus_consecutive, bonus_weekly,
            easter_type, easter_pts,
        ))

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
            """SELECT u.qq, u.consecutive_days FROM users u
               INNER JOIN sign_in_log s ON u.qq=s.qq AND u.group_id=s.group_id
               WHERE u.group_id=? AND s.sign_date=?
               ORDER BY u.consecutive_days DESC LIMIT 1""",
            (group_id, self._today_str()),
        )

    # ─── point_transactions ────────────────────────────────

    async def insert_transaction(
        self, qq: str, group_id: str, amount: int,
        balance_after: int, reason: str, ref_id: int = None,
    ):
        await self._db.execute(
            "INSERT INTO point_transactions (qq, group_id, amount, balance_after, reason, ref_id) VALUES (?, ?, ?, ?, ?, ?)",
            (qq, group_id, amount, balance_after, reason, ref_id),
        )

    async def get_transactions(
        self, qq: str = None, group_id: str = None,
        limit: int = 10, offset: int = 0,
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

    async def insert_lottery(
        self, qq: str, group_id: str, cost: int,
        reward: int, is_win: int, tier_label: str,
    ):
        await self._db.execute(
            "INSERT INTO lottery_record (qq, group_id, cost, reward_amount, is_win, tier_label) VALUES (?, ?, ?, ?, ?, ?)",
            (qq, group_id, cost, reward, is_win, tier_label),
        )

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
        return cur.lastrowid

    async def soft_delete_item(self, item_id: int):
        await self._db.execute(
            "UPDATE redeem_items SET is_active=0, updated_at=datetime('now','localtime') WHERE id=?",
            (item_id,),
        )

    async def update_item_field(self, item_id: int, field: str, value):
        allowed = {"name", "cost", "stock", "description", "discount_price", "discount_end_time"}
        if field not in allowed:
            raise ValueError(f"Field '{field}' not allowed")
        sql = f"UPDATE redeem_items SET {field}=?, updated_at=datetime('now','localtime') WHERE id=?"
        await self._db.execute(sql, (value, item_id))

    async def deduct_stock(self, item_id: int, quantity: int) -> bool:
        cur = await self._db.execute(
            "UPDATE redeem_items SET stock=stock-? WHERE id=? AND (stock=-1 OR stock>=?)",
            (quantity, item_id, quantity),
        )
        return cur.rowcount > 0

    # ─── redeem_records ───────────────────────────────────

    async def get_record_count_by_prefix(self, prefix: str) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS cnt FROM redeem_records WHERE record_no LIKE ?",
            (f"{prefix}%",),
        )
        return row["cnt"] if row else 0

    async def insert_redeem_record(
        self, record_no: str, qq: str, group_id: str,
        item_id: int, item_name: str, item_cost: int, quantity: int,
    ):
        await self._db.execute(
            "INSERT INTO redeem_records (record_no, qq, group_id, item_id, item_name, item_cost, quantity) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record_no, qq, group_id, item_id, item_name, item_cost, quantity),
        )

    async def get_redeem_record(self, record_no: str):
        return await self._db.fetchone(
            "SELECT * FROM redeem_records WHERE record_no=?", (record_no,)
        )

    async def get_redeem_records_by_user(
        self, qq: str, limit: int = 10, offset: int = 0,
    ):
        return await self._db.fetchall(
            "SELECT * FROM redeem_records WHERE qq=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (qq, limit, offset),
        )

    async def get_redeem_records_all(
        self, status: str = None, limit: int = 10, offset: int = 0,
    ):
        if status:
            return await self._db.fetchall(
                "SELECT * FROM redeem_records WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        return await self._db.fetchall(
            "SELECT * FROM redeem_records ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )

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

    async def is_admin(self, qq: str, group_id: str = None) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 FROM admins WHERE qq=? AND (group_id IS NULL OR group_id=?) LIMIT 1",
            (qq, group_id or ""),
        )
        return row is not None

    async def add_admin(self, qq: str, added_by: str, group_id: str = None):
        await self._db.execute(
            "INSERT OR IGNORE INTO admins (qq, group_id, added_by) VALUES (?, ?, ?)",
            (qq, group_id, added_by),
        )

    async def remove_admin(self, qq: str, group_id: str = None):
        if group_id:
            await self._db.execute(
                "DELETE FROM admins WHERE qq=? AND group_id=?", (qq, group_id)
            )
        else:
            await self._db.execute("DELETE FROM admins WHERE qq=?", (qq,))

    # ─── date_rewards ─────────────────────────────────────

    async def get_active_date_rewards(self):
        return await self._db.fetchall(
            "SELECT * FROM date_rewards WHERE is_active=1"
        )

    # ─── easter_events ────────────────────────────────────

    async def get_active_easter_events(self):
        return await self._db.fetchall(
            "SELECT * FROM easter_events WHERE is_active=1"
        )

    # ─── backup_configs ───────────────────────────────────

    async def get_active_backup_configs(self):
        return await self._db.fetchall(
            "SELECT * FROM backup_configs WHERE is_active=1"
        )

    async def update_backup_time(self, cfg_id: int):
        await self._db.execute(
            "UPDATE backup_configs SET last_backup_time=datetime('now','localtime') WHERE id=?",
            (cfg_id,),
        )

    # ─── birthday_announce_log ─────────────────────────────

    async def was_birthday_announced(self, group_id: str, announce_date: str) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 FROM birthday_announce_log WHERE group_id=? AND announce_date=? LIMIT 1",
            (group_id, announce_date),
        )
        return row is not None

    async def mark_birthday_announced(self, group_id: str, announce_date: str, qq_list: str):
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

    async def set_daily_keyword(self, group_id: str, keyword: str, points: int, set_by: str):
        today = self._today_str()
        await self._db.execute(
            "INSERT OR REPLACE INTO daily_keyword (group_id, keyword, points, set_by, set_date) VALUES (?, ?, ?, ?, ?)",
            (group_id, keyword, points, set_by, today),
        )

    async def clear_daily_keyword(self, group_id: str):
        today = self._today_str()
        await self._db.execute(
            "DELETE FROM daily_keyword WHERE group_id=? AND set_date=?", (group_id, today)
        )

    async def has_claimed_daily_keyword(self, kw_id: int, qq: str) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 FROM daily_keyword_claim WHERE kw_id=? AND qq=? LIMIT 1",
            (kw_id, qq),
        )
        return row is not None

    async def claim_daily_keyword(self, kw_id: int, qq: str, group_id: str, points: int):
        await self._db.execute(
            "INSERT OR IGNORE INTO daily_keyword_claim (kw_id, qq, group_id, points_earned) VALUES (?, ?, ?, ?)",
            (kw_id, qq, group_id, points),
        )

    # ─── plugin_config ────────────────────────────────────

    async def get_config(self, key: str) -> Optional[str]:
        row = await self._db.fetchone(
            "SELECT value FROM plugin_config WHERE key=?", (key,)
        )
        return row["value"] if row else None

    async def set_config(self, key: str, value: str):
        await self._db.execute(
            "INSERT OR REPLACE INTO plugin_config (key, value, updated_at) VALUES (?, ?, datetime('now','localtime'))",
            (key, value),
        )

    async def get_all_config(self):
        return await self._db.fetchall("SELECT * FROM plugin_config ORDER BY key")
