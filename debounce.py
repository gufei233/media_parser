"""防抖器"""
import time
import random
from collections import defaultdict
from typing import Dict, Union, Callable


class Debouncer:
    """防抖器，防止短时间内重复解析同一链接（支持动态配置）"""

    def __init__(self, interval: Union[int, Callable[[], int]]):
        """初始化防抖器

        Args:
            interval: 防抖时间间隔（秒），可以是固定值或返回int的函数
        """
        self._interval = interval
        # 链接缓存：{session_id: {link: timestamp}}
        self.link_cache: Dict[str, Dict[str, float]] = defaultdict(dict)
        # 资源ID缓存：{session_id: {resource_id: timestamp}}
        self.resource_cache: Dict[str, Dict[str, float]] = defaultdict(dict)

    @property
    def interval(self) -> int:
        """动态获取防抖间隔"""
        if callable(self._interval):
            return self._interval()
        return self._interval

    def hit_link(self, session_id: str, link: str) -> bool:
        """检查链接是否在防抖时间内

        Args:
            session_id: 会话ID
            link: 链接

        Returns:
            bool: True表示命中防抖（应跳过），False表示未命中（可以解析）
        """
        if self.interval == 0:
            return False

        # 10% 概率自动清理过期缓存，避免内存持续增长
        if random.random() < 0.1:
            self.clear_expired()

        now = time.time()
        last_time = self.link_cache[session_id].get(link, 0)

        # 如果在防抖时间内
        if now - last_time < self.interval:
            return True

        # 记录本次解析时间
        self.link_cache[session_id][link] = now
        return False

    def hit_resource(self, session_id: str, resource_id: str) -> bool:
        """检查资源ID是否在防抖时间内

        Args:
            session_id: 会话ID
            resource_id: 资源ID（如视频ID）

        Returns:
            bool: True表示命中防抖（应跳过），False表示未命中（可以解析）
        """
        if self.interval == 0:
            return False

        now = time.time()
        last_time = self.resource_cache[session_id].get(resource_id, 0)

        # 如果在防抖时间内
        if now - last_time < self.interval:
            return True

        # 记录本次解析时间
        self.resource_cache[session_id][resource_id] = now
        return False

    def clear_expired(self):
        """清理过期的缓存记录"""
        now = time.time()

        # 清理链接缓存
        for session_id in list(self.link_cache.keys()):
            session_cache = self.link_cache[session_id]
            expired_links = [
                link
                for link, timestamp in session_cache.items()
                if now - timestamp > self.interval
            ]
            for link in expired_links:
                del session_cache[link]

            # 如果会话缓存为空，删除整个会话
            if not session_cache:
                del self.link_cache[session_id]

        # 清理资源ID缓存
        for session_id in list(self.resource_cache.keys()):
            session_cache = self.resource_cache[session_id]
            expired_resources = [
                resource_id
                for resource_id, timestamp in session_cache.items()
                if now - timestamp > self.interval
            ]
            for resource_id in expired_resources:
                del session_cache[resource_id]

            # 如果会话缓存为空，删除整个会话
            if not session_cache:
                del self.resource_cache[session_id]
