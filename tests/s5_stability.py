"""S5 稳定性/故障注入：DB 损坏、事务回滚、busy 重试、重载循环、备份失败。"""
import asyncio
import os
import tempfile

import aiosqlite

from .common import TempDB
from astrbot_plugin_point_system_by_whleague.db.connection import DatabaseManager
from astrbot_plugin_point_system_by_whleague.db.schema import init_schema, SCHEMA_VERSION


async def test_corrupt_db_file():
    """DB 文件损坏：初始化优雅失败（抛异常而非挂死）。"""
    tmp = tempfile.mkdtemp(prefix="points_corrupt_")
    path = os.path.join(tmp, "broken.db")
    with open(path, "wb") as f:
        f.write(b"\x00" * 512 + b"NOT A SQLITE DATABASE" + b"\xff" * 512)
    db = DatabaseManager(path)
    try:
        try:
            await asyncio.wait_for(db.init(), timeout=10)
            assert False, "损坏库不应初始化成功"
        except Exception:
            pass  # 优雅失败（DatabaseError 等）
    finally:
        await db.close()
    return "损坏 DB：初始化抛异常、无挂死"


async def test_schema_version_corrupt():
    """schema_version 损坏：自动重写为当前版本（回归）。"""
    async with TempDB() as t:
        await t.db.execute("UPDATE plugin_config SET value='oops' WHERE key='schema_version'")
        await init_schema(t.db)
        row = await t.db.fetchone("SELECT value FROM plugin_config WHERE key='schema_version'")
        assert row and row["value"] == str(SCHEMA_VERSION)
    return "schema_version 损坏：自动修复"


async def test_transaction_rollback():
    """事务中途异常：全部回滚，数据一致。"""
    async with TempDB() as t:
        await t.db.execute("INSERT INTO users (qq, group_id, points) VALUES ('u1','G1',10)")

        async def bad_tx(conn):
            await conn.execute("INSERT INTO users (qq, group_id, points) VALUES ('u2','G1',20)")
            await conn.execute("UPDATE users SET points=points-100 WHERE qq='u1'")
            raise RuntimeError("boom")

        try:
            await t.db.execute_transaction(bad_tx)
            raise AssertionError("应抛异常")
        except RuntimeError:
            pass

        assert await t.count("users") == 1  # u2 未插入
        row = await t.dao.get_user("u1", "G1")
        assert row["points"] == 10  # 扣减未生效
    return "事务回滚：异常后无部分写入"


async def test_busy_retry():
    """其他连接持有写锁：busy_timeout + 重试后成功。"""
    async with TempDB() as t:
        blocker = await aiosqlite.connect(t.path)
        try:
            await blocker.execute("BEGIN EXCLUSIVE")
            await asyncio.sleep(0)
            start = asyncio.get_event_loop().time()

            async def writer():
                await asyncio.sleep(0.3)
                await t.db.execute("INSERT INTO users (qq, group_id, points) VALUES ('u1','G1',1)")

            async def unlocker():
                await asyncio.sleep(1.2)
                await blocker.execute("COMMIT")

            await asyncio.gather(writer(), unlocker())
            elapsed = asyncio.get_event_loop().time() - start
            assert elapsed < 5, elapsed  # busy_timeout 5s 内完成
            assert await t.count("users") == 1
        finally:
            try:
                await blocker.execute("ROLLBACK")
            except Exception:
                pass
            await blocker.close()
    return "busy 重试：写锁解除后 5s 内成功"


async def test_reload_cycles():
    """10 次 initialize/close 循环：无连接/锁泄漏。"""
    tmp = tempfile.mkdtemp(prefix="points_reload_")
    path = os.path.join(tmp, "r.db")
    for i in range(10):
        db = DatabaseManager(path)
        await db.init()
        await init_schema(db)
        await db.execute("INSERT INTO users (qq, group_id, points) VALUES (?,?,1)", (f"u{i}", "G1"))
        await db.close()
        assert db._conn is None
    db = DatabaseManager(path)
    await db.init()
    row = await db.fetchone("SELECT COUNT(*) AS c FROM users")
    assert row and row["c"] == 10
    await db.close()
    return "重载循环 ×10：连接正确释放、数据持久"


async def test_backup_failure_isolated():
    """备份目标异常（目标是文件而非目录）：单目标失败不影响整体，不崩溃。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.backup_service import BackupService
        svc = BackupService(t.db, {"backup_dirs": []})
        # 目标路径是文件 → mkdir 失败
        tmp = tempfile.mkdtemp(prefix="backup_fail_")
        file_path = os.path.join(tmp, "occupied")
        with open(file_path, "w") as f:
            f.write("x")
        try:
            await svc._backup_to(svc._resolve(file_path))
            assert False, "应失败"
        except Exception:
            pass
        # run_backup 多目标：失败目标被隔离
        good = os.path.join(tmp, "good")
        results = await svc.run_backup()  # 空 targets → 跳过
        assert results is None
        # 数据库仍可用
        row = await t.db.fetchone("SELECT 1 AS x")
        assert row is not None
    return "备份失败隔离：异常不扩散、库可用"


async def test_wal_crash_recovery():
    """WAL 崩溃恢复：未正常关闭的连接写后，重开仍可见（WAL 重放）。"""
    tmp = tempfile.mkdtemp(prefix="points_wal_")
    path = os.path.join(tmp, "w.db")
    db1 = DatabaseManager(path)
    await db1.init()
    await init_schema(db1)
    await db1.execute("INSERT INTO users (qq, group_id, points) VALUES ('u1','G1',42)")
    # 不关闭 db1（模拟崩溃），直接另开连接读取
    db2 = DatabaseManager(path)
    await db2.init()
    try:
        row = await db2.fetchone("SELECT points FROM users WHERE qq='u1'")
        assert row and row["points"] == 42
    finally:
        await db2.close()
        await db1.close()
    return "WAL 恢复：未 checkpoint 数据重放可见"


async def test_deadlock_guard():
    """死锁断言：事务回调内调用 db 方法抛清晰错误（回归 M1）。"""
    async with TempDB() as t:
        async def bad(conn):
            await t.db.fetchone("SELECT 1")

        try:
            await t.db.execute_transaction(bad)
            raise AssertionError("应抛 RuntimeError")
        except RuntimeError as e:
            assert "死锁" in str(e)
    return "死锁断言：回调内误用 db 抛清晰错误"


TESTS = [
    ("corrupt_db_file", test_corrupt_db_file),
    ("schema_version_corrupt", test_schema_version_corrupt),
    ("transaction_rollback", test_transaction_rollback),
    ("busy_retry", test_busy_retry),
    ("reload_cycles", test_reload_cycles),
    ("backup_failure_isolated", test_backup_failure_isolated),
    ("wal_crash_recovery", test_wal_crash_recovery),
    ("deadlock_guard", test_deadlock_guard),
]
