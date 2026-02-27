"""Plugin configuration helpers."""
from urllib.parse import urlparse

from astrbot.api import AstrBotConfig


class MediaParserConfig:
    """Media parser plugin config adapter."""

    def __init__(self, config: AstrBotConfig):
        self.config = config

    @staticmethod
    def _to_int(value, default: int, min_value: int, max_value: int) -> int:
        try:
            num = int(value)
        except (TypeError, ValueError):
            return default
        return max(min_value, min(max_value, num))

    @property
    def enabled_sessions(self):
        value = self.config.get("enabled_sessions", [])
        return value if isinstance(value, list) else []

    @property
    def debounce_interval(self):
        return self._to_int(self.config.get("debounce_interval", 300), 300, 0, 3600)

    @property
    def source_max_size(self):
        return self._to_int(self.config.get("source_max_size", 90), 90, 1, 10240)  # MB

    @property
    def source_max_minute(self):
        return self._to_int(self.config.get("source_max_minute", 15), 15, 1, 360)  # minutes

    @property
    def download_timeout(self):
        return self._to_int(self.config.get("download_timeout", 280), 280, 10, 3600)  # seconds

    @property
    def download_retry_times(self):
        return self._to_int(self.config.get("download_retry_times", 3), 3, 0, 10)

    @property
    def common_timeout(self):
        return self._to_int(self.config.get("common_timeout", 15), 15, 3, 600)  # seconds

    @property
    def show_download_fail_tip(self):
        return bool(self.config.get("show_download_fail_tip", True))

    @property
    def forward_threshold(self):
        return self._to_int(self.config.get("forward_threshold", 3), 3, 0, 50)

    @property
    def enable_cf_proxy(self):
        return bool(self.config.get("enable_cf_proxy", False))

    @property
    def cf_proxy_url(self):
        raw = str(self.config.get("cf_proxy_url", "") or "").strip().rstrip("/")
        if not raw:
            return ""
        try:
            parsed = urlparse(raw)
        except Exception:
            return ""
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return raw

    @property
    def douyin_info_render_mode(self):
        mode = self.config.get("douyin_info_render_mode", "image")
        if mode not in {"text", "image", "both"}:
            return "image"
        return mode

    @property
    def max_duration(self):
        return self.source_max_minute * 60

    @property
    def max_size(self):
        return self.source_max_size * 1024 * 1024

    def save_config(self):
        self.config.save_config()

    def is_session_enabled(self, session_id: str, is_admin: bool, is_wake: bool) -> bool:
        if is_admin:
            return True
        if is_wake:
            return True
        if not self.enabled_sessions:
            return True
        return session_id in self.enabled_sessions

    def add_enabled_session(self, session_id: str):
        sessions = self.enabled_sessions
        if session_id not in sessions:
            sessions.append(session_id)
            self.config["enabled_sessions"] = sessions
            self.save_config()

    def remove_enabled_session(self, session_id: str):
        sessions = self.enabled_sessions
        if session_id in sessions:
            sessions.remove(session_id)
            self.config["enabled_sessions"] = sessions
            self.save_config()
