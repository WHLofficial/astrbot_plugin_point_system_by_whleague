import os
import asyncio
import aiosqlite
from astrbot.api import logger

_RETRY_COUNT = 3
_RETRY_DELAY = 0.05


class DatabaseManager:
    def __init__(self, db_path: str = None):
        if db_path is None:
            base = os.path.join(os.getcwd(), "data")
            os.makedirs(base, exist_ok=True)
            db_path = os.path.join(base, "points_system.db")
        self._db_path = db_path
        self._conn: aiosqlite.Connection = None
        self._lock = asyncio.Lock()

    async def init(self):
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA cache_size=-8000")
        logger.info(f"Database opened: {self._db_path} (WAL mode)")

    @property
    def conn(self) -> aiosqlite.Connection:
        return self._conn

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    async def execute(self, sql: str, params=()):
        async with self._lock:
            for attempt in range(_RETRY_COUNT):
                try:
                    cur = await self._conn.execute(sql, params)
                    await self._conn.commit()
                    return cur
                except aiosqlite.OperationalError as e:
                    if "database is locked" in str(e) and attempt < _RETRY_COUNT - 1:
                        await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
                        continue
                    raise

    async def execute_many(self, sql: str, params_list):
        async with self._lock:
            for attempt in range(_RETRY_COUNT):
                try:
                    await self._conn.executemany(sql, params_list)
                    await self._conn.commit()
                    return
                except aiosqlite.OperationalError as e:
                    if "database is locked" in str(e) and attempt < _RETRY_COUNT - 1:
                        await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
                        continue
                    raise

    async def fetchone(self, sql: str, params=()):
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            return await cur.fetchone()

    async def fetchall(self, sql: str, params=()):
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            return await cur.fetchall()

    async def execute_transaction(self, coro):
        async with self._lock:
            for attempt in range(_RETRY_COUNT):
                try:
                    await self._conn.execute("BEGIN IMMEDIATE")
                    result = await coro(self._conn)
                    await self._conn.commit()
                    return result
                except aiosqlite.OperationalError as e:
                    await self._conn.rollback()
                    if "database is locked" in str(e) and attempt < _RETRY_COUNT - 1:
                        await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
                        continue
                    raise
                except BaseException:
                    await self._conn.rollback()
                    raise

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed.")
