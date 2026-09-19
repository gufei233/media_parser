"""
小红书解析器：App 端签名接口（Android 9.43.1 MUA/SIG/S1/Shield 纯 Python 实现）。

只有一条解析路径，不回退网页解析。失败时返回
{'error': True, 'message': ...}（含服务端原始 msg 与 code），由 main.py
直接把报错输出到会话。
"""

from pathlib import Path

from astrbot.api import logger

try:
    from .xhs_app_async import AsyncXhsAppParser
except ImportError:
    from xhs_app_async import AsyncXhsAppParser


def _default_pool_root() -> str:
    """设备档案目录。

    按 AstrBot 插件存储规范，持久化数据应放在 data/plugin_data/<plugin>/
    下（插件更新/重装不会丢设备身份）；拿不到框架路径时（单测/独立运行）
    回退到插件目录内的 xhs_app_data/。
    """
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        return str(
            Path(get_astrbot_data_path())
            / "plugin_data"
            / "media_parser"
            / "device_pool"
        )
    except Exception:
        return str(Path(__file__).resolve().parent / "xhs_app_data" / "device_pool")


class AsyncXiaohongshuParser:
    """小红书 App 签名解析器（异步封装，失败即报错，无兜底路径）。"""

    def __init__(
        self,
        enable_cf_proxy: bool = False,
        cf_proxy_url: str = "",
    ):
        self._app_parser: AsyncXhsAppParser | None = None
        # CF 反代：imagefeed 经 Worker 出口，绕开本机 IP 的风控标记
        self._cf_proxy_url = cf_proxy_url if enable_cf_proxy else ""

    def _get_app_parser(self) -> AsyncXhsAppParser:
        if self._app_parser is None:
            self._app_parser = AsyncXhsAppParser(
                pool_root=_default_pool_root(),
                cf_proxy_url=self._cf_proxy_url,
            )
        return self._app_parser

    async def close(self):
        if self._app_parser:
            await self._app_parser.close()
            self._app_parser = None

    async def parse(self, url: str) -> dict:
        """解析分享口令 / 短链 / 笔记链接 / note_id。"""
        result = await self._get_app_parser().parse(url)
        if result.get("error"):
            logger.error(f"小红书解析失败: {result.get('message')}")
        return result
