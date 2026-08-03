import os
from datetime import datetime
from pathlib import Path

from astrbot.api import logger


class BackupService:
    def __init__(self, db, config_cache: dict):
        self._db = db
        self._config_cache = config_cache

    async def run_backup(self):
        targets = self._config_cache.get("backup_dirs", [])
        if not targets:
            logger.info("No backup dirs configured, skipping.")
            return

        for target in targets:
            try:
                dst_dir = self._resolve(target)
                await self._backup_to(dst_dir)
                logger.info(f"Backup completed: {self._db.db_path} -> {dst_dir}")
            except Exception as e:
                logger.error(f"Backup to {target} failed: {e}")

    def _resolve(self, target: str) -> Path:
        """Resolve a backup directory.

        Absolute paths are used as-is; relative paths are resolved against
        the database directory so the plugin works on any deployment
        (e.g. cloud servers where the working directory is not stable).

        Args:
            target: Backup directory as configured.

        Returns:
            Resolved backup directory path.
        """
        p = Path(os.path.expanduser(target.strip()))
        if not p.is_absolute():
            p = Path(self._db.db_path).parent / p
        return p

    async def _backup_to(self, dst_dir: Path):
        """通过 VACUUM INTO 生成数据库一致快照（含 WAL 数据），避免复制不完整。

        目标文件已存在时（同日多次备份/时钟回拨）自动追加序号，避免失败或覆盖。
        """
        await self.backup_unique(dst_dir)

    async def backup_unique(self, dst_dir: Path, tag: str = "") -> Path:
        """生成唯一文件名的快照备份并返回目标路径。

        Args:
            dst_dir: 目标目录。
            tag: 文件名附加标签（如 before_clear）。

        Returns:
            实际写入的备份文件路径。
        """
        dst_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"_{tag}" if tag else ""
        dst = dst_dir / f"points_system_{ts}{suffix}.db"
        n = 2
        while dst.exists():
            dst = dst_dir / f"points_system_{ts}{suffix}_{n}.db"
            n += 1
        escaped = str(dst).replace("'", "''")
        await self._db.execute(f"VACUUM INTO '{escaped}'")
        self._prune_old_backups(dst_dir)
        return dst

    def _prune_old_backups(self, dst_dir: Path) -> None:
        """保留最近 N 份备份，超出删除最旧（0/未配置表示不清理）。

        Args:
            dst_dir: 备份目录（只清理本插件命名的 points_system_*.db）。
        """
        keep = int(self._config_cache.get("backup_keep_count", 30) or 0)
        if keep <= 0:
            return
        try:
            files = sorted(
                dst_dir.glob("points_system_*.db"),
                key=lambda p: p.stat().st_mtime,
            )
        except OSError as e:
            logger.warning(f"Failed to list backups for pruning: {e}")
            return
        for stale in files[:-keep]:
            try:
                stale.unlink(missing_ok=True)
                logger.info(f"Pruned old backup: {stale}")
            except OSError as e:
                logger.warning(f"Failed to prune backup {stale}: {e}")
