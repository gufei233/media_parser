"""
小红书解析器：App 端签名接口（Android 9.43.1 MUA/SIG/S1/Shield 纯 Python 实现）。

只有一条解析路径，不回退网页解析。失败时返回
{'error': True, 'message': ...}（含服务端原始 msg 与 code），由 main.py
直接把报错输出到会话。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from astrbot.api import logger

try:
    from .xhs_app.xhs_app_client import UA as XHS_UA
    from .xhs_app_async import AsyncXhsAppParser
except ImportError:
    from xhs_app.xhs_app_client import UA as XHS_UA
    from xhs_app_async import AsyncXhsAppParser

# CDN 对无 App UA 的请求会 403（AstrBot 自己去拉 fromURL 就是这个结果）。
_XHS_CDN_HEADERS = {
    "User-Agent": XHS_UA,
    "Accept": "*/*",
    "Referer": "https://app.xhs.cn/",
}


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


def _suffix_from_url(url: str, default: str = ".bin") -> str:
    path = url.split("?", 1)[0]
    ext = Path(path).suffix.lower()
    if ext in {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".heic",
        ".heif",
        ".mp4",
        ".mov",
        ".m4a",
    }:
        return ext
    return default


def _suffix_from_bytes(raw: bytes, kind: str, fallback: str) -> str:
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return ".webp"
    if len(raw) >= 12 and raw[4:8] == b"ftyp":
        brand = raw[8:12]
        if brand in {b"heic", b"heix", b"mif1", b"msf1", b"hevc", b"hevx"}:
            return ".heic"
        return ".mp4" if kind == "video" else ".heic"
    return fallback


class AsyncXiaohongshuParser:
    """小红书 App 签名解析器（异步封装，失败即报错，无兜底路径）。"""

    def __init__(
        self,
        enable_cf_proxy: bool = False,
        cf_proxy_url: str = "",
    ):
        self._app_parser: AsyncXhsAppParser | None = None
        self._http: httpx.AsyncClient | None = None
        # CF 反代：imagefeed 经 Worker 出口，绕开本机 IP 的风控标记；
        # 媒体下载直连 403 时同样走 Worker /download。
        self._enable_cf_proxy = enable_cf_proxy
        self._cf_proxy_url = cf_proxy_url.strip().rstrip("/") if enable_cf_proxy else ""

    def update_config(self, enable_cf_proxy: bool, cf_proxy_url: str):
        """同步运行时配置（与抖音下载器同一套开关）。"""
        self._enable_cf_proxy = enable_cf_proxy
        self._cf_proxy_url = cf_proxy_url.strip().rstrip("/") if enable_cf_proxy else ""
        if self._app_parser is not None and self._app_parser._manager is not None:
            self._app_parser._manager.set_cf_proxy_url(self._cf_proxy_url)

    def _get_app_parser(self) -> AsyncXhsAppParser:
        if self._app_parser is None:
            self._app_parser = AsyncXhsAppParser(
                pool_root=_default_pool_root(),
                cf_proxy_url=self._cf_proxy_url,
            )
        return self._app_parser

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                verify=False,
                follow_redirects=True,
                timeout=httpx.Timeout(60.0),
            )
        return self._http

    async def close(self):
        if self._app_parser:
            await self._app_parser.close()
            self._app_parser = None
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    async def parse(self, url: str) -> dict:
        """解析分享口令 / 短链 / 笔记链接 / note_id。"""
        result = await self._get_app_parser().parse(url)
        if result.get("error"):
            logger.error(f"小红书解析失败: {result.get('message')}")
        return result

    async def download_file(self, url: str, save_path: str) -> bool:
        """用 App UA 下载媒体。直连 403 时走 CF Worker /download。"""
        if not url or not url.startswith(("http://", "https://")):
            return False
        client = await self._get_http()
        try:
            async with client.stream("GET", url, headers=_XHS_CDN_HEADERS) as resp:
                if resp.status_code in (200, 206):
                    with open(save_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(65536):
                            if chunk:
                                f.write(chunk)
                    if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                        return True
                logger.warning(f"XHS CDN 直连失败: HTTP {resp.status_code}")
        except Exception as exc:
            logger.warning(f"XHS CDN 直连异常: {type(exc).__name__}: {exc}")

        if not self._cf_proxy_url:
            return False
        proxy_url = self._cf_proxy_url
        if not proxy_url.endswith("/download"):
            proxy_url = f"{proxy_url}/download"
        try:
            logger.info(f"XHS 媒体改走 CF 下载: {proxy_url}")
            async with client.stream(
                "POST",
                proxy_url,
                json={"url": url, "headers": dict(_XHS_CDN_HEADERS)},
                timeout=httpx.Timeout(120.0),
            ) as resp:
                if resp.status_code >= 400:
                    try:
                        err = json.loads(await resp.aread())
                        logger.error(
                            f"XHS CF 下载失败: {err.get('error', resp.status_code)}"
                        )
                    except Exception:
                        logger.error(f"XHS CF 下载失败: HTTP {resp.status_code}")
                    return False
                with open(save_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(65536):
                        if chunk:
                            f.write(chunk)
            return os.path.exists(save_path) and os.path.getsize(save_path) > 0
        except Exception as exc:
            logger.error(f"XHS CF 下载异常: {type(exc).__name__}: {exc}")
            return False
