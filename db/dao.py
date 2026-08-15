from datetime import datetime

from ..utils.helpers import period_start_str, today_str


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

    async def get_rank_in_group(
        self, qq: str, group_id: str, min_points: int = 1
    ) -> int | None:
        """返回用户在本群积分榜的排名（1 起，同分同名次）。

        与排行榜口径一致：非本群成员或积分低于 min_points 时返回 None（未上榜）。
        """
        row = await self._db.fetchone(
            "SELECT a.points FROM users u JOIN accounts a ON a.qq=u.qq "
            "WHERE u.group_id=? AND u.qq=?",
            (group_id, qq),
        )
        if not row or row["points"] < min_points:
            return None
        rank_row = await self._db.fetchone(
            "SELECT COUNT(*)+1 AS rank FROM users u JOIN accounts a ON a.qq=u.qq "
            "WHERE u.group_id=? AND a.points > ?",
            (group_id, row["points"]),
        )
        return rank_row["rank"] if rank_row else 1

    async def get_rank_global(
        self, qq: str, min_points: int = 1
    ) -> tuple | None:
        """返回用户全局积分榜排名（1 起，同分同名次）。

        与 get_top_n_global 口径一致（accounts 全局 + 最近活跃群）：账户不存在
        或积分低于 min_points 时返回 None（未上榜）。
        """
        row = await self._db.fetchone(
            "SELECT a.points, "
            "(SELECT u2.group_id FROM users u2 WHERE u2.qq=a.qq "
            " ORDER BY u2.updated_at DESC, u2.id DESC LIMIT 1) AS group_id "
            "FROM accounts a WHERE a.qq=?",
            (qq,),
        )
        if not row or row["points"] < min_points:
            return None
        rank_row = await self._db.fetchone(
            "SELECT COUNT(*)+1 AS rank FROM accounts a WHERE a.points > ?",
            (row["points"],),
        )
        return (
            rank_row["rank"] if rank_row else 1,
            row["points"],
            row["group_id"],
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

    async def set_redeem_status(
        self, record_no: str, new_status: str, admin_qq: str, note: str = ""
    ):
        """设置兑换记录状态（verified/rejected），审计列互斥写入。

        Args:
            record_no: 记录编号。
            new_status: 目标状态，仅 verified / rejected。
            admin_qq: 操作管理员 QQ。
            note: 备注（覆盖原备注）。

        Returns:
            新状态字符串；记录不存在返回 None。
        """
        record = await self.get_redeem_record(record_no)
        if not record:
            return None
        now = self._now_str()
        if new_status == "rejected":
            await self._db.execute(
                "UPDATE redeem_records SET status='rejected', rejected_at=?, rejected_by=?, "
                "verified_at=NULL, verified_by=NULL, admin_note=? WHERE record_no=?",
                (now, admin_qq, note, record_no),
            )
            return "rejected"
        await self._db.execute(
            "UPDATE redeem_records SET status='verified', verified_at=?, verified_by=?, "
            "rejected_at=NULL, rejected_by=NULL, admin_note=? WHERE record_no=?",
            (now, admin_qq, note, record_no),
        )
        return "verified"

    async def restore_stock(self, item_id: int, quantity: int):
        """驳回时恢复物品库存；无限库存（-1）保持不变。"""
        await self._db.execute(
            "UPDATE redeem_items SET stock=CASE WHEN stock=-1 THEN -1 ELSE stock+? END "
            "WHERE id=?",
            (quantity, item_id),
        )

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

    # ─── rob_records ─────────────────────────────────────

    @staticmethod
    async def count_robs_today(conn, qq: str) -> int:
        """统计用户本业务日的打劫次数（必须在事务回调内用传入的 conn 调用）。

        与 lottery 每日限次口径一致：按 QQ 全局统计（跨群共享钱包），
        以 period_start_str() 为区间起点。
        """
        async with conn.execute(
            "SELECT COUNT(*) AS cnt FROM rob_records WHERE qq=? AND created_at>=?",
            (qq, period_start_str()),
        ) as cur:
            row = await cur.fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    async def target_robs_today(conn, target_qq: str) -> tuple[int, int]:
        """统计目标本业务日的打劫情况（必须在事务回调内用传入的 conn 调用）。

        Returns:
            (总次数, 成功次数)：总次数含失败（目标每日被劫上限用，跨群全局统计）；
            成功次数用于收益衰减（COALESCE 保证无记录时返回 0 而非 NULL）。
        """
        async with conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(success), 0) AS wins "
            "FROM rob_records WHERE target_qq=? AND created_at>=?",
            (target_qq, period_start_str()),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return 0, 0
        return row["total"], row["wins"]

    @staticmethod
    async def insert_rob_record(
        conn,
        qq: str,
        target_qq: str,
        group_id: str,
        cost: int,
        stolen: int,
        success: bool,
    ):
        """写入打劫记录（必须在事务回调内用传入的 conn 调用）。"""
        await conn.execute(
            "INSERT INTO rob_records (qq, target_qq, group_id, cost, stolen, success) "
            "VALUES (?,?,?,?,?,?)",
            (qq, target_qq, group_id, cost, stolen, 1 if success else 0),
        )

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
