"""匿名解析器的持久化设备池。

设备身份文件只保存一套成组身份；池状态单独保存，避免客户端刷新会话时
覆盖 status/cooldown 等调度信息。
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path


class DevicePool:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.devices_dir = self.root / "devices"
        self.failures_dir = self.root / "failures"
        self.active_file = self.root / "active.json"
        self.devices_dir.mkdir(parents=True, exist_ok=True)
        self.failures_dir.mkdir(parents=True, exist_ok=True)

    def _write_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def active_profile(self) -> Path | None:
        if not self.active_file.exists():
            return None
        try:
            data = json.loads(self.active_file.read_text(encoding="utf-8"))
            path = Path(data.get("profile", ""))
        except (OSError, ValueError, TypeError):
            return None
        if not path.is_absolute():
            path = self.root / path
        if not path.exists():
            return None
        return path

    def set_active(self, profile: str | Path) -> Path:
        path = Path(profile).resolve()
        try:
            relative = path.relative_to(self.root.resolve())
            rendered = str(relative)
        except ValueError:
            rendered = str(path)
        self._write_json(
            self.active_file,
            {
                "profile": rendered,
                "updated_at": int(time.time()),
            },
        )
        return path

    def new_profile_path(self) -> Path:
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        return self.devices_dir / f"device_{stamp}_{secrets.token_hex(4)}.json"

    def record_failure(
        self, profile: str | Path, *, code=None, message: str = "", stage: str = ""
    ) -> Path:
        profile_path = Path(profile).resolve()
        event = {
            "profile": str(profile_path),
            "code": code,
            "message": message,
            "stage": stage,
            "failed_at": int(time.time()),
        }
        target = self.failures_dir / (
            profile_path.stem + "_" + str(event["failed_at"]) + ".json"
        )
        self._write_json(target, event)
        return target

    def mark_cooldown(
        self, profile: str | Path, *, code=None, message: str = "", stage: str = ""
    ) -> Path:
        """把设备标记为冷却，并记录失败证据；不会删除设备档案。"""
        profile_path = Path(profile).resolve()
        marker = profile_path.with_suffix(".status.json")
        self._write_json(
            marker,
            {
                "status": "cooldown",
                "code": code,
                "message": message,
                "stage": stage,
                "updated_at": int(time.time()),
            },
        )
        self.record_failure(profile_path, code=code, message=message, stage=stage)
        current = self.active_profile()
        if current and current == profile_path:
            self.active_file.unlink(missing_ok=True)
        return marker

    def is_cooldown(self, profile: str | Path) -> bool:
        marker = Path(profile).with_suffix(".status.json")
        if not marker.exists():
            return False
        try:
            return (
                json.loads(marker.read_text(encoding="utf-8")).get("status")
                == "cooldown"
            )
        except (OSError, ValueError, TypeError):
            return True
