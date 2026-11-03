"""Execution lock providing cross-process concurrency control with stale-lock TTL."""

import os
import time
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import settings


class LockAcquisitionError(Exception):
    """Raised when an execution lock cannot be acquired due to an active concurrent run."""
    pass


class ExecutionLock:
    """File-backed process mutex lock with PID, hostname, and TTL expiration.
    
    Prevents multiple scheduled jobs or overlapping runners from executing simultaneously
    and producing race conditions.
    """

    def __init__(self, lock_file: Optional[Path] = None, timeout_seconds: int = 600):
        self.lock_file = lock_file or (Path(settings.DATABASE_URL.replace("sqlite:///", "")).parent / ".pipeline.lock"
                                      if settings.DATABASE_URL.startswith("sqlite")
                                      else Path(".pipeline.lock"))
        self.timeout_seconds = timeout_seconds
        self._acquired = False

    def acquire(self) -> bool:
        """Attempt to acquire the execution lock.
        
        Returns True if acquired. Raises LockAcquisitionError if active lock exists.
        """
        now = time.time()
        pid = os.getpid()
        hostname = socket.gethostname()

        if self.lock_file.exists():
            try:
                content = self.lock_file.read_text(encoding="utf-8").strip()
                lock_data = {}
                for line in content.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        lock_data[k.strip()] = v.strip()
                
                lock_time = float(lock_data.get("timestamp", 0))
                lock_pid = lock_data.get("pid", "unknown")
                
                # Check if existing lock is still valid or stale
                if now - lock_time < self.timeout_seconds:
                    raise LockAcquisitionError(
                        f"Active execution lock held by PID {lock_pid} on {lock_data.get('hostname')} "
                        f"since {lock_data.get('iso_time')} (age: {int(now - lock_time)}s < TTL: {self.timeout_seconds}s). "
                        "Aborting run to prevent concurrent duplicate processing."
                    )
                else:
                    # Lock is older than timeout_seconds; treat as stale and reclaim
                    pass
            except (ValueError, OSError, LockAcquisitionError):
                raise

        # Write lock payload
        payload = (
            f"pid={pid}\n"
            f"hostname={hostname}\n"
            f"timestamp={now}\n"
            f"iso_time={datetime.now(timezone.utc).isoformat()}\n"
        )
        self.lock_file.write_text(payload, encoding="utf-8")
        self._acquired = True
        return True

    def release(self) -> None:
        """Release the execution lock."""
        if self._acquired and self.lock_file.exists():
            try:
                self.lock_file.unlink(missing_ok=True)
            except OSError:
                pass
            self._acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
