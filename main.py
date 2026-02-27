"""
媒体解析插件主文件 - 完全异步版本
支持解析抖音和小红书链接
"""
import re
import os
import asyncio
import base64
import tempfile
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

try:
    from .config import MediaParserConfig
    from .debounce import Debouncer
    from .async_dysk import AsyncDouyinDownloader
    from .async_xhs import AsyncXiaohongshuParser
except ImportError:
    from config import MediaParserConfig
    from debounce import Debouncer
    from async_dysk import AsyncDouyinDownloader
    from async_xhs import AsyncXiaohongshuParser


DOUYIN_INFO_CARD_TEMPLATE = """
<div style="
  width: {{ card_width }}px;
  height: {{ card_height }}px;
  min-height: {{ card_height }}px;
  font-family: HarmonyOSHans-Regular, bilifont, -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', 'Hiragino Sans GB', 'WenQuanYi Micro Hei', 'Segoe UI', 'Roboto', sans-serif;
  color: #f3f6ff;
  background: #0b1322;
  overflow: hidden;
  position: relative;
  box-sizing: border-box;
">
  <style>
    {% if font_regular_url %}
    @font-face {
      font-family: HarmonyOSHans-Regular;
      src: url('{{ font_regular_url }}') format('truetype');
      font-style: normal;
      font-weight: 400;
      font-display: swap;
    }
    {% endif %}
    {% if font_medium_url %}
    @font-face {
      font-family: HarmonyOSHans-Regular;
      src: url('{{ font_medium_url }}') format('truetype');
      font-style: normal;
      font-weight: 500;
      font-display: swap;
    }
    {% endif %}
    {% if font_bold_url %}
    @font-face {
      font-family: HarmonyOSHans-Regular;
      src: url('{{ font_bold_url }}') format('truetype');
      font-style: normal;
      font-weight: 700;
      font-display: swap;
    }
    {% endif %}
    html, body {
      width: {{ card_width }}px;
      height: {{ card_height }}px;
      margin: 0;
      padding: 0;
      overflow: hidden;
      background: transparent;
      font-family: HarmonyOSHans-Regular, bilifont, -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', 'Hiragino Sans GB', 'WenQuanYi Micro Hei', 'Segoe UI', 'Roboto', sans-serif;
    }
    * { box-sizing: border-box; }
  </style>
  {% if cover_url %}
  <img src="{{ cover_url }}" alt="cover" style="
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    filter: saturate(112%) brightness(1.02);
    transform: scale(1.02);
  " />
  <img src="{{ cover_url }}" alt="" style="
    position: absolute;
    inset: -24px;
    width: calc(100% + 48px);
    height: calc(100% + 48px);
    object-fit: cover;
    filter: blur(46px) saturate(112%);
    transform: scale(1.18);
    opacity: 0.1;
  " />
  {% endif %}

  <div style="
    position: absolute;
    left: 0;
    right: 0;
    top: 0;
    height: 44%;
    background: linear-gradient(
      to bottom,
      rgba(35, 39, 48, 0.34) 0%,
      rgba(35, 39, 48, 0.16) 56%,
      rgba(35, 39, 48, 0) 100%
    );
    z-index: 1;
  "></div>
  <div style="
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 46%;
    background: linear-gradient(
      to top,
      rgba(35, 39, 48, 0.4) 0%,
      rgba(35, 39, 48, 0.18) 56%,
      rgba(35, 39, 48, 0) 100%
    );
    z-index: 1;
  "></div>

  <div style="
    position: relative;
    z-index: 2;
    width: calc(100% / {{ ui_scale }});
    height: calc(100% / {{ ui_scale }});
    transform: scale({{ ui_scale }});
    transform-origin: top left;
    padding: 28px 34px 30px 34px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  ">
    <div>
      <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;">
        <div style="display: flex; align-items: center; gap: 14px; min-width: 0; width: 50%; max-width: 50%;">
          {% if author_avatar %}
          <img src="{{ author_avatar }}" alt="avatar" style="width: {{ avatar_size }}px; height: {{ avatar_size }}px; border-radius: 50%; object-fit: cover; border: 2px solid rgba(255,255,255,0.26);" />
          {% endif %}
          <div style="min-width: 0;">
            <div style="
              font-size: {{ author_font_size }}px;
              font-weight: 700;
              line-height: 1.14;
              overflow: hidden;
              {% if author_allow_wrap %}
              display: -webkit-box;
              -webkit-line-clamp: 2;
              -webkit-box-orient: vertical;
              white-space: normal;
              word-break: break-all;
              {% else %}
              white-space: nowrap;
              text-overflow: ellipsis;
              {% endif %}
            ">{{ author_name_html | safe }}</div>
          </div>
        </div>
        <div style="
          width: 50%;
          max-width: 50%;
          min-width: 0;
          display: flex;
          justify-content: flex-end;
          align-items: flex-start;
          flex-shrink: 0;
          gap: {{ stat_col_gap }}px;
          text-align: center;
          color: rgba(239, 245, 255, 0.92);
          text-shadow: 0 1px 6px rgba(0, 0, 0, 0.3);
        ">
        <div style="width: {{ stat_item_width }}px; display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 0;">
          <svg xmlns="http://www.w3.org/2000/svg" width="{{ stat_icon_size }}" height="{{ stat_icon_size }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="opacity: 0.66;">
            <path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"></path>
          </svg>
          <span style="display: block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; font-size: {{ stat_font_size }}px; font-weight: 500; white-space: nowrap; line-height: 1; font-variant-numeric: tabular-nums; font-feature-settings: 'tnum' 1; font-family: HarmonyOSHans-Regular, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif;">{{ digg_count }}</span>
        </div>
        <div style="width: {{ stat_item_width }}px; display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 0;">
          <svg xmlns="http://www.w3.org/2000/svg" width="{{ stat_icon_size }}" height="{{ stat_icon_size }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="opacity: 0.66;">
            <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"></path>
          </svg>
          <span style="display: block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; font-size: {{ stat_font_size }}px; font-weight: 500; white-space: nowrap; line-height: 1; font-variant-numeric: tabular-nums; font-feature-settings: 'tnum' 1; font-family: HarmonyOSHans-Regular, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif;">{{ comment_count }}</span>
        </div>
        <div style="width: {{ stat_item_width }}px; display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 0;">
          <svg xmlns="http://www.w3.org/2000/svg" width="{{ stat_icon_size }}" height="{{ stat_icon_size }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="opacity: 0.66;">
            <path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a.53.53 0 0 0 .399.29l5.166.751a.53.53 0 0 1 .294.904l-3.738 3.644a.53.53 0 0 0-.152.468l.882 5.14a.53.53 0 0 1-.768.559l-4.62-2.43a.53.53 0 0 0-.494 0l-4.62 2.43a.53.53 0 0 1-.768-.559l.882-5.14a.53.53 0 0 0-.152-.468L2.76 8.919a.53.53 0 0 1 .294-.904l5.166-.751a.53.53 0 0 0 .399-.29z"></path>
          </svg>
          <span style="display: block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; font-size: {{ stat_font_size }}px; font-weight: 500; white-space: nowrap; line-height: 1; font-variant-numeric: tabular-nums; font-feature-settings: 'tnum' 1; font-family: HarmonyOSHans-Regular, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif;">{{ collect_count }}</span>
        </div>
        <div style="width: {{ stat_item_width }}px; display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 0;">
          <svg xmlns="http://www.w3.org/2000/svg" width="{{ stat_icon_size }}" height="{{ stat_icon_size }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="opacity: 0.66;">
            <circle cx="18" cy="5" r="3"></circle>
            <circle cx="6" cy="12" r="3"></circle>
            <circle cx="18" cy="19" r="3"></circle>
            <line x1="8.59" x2="15.42" y1="13.51" y2="17.49"></line>
            <line x1="15.41" x2="8.59" y1="6.51" y2="10.49"></line>
          </svg>
          <span style="display: block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; font-size: {{ stat_font_size }}px; font-weight: 500; white-space: nowrap; line-height: 1; font-variant-numeric: tabular-nums; font-feature-settings: 'tnum' 1; font-family: HarmonyOSHans-Regular, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif;">{{ share_count }}</span>
        </div>
      </div>
      </div>

      <div style="
        margin-top: 10px;
        font-size: {{ meta_font_size }}px;
        line-height: 1.58;
        color: rgba(242, 247, 255, 0.95);
        word-break: break-word;
        text-shadow: 0 1px 3px rgba(0, 0, 0, 0.18);
      ">
        <div>{{ create_time_html | safe }}</div>
      </div>
    </div>

    <div style="
      align-self: stretch;
      width: calc(100% / {{ bottom_scale }});
      transform: scale({{ bottom_scale }});
      transform-origin: left bottom;
      display: flex;
      flex-direction: column;
      align-items: stretch;
      gap: 10px;
    ">
      <div style="
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        gap: 12px;
      ">
      <div style="
        width: 50%;
        max-width: 50%;
        min-width: 0;
        display: inline-flex;
        align-items: center;
        justify-content: flex-start;
        gap: 8px;
        flex-wrap: wrap;
        overflow: hidden;
      ">
        <div style="padding: 9px 16px; border-radius: 999px; font-size: 24px; background: rgba(255,255,255,0.19); white-space: nowrap;">
          {{ media_type_html | safe }}
        </div>
        {% if duration %}
        <div style="padding: 9px 16px; border-radius: 999px; font-size: 24px; background: rgba(255,255,255,0.19); white-space: nowrap;">
          {{ duration_html | safe }}
        </div>
        {% endif %}
      </div>

      <div style="
        width: 50%;
        max-width: 50%;
        min-width: 0;
        display: flex;
        justify-content: flex-end;
      ">
      {% if music_title and music_cover %}
      <div style="
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 6px 10px 6px 6px;
        border-radius: 16px;
        background: rgba(255, 255, 255, 0.2);
        backdrop-filter: blur(14px);
        border: 1px solid rgba(255,255,255,0.3);
        box-shadow: 0 8px 18px rgba(3, 8, 18, 0.22);
        overflow: hidden;
        max-width: 100%;
      ">
        <div style="position: relative; width: 44px; height: 44px; flex-shrink: 0;">
          <img src="{{ music_cover }}" alt="" style="
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            border-radius: 10px;
            object-fit: cover;
            filter: blur(4px);
            transform: scale(1.08);
          " />
          <img src="{{ music_cover }}" alt="" style="
            position: relative;
            width: 100%;
            height: 100%;
            border-radius: 10px;
            object-fit: cover;
            z-index: 1;
          " />
        </div>
        <div style="display: flex; flex-direction: column; gap: 2px; min-width: 0;">
          <div style="
            font-size: 20px;
            font-weight: 600;
            color: rgba(248, 251, 255, 0.96);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          ">{{ music_title_html | safe }}</div>
          <div style="
            font-size: 16px;
            color: rgba(233, 241, 255, 0.76);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          ">{{ music_author_html | safe }}</div>
        </div>
      </div>
      {% endif %}
      </div>
      </div>

      <div style="
        align-self: stretch;
        backdrop-filter: blur(20px);
        background: rgba(18, 33, 60, 0.16);
        border: 1px solid rgba(255,255,255,0.26);
        border-radius: 24px;
        padding: 20px 22px 22px 22px;
        box-shadow: 0 12px 24px rgba(3, 8, 18, 0.24);
      ">
        <div style="font-size: {{ desc_font_size }}px; line-height: 1.32; font-weight: 700; word-break: break-word; text-shadow: 0 2px 8px rgba(0,0,0,0.28);">
          {{ desc_html | safe }}
        </div>
      </div>
    </div>
  </div>
</div>
"""


@register("media_parser", "Author", "抖音小红书链接解析插件（异步优化版）", "2.2.0")
class MediaParserPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # Configuration
        self.cfg = MediaParserConfig(config)
        # Debouncer
        self.debouncer = Debouncer(lambda: self.cfg.debounce_interval)
        # Parsers
        self.xhs_parser = AsyncXiaohongshuParser()
        self._font_urls = self._build_local_font_urls()
        # URL patterns
        self.dy_patterns = [
            r"https?://v\.douyin\.com/[a-zA-Z0-9_-]+/?",
            r"https?://(?:www\.)?douyin\.com/[^\s]+",
            r"https?://(?:www\.)?iesdouyin\.com/[^\s]+",
        ]
        self.xhs_patterns = [
            r"https?://(?:www\.)?xiaohongshu\.com/[^\s]+",
            r"https?://xhslink\.com/[^\s]+",
        ]

        logger.info("媒体解析插件初始化完成（异步版）")
        logger.info(f"白名单会话数: {len(self.cfg.enabled_sessions)}")
        logger.info(f"防抖时间: {self.cfg.debounce_interval}s")
        logger.info(f"最大文件大小: {self.cfg.source_max_size}MB")
        logger.info(f"最大视频时长: {self.cfg.source_max_minute}分钟")
        logger.info(f"抖音信息渲染模式: {self.cfg.douyin_info_render_mode}")
        if self._font_urls:
            logger.info("已加载本地 HarmonyOS 字体资源")

    async def terminate(self):
        """Release parser resources on plugin unload."""
        logger.info("正在清理资源...")
        if self.xhs_parser:
            await self.xhs_parser.close()
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
        if self.debouncer.hit_link(umo, check_url):
            logger.warning(
                f"[debounce] Skip parsing duplicated link within interval: {check_url}"
            )
            return

        # ========== 瑙ｆ瀽澶勭悊 ==========
        if dy_url:
            async for result in self.parse_douyin(event, dy_url):
                yield result
            event.stop_event()
        elif xhs_url:
            async for result in self.parse_xiaohongshu(event, xhs_url):
                yield result
            event.stop_event()

    # ==================== 鎶栭煶瑙ｆ瀽锛堝畬鍏ㄥ紓姝ワ級====================

    async def parse_douyin(self, event: AstrMessageEvent, url: str):
        """Parse Douyin link asynchronously."""
        try:
            logger.info(f"Start parsing Douyin link: {url}")

            # Create a new downloader per request to avoid session reuse issues.
            dy_downloader = AsyncDouyinDownloader(
                enable_cf_proxy=self.cfg.enable_cf_proxy,
                cf_proxy_url=self.cfg.cf_proxy_url,
                download_retry_times=self.cfg.download_retry_times,
                download_timeout=self.cfg.download_timeout,
                common_timeout=self.cfg.common_timeout,
                max_size=self.cfg.max_size,
                max_duration=self.cfg.max_duration,
            )

            try:
                result = await dy_downloader.get_detail(url)

                if not result:
                    logger.error("Douyin parse returned None")
                    yield event.plain_result(f"Parse failed. Open link directly:\n{url}")
                    return

                uin = event.get_sender_id()
                name = event.get_sender_name()

                downloads = result.get("downloads", [])
                images, video_links = self._extract_douyin_media(downloads)
                media_bytes_cache: Dict[str, bytes] = {}

                # Info render mode: text / image / both
                render_mode = self.cfg.douyin_info_render_mode
                if render_mode in {"image", "both"}:
                    info_image_url = await self._render_douyin_info_image(
                        result=result,
                        image_count=len(images),
                        video_count=len(video_links),
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

            finally:
                await dy_downloader.close()

        except Exception as e:
            error_msg = f"Douyin parse failed: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            if self.cfg.show_download_fail_tip:
                yield event.plain_result(f"Parse failed: {str(e)}")

    @staticmethod
    def _count_cjk(text: str) -> int:
        return len(re.findall(r"[\u4e00-\u9fff]", text))

    @staticmethod
    def _mojibake_score(text: str) -> int:
        if not text:
            return 0
        markers = (
            "Ã",
            "Â",
            "â",
            "å",
            "ä",
            "ç",
            "é",
            "è",
            "ê",
            "ë",
            "ì",
            "í",
            "î",
            "ï",
            "ð",
            "ñ",
            "ò",
            "ó",
            "ô",
            "õ",
            "ö",
            "ù",
            "ú",
            "û",
            "ü",
            "ý",
            "þ",
            "€",
            "™",
            " ",
        )
        return sum(text.count(ch) for ch in markers)

    @staticmethod
    def _gbk_mojibake_score(text: str) -> int:
        if not text:
            return 0
        markers = (
            "锛",
            "銆",
            "鈥",
            "鈻",
            "鎴",
            "鐨",
            "鍦",
            "涓",
            "鏄",
            "浣",
            "鍙",
            "瀵",
            "璇",
            "鎵",
            "鍒",
            "绗",
            "澶",
            "鍥",
            "鏂",
            "鏃",
            "鍐",
            "寮",
            "闂",
            "閮",
        )
        return sum(text.count(ch) for ch in markers)

    @staticmethod
    def _common_cjk_score(text: str) -> int:
        if not text:
            return 0
        common = set(
            "的一是不了人我在有他这为之大来以个中上们到说国和地也子时道出而要于就下得可你年生会那后能对着事其里所去行过家十用发天如然作方成者多日都三小军二无同么经当起与好看学进种将还分此心前面又定见只主没公从知全工"
        )
        return sum(1 for ch in text if ch in common)

    @staticmethod
    def _text_quality(text: str) -> Tuple[int, int, int, int]:
        cjk = MediaParserPlugin._count_cjk(text)
        common = MediaParserPlugin._common_cjk_score(text)
        latin_bad = MediaParserPlugin._mojibake_score(text)
        gbk_bad = MediaParserPlugin._gbk_mojibake_score(text)
        bad = latin_bad * 2 + gbk_bad * 3 + text.count(" ") * 4
        quality = common * 4 + cjk - bad
        return quality, cjk, common, bad

    @staticmethod
    def _repair_mojibake_text(text: str) -> str:
        """Try to recover mojibake text from common wrong decoding paths."""
        if not text:
            return text

        best = text
        best_quality = MediaParserPlugin._text_quality(text)
        if (
            MediaParserPlugin._mojibake_score(text) == 0
            and MediaParserPlugin._gbk_mojibake_score(text) == 0
        ):
            return text

        for source_enc in ("latin1", "cp1252", "gb18030", "gbk"):
            try:
                candidate = text.encode(source_enc).decode("utf-8")
            except Exception:
                continue

            # Some payloads are double-garbled; try one extra pass.
            try:
                second = candidate.encode(source_enc).decode("utf-8")
                if MediaParserPlugin._text_quality(second)[0] > MediaParserPlugin._text_quality(candidate)[0]:
                    candidate = second
            except Exception:
                pass

            cand_quality = MediaParserPlugin._text_quality(candidate)
            if cand_quality[0] >= best_quality[0] + 2 or (
                cand_quality[0] > best_quality[0] and cand_quality[3] < best_quality[3]
            ):
                best = candidate
                best_quality = cand_quality

        return best

    @staticmethod
    def _normalize_text(value: Any, default: str = "") -> str:
        if value is None:
            return default
        text = MediaParserPlugin._repair_mojibake_text(str(value))
        text = text.replace("\r", " ").replace("\n", " ").strip()
        text = re.sub(r"\s+", " ", text)
        return text or default

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
        out: List[str] = []
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
    def _pick_cover_url(downloads: List[Any]) -> str:
        for item in downloads:
            if MediaParserPlugin._is_http_url(item):
                return item
            if isinstance(item, dict):
                for key in ("cover", "image"):
                    value = item.get(key)
                    if MediaParserPlugin._is_http_url(value):
                        return value
        return ""

    def _extract_douyin_media(self, downloads: List[Any]) -> Tuple[List[str], List[str]]:
        images: List[str] = []
        video_links: List[str] = []

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

    def _build_douyin_info_nodes(self, result: Dict[str, Any], uin: str, name: str) -> List[Any]:
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
        result: Dict[str, Any],
        image_count: int,
        video_count: int,
        dy_downloader: AsyncDouyinDownloader,
        media_bytes_cache: Optional[Dict[str, bytes]] = None,
    ) -> Optional[str]:
        try:
            author = result.get("author") or {}
            stats = result.get("statistics") or {}
            music = result.get("music") or {}
            desc = self._normalize_text(result.get("desc"), "无描述")
            if len(desc) > 80:
                desc = desc[:77] + "..."

            cover_source_url = self._pick_cover_url(result.get("downloads", []))
            cover_url = await self._to_data_url_if_possible(
                dy_downloader,
                cover_source_url,
                media_bytes_cache,
            )
            author_avatar = await self._to_data_url_if_possible(
                dy_downloader,
                self._normalize_text(author.get("avatar"), ""),
                media_bytes_cache,
            )
            music_cover = await self._to_data_url_if_possible(
                dy_downloader,
                self._normalize_text(music.get("cover"), ""),
                media_bytes_cache,
            )
            cover_raw = (
                media_bytes_cache.get(cover_source_url, b"")
                if media_bytes_cache and cover_source_url
                else b""
            )
            cover_size = self._get_image_size(cover_raw)
            if not cover_size:
                cover_size = self._get_image_size_from_data_url(cover_url)
            card_width, card_height, ui_scale = self._compute_render_size(cover_size)
            overlay_metrics = self._compute_overlay_metrics(card_width, card_height)
            author_name = self._normalize_text(author.get("nickname"), "未知作者")
            media_type = self._normalize_text(result.get("type"), "unknown")
            create_time = self._normalize_text(result.get("create_time"), "-")
            duration = self._normalize_text(result.get("duration"), "")
            music_title = self._normalize_text(music.get("title"), "")
            music_author = self._normalize_text(music.get("author"), "")

            render_data = {
                "work_id": self._normalize_text(result.get("id"), "-"),
                "desc": desc,
                "media_type": media_type,
                "author_name": author_name,
                "author_avatar": author_avatar,
                "cover_url": cover_url,
                "digg_count": self._format_count(stats.get("digg_count", 0)),
                "comment_count": self._format_count(stats.get("comment_count", 0)),
                "collect_count": self._format_count(stats.get("collect_count", 0)),
                "share_count": self._format_count(stats.get("share_count", 0)),
                "create_time": create_time,
                "duration": duration,
                "music_title": music_title,
                "music_author": music_author,
                "music_cover": music_cover,
                "author_name_html": self._to_html_entities(author_name),
                "desc_html": self._to_html_entities(desc),
                "media_type_html": self._to_html_entities(media_type),
                "create_time_html": self._to_html_entities(create_time),
                "duration_html": self._to_html_entities(duration),
                "music_title_html": self._to_html_entities(music_title),
                "music_author_html": self._to_html_entities(music_author),
                "image_count": image_count,
                "video_count": video_count,
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
        media_bytes_cache: Optional[Dict[str, bytes]] = None,
    ) -> str:
        if not self._is_http_url(source_url):
            return source_url

        if media_bytes_cache is not None and source_url in media_bytes_cache:
            cached_bytes = media_bytes_cache.get(source_url, b"")
            if cached_bytes:
                mime = self._detect_image_mime(cached_bytes)
                base64_str = base64.b64encode(cached_bytes).decode("ascii")
                return f"data:{mime};base64,{base64_str}"

        temp_path = None
        try:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            temp_path = temp_file.name
            temp_file.close()

            success = await dy_downloader.download_video(source_url, temp_path)
            if success and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                with open(temp_path, "rb") as f:
                    raw = f.read()
                if media_bytes_cache is not None:
                    media_bytes_cache[source_url] = raw
                mime = self._detect_image_mime(raw)
                base64_str = base64.b64encode(raw).decode("ascii")
                return f"data:{mime};base64,{base64_str}"
        except Exception as e:
            logger.debug(f"Failed to convert resource to data URL, fallback URL: {source_url}, error: {e}")
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

        return source_url

    def _build_local_font_urls(self) -> Dict[str, str]:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        font_dir = os.path.join(base_dir, "fonts")
        file_map = {
            "regular": "HarmonyOS_Sans_SC_Regular.ttf",
            "medium": "HarmonyOS_Sans_SC_Medium.ttf",
            "bold": "HarmonyOS_Sans_SC_Bold.ttf",
        }
        urls: Dict[str, str] = {}
        for key, file_name in file_map.items():
            file_path = os.path.join(font_dir, file_name)
            if os.path.exists(file_path):
                urls[key] = self._path_to_file_url(file_path)
        return urls

    @staticmethod
    def _path_to_file_url(path: str) -> str:
        return Path(path).resolve().as_uri()

    @staticmethod
    def _get_image_size(raw: bytes) -> Optional[Tuple[int, int]]:
        if not raw or len(raw) < 10:
            return None

        # PNG
        if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
            width = int.from_bytes(raw[16:20], "big")
            height = int.from_bytes(raw[20:24], "big")
            if width > 0 and height > 0:
                return width, height

        # GIF
        if raw.startswith((b"GIF87a", b"GIF89a")) and len(raw) >= 10:
            width = int.from_bytes(raw[6:8], "little")
            height = int.from_bytes(raw[8:10], "little")
            if width > 0 and height > 0:
                return width, height

        # JPEG
        if raw.startswith(b"\xff\xd8"):
            idx = 2
            raw_len = len(raw)
            sof_markers = {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }
            while idx + 8 < raw_len:
                if raw[idx] != 0xFF:
                    idx += 1
                    continue
                marker = raw[idx + 1]
                idx += 2
                if marker in (0xD8, 0xD9):
                    continue
                if idx + 2 > raw_len:
                    break
                seg_len = int.from_bytes(raw[idx : idx + 2], "big")
                if seg_len < 2 or idx + seg_len > raw_len:
                    break
                if marker in sof_markers and idx + 7 < raw_len:
                    height = int.from_bytes(raw[idx + 3 : idx + 5], "big")
                    width = int.from_bytes(raw[idx + 5 : idx + 7], "big")
                    if width > 0 and height > 0:
                        return width, height
                idx += seg_len

        # WEBP
        if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            chunk = raw[12:16]
            if chunk == b"VP8X" and len(raw) >= 30:
                width = int.from_bytes(raw[24:27], "little") + 1
                height = int.from_bytes(raw[27:30], "little") + 1
                if width > 0 and height > 0:
                    return width, height
            if chunk == b"VP8L" and len(raw) >= 25:
                b0 = raw[21]
                b1 = raw[22]
                b2 = raw[23]
                b3 = raw[24]
                width = ((b1 & 0x3F) << 8 | b0) + 1
                height = ((b3 & 0x0F) << 10 | b2 << 2 | (b1 >> 6)) + 1
                if width > 0 and height > 0:
                    return width, height
            if chunk == b"VP8 " and len(raw) >= 30:
                # Lossy WebP (VP8): parse frame header width/height.
                # Layout: 3-byte frame tag + 0x9d012a start code + 2-byte w + 2-byte h.
                if raw[23:26] == b"\x9d\x01\x2a":
                    width = int.from_bytes(raw[26:28], "little") & 0x3FFF
                    height = int.from_bytes(raw[28:30], "little") & 0x3FFF
                    if width > 0 and height > 0:
                        return width, height

        return None

    @staticmethod
    def _get_image_size_from_data_url(data_url: str) -> Optional[Tuple[int, int]]:
        if not data_url or not data_url.startswith("data:image"):
            return None
        comma_idx = data_url.find(",")
        if comma_idx <= 0:
            return None
        meta = data_url[:comma_idx].lower()
        payload = data_url[comma_idx + 1 :]
        if ";base64" not in meta:
            return None
        try:
            raw = base64.b64decode(payload)
        except Exception:
            return None
        return MediaParserPlugin._get_image_size(raw)

    @staticmethod
    def _compute_render_size(
        cover_size: Optional[Tuple[int, int]]
    ) -> Tuple[int, int, float]:
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
    def _compute_overlay_metrics(card_width: int, card_height: int) -> Dict[str, Any]:
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
        stat_item_width = max(44, min(int(round(76 * base)), int(round(stat_item_width_limit))))

        meta_font_size = max(19, min(24, int(round(23 * base))))
        desc_font_size = max(26, min(38, int(round(card_width * 0.043))))

        # Keep portrait bottom controls unchanged while enlarging landscape.
        aspect = card_width / max(1, card_height)
        if aspect >= 1.5:
            bottom_scale = 1.65
        elif aspect >= 1.15:
            bottom_scale = 1
        elif aspect >= 0.9:
            bottom_scale = 0.95
        else:
            bottom_scale = 0.95

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
        media_bytes_cache: Optional[Dict[str, bytes]] = None,
    ):
        """Download and send media files asynchronously."""
        logger.info(
            f"Start sending media files: {len(images)} images, {len(video_links)} videos"
        )

        # Download images
        for i, img_url in enumerate(images):
            temp_path = None
            try:
                logger.info(f"Downloading image {i+1}/{len(images)}")

                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                temp_path = temp_file.name
                temp_file.close()

                success = False
                # Reuse cache first to avoid duplicate downloads.
                if media_bytes_cache and img_url in media_bytes_cache:
                    raw = media_bytes_cache.get(img_url, b"")
                    if raw:
                        with open(temp_path, "wb") as f:
                            f.write(raw)
                        success = True

                if not success:
                    success = await dy_downloader.download_video(img_url, temp_path)
                    if (
                        success
                        and media_bytes_cache is not None
                        and os.path.exists(temp_path)
                        and os.path.getsize(temp_path) > 0
                    ):
                        try:
                            with open(temp_path, "rb") as f:
                                media_bytes_cache[img_url] = f.read()
                        except Exception:
                            pass

                await asyncio.sleep(2)

                if success and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    result = event.make_result()
                    result.chain = [Comp.Image.fromFileSystem(temp_path)]
                    await event.send(result)
                    logger.info(f"Image {i+1} sent successfully")
                else:
                    if self.cfg.show_download_fail_tip:
                        await event.send(event.plain_result(f"Image download failed: {img_url}"))
                    logger.warning(f"Image {i+1} download failed")

            except Exception as e:
                logger.error(f"Image {i+1} processing error: {e}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except Exception as e:
                        logger.warning(f"Failed to cleanup temp file: {temp_path}, {e}")

        # Download videos
        for i, video_url in enumerate(video_links):
            temp_path = None
            try:
                logger.info(f"Downloading video {i+1}/{len(video_links)}")

                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                temp_path = temp_file.name
                temp_file.close()

                success = await dy_downloader.download_video(video_url, temp_path)

                await asyncio.sleep(3)

                # Video file should be at least 10KB.
                min_video_size = 10 * 1024
                if success and os.path.exists(temp_path):
                    file_size = os.path.getsize(temp_path)
                    if file_size >= min_video_size:
                        result = event.make_result()
                        result.chain = [Comp.Video.fromFileSystem(temp_path)]
                        await event.send(result)
                        logger.info(f"Video {i+1} sent successfully, size: {file_size} bytes")
                    else:
                        logger.warning(
                            f"Video {i+1} file too small ({file_size} bytes), skip sending"
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
                    logger.warning(f"Video {i+1} download failed")

            except Exception as e:
                logger.error(f"Video {i+1} processing error: {e}")
            finally:
                if temp_path and os.path.exists(temp_path):
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

            nodes = []
            nodes.append(
                Comp.Node(
                    uin=uin,
                    name=name,
                    content=[Comp.Plain(f"title: {result.get('title', 'Xiaohongshu content')}")],
                )
            )
            nodes.append(
                Comp.Node(
                    uin=uin,
                    name=name,
                    content=[Comp.Plain(f"content: {result.get('content', '')}")],
                )
            )

            yield event.chain_result([Comp.Nodes(nodes=nodes)])

            if result.get("cover"):
                yield event.chain_result([Comp.Image.fromURL(result["cover"])])

            if result.get("images"):
                for img_url in result["images"]:
                    yield event.chain_result([Comp.Image.fromURL(img_url)])

            if result.get("videos"):
                for video_url in result["videos"]:
                    yield event.chain_result([Comp.Video.fromURL(video_url)])

        except Exception as e:
            error_msg = f"Xiaohongshu parse failed: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            if self.cfg.show_download_fail_tip:
                yield event.plain_result(f"Parse failed: {str(e)}")

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
