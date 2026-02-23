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
import traceback
from typing import Optional, Dict

import aiohttp
from aiohttp import CookieJar
from astrbot.api import logger

# 从同步版本导入 ABogus 和 Extractor
try:
    from .dysk import ABogus, Extractor, USERAGENT
except ImportError:
    from dysk import ABogus, Extractor, USERAGENT


class AsyncDouyinDownloader:
    """异步抖音下载器 - 特别注意 Cookie 传递"""

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
        self.download_retry_times = download_retry_times
        self.download_timeout = download_timeout
        self.common_timeout = common_timeout
        self.max_size = max_size  # 字节
        self.max_duration = max_duration  # 秒

        # ========== Cookie 管理（关键修复）==========
        # 使用 aiohttp 的 CookieJar 来自动管理 cookies
        self._cookie_jar = CookieJar(unsafe=True)  # unsafe=True 允许跨域cookie
        self._cookies: Dict[str, str] = {}  # 用于手动传递给CF Worker

        # Session 延迟创建
        self._session: Optional[aiohttp.ClientSession] = None
        self._initialized = False

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 session - 使用 CookieJar 自动管理 cookies"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            # 创建session时传入cookie_jar，让aiohttp自动管理cookies
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                cookie_jar=self._cookie_jar
            )
        return self._session

    async def close(self):
        """关闭 session"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _init_tokens(self):
        """初始化 tokens（msToken 和 ttwid）"""
        if self._initialized:
            return

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
                    # CookieJar 会自动保存响应中的 cookies（如 ttwid）
                    logger.info("ttwid 初始化成功")
                else:
                    logger.warning(f"初始化 ttwid 失败: HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"初始化 ttwid 异常: {e}")

        self._initialized = True

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

    async def get_detail(self, url_input: str) -> Optional[dict]:
        """获取视频详情（主入口）"""
        try:
            # 确保已初始化
            await self._init_tokens()

            url = url_input.strip()

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
            return await self._fetch_detail_api(aweme_id, params)

        except Exception as e:
            logger.error(f"get_detail 异常: {e}")
            logger.error(traceback.format_exc())
            return None

    async def _resolve_short_url(self, url: str) -> Optional[str]:
        """
        解析短链接获取 aweme_id
        ========== 最关键的 Cookie 保存时机 ==========
        """
        url_match = re.search(r'(https?://\S+)', url)
        if url_match:
            url = url_match.group(1)

        session = await self._get_session()

        # ========== 重定向请求（Cookie 获取的关键时刻）==========
        headers = {
            "User-Agent": USERAGENT,
            "Referer": "https://www.douyin.com/",
        }

        # CF Worker模式需要手动传递Cookie（因为CF Worker不会自动转发cookies）
        # 直连模式不设置Cookie header，让CookieJar自动管理
        if self.enable_cf_proxy and self.cf_proxy_url:
            headers["Cookie"] = self._get_cookie_string()

        final_url = None
        for attempt in range(self.download_retry_times):
            try:
                # 使用 HEAD 请求跟随重定向
                async with session.head(
                    url,
                    headers=headers,
                    allow_redirects=True,
                    ssl=False
                ) as resp:
                    final_url = str(resp.url)
                    # CookieJar 会自动保存响应中的 cookies
                    break
            except Exception as e:
                if attempt == self.download_retry_times - 1:
                    logger.error(f"链接解析失败(重试{self.download_retry_times}次): {e}")
                    return None
                await asyncio.sleep(1)

        if not final_url:
            logger.error("链接解析失败: 无法获取重定向URL")
            return None

        logger.debug(f"重定向后 URL: {final_url}")

        # 调试：输出当前所有 cookies（仅在 debug 级别）
        if logger.level <= 10:  # DEBUG level
            logger.debug(f"==========当前 CookieJar中的 Cookies==========")
            for cookie in self._cookie_jar:
                cookie_value = cookie.value
                display_value = cookie_value[:50] if len(cookie_value) > 50 else cookie_value
                logger.debug(f"Cookie from Jar: {cookie.key}={display_value}")
            for k, v in self._cookies.items():
                display_value = v[:50] if len(v) > 50 else v
                logger.debug(f"Cookie manual: {k}={display_value}")

        # 从 URL 中提取 aweme_id
        pattern = re.compile(r'/(?:video|note|slides)/(\d+)')
        match = pattern.search(final_url)
        if match:
            return match.group(1)

        match = re.search(r'(?:modal_id|mid|aweme_id)=(\d+)', final_url)
        if match:
            return match.group(1)

        return None

    async def _fetch_detail_api(self, aweme_id: str, params: dict) -> Optional[dict]:
        """请求详情 API"""
        session = await self._get_session()

        # 使用 CF 代理或直连
        if self.enable_cf_proxy and self.cf_proxy_url:
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
        if self.enable_cf_proxy and self.cf_proxy_url:
            headers["Cookie"] = self._get_cookie_string()
            logger.debug(f"API 请求 Cookie 前50字符: {self._get_cookie_string()[:50]}...")

        logger.debug(f"API: {api}")
        logger.debug(f"请求参数: aweme_id={aweme_id}, a_bogus={params.get('a_bogus', '')[:20]}...")

        try:
            async with session.get(api, params=params, headers=headers, ssl=False) as resp:
                logger.debug(f"API 响应状态: {resp.status}")
                logger.debug(f"实际请求 URL: {resp.url}")

                # CookieJar 会自动保存响应中的 cookies

                if resp.status == 200:
                    try:
                        # 先读取响应文本，检查是否为空
                        text = await resp.text()
                        if not text or len(text) == 0:
                            logger.error("API 返回空响应")
                            return None

                        # 尝试解析 JSON
                        resp_json = json.loads(text)

                        # 如果使用 CF 代理，响应可能被 Base64 编码
                        if (self.enable_cf_proxy and self.cf_proxy_url and
                            isinstance(resp_json, dict) and 'encoding' in resp_json):
                            if resp_json.get('encoding') == 'base64':
                                decoded_text = base64.b64decode(resp_json['data']).decode('utf-8')
                                data = json.loads(decoded_text)
                            else:
                                data = resp_json
                        else:
                            data = resp_json

                        if data.get("aweme_detail"):
                            return self.extractor.extract_data(data["aweme_detail"])
                        else:
                            logger.error("未获取到 aweme_detail")
                            logger.debug(f"响应数据: {json.dumps(data, ensure_ascii=False)[:200]}...")
                            return None
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON 解析失败: {e}")
                        logger.debug(f"响应内容前200字符: {text[:200] if text else '空'}")
                        return None
                    except Exception as e:
                        logger.error(f"处理响应异常: {e}")
                        return None
                else:
                    logger.error(f"API 请求失败: HTTP {resp.status}")
                    text = await resp.text()
                    logger.debug(f"响应内容: {text[:200]}...")
                    return None
        except Exception as e:
            logger.error(f"API 请求异常: {e}")
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
        session = await self._get_session()

        headers = {
            "User-Agent": USERAGENT,
            "Accept": "image/webp,image/apng,image/*,video/mp4,*/*;q=0.8",
            "Referer": "https://www.douyin.com/",
        }

        # ========== 第一步：尝试直连下载（支持断点续传）==========
        total_size = 0  # 已下载的总字节数
        expected_size = None  # 文件总大小（从首次请求获取）
        max_retries = self.download_retry_times * 3  # 续传允许更多次重试

        logger.info(f"[下载] 开始: {save_path}")

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    await asyncio.sleep(min(2 * attempt, 10))

                req_headers = dict(headers)
                file_mode = 'wb'

                # 如果已有部分数据，使用 Range 请求续传
                if total_size > 0 and os.path.exists(save_path):
                    req_headers["Range"] = f"bytes={total_size}-"
                    file_mode = 'ab'  # 追加模式
                    logger.info(f"[下载] 续传从 {total_size} bytes 开始 (尝试 {attempt+1}/{max_retries})")
                elif attempt > 0:
                    logger.info(f"[下载] 重试 {attempt}/{max_retries}")

                timeout = aiohttp.ClientTimeout(total=self.download_timeout)

                async with session.get(url, headers=req_headers, ssl=False, timeout=timeout) as resp:
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
                            # 403 不续传，重置
                            total_size = 0
                        continue

                    # 首次请求（200）时获取文件总大小
                    if status == 200:
                        total_size = 0  # 200 表示服务器不支持续传或从头开始
                        file_mode = 'wb'
                        expected_size = resp.content_length
                        if expected_size and self.max_size and expected_size > self.max_size:
                            size_mb = expected_size / 1024 / 1024
                            limit_mb = self.max_size / 1024 / 1024
                            logger.warning(f"[下载] 文件大小 {size_mb:.2f}MB 超过限制 {limit_mb:.2f}MB")
                            return False
                    elif status == 206:
                        # 206 Partial Content - 续传成功
                        # 尝试从 Content-Range 获取总大小
                        content_range = resp.headers.get("Content-Range", "")
                        if "/" in content_range:
                            try:
                                expected_size = int(content_range.split("/")[-1])
                            except (ValueError, IndexError):
                                pass

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
                                continue  # 继续循环，下次用 Range 续传
                        else:
                            # 没有 expected_size，且流正常结束，视为成功
                            logger.info(f"[下载] 完成: {save_path}, 大小: {total_size} bytes")
                            return True

                    except aiohttp.ClientPayloadError:
                        # 连接中断，检查是否需要续传
                        if expected_size and total_size > 0:
                            ratio = total_size / expected_size
                            if ratio >= 0.95:
                                logger.warning(f"[下载] 近似完成（{ratio:.1%}）: {save_path}, {total_size}/{expected_size} bytes")
                                return True
                            logger.warning(f"[下载] 连接中断（{ratio:.1%}），已下载 {total_size}/{expected_size} bytes，将续传...")
                            continue
                        elif total_size > 0:
                            logger.warning(f"[下载] 连接中断（无总大小），已下载 {total_size} bytes，将续传...")
                            continue
                        else:
                            logger.error(f"[下载] Payload 错误，无数据")

            except asyncio.TimeoutError:
                if total_size > 0 and expected_size:
                    logger.warning(f"[下载] 超时，已下载 {total_size}/{expected_size} bytes，将续传...")
                    continue
                logger.error(f"[下载] 超时 (尝试 {attempt+1}/{max_retries})")
            except Exception as e:
                logger.error(f"[下载] 异常 (尝试 {attempt+1}/{max_retries}): {e}")
                # 异常后重置，从头开始
                total_size = 0

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
        通过CF Worker代理下载文件

        Args:
            url: 原始下载URL
            save_path: 保存路径

        Returns:
            bool: 是否成功
        """
        session = await self._get_session()

        # 确保 CF Worker URL 以 /download 结尾
        proxy_url = self.cf_proxy_url.rstrip("/")
        if not proxy_url.endswith("/download"):
            proxy_url = f"{proxy_url}/download"

        # CF Worker代理请求
        proxy_data = {
            "url": url,
            "headers": {
                "User-Agent": USERAGENT,
                "Accept": "image/webp,image/apng,image/*,video/mp4,*/*;q=0.8",
                "Referer": "https://www.douyin.com/",
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
                if resp.status != 200:
                    logger.error(f"[下载] CF代理返回错误: HTTP {resp.status}")
                    return False

                # 读取响应
                resp_data = await resp.json()

                # 检查是否成功
                if not resp_data.get("success"):
                    error = resp_data.get("error", "未知错误")
                    logger.error(f"[下载] CF代理返回错误: {error}")
                    return False

                # 获取文件内容（可能是base64编码）
                content = resp_data.get("content")
                if not content:
                    logger.error(f"[下载] CF代理返回空内容")
                    return False

                # 解码
                try:
                    file_data = base64.b64decode(content)
                except Exception:
                    file_data = content.encode() if isinstance(content, str) else content

                # 检查文件大小限制
                if self.max_size and len(file_data) > self.max_size:
                    limit_mb = self.max_size / 1024 / 1024
                    size_mb = len(file_data) / 1024 / 1024
                    logger.warning(f"[下载] CF代理文件大小 {size_mb:.2f}MB 超过限制 {limit_mb:.2f}MB")
                    return False

                with open(save_path, 'wb') as f:
                    f.write(file_data)

                logger.info(f"[下载] CF代理下载完成: {save_path}, 大小: {len(file_data)} bytes")
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
