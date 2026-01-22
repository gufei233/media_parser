"""配置管理类"""
from astrbot.api import AstrBotConfig


class MediaParserConfig:
    """媒体解析插件配置管理类（支持动态读取）"""

    def __init__(self, config: AstrBotConfig):
        self.config = config

    # ========== 动态属性：每次访问都从 config 读取 ==========
    @property
    def enabled_sessions(self):
        return self.config.get("enabled_sessions", [])

    @property
    def debounce_interval(self):
        return self.config.get("debounce_interval", 300)

    @property
    def source_max_size(self):
        return self.config.get("source_max_size", 90)  # MB

    @property
    def source_max_minute(self):
        return self.config.get("source_max_minute", 15)  # 分钟

    @property
    def download_timeout(self):
        return self.config.get("download_timeout", 280)  # 秒

    @property
    def download_retry_times(self):
        return self.config.get("download_retry_times", 3)

    @property
    def common_timeout(self):
        return self.config.get("common_timeout", 15)  # 秒

    @property
    def show_download_fail_tip(self):
        return self.config.get("show_download_fail_tip", True)

    @property
    def forward_threshold(self):
        return self.config.get("forward_threshold", 3)

    @property
    def enable_cf_proxy(self):
        return self.config.get("enable_cf_proxy", False)

    @property
    def cf_proxy_url(self):
        return self.config.get("cf_proxy_url", "")

    # ========== 派生字段 ==========
    @property
    def max_duration(self):
        return self.source_max_minute * 60  # 转换为秒

    @property
    def max_size(self):
        return self.source_max_size * 1024 * 1024  # 转换为字节

    def save_config(self):
        """保存配置到磁盘"""
        self.config.save_config()

    def is_session_enabled(self, session_id: str, is_admin: bool, is_wake: bool) -> bool:
        """检查会话是否启用解析

        Args:
            session_id: 会话ID
            is_admin: 是否管理员
            is_wake: 是否唤醒/@bot

        Returns:
            bool: 是否启用
        """
        # 管理员不受限制
        if is_admin:
            return True

        # 艾特唤醒不受限制
        if is_wake:
            return True

        # 白名单为空，全局启用
        if not self.enabled_sessions:
            return True

        # 检查是否在白名单中
        return session_id in self.enabled_sessions

    def add_enabled_session(self, session_id: str):
        """添加会话到白名单"""
        sessions = self.enabled_sessions  # 读取当前列表
        if session_id not in sessions:
            sessions.append(session_id)
            self.config["enabled_sessions"] = sessions
            self.save_config()

    def remove_enabled_session(self, session_id: str):
        """从白名单移除会话"""
        sessions = self.enabled_sessions  # 读取当前列表
        if session_id in sessions:
            sessions.remove(session_id)
            self.config["enabled_sessions"] = sessions
            self.save_config()
