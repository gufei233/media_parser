"""
媒体解析插件主文件 - 完全异步版本
支持解析抖音和小红书链接
"""

import asyncio
import base64
import os
import re
import tempfile
import time
import traceback
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from PIL import Image

try:
    from .async_dysk import AsyncDouyinDownloader
    from .async_xhs import AsyncXiaohongshuParser
    from .config import MediaParserConfig
    from .debounce import Debouncer
    from .utils import normalize_text
except ImportError:
    from async_dysk import AsyncDouyinDownloader
    from async_xhs import AsyncXiaohongshuParser
    from config import MediaParserConfig
    from debounce import Debouncer
    from utils import normalize_text


def _load_template(name: str) -> str:
    tmpl_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "templates", name
    )
    with open(tmpl_path, "r", encoding="utf-8") as f:
        return f.read()


DOUYIN_INFO_CARD_TEMPLATE = _load_template("douyin_info_card.html")


def _live_forward_segments(live_pairs) -> list:
    """把实况笔记的 livePairs 切成连续媒体段，每段对应一条合并转发。

    连续的普通静图合为一段，连续的实况图合为另一段；实况段内按原顺序
    静图→实况视频交错。返回 [kind, url] 项列表的列表，kind 为 image/video，
    节点化由调用方完成。
    """
    segments: list = []
    for pair in live_pairs or []:
        items: list = []
        if pair.get("image"):
            items.append(("image", pair["image"]))
        if pair.get("video"):
            items.append(("video", pair["video"]))
        if not items:
            continue
        kind = "live" if pair.get("video") else "plain"
        if segments and segments[-1][0] == kind:
            segments[-1][1].extend(items)
        else:
            segments.append((kind, items))
    return [items for _, items in segments]


@register("media_parser", "顾绯", "抖音小红书链接解析插件（异步优化版）", "2.4.3")
class MediaParserPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # Configuration
        self.cfg = MediaParserConfig(config)
        # Debouncer
        self.debouncer = Debouncer(lambda: self.cfg.debounce_interval)
        # Parsers - reusable instance
        self.xhs_parser = AsyncXiaohongshuParser()
        self.dy_downloader = AsyncDouyinDownloader(
            enable_cf_proxy=self.cfg.enable_cf_proxy,
            cf_proxy_url=self.cfg.cf_proxy_url,
            download_retry_times=self.cfg.download_retry_times,
            download_timeout=self.cfg.download_timeout,
            common_timeout=self.cfg.common_timeout,
            max_size=self.cfg.max_size,
            max_duration=self.cfg.max_duration,
        )
        self._font_urls = self._build_local_font_urls()
        # URL patterns
        self.dy_patterns = [
            r"https?://v\.douyin\.com/[a-zA-Z0-9_-]+/?",
            r"https?://(?:www\.)?douyin\.com/(?:video|note|slides)/\d+[^\s]*",
            r"https?://(?:www\.)?douyin\.com/[^\s]*(?:modal_id|mid|aweme_id)=\d+[^\s]*",
            r"https?://(?:www\.)?iesdouyin\.com/share/(?:video|note|slides)/\d+[^\s]*",
        ]
        self.xhs_patterns = [
            r"https?://(?:www\.)?xiaohongshu\.com/[^\s]+",
            r"https?://xhslink\.com/[^\s]+",
            r"https?://xhslink\.cn/[^\s]+",
        ]

        logger.info("媒体解析插件初始化完成（异步版）")
        logger.info(f"白名单会话数: {len(self.cfg.enabled_sessions)}")
        logger.info(f"防抖时间: {self.cfg.debounce_interval}s")
        logger.info(f"最大文件大小: {self.cfg.source_max_size}MB")
        logger.info(f"最大视频时长: {self.cfg.source_max_minute}分钟")
        logger.info(f"抖音信息渲染模式: {self.cfg.douyin_info_render_mode}")
        if self._font_urls:
            logger.info("已加载本地 HarmonyOS 字体资源")

    def _sync_downloader_config(self):
        """Sync latest config values to the reusable downloader instance."""
        self.dy_downloader.update_config(
            enable_cf_proxy=self.cfg.enable_cf_proxy,
            cf_proxy_url=self.cfg.cf_proxy_url,
            download_retry_times=self.cfg.download_retry_times,
            download_timeout=self.cfg.download_timeout,
            common_timeout=self.cfg.common_timeout,
            max_size=self.cfg.max_size,
            max_duration=self.cfg.max_duration,
        )

    async def terminate(self):
        """Release parser resources on plugin unload."""
        logger.info("正在清理资源...")
        if self.xhs_parser:
            await self.xhs_parser.close()
        if self.dy_downloader:
            await self.dy_downloader.close()
        logger.info("资源清理完成")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def parse_media_link(self, event: AstrMessageEvent):
        """媒体链接解析入口"""
        # Session whitelist check
        umo = event.unified_msg_origin
        if not self.cfg.is_session_enabled(
            umo, event.is_admin(), event.is_at_or_wake_command
        ):
            return

        text = event.message_str
        # URL match
        dy_url = None
        for pattern in self.dy_patterns:
            match = re.search(pattern, text)
            if match:
                dy_url = match.group(0)
                break

        xhs_url = None
        for pattern in self.xhs_patterns:
            match = re.search(pattern, text)
            if match:
                xhs_url = match.group(0)
                break
        # No supported URL found
        if not dy_url and not xhs_url:
            return
        # Debounce check
        check_url = dy_url or xhs_url
        is_duplicate, reservation = self.debouncer.reserve_link(umo, check_url)
        if is_duplicate:
            logger.warning(
                f"[debounce] Skip parsing duplicated link within interval: {check_url}"
            )
            return

        # ========== 解析处理 ==========
        if dy_url:
            async for result in self.parse_douyin(
                event, dy_url, debounce_reservation=reservation
            ):
                yield result
            event.stop_event()
        elif xhs_url:
            async for result in self.parse_xiaohongshu(event, xhs_url):
                yield result
            event.stop_event()

    # ==================== 抖音解析（完全异步）====================

    async def parse_douyin(
        self,
        event: AstrMessageEvent,
        url: str,
        debounce_reservation: float | None = None,
    ):
        """Parse Douyin link asynchronously."""
        result = None
        reservation_committed = False
        session_id = event.unified_msg_origin
        try:
            logger.info(f"Start parsing Douyin link: {url}")

            # Sync config in case user changed settings at runtime.
            self._sync_downloader_config()
            dy_downloader = self.dy_downloader

            result = await dy_downloader.get_detail(url)

            if not result:
                self.debouncer.release_link(session_id, url, debounce_reservation)
                logger.error(
                    "Douyin parse returned None; debounce reservation released"
                )
                yield event.plain_result(f"Parse failed. Open link directly:\n{url}")
                return

            reservation_committed = True
            uin = event.get_sender_id()
            name = event.get_sender_name()

            downloads = result.get("downloads", [])
            images, video_links = self._extract_douyin_media(downloads)
            media_bytes_cache: dict[str, bytes] = {}

            # Info render mode: text / image / both
            render_mode = self.cfg.douyin_info_render_mode
            if render_mode in {"image", "both"}:
                info_image_url = await self._render_douyin_info_image(
                    result=result,
                    dy_downloader=dy_downloader,
                    media_bytes_cache=media_bytes_cache,
                )
                if info_image_url:
                    yield event.image_result(info_image_url)
                elif render_mode == "image":
                    logger.warning(
                        "Douyin info image render failed, falling back to text mode"
                    )
                    nodes = self._build_douyin_info_nodes(result, uin, name)
                    yield event.chain_result([Comp.Nodes(nodes=nodes)])

            if render_mode in {"text", "both"}:
                nodes = self._build_douyin_info_nodes(result, uin, name)
                yield event.chain_result([Comp.Nodes(nodes=nodes)])

            # Duration limit check
            duration_seconds = result.get("duration_seconds", 0)
            if duration_seconds > 0:
                if self.cfg.max_duration and duration_seconds > self.cfg.max_duration:
                    max_minutes = self.cfg.max_duration / 60
                    actual_minutes = duration_seconds / 60
                    warning_msg = (
                        f"Video duration {actual_minutes:.1f} min exceeds limit "
                        f"{max_minutes:.1f} min. Skip video download."
                    )
                    logger.warning(warning_msg)
                    if self.cfg.show_download_fail_tip:
                        yield event.plain_result(warning_msg)
                    return

            logger.info(
                f"Ready to send media: {len(images)} images, {len(video_links)} videos"
            )

            if images or video_links:
                await self._send_media_async(
                    event, dy_downloader, images, video_links, media_bytes_cache
                )
            else:
                logger.warning("No media file available to send")

        except Exception as e:
            error_msg = f"Douyin parse failed: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            if self.cfg.show_download_fail_tip:
                yield event.plain_result(f"Parse failed: {e!s}")
        finally:
            if not reservation_committed:
                self.debouncer.release_link(session_id, url, debounce_reservation)

    @staticmethod
    def _normalize_text(value: Any, default: str = "") -> str:
        return normalize_text(value, default)

    @staticmethod
    def _format_count(value: Any) -> str:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return str(value or 0)

        if number >= 100000000:
            return f"{number / 100000000:.1f}\u4ebf"
        if number >= 10000:
            return f"{number / 10000:.1f}\u4e07"
        return f"{number:,}"

    @staticmethod
    def _to_html_entities(value: Any) -> str:
        text = "" if value is None else str(value)
        if not text:
            return ""
        escaped = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )
        out: list[str] = []
        for ch in escaped:
            code = ord(ch)
            if 32 <= code <= 126:
                out.append(ch)
            else:
                out.append(f"&#{code};")
        return "".join(out)

    @staticmethod
    def _is_http_url(url: Any) -> bool:
        if not isinstance(url, str) or not url:
            return False
        try:
            parsed = urlparse(url.strip())
        except Exception:
            return False
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _pick_cover_url(downloads: list[Any]) -> str:
        for item in downloads:
            if MediaParserPlugin._is_http_url(item):
                return item
            if isinstance(item, dict):
                for key in ("cover", "image"):
                    value = item.get(key)
                    if MediaParserPlugin._is_http_url(value):
                        return value
        return ""

    def _extract_douyin_media(
        self, downloads: list[Any]
    ) -> tuple[list[str], list[str]]:
        images: list[str] = []
        video_links: list[str] = []

        for item in downloads:
            if isinstance(item, str):
                if self._is_http_url(item):
                    images.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "video":
                    cover = item.get("cover")
                    video_url = item.get("url")
                    if self._is_http_url(cover):
                        images.append(cover)
                    if self._is_http_url(video_url):
                        video_links.append(video_url)
                elif item.get("type") == "live_photo":
                    image_url = item.get("image")
                    video_url = item.get("video")
                    if self._is_http_url(image_url):
                        images.append(image_url)
                    if self._is_http_url(video_url):
                        video_links.append(video_url)

        return images, video_links

    def _build_douyin_info_nodes(
        self, result: dict[str, Any], uin: str, name: str
    ) -> list[Any]:
        nodes = []

        author = result.get("author") or {}
        info_text = (
            f"id: {result.get('id', '')}\n"
            f"desc: {result.get('desc', '')}\n"
            f"create_time: {result.get('create_time', '')}\n"
            f"nickname: {author.get('nickname', '')}"
        )
        nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Plain(info_text)]))

        music = result.get("music") or {}
        music_text = (
            f"uid: {author.get('uid', '')}\n"
            f"author: {music.get('author', '')}\n"
            f"title: {music.get('title', '')}\n"
            f"url: {music.get('url', '')}"
        )
        nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Plain(music_text)]))

        stats = result.get("statistics") or {}
        stats_text = (
            f"digg_count: {stats.get('digg_count', 0)}\n"
            f"comment_count: {stats.get('comment_count', 0)}\n"
            f"collect_count: {stats.get('collect_count', 0)}\n"
            f"share_count: {stats.get('share_count', 0)}"
        )
        nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Plain(stats_text)]))

        type_text = f"type: {result.get('type', '')}"
        duration_str = result.get("duration", "")
        if duration_str:
            type_text += f"\nduration: {duration_str}"
        nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Plain(type_text)]))

        return nodes

    async def _render_douyin_info_image(
        self,
        result: dict[str, Any],
        dy_downloader: AsyncDouyinDownloader,
        media_bytes_cache: dict[str, bytes] | None = None,
    ) -> str | None:
        try:
            author = result.get("author") or {}
            stats = result.get("statistics") or {}
            music = result.get("music") or {}
            desc = self._normalize_text(result.get("desc"), "无描述")
            if len(desc) > 80:
                desc = desc[:77] + "..."

            cover_source_url = self._pick_cover_url(result.get("downloads", []))
            avatar_source = self._normalize_text(author.get("avatar"), "")
            music_cover_source = self._normalize_text(music.get("cover"), "")
            cover_url, author_avatar, music_cover = await asyncio.gather(
                self._to_data_url_if_possible(
                    dy_downloader, cover_source_url, media_bytes_cache
                ),
                self._to_data_url_if_possible(
                    dy_downloader, avatar_source, media_bytes_cache
                ),
                self._to_data_url_if_possible(
                    dy_downloader, music_cover_source, media_bytes_cache
                ),
            )
            cover_raw = (
                media_bytes_cache.get(cover_source_url, b"")
                if media_bytes_cache and cover_source_url
                else b""
            )
            cover_size = self._get_image_size(cover_raw)
            card_width, card_height, ui_scale = self._compute_render_size(cover_size)
            overlay_metrics = self._compute_overlay_metrics(card_width, card_height)
            author_name = self._normalize_text(author.get("nickname"), "未知作者")
            media_type = self._normalize_text(result.get("type"), "unknown")
            create_time = self._normalize_text(result.get("create_time"), "-")
            duration = self._normalize_text(result.get("duration"), "")
            music_title = self._normalize_text(music.get("title"), "")
            music_author = self._normalize_text(music.get("author"), "")

            render_data = {
                "author_avatar": author_avatar,
                "cover_url": cover_url,
                "digg_count": self._format_count(stats.get("digg_count", 0)),
                "comment_count": self._format_count(stats.get("comment_count", 0)),
                "collect_count": self._format_count(stats.get("collect_count", 0)),
                "share_count": self._format_count(stats.get("share_count", 0)),
                "duration": duration,
                "music_title": music_title,
                "music_cover": music_cover,
                "author_name_html": self._to_html_entities(author_name),
                "desc_html": self._to_html_entities(desc),
                "media_type_html": self._to_html_entities(media_type),
                "create_time_html": self._to_html_entities(create_time),
                "duration_html": self._to_html_entities(duration),
                "music_title_html": self._to_html_entities(music_title),
                "music_author_html": self._to_html_entities(music_author),
                "card_width": card_width,
                "card_height": card_height,
                "ui_scale": ui_scale,
                **overlay_metrics,
                "font_regular_url": self._font_urls.get("regular", ""),
                "font_medium_url": self._font_urls.get("medium", ""),
                "font_bold_url": self._font_urls.get("bold", ""),
            }
            return await self.html_render(
                DOUYIN_INFO_CARD_TEMPLATE,
                render_data,
                options={
                    "type": "jpeg",
                    "quality": 92,
                    "full_page": True,
                    "clip": {
                        "x": 0,
                        "y": 0,
                        "width": card_width,
                        "height": card_height,
                    },
                    "animations": "disabled",
                    "scale": "device",
                },
            )
        except Exception as e:
            logger.error(f"Douyin info image render failed: {e}")
            return None

    async def _to_data_url_if_possible(
        self,
        dy_downloader: AsyncDouyinDownloader,
        source_url: str,
        media_bytes_cache: dict[str, bytes] | None = None,
    ) -> str:
        if not self._is_http_url(source_url):
            return source_url

        if media_bytes_cache is not None and source_url in media_bytes_cache:
            cached_bytes = media_bytes_cache.get(source_url, b"")
            if cached_bytes:
                mime = self._detect_image_mime(cached_bytes)
                base64_str = base64.b64encode(cached_bytes).decode("ascii")
                return f"data:{mime};base64,{base64_str}"

        try:
            raw = await dy_downloader.download_to_bytes(source_url)
            if raw:
                if media_bytes_cache is not None:
                    media_bytes_cache[source_url] = raw
                mime = self._detect_image_mime(raw)
                base64_str = base64.b64encode(raw).decode("ascii")
                return f"data:{mime};base64,{base64_str}"
        except Exception as e:
            logger.debug(
                f"Failed to convert resource to data URL, fallback URL: {source_url}, error: {e}"
            )

        return source_url

    def _build_local_font_urls(self) -> dict[str, str]:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        font_dir = os.path.join(base_dir, "fonts")
        file_map = {
            "regular": "HarmonyOS_Sans_SC_Regular.ttf",
            "medium": "HarmonyOS_Sans_SC_Medium.ttf",
            "bold": "HarmonyOS_Sans_SC_Bold.ttf",
        }
        urls: dict[str, str] = {}
        for key, file_name in file_map.items():
            file_path = os.path.join(font_dir, file_name)
            if os.path.exists(file_path):
                urls[key] = self._path_to_file_url(file_path)
        return urls

    @staticmethod
    def _path_to_file_url(path: str) -> str:
        return Path(path).resolve().as_uri()

    @staticmethod
    def _get_image_size(raw: bytes) -> tuple[int, int] | None:
        if not raw or len(raw) < 10:
            return None
        try:
            with Image.open(BytesIO(raw)) as img:
                return img.size if img.size[0] > 0 and img.size[1] > 0 else None
        except Exception:
            return None

    @staticmethod
    def _compute_render_size(
        cover_size: tuple[int, int] | None,
    ) -> tuple[int, int, float]:
        default_size = (1280, 720, 1.0)
        if not cover_size:
            return default_size

        width, height = cover_size
        if width <= 0 or height <= 0:
            return default_size

        ratio = width / height
        if ratio <= 0:
            return default_size

        # Keep extreme aspect ratios in a reasonable range to avoid broken layout.
        min_ratio = 0.5
        max_ratio = 2.0
        if ratio < min_ratio:
            width = int(round(height * min_ratio))
        elif ratio > max_ratio:
            height = int(round(width / max_ratio))
        ratio = width / height if height > 0 else 16 / 9

        # Size by aspect ratio only so identical ratios render with identical visual scale
        # regardless of source pixel resolution.
        target_long_edge = 1280
        if ratio >= 1.0:
            card_width = int(round(target_long_edge))
            card_height = int(round(target_long_edge / ratio))
        else:
            card_height = int(round(target_long_edge))
            card_width = int(round(target_long_edge * ratio))
        card_width = max(480, card_width)
        card_height = max(480, card_height)

        # For narrow portrait covers, slightly reduce overlay scale to avoid crowding.
        ui_scale = 1.0
        if card_width < 720:
            ui_scale -= min(0.14, (720 - card_width) / 720 * 0.14)
        elif card_width < 960:
            ui_scale += min(0.03, (960 - card_width) / 960 * 0.03)
        if card_height >= card_width * 1.6:
            ui_scale -= 0.03
        ui_scale = max(0.86, min(1.06, ui_scale))

        return card_width, card_height, round(ui_scale, 3)

    @staticmethod
    def _compute_overlay_metrics(card_width: int, card_height: int) -> dict[str, Any]:
        """Compute overlay metrics under a top-half-width constraint.

        Top area is split into 50% (avatar+nickname) and 50% (stats), and all
        typography/icon sizes are derived from half width so elements do not
        cross the center line on portrait covers.
        """
        half_width = max(220.0, card_width / 2.0)
        base = max(0.78, min(1.0, half_width / 420.0))

        avatar_size = max(50, min(70, int(round(66 * base))))
        author_font_size = max(22, min(32, int(round(32 * base))))
        author_allow_wrap = author_font_size <= 25

        stat_icon_size = max(20, min(28, int(round(28 * base))))
        stat_font_size = max(16, min(24, int(round(24 * base))))
        stat_col_gap = max(6, min(11, int(round(10 * base))))
        stat_item_width_limit = max(44.0, (half_width - stat_col_gap * 3) / 4.0)
        stat_item_width = max(
            44, min(int(round(76 * base)), int(round(stat_item_width_limit)))
        )

        meta_font_size = max(19, min(24, int(round(23 * base))))
        desc_font_size = max(26, min(34, int(round(card_width * 0.036))))

        # Scale bottom section proportionally with aspect ratio.
        # Portrait cards keep UI compact; landscape cards enlarge slightly
        # but cap at 1.25 to avoid oversized bottom elements.
        aspect = card_width / max(1, card_height)
        if aspect <= 0.75:
            bottom_scale = 0.92
        elif aspect <= 1.15:
            # Linear from 0.92 to 1.0
            bottom_scale = 0.92 + (aspect - 0.75) / (1.15 - 0.75) * 0.08
        else:
            # Linear from 1.0 to 1.25, capped
            bottom_scale = min(1.25, 1.0 + (aspect - 1.15) / (2.0 - 1.15) * 0.25)

        return {
            "avatar_size": avatar_size,
            "author_font_size": author_font_size,
            "author_allow_wrap": author_allow_wrap,
            "stat_icon_size": stat_icon_size,
            "stat_font_size": stat_font_size,
            "stat_col_gap": stat_col_gap,
            "stat_item_width": stat_item_width,
            "meta_font_size": meta_font_size,
            "desc_font_size": desc_font_size,
            "bottom_scale": bottom_scale,
        }

    @staticmethod
    def _detect_image_mime(raw: bytes) -> str:
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if raw.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if raw.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            return "image/webp"
        return "image/jpeg"

    async def _send_media_async(
        self,
        event,
        dy_downloader,
        images,
        video_links,
        media_bytes_cache: dict[str, bytes] | None = None,
    ):
        """Download and send media files asynchronously with concurrent image downloads."""
        logger.info(
            f"Start sending media files: {len(images)} images, {len(video_links)} videos"
        )

        # --- Concurrent image download ---
        sem = asyncio.Semaphore(4)

        async def _download_image(img_url: str) -> str | None:
            """Download a single image; returns temp file path or None."""
            async with sem:
                # Reuse cache first to avoid duplicate downloads.
                if media_bytes_cache and img_url in media_bytes_cache:
                    raw = media_bytes_cache.get(img_url, b"")
                    if raw:
                        temp_file = tempfile.NamedTemporaryFile(
                            delete=False, suffix=".jpg"
                        )
                        temp_path = temp_file.name
                        temp_file.close()
                        with open(temp_path, "wb") as f:
                            f.write(raw)
                        return temp_path

                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                temp_path = temp_file.name
                temp_file.close()

                success = await dy_downloader.download_video(img_url, temp_path)
                if (
                    success
                    and os.path.exists(temp_path)
                    and os.path.getsize(temp_path) > 0
                ):
                    if media_bytes_cache is not None:
                        try:
                            with open(temp_path, "rb") as f:
                                media_bytes_cache[img_url] = f.read()
                        except Exception:
                            pass
                    return temp_path

                # Cleanup on failure
                if os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except Exception:
                        pass
                return None

        # Kick off all image downloads concurrently
        image_tasks = [_download_image(url) for url in images]
        image_results = await asyncio.gather(*image_tasks, return_exceptions=True)

        # Collect successfully downloaded image paths (preserve order)
        image_paths: list[str | None] = []
        for idx, res in enumerate(image_results):
            if isinstance(res, Exception):
                logger.error(f"Image {idx + 1} download exception: {res}")
                image_paths.append(None)
            else:
                image_paths.append(res)

        # Send images sequentially (preserves order for the user)
        for i, temp_path in enumerate(image_paths):
            try:
                if (
                    temp_path
                    and os.path.exists(temp_path)
                    and os.path.getsize(temp_path) > 0
                ):
                    result = event.make_result()
                    result.chain = [Comp.Image.fromFileSystem(temp_path)]
                    await event.send(result)
                    logger.info(f"Image {i + 1} sent successfully")
                else:
                    if self.cfg.show_download_fail_tip:
                        await event.send(
                            event.plain_result(f"Image download failed: {images[i]}")
                        )
                    logger.warning(f"Image {i + 1} download failed")
            except Exception as e:
                logger.error(f"Image {i + 1} send error: {e}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    # Brief delay so the framework can finish reading the file
                    await asyncio.sleep(0.5)
                    try:
                        os.unlink(temp_path)
                    except Exception as e:
                        logger.warning(f"Failed to cleanup temp file: {temp_path}, {e}")

        # --- Download and send videos sequentially ---
        for i, video_url in enumerate(video_links):
            temp_path = None
            try:
                logger.info(f"Downloading video {i + 1}/{len(video_links)}")

                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                temp_path = temp_file.name
                temp_file.close()

                success = await dy_downloader.download_video(video_url, temp_path)

                # Video file should be at least 10KB.
                min_video_size = 10 * 1024
                if success and os.path.exists(temp_path):
                    file_size = os.path.getsize(temp_path)
                    if file_size >= min_video_size:
                        result = event.make_result()
                        result.chain = [Comp.Video.fromFileSystem(temp_path)]
                        await event.send(result)
                        logger.info(
                            f"Video {i + 1} sent successfully, size: {file_size} bytes"
                        )
                    else:
                        logger.warning(
                            f"Video {i + 1} file too small ({file_size} bytes), skip sending"
                        )
                        if self.cfg.show_download_fail_tip:
                            await event.send(
                                event.plain_result(
                                    "Video download incomplete, open original link directly."
                                )
                            )
                else:
                    if self.cfg.show_download_fail_tip:
                        await event.send(event.plain_result(f"Video link: {video_url}"))
                    logger.warning(f"Video {i + 1} download failed")

            except Exception as e:
                logger.error(f"Video {i + 1} processing error: {e}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    await asyncio.sleep(0.5)
                    try:
                        os.unlink(temp_path)
                    except Exception as e:
                        logger.warning(f"Failed to cleanup temp file: {temp_path}, {e}")

    async def parse_xiaohongshu(self, event: AstrMessageEvent, url: str):
        """Parse Xiaohongshu link asynchronously."""
        try:
            logger.info(f"Start parsing Xiaohongshu link: {url}")

            result = await self.xhs_parser.parse(url)

            if result.get("error"):
                error_msg = result.get("message", "Unknown error")
                logger.error(f"Xiaohongshu parse failed: {error_msg}")
                if self.cfg.show_download_fail_tip:
                    yield event.plain_result(f"Parse failed: {error_msg}")
                return

            uin = event.get_sender_id()
            name = event.get_sender_name()

            def _text_node(text: str) -> Any:
                return Comp.Node(uin=uin, name=name, content=[Comp.Plain(text)])

            def _media_node(components: list) -> Any:
                return Comp.Node(uin=uin, name=name, content=components)

            # --- 信息合并转发：作者 / 标题 / 正文 / 数据 / 话题 / 链接 ---
            author = result.get("author") or {}
            counts = result.get("counts") or {}

            author_line = f"作者：{author.get('name') or '未知作者'}"
            if author.get("redId"):
                author_line += f"（小红书号：{author['redId']}）"
            nodes = [
                _text_node(author_line),
                _text_node(f"标题：{result.get('title', '小红书内容')}"),
                _text_node(f"正文：{result.get('content', '')}"),
            ]
            stats_line = (
                f"赞 {self._format_count(counts.get('liked'))} · "
                f"藏 {self._format_count(counts.get('collected'))} · "
                f"评 {self._format_count(counts.get('comments'))} · "
                f"转 {self._format_count(counts.get('shares'))}"
            )
            if result.get("isAds"):
                stats_line += " ｜ 推广"
            if result.get("ipLocation"):
                stats_line += f" ｜ IP {result['ipLocation']}"
            created_at = int(result.get("createdAt") or 0)
            if created_at > 0:
                stats_line += (
                    f" ｜ {time.strftime('%Y-%m-%d %H:%M', time.localtime(created_at))}"
                )
            nodes.append(_text_node(stats_line))
            topics = result.get("topics") or []
            if topics:
                nodes.append(
                    _text_node("话题：" + " ".join(f"#{t}" for t in topics[:12]))
                )
            if result.get("originalUrl"):
                nodes.append(_text_node(f"链接：{result['originalUrl']}"))
            yield event.chain_result([Comp.Nodes(nodes=nodes)])

            # --- 媒体输出 ---
            if result.get("contentType") == "video":
                # 视频笔记：封面 + 全部视频放一个合并转发
                media_nodes: list[Comp.Node] = []
                if result.get("cover"):
                    media_nodes.append(
                        _media_node([Comp.Image.fromURL(result["cover"])])
                    )
                for video_url in result.get("videos") or []:
                    media_nodes.append(_media_node([Comp.Video.fromURL(video_url)]))
                if media_nodes:
                    yield event.chain_result([Comp.Nodes(nodes=media_nodes)])
            elif result.get("isLivePhoto") and result.get("livePairs"):
                # 实况笔记：连续普通静图合一条合并转发，实况段（静图→实况视频
                # 交错）合一条。静图与视频必须分节点，同一节点内图+视频混排时
                # 平台会吞掉静图
                for items in _live_forward_segments(result["livePairs"]):
                    seg_nodes: list[Comp.Node] = []
                    for kind, url in items:
                        if kind == "image":
                            seg_nodes.append(_media_node([Comp.Image.fromURL(url)]))
                        else:
                            seg_nodes.append(_media_node([Comp.Video.fromURL(url)]))
                    if seg_nodes:
                        yield event.chain_result([Comp.Nodes(nodes=seg_nodes)])
            else:
                # 图文笔记：全部图片放一个合并转发
                media_nodes = [
                    _media_node([Comp.Image.fromURL(img_url)])
                    for img_url in result.get("images") or []
                ]
                if media_nodes:
                    yield event.chain_result([Comp.Nodes(nodes=media_nodes)])

        except Exception as e:
            error_msg = f"Xiaohongshu parse failed: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            if self.cfg.show_download_fail_tip:
                yield event.plain_result(f"Parse failed: {e!s}")

    # ==================== Admin Commands ====================

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("开启解析")
    async def enable_parser(self, event: AstrMessageEvent):
        """Enable parser for current session."""
        umo = event.unified_msg_origin
        if umo not in self.cfg.enabled_sessions:
            self.cfg.add_enabled_session(umo)
            yield event.plain_result("解析已开启")
        else:
            yield event.plain_result("解析已处于开启状态")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("关闭解析")
    async def disable_parser(self, event: AstrMessageEvent):
        """Disable parser for current session."""
        umo = event.unified_msg_origin
        if umo in self.cfg.enabled_sessions:
            self.cfg.remove_enabled_session(umo)
            yield event.plain_result("解析已关闭")
        elif len(self.cfg.enabled_sessions) == 0:
            yield event.plain_result("白名单为空，当前为全局开启模式")
        else:
            yield event.plain_result("解析已处于关闭状态")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("解析状态")
    async def parser_status(self, event: AstrMessageEvent):
        """Show current parser status."""
        umo = event.unified_msg_origin
        is_enabled = self.cfg.is_session_enabled(
            umo, event.is_admin(), event.is_at_or_wake_command
        )

        status_text = (
            "媒体解析插件状态\n\n"
            f"当前会话: {'已开启' if is_enabled else '已关闭'}\n"
            f"白名单会话数: {len(self.cfg.enabled_sessions)}\n"
            f"防抖时间: {self.cfg.debounce_interval}s\n"
            f"最大文件大小: {self.cfg.source_max_size}MB\n"
            f"最大视频时长: {self.cfg.source_max_minute}分钟\n"
            f"抖音信息渲染模式: {self.cfg.douyin_info_render_mode}\n"
            f"下载重试次数: {self.cfg.download_retry_times}\n"
            f"CF 代理: {'已启用' if self.cfg.enable_cf_proxy else '未启用'}"
        )
        yield event.plain_result(status_text)
