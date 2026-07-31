import os
import asyncio
import aiosqlite
from typing import Optional
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
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = _default_db_path()
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA cache_size=-8000")
        self._conn = conn
        logger.info(f"Database opened: {self._db_path} (WAL mode)")

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
        async with self._lock:
            cur = await self.conn.execute(sql, params)
            return await cur.fetchone()

    async def fetchall(self, sql: str, params=()):
        async with self._lock:
            cur = await self.conn.execute(sql, params)
            return await cur.fetchall()

    async def execute_transaction(self, coro):
        async with self._lock:
            conn = self.conn
            for attempt in range(_RETRY_COUNT):
                try:
                    await conn.execute("BEGIN IMMEDIATE")
                    result = await coro(conn)
                    await conn.commit()
                    return result
                except aiosqlite.OperationalError as e:
                    await conn.rollback()
                    if "database is locked" in str(e) and attempt < _RETRY_COUNT - 1:
                        await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
                        continue
                    raise
                except BaseException:
                    await conn.rollback()
                    raise

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed.")
