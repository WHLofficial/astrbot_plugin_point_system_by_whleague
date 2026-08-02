import asyncio
import os

import aiosqlite

from astrbot.api import logger

_RETRY_COUNT = 3
_RETRY_DELAY = 0.05


def _default_db_path() -> str:
    """Resolve the plugin database path.

    Prefers the AstrBot plugin data directory; falls back to the AstrBot
    data directory, then the current working directory, so the plugin
    works both inside AstrBot and standalone.
    """
    base = None
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        base = get_astrbot_plugin_data_path()
    except Exception:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            base = get_astrbot_data_path()
        except Exception:
            base = os.getcwd()
    base = os.path.join(base, "astrbot_plugin_point_system_by_whleague")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "points_system.db")


class DatabaseManager:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = _default_db_path()
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._lock_owner: asyncio.Task | None = None
        """当前持有写锁的任务（仅用于检测事务回调内的重入调用）。"""

    async def init(self) -> None:
        conn = await aiosqlite.connect(self._db_path)
        try:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA cache_size=-8000")
        except BaseException:
            # 初始化失败时释放连接，避免 aiosqlite 非 daemon 工作线程阻塞进程退出
            await conn.close()
            raise
        self._conn = conn
        logger.info(f"Database opened: {self._db_path} (WAL mode)")

    def _ensure_lock_free(self) -> None:
        """仅拦截"当前任务自己持有写锁"的情况（事务回调内的重入调用）。

        并发任务等待锁是正常行为，不能误判（asyncio.Lock 不区分持有者，
        因此用 _lock_owner 追踪实际持锁任务）。
        """
        if self._lock_owner is asyncio.current_task():
            raise RuntimeError(
                "禁止在事务回调（execute_transaction 的 coro）内调用 db/dao 方法，"
                "否则会因重复获取同一把锁而死锁；请直接使用传入的 conn。"
            )

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not initialized"
        return self._conn

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    async def execute(self, sql: str, params=()):
        self._ensure_lock_free()
        async with self._lock:
            conn = self.conn
            for attempt in range(_RETRY_COUNT):
                try:
                    cur = await conn.execute(sql, params)
                    await conn.commit()
                    return cur
                except aiosqlite.OperationalError as e:
                    if "database is locked" in str(e) and attempt < _RETRY_COUNT - 1:
                        await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
                        continue
                    raise

    async def fetchone(self, sql: str, params=()):
        self._ensure_lock_free()
        async with self._lock:
            async with self.conn.execute(sql, params) as cur:
                return await cur.fetchone()

    async def fetchall(self, sql: str, params=()):
        self._ensure_lock_free()
        async with self._lock:
            async with self.conn.execute(sql, params) as cur:
                return await cur.fetchall()

    async def execute_transaction(self, coro):
        self._ensure_lock_free()
        async with self._lock:
            self._lock_owner = asyncio.current_task()
            try:
                conn = self.conn
                for attempt in range(_RETRY_COUNT):
                    try:
                        await conn.execute("BEGIN IMMEDIATE")
                        result = await coro(conn)
                        await conn.commit()
                        return result
                    except aiosqlite.OperationalError as e:
                        await conn.rollback()
                        if (
                            "database is locked" in str(e)
                            and attempt < _RETRY_COUNT - 1
                        ):
                            await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
                            continue
                        raise
                    except BaseException:
                        await conn.rollback()
                        raise
            finally:
                self._lock_owner = None

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed.")
