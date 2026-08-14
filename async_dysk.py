"""
异步版本的抖音下载器
保持原有解析逻辑，使用 aiohttp 替代 requests
特别注意 Cookie 的传递问题
"""
import re
import os
import json
import random
import string
import asyncio
import base64
import binascii
import time
import traceback
from typing import Optional, Dict
from urllib.parse import urlparse, urljoin

import aiohttp
from aiohttp import CookieJar
from astrbot.api import logger

# 从同步版本导入 ABogus 和 Extractor
try:
    from .dysk import ABogus, Extractor, USERAGENT
    from .utils import result_mojibake_score, decode_text_bytes
except ImportError:
    from dysk import ABogus, Extractor, USERAGENT
    from utils import result_mojibake_score, decode_text_bytes

# Token 有效期（秒），超过后重新初始化
_TOKEN_TTL = 1800


class AsyncDouyinDownloader:
    """异步抖音下载器 - 支持实例复用"""

    def __init__(
        self,
        enable_cf_proxy=False,
        cf_proxy_url="",
        download_retry_times=3,
        download_timeout=280,
        common_timeout=15,
        max_size=None,
        max_duration=None
    ):
        self.ab = ABogus(USERAGENT)
        self.extractor = Extractor()
        self.enable_cf_proxy = enable_cf_proxy
        self.cf_proxy_url = cf_proxy_url.rstrip("/") if cf_proxy_url else ""

        # 配置参数
        self.download_retry_times = self._normalize_retry_times(download_retry_times)
        self.download_timeout = download_timeout
        self.common_timeout = common_timeout
        self.max_size = max_size  # 字节
        self.max_duration = max_duration  # 秒

        # ========== Cookie 管理 ==========
        self._cookie_jar = CookieJar(unsafe=True)
        self._cookies: Dict[str, str] = {}

        # Session 延迟创建
        self._session: Optional[aiohttp.ClientSession] = None
        self._initialized = False
        self._init_time: float = 0

    def update_config(
        self,
        enable_cf_proxy: bool,
        cf_proxy_url: str,
        download_retry_times: int,
        download_timeout: int,
        common_timeout: int,
        max_size: Optional[int],
        max_duration: Optional[int],
    ):
        """Update runtime config without recreating the instance."""
        self.enable_cf_proxy = enable_cf_proxy
        self.cf_proxy_url = cf_proxy_url.rstrip("/") if cf_proxy_url else ""
        self.download_retry_times = self._normalize_retry_times(download_retry_times)
        self.download_timeout = download_timeout
        self.common_timeout = common_timeout
        self.max_size = max_size
        self.max_duration = max_duration

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 session - 使用 CookieJar 自动管理 cookies"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.common_timeout)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                cookie_jar=self._cookie_jar
            )
        return self._session

    async def close(self):
        """关闭 session"""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._initialized = False

    async def _ensure_tokens(self):
        """确保 token 已初始化且未过期，过期则重新获取"""
        now = time.monotonic()
        if self._initialized and (now - self._init_time) < _TOKEN_TTL:
            return
        # Token 过期或首次初始化
        await self._init_tokens()

    async def _init_tokens(self):
        """初始化 tokens（msToken 和 ttwid）"""
        logger.info("正在初始化 (获取 ttwid/msToken)...")

        # 1. 生成 msToken
        base_str = string.digits + string.ascii_letters
        ms_token = "".join(random.choice(base_str) for _ in range(156))
        self._cookies["msToken"] = ms_token
        logger.debug(f"生成 msToken: {ms_token[:20]}...")

        # 2. 尝试获取 ttwid
        data = {
            "region": "cn",
            "aid": 1768,
            "needFid": False,
            "service": "www.ixigua.com",
            "migrate_info": {"ticket": "", "source": "node"},
            "cbUrlProtocol": "https",
            "union": True
        }

        session = await self._get_session()

        # 使用 CF 代理或直连
        if self.enable_cf_proxy and self.cf_proxy_url:
            url = f"{self.cf_proxy_url}/ttwid/ttwid/union/register/"
        else:
            url = "https://ttwid.bytedance.com/ttwid/union/register/"

        try:
            async with session.post(url, json=data) as resp:
                if resp.status == 200:
                    logger.info("ttwid 初始化成功")
                else:
                    logger.warning(f"初始化 ttwid 失败: HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"初始化 ttwid 异常: {e}")

        self._initialized = True
        self._init_time = time.monotonic()

    def _get_cookie_string(self) -> str:
        """
        构建 Cookie 字符串
        从 CookieJar 中提取所有 cookies 并合并手动设置的 cookies
        """
        cookies_dict = {}

        # 1. 从 CookieJar 中提取 cookies
        for cookie in self._cookie_jar:
            cookies_dict[cookie.key] = cookie.value

        # 2. 合并手动设置的 cookies（如 msToken）
        cookies_dict.update(self._cookies)

        # 3. 构建 Cookie 字符串
        return "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])

    @staticmethod
    def _is_valid_http_url(url: str) -> bool:
        if not isinstance(url, str) or not url:
            return False
        try:
            parsed = urlparse(url.strip())
        except Exception:
            return False
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _normalize_retry_times(value) -> int:
        """Normalize the configured number of retries after the first attempt."""
        try:
            retries = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, min(10, retries))

    @property
    def _attempt_limit(self) -> int:
        """Return the first attempt plus the configured extra retries."""
        return self.download_retry_times + 1

    @staticmethod
    def _extract_http_url(value: str) -> str:
        """Extract the first HTTP URL from a URL or a full share message."""
        if not isinstance(value, str):
            return ""
        match = re.search(r"https?://[^\s<>\"']+", value)
        if not match:
            return ""
        return match.group(0).rstrip(",.;:!?)]}>，。；：！？）】》」』")

    @staticmethod
    def _extract_aweme_id(url: str) -> Optional[str]:
        """Extract a Douyin work ID from a path or query parameter."""
        if not isinstance(url, str) or not url:
            return None
        match = re.search(r"/(?:video|note|slides)/(\d+)", url)
        if match:
            return match.group(1)
        match = re.search(r"(?:[?&](?:modal_id|mid|aweme_id)=)(\d+)", url)
        return match.group(1) if match else None

    @classmethod
    def _extract_aweme_id_from_response(cls, response) -> Optional[str]:
        """Inspect successful redirect hops and the final response URL."""
        candidates = []
        for item in getattr(response, "history", ()) or ():
            if getattr(item, "status", 0) >= 400:
                continue
            history_url = str(getattr(item, "url", "") or "")
            if history_url:
                candidates.append(history_url)
            location = getattr(item, "headers", {}).get("Location")
            if location:
                candidates.append(urljoin(history_url, location))
        if getattr(response, "status", 0) < 500:
            final_url = str(getattr(response, "url", "") or "")
            if final_url:
                candidates.append(final_url)

        for candidate in candidates:
            aweme_id = cls._extract_aweme_id(candidate)
            if aweme_id:
                return aweme_id
        return None

    async def get_detail(self, url_input: str) -> Optional[dict]:
        """获取视频详情（主入口）"""
        try:
            url = self._extract_http_url(url_input)
            if not self._is_valid_http_url(url):
                logger.error(f"无效链接: {url_input}")
                return None

            # 确保已初始化
            await self._ensure_tokens()

            # 1. 解析短链接获取 aweme_id
            aweme_id = await self._resolve_short_url(url)

            if not aweme_id:
                logger.error("无法解析出 aweme_id")
                return None

            logger.info(f"解析到 ID: {aweme_id}")

            # 2. 构造 API 请求参数
            params = {
                "device_platform": "webapp",
                "aid": "6383",
                "channel": "channel_pc_web",
                "aweme_id": aweme_id,
                "update_version_code": "170400",
                "pc_client_type": "1",
                "version_code": "190500",
                "version_name": "19.5.0",
                "cookie_enabled": "true",
                "platform": "PC",
                "downlink": "10",
                "msToken": self._cookies.get("msToken", "")
            }

            # 3. 生成 a_bogus
            params["a_bogus"] = self.ab.get_value(params)

            # 4. 发送 API 请求
            result = await self._fetch_detail_api(aweme_id, params)
            if not result:
                return None

            # CF 详情链路如果疑似乱码，尝试直连重试并择优结果。
            if self.enable_cf_proxy and self.cf_proxy_url:
                cf_score = result_mojibake_score(result)
                if cf_score >= 3:
                    logger.warning(
                        f"Detected possible mojibake in CF detail response (score={cf_score}), retrying direct API"
                    )
                    direct_result = await self._fetch_detail_api(
                        aweme_id, params, force_direct=True
                    )
                    if direct_result:
                        direct_score = result_mojibake_score(direct_result)
                        if direct_score + 1 < cf_score:
                            logger.info(
                                f"Using direct API detail result to avoid mojibake (cf={cf_score}, direct={direct_score})"
                            )
                            return direct_result

            return result

        except Exception as e:
            logger.error(f"get_detail 异常: {e}")
            logger.error(traceback.format_exc())
            return None

    async def _resolve_short_url(self, url: str) -> Optional[str]:
        """Resolve a Douyin URL and extract its work ID."""
        url = self._extract_http_url(url)
        if not self._is_valid_http_url(url):
            return None

        session = await self._get_session()
        headers = {
            "User-Agent": USERAGENT,
            "Referer": "https://www.douyin.com/",
        }

        # CF only proxies the detail API; short links still resolve directly.
        if self.enable_cf_proxy and self.cf_proxy_url:
            headers["Cookie"] = self._get_cookie_string()

        # Visit full and short links alike so the landing page can populate the
        # CookieJar before the detail API request. Douyin may return 404 for the
        # final HEAD even though the URL (and its ID) are valid.
        try:
            async with session.head(
                url,
                headers=headers,
                allow_redirects=True,
            ) as resp:
                logger.debug(
                    f"短链 HEAD 响应: HTTP {resp.status}, 最终URL: {resp.url}"
                )
                aweme_id = self._extract_aweme_id_from_response(resp)
                if aweme_id:
                    self._log_cookie_names()
                    return aweme_id
                logger.debug("短链 HEAD 重定向链未包含作品ID，回退 GET")
        except Exception as e:
            logger.warning(f"短链 HEAD 请求失败，回退 GET: {e}")

        # Some endpoints treat HEAD differently. GET is a compatibility fallback;
        # transient failures use one initial attempt plus configured extra retries.
        for attempt in range(self._attempt_limit):
            failure = None
            try:
                async with session.get(
                    url,
                    headers=headers,
                    allow_redirects=True,
                ) as resp:
                    logger.debug(
                        f"短链 GET 响应: HTTP {resp.status}, 最终URL: {resp.url}"
                    )
                    aweme_id = self._extract_aweme_id_from_response(resp)
                    if aweme_id:
                        self._log_cookie_names()
                        return aweme_id
                    if resp.status in {408, 425, 429} or resp.status >= 500:
                        failure = f"HTTP {resp.status}"
                    else:
                        logger.error("链接解析失败: 重定向链中未找到作品ID")
                        return None
            except Exception as e:
                failure = f"{type(e).__name__}: {e}"

            if attempt + 1 >= self._attempt_limit:
                logger.error(
                    f"链接解析失败(GET尝试{self._attempt_limit}次): {failure}"
                )
                return None
            logger.warning(
                f"短链 GET 请求失败({failure})，准备第{attempt + 2}次尝试"
            )
            await asyncio.sleep(1)

        return None

    def _log_cookie_names(self):
        """Log cookie presence without exposing token values."""
        if logger.level <= 10:  # DEBUG level
            jar_names = [cookie.key for cookie in self._cookie_jar]
            manual_names = list(self._cookies)
            logger.debug(
                f"当前 Cookies: jar={jar_names}, manual={manual_names}"
            )

    async def _fetch_detail_api(
        self, aweme_id: str, params: dict, force_direct: bool = False
    ) -> Optional[dict]:
        """请求详情 API"""
        session = await self._get_session()

        # 使用 CF 代理或直连
        use_cf = bool(
            self.enable_cf_proxy and self.cf_proxy_url and not force_direct
        )
        if use_cf:
            api = f"{self.cf_proxy_url}/douyin/aweme/v1/web/aweme/detail/"
        else:
            api = "https://www.douyin.com/aweme/v1/web/aweme/detail/"

        # ========== 设置请求头 ==========
        headers = {
            "User-Agent": USERAGENT,
            "Referer": "https://www.douyin.com/",
        }

        # CF Worker模式需要手动传递Cookie
        # 直连模式不设置Cookie header，让CookieJar自动管理
        if use_cf:
            headers["Cookie"] = self._get_cookie_string()
            self._log_cookie_names()

        route = "CF" if use_cf else "direct"
        logger.debug(f"详情 API 路由: {route}, aweme_id={aweme_id}")

        try:
            async with session.get(api, params=params, headers=headers) as resp:
                logger.debug(f"详情 API 响应: route={route}, HTTP {resp.status}")
                raw = await resp.read()

                if resp.status != 200:
                    logger.error(
                        f"详情 API 请求失败: route={route}, HTTP {resp.status}, body_size={len(raw)}"
                    )
                    return None
                if not raw:
                    logger.error(f"详情 API 返回空响应: route={route}")
                    return None

                text = decode_text_bytes(raw)
                if not text:
                    logger.error(f"详情 API 响应解码后为空: route={route}")
                    return None

                try:
                    data = json.loads(text)
                except json.JSONDecodeError as e:
                    logger.error(
                        f"详情 API JSON 解析失败: route={route}, error={e.msg}"
                    )
                    return None

                # 代理层可能将上游原始响应包装为 Base64 JSON。
                if isinstance(data, dict) and "encoding" in data:
                    if data.get("encoding") != "base64" or not isinstance(
                        data.get("data"), str
                    ):
                        logger.error(f"详情 API 代理包装无效: route={route}")
                        return None
                    try:
                        decoded_raw = base64.b64decode(data["data"], validate=True)
                        decoded_text = decode_text_bytes(decoded_raw)
                        if not decoded_text:
                            logger.error("详情 API 代理上游返回空响应")
                            return None
                        data = json.loads(decoded_text)
                    except (binascii.Error, ValueError, json.JSONDecodeError) as e:
                        logger.error(
                            f"详情 API 代理响应解码失败: {type(e).__name__}"
                        )
                        return None

                if not isinstance(data, dict):
                    logger.error(
                        f"详情 API 响应类型无效: route={route}, type={type(data).__name__}"
                    )
                    return None

                status_code = data.get("status_code")
                if status_code not in (None, 0):
                    status_msg = str(data.get("status_msg") or "")[:120]
                    logger.warning(
                        f"详情 API 业务状态异常: route={route}, status_code={status_code}, status_msg={status_msg}"
                    )

                detail = data.get("aweme_detail")
                if not isinstance(detail, dict) or not detail:
                    logger.error(
                        f"详情 API 缺少 aweme_detail: route={route}, status_code={status_code}"
                    )
                    return None

                try:
                    return self.extractor.extract_data(detail)
                except Exception as e:
                    logger.error(
                        f"抖音详情字段提取失败: route={route}, error={type(e).__name__}: {e}"
                    )
                    return None
        except Exception as e:
            logger.error(
                f"详情 API 请求异常: route={route}, error={type(e).__name__}: {e}"
            )
            return None

    async def download_to_bytes(self, url: str) -> Optional[bytes]:
        """Download a URL directly into memory, returning bytes or None."""
        if not self._is_valid_http_url(url):
            return None
        session = await self._get_session()
        headers = {
            "User-Agent": USERAGENT,
            "Accept": "*/*",
            "Referer": "https://www.douyin.com/?recommend=1",
        }
        for attempt in range(self._attempt_limit):
            failure = None
            try:
                timeout = aiohttp.ClientTimeout(total=self.common_timeout)
                async with session.get(url, headers=headers, timeout=timeout) as resp:
                    if resp.status in (200, 206):
                        return await resp.read()
                    failure = f"HTTP {resp.status}"
                    if resp.status not in {408, 425, 429} and resp.status < 500:
                        logger.debug(f"[download_to_bytes] {failure} for {url}")
                        return None
            except Exception as e:
                failure = f"{type(e).__name__}: {e}"

            if attempt + 1 >= self._attempt_limit:
                logger.debug(f"[download_to_bytes] failed after {self._attempt_limit} attempts: {failure}")
                return None
            await asyncio.sleep(1)
        return None

    async def download_video(
        self,
        url: str,
        save_path: str = "video.mp4"
    ) -> bool:
        """
        下载视频或图片（支持断点续传和CF代理回退）

        下载策略：
        1. 先尝试直连下载，支持 Range 断点续传（CDN 可能中途断开连接）
        2. 如果全部失败且启用了CF代理，则尝试通过CF Worker代理下载
        """
        if not self._is_valid_http_url(url):
            logger.error(f"[下载] 无效URL: {url}")
            return False

        session = await self._get_session()

        # 下载请求头（参考 TikTokDownloader）
        # - 始终带 Range: bytes=0- 告知CDN客户端支持续传
        # - 使用极简Cookie，避免被CDN识别为异常请求
        # - Accept 使用 */* 而非复杂的 MIME 列表
        headers = {
            "User-Agent": USERAGENT,
            "Accept": "*/*",
            "Range": "bytes=0-",
            "Referer": "https://www.douyin.com/?recommend=1",
            "Cookie": "dy_swidth=1536; dy_sheight=864",
        }

        # ========== 第一步：尝试直连下载（支持断点续传）==========
        total_size = 0  # 已下载的总字节数
        expected_size = None  # 文件总大小（从首次请求获取）
        max_attempts = self._attempt_limit  # 首次请求 + 额外重试
        attempt = 0

        logger.info(f"[下载] 开始: {save_path}")

        while attempt < max_attempts:
            attempt += 1
            prev_size = total_size
            try:
                if attempt > 1:
                    await asyncio.sleep(min(2 * (attempt - 1) + 1, 10))

                req_headers = dict(headers)
                file_mode = 'wb'

                # 如果已有部分数据，使用 Range 请求续传
                if total_size > 0 and os.path.exists(save_path):
                    req_headers["Range"] = f"bytes={total_size}-"
                    file_mode = 'ab'  # 追加模式
                    logger.info(f"[下载] 续传从 {total_size} bytes 开始 (第{attempt}/{max_attempts}次请求)")
                elif attempt > 1:
                    logger.info(f"[下载] 重试 (第{attempt}次请求)")

                timeout = aiohttp.ClientTimeout(total=self.download_timeout)

                async with session.get(url, headers=req_headers, timeout=timeout) as resp:
                    status = resp.status

                    if status == 416:
                        # Range Not Satisfiable - 文件可能已完整
                        if total_size > 0:
                            logger.info(f"[下载] 服务器返回416，文件可能已完整: {total_size} bytes")
                            return True
                        logger.error(f"[下载] 失败: HTTP 416")
                        break

                    if status not in (200, 206):
                        logger.error(f"[下载] 失败: HTTP {status}")
                        if status == 403:
                            total_size = 0
                        continue

                    # 获取文件总大小
                    if status == 200:
                        total_size = 0
                        file_mode = 'wb'
                        expected_size = resp.content_length
                    elif status == 206:
                        content_range = resp.headers.get("Content-Range", "")
                        if "/" in content_range:
                            try:
                                expected_size = int(content_range.split("/")[-1])
                            except (ValueError, IndexError):
                                pass
                        if total_size == 0:
                            file_mode = 'wb'

                    if expected_size and self.max_size and expected_size > self.max_size:
                        size_mb = expected_size / 1024 / 1024
                        limit_mb = self.max_size / 1024 / 1024
                        logger.warning(f"[下载] 文件大小 {size_mb:.2f}MB 超过限制 {limit_mb:.2f}MB")
                        return False

                    try:
                        with open(save_path, file_mode) as f:
                            async for chunk in resp.content.iter_chunked(65536):
                                if chunk:
                                    f.write(chunk)
                                    total_size += len(chunk)

                                    if self.max_size and total_size > self.max_size:
                                        limit_mb = self.max_size / 1024 / 1024
                                        logger.warning(f"[下载] 实际大小超过限制 {limit_mb:.2f}MB，停止下载")
                                        f.close()
                                        if os.path.exists(save_path):
                                            os.unlink(save_path)
                                        return False

                        # 检查是否下载完整
                        if expected_size and total_size >= expected_size:
                            logger.info(f"[下载] 完成: {save_path}, 大小: {total_size} bytes")
                            return True
                        elif expected_size:
                            ratio = total_size / expected_size
                            if ratio >= 0.95:
                                logger.info(f"[下载] 近似完成（{ratio:.1%}）: {save_path}, {total_size}/{expected_size} bytes")
                                return True
                            else:
                                logger.warning(f"[下载] 连接断开，已下载 {ratio:.1%} ({total_size}/{expected_size} bytes)，将续传...")
                        else:
                            logger.info(f"[下载] 完成: {save_path}, 大小: {total_size} bytes")
                            return True

                    except aiohttp.ClientPayloadError:
                        if expected_size and total_size > 0:
                            ratio = total_size / expected_size
                            if ratio >= 0.95:
                                logger.warning(f"[下载] 近似完成（{ratio:.1%}）: {save_path}, {total_size}/{expected_size} bytes")
                                return True
                            logger.warning(f"[下载] 连接中断（{ratio:.1%}），已下载 {total_size}/{expected_size} bytes，将续传...")
                        elif total_size > 0:
                            logger.warning(f"[下载] 连接中断（无总大小），已下载 {total_size} bytes，将续传...")
                        else:
                            logger.error(f"[下载] Payload 错误，无数据")

            except asyncio.TimeoutError:
                if total_size > 0 and expected_size:
                    logger.warning(f"[下载] 超时，已下载 {total_size}/{expected_size} bytes，将续传...")
                else:
                    logger.error(f"[下载] 超时 (第{attempt}次请求)")
            except Exception as e:
                logger.error(f"[下载] 异常 (第{attempt}次请求): {e}")
                total_size = 0

            # 失败请求消耗一次尝试；下一轮使用 Range 续传已下载部分。
            if total_size > prev_size:
                logger.debug(
                    f"[下载] 本次请求取得 {total_size - prev_size} bytes，准备续传"
                )

        # 直连全部失败，检查是否有部分下载的数据可用
        if total_size > 0 and expected_size:
            ratio = total_size / expected_size
            if ratio >= 0.95:
                logger.warning(f"[下载] 重试耗尽但近似完成（{ratio:.1%}），保留文件")
                return True
            else:
                logger.error(f"[下载] 重试耗尽，仅下载 {ratio:.1%} ({total_size}/{expected_size} bytes)")
                if os.path.exists(save_path):
                    os.unlink(save_path)

        # ========== 第二步：如果直连失败且启用了CF代理，则尝试代理下载 ==========
        if self.enable_cf_proxy and self.cf_proxy_url:
            logger.info(f"[下载] 直连失败，尝试使用CF代理下载...")
            try:
                return await self._download_via_cf_proxy(url, save_path)
            except Exception as e:
                logger.error(f"[下载] CF代理下载失败: {e}")
                return False

        return False

    async def _download_via_cf_proxy(self, url: str, save_path: str) -> bool:
        """
        通过CF Worker代理下载文件（流式）

        Worker v3 直接流式转发二进制数据，不再 base64 编码。
        错误时返回 JSON（status != 200），成功时返回二进制流（status 200）。
        """
        if not self._is_valid_http_url(url):
            logger.error(f"[下载] CF代理目标URL无效: {url}")
            return False

        session = await self._get_session()

        # 确保 CF Worker URL 以 /download 结尾
        proxy_url = self.cf_proxy_url.rstrip("/")
        if not self._is_valid_http_url(proxy_url):
            logger.error(f"[下载] CF代理地址无效: {self.cf_proxy_url}")
            return False
        if not proxy_url.endswith("/download"):
            proxy_url = f"{proxy_url}/download"

        # CF Worker代理请求（对齐 TikTokDownloader 的下载头）
        proxy_data = {
            "url": url,
            "headers": {
                "User-Agent": USERAGENT,
                "Accept": "*/*",
                "Range": "bytes=0-",
                "Referer": "https://www.douyin.com/?recommend=1",
                "Cookie": "dy_swidth=1536; dy_sheight=864",
            }
        }

        try:
            logger.info(f"[下载] CF代理请求: {proxy_url}")

            timeout = aiohttp.ClientTimeout(total=self.download_timeout)
            async with session.post(
                proxy_url,
                json=proxy_data,
                timeout=timeout
            ) as resp:
                # Worker 错误时返回 JSON（status 4xx/5xx）
                if resp.status >= 400:
                    try:
                        err_data = await resp.json()
                        error = err_data.get("error", f"HTTP {resp.status}")
                    except Exception:
                        error = f"HTTP {resp.status}"
                    logger.error(f"[下载] CF代理返回错误: {error}")
                    return False

                # 检查文件大小限制
                content_length = resp.content_length
                if content_length and self.max_size and content_length > self.max_size:
                    limit_mb = self.max_size / 1024 / 1024
                    size_mb = content_length / 1024 / 1024
                    logger.warning(f"[下载] CF代理文件大小 {size_mb:.2f}MB 超过限制 {limit_mb:.2f}MB")
                    return False

                # 流式写入文件
                total_size = 0
                with open(save_path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        if chunk:
                            f.write(chunk)
                            total_size += len(chunk)

                            if self.max_size and total_size > self.max_size:
                                limit_mb = self.max_size / 1024 / 1024
                                logger.warning(f"[下载] CF代理实际大小超限 {limit_mb:.2f}MB，停止")
                                f.close()
                                if os.path.exists(save_path):
                                    os.unlink(save_path)
                                return False

                if total_size == 0:
                    logger.error(f"[下载] CF代理返回空内容")
                    return False

                logger.info(f"[下载] CF代理下载完成: {save_path}, 大小: {total_size} bytes")
                return True

        except asyncio.TimeoutError:
            logger.error(f"[下载] CF代理超时")
            return False
        except Exception as e:
            logger.error(f"[下载] CF代理异常: {e}")
            return False


# ========== 调试用的测试函数 ==========
async def test_downloader():
    """测试异步下载器"""
    downloader = AsyncDouyinDownloader()

    test_url = input("请输入抖音链接: ")

    try:
        result = await downloader.get_detail(test_url)
        if result:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print("解析失败")
    finally:
        await downloader.close()


if __name__ == "__main__":
    asyncio.run(test_downloader())
