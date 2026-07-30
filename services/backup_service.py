import os
import shutil
from datetime import datetime
from astrbot.api import logger


class BackupService:
    def __init__(self, db, dao):
        self._db = db
        self._dao = dao

    async def run_backup(self):
        targets = await self._dao.get_active_backup_configs()
        if not targets:
            logger.info("No backup targets configured, skipping.")
            return

        src = self._db.conn.database
        try:
            await self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            logger.warning(f"WAL checkpoint failed: {e}")

        for t in targets:
            try:
                self._backup_to(src, t["target_path"])
                await self._dao.update_backup_time(t["id"])
                logger.info(f"Backup completed: {src} -> {t['target_path']}")
            except Exception as e:
                logger.error(f"Backup to {t['target_path']} failed: {e}")

    def _backup_to(self, src: str, dst_dir: str):
        os.makedirs(dst_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(dst_dir, f"points_system_{ts}.db")
        shutil.copy2(src, dst)
