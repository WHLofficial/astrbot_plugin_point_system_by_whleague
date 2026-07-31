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
        """通过 VACUUM INTO 生成数据库一致快照（含 WAL 数据），避免复制不完整。"""
        dst_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = dst_dir / f"points_system_{ts}.db"
        escaped = str(dst).replace("'", "''")
        await self._db.execute(f"VACUUM INTO '{escaped}'")
