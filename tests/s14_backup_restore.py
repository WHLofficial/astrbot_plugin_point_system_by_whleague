"""S14 备份恢复：唯一序号、VACUUM 快照可重开且数据一致、多目录部分失败隔离、备份后源库可写。"""

import os
import tempfile
from pathlib import Path

import aiosqlite

from .common import TempDB


async def test_backup_unique_sequence():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.backup_service import (
            BackupService,
        )

        svc = BackupService(t.db, {"backup_dirs": []})
        dst = Path(tempfile.mkdtemp(prefix="bak_seq_"))
        p1 = await svc.backup_unique(dst)
        p2 = await svc.backup_unique(dst)
        assert p1.exists() and p2.exists()
        assert p1 != p2  # 同秒重复备份自动追加序号
        assert p2.name.endswith("_2.db"), p2.name
        # tag 生效
        p3 = await svc.backup_unique(dst, "before_clear")
        assert "before_clear" in p3.name
    return "备份：唯一序号递增/避免覆盖/tag 命名"


async def test_backup_restore_consistency():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.backup_service import (
            BackupService,
        )

        svc = BackupService(t.db, {"backup_dirs": []})
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',10)"
        )
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u2',20)"
        )
        dst = Path(tempfile.mkdtemp(prefix="bak_restore_"))
        bak = await svc.backup_unique(dst)
        # 备份后源库继续写入
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u3',30)"
        )
        # 快照不包含新写入（一致性快照）
        async with aiosqlite.connect(bak) as conn:
            conn.row_factory = aiosqlite.Row
            row = await (
                await conn.execute("SELECT COUNT(*) AS c FROM accounts")
            ).fetchone()
            assert row["c"] == 2
            # 数据内容一致
            row = await (
                await conn.execute("SELECT points FROM accounts WHERE qq='u2'")
            ).fetchone()
            assert row["points"] == 20
    return "备份恢复：快照重开数据一致、不包含备份后写入"


async def test_run_backup_partial_failure():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.backup_service import (
            BackupService,
        )

        base = tempfile.mkdtemp(prefix="bak_partial_")
        good = os.path.join(base, "good")
        bad_file = os.path.join(base, "occupied")
        with open(bad_file, "w") as f:
            f.write("x")
        svc = BackupService(t.db, {"backup_dirs": [good, bad_file]})
        await svc.run_backup()  # 单目标失败不抛异常
        assert os.path.isdir(good)
        assert any(f.endswith(".db") for f in os.listdir(good))
        # 库仍可用
        row = await t.db.fetchone("SELECT 1 AS x")
        assert row is not None
    return "备份：多目录部分失败隔离、成功目标照常写入"


async def test_source_writable_after_backup():
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.backup_service import (
            BackupService,
        )

        svc = BackupService(t.db, {"backup_dirs": []})
        await svc.run_backup()  # 空 targets 跳过不报错
        await svc.backup_unique(Path(tempfile.mkdtemp(prefix="bak_wr_")))
        await t.db.execute(
            "INSERT INTO accounts (qq, points) VALUES ('u1',1)"
        )
        assert await t.count("accounts") == 1
    return "备份：空目录跳过、备份后源库可写"


async def test_backup_keep_count():
    """备份保留 N 份：超出删除最旧；0/未配置不清理。"""
    async with TempDB() as t:
        from astrbot_plugin_point_system_by_whleague.services.backup_service import (
            BackupService,
        )

        dst = Path(tempfile.mkdtemp(prefix="bak_keep_"))
        svc = BackupService(t.db, {"backup_keep_count": 2})
        p1 = await svc.backup_unique(dst)
        p2 = await svc.backup_unique(dst)
        p3 = await svc.backup_unique(dst)
        files = sorted(dst.glob("points_system_*.db"))
        assert len(files) == 2, [f.name for f in files]
        assert p3 in files and p2 in files
        assert p1 not in files  # 最旧被清理
        # 0 = 不清理
        svc0 = BackupService(t.db, {"backup_keep_count": 0})
        await svc0.backup_unique(dst)
        assert len(list(dst.glob("points_system_*.db"))) == 3
        # 未配置键（旧缓存）默认 30 份，不会误删现有备份
        svc_legacy = BackupService(t.db, {})
        await svc_legacy.backup_unique(dst)
        assert len(list(dst.glob("points_system_*.db"))) == 4
    return "备份：保留 N 份清理最旧、0/缺省不清理"


TESTS = [
    ("backup_unique_sequence", test_backup_unique_sequence),
    ("backup_restore_consistency", test_backup_restore_consistency),
    ("backup_partial_failure", test_run_backup_partial_failure),
    ("backup_source_writable", test_source_writable_after_backup),
    ("backup_keep_count", test_backup_keep_count),
]
