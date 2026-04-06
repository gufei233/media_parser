"""
小红书 API 客户端 - 通过 edith.xiaohongshu.com API 获取无水印数据。
依赖 xhs_encrypt_helper.py 提供的加密函数。
"""
import time
import uuid
import json
import asyncio
from urllib.parse import urlparse

import aiohttp
from astrbot.api import logger

from . import xhs_encrypt_helper as enc


# RedCrack 项目中的默认 API 请求头
DEFAULT_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "content-type": "application/json;charset=UTF-8",
    "origin": "https://www.xiaohongshu.com",
    "priority": "u=1, i",
    "referer": "https://www.xiaohongshu.com/",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


class XhsApiClient:
    """轻量级小红书 API 客户端，仅用于获取笔记详情。"""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._cookies: dict = {}
        self._fp: dict = {}
        self._initialized = False

    # ==================== 生命周期 ====================

    async def initialize(self):
        """初始化：创建 session、生成全部 cookie 链。"""
        if self._initialized and self._session and not self._session.closed:
            return

        timeout = aiohttp.ClientTimeout(total=30)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers=DEFAULT_HEADERS.copy(),
        )

        try:
            await self._init_cookies()
            self._initialized = True
            logger.info("XhsApiClient 初始化完成")
        except Exception:
            await self.close()
            raise

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._initialized = False

    # ==================== Cookie 初始化链 ====================

    async def _init_cookies(self):
        """按顺序生成 a1→webId→websectiga→fingerprint→gid→web_session"""
        # 1) a1, webId
        a1, web_id = enc.generate_a1_and_webid()
        self._cookies.update({
            "a1": a1,
            "webId": web_id,
            "webBuild": enc.ARTIFACT_VERSION,
            "xsecappid": enc.APP_ID,
            "loadts": str(int(time.time())),
            "abRequestId": str(uuid.uuid4()),
        })
        self._apply_cookies()

        # 2) websectiga + sec_poison_id
        await self._fetch_websectiga()

        # 3) 生成指纹
        ua = self._session.headers.get("user-agent", DEFAULT_HEADERS["user-agent"])
        self._fp = enc.generate_fingerprint(self._cookies, ua)

        # 4) gid (+ acw_tc 由 Set-Cookie 返回)
        await self._fetch_gid()

        # 5) web_session (游客激活)
        await self._fetch_web_session()

    def _apply_cookies(self):
        """把 self._cookies 写入 session cookie jar"""
        for k, v in self._cookies.items():
            self._session.cookie_jar.update_cookies({k: str(v)})

    def _read_cookies(self) -> dict:
        """从 session cookie jar 读取所有 cookie 并同步到 self._cookies"""
        for cookie in self._session.cookie_jar:
            self._cookies[cookie.key] = cookie.value
        return dict(self._cookies)

    # ==================== Cookie 步骤实现 ====================

    async def _fetch_websectiga(self):
        """调用 /api/sec/v1/scripting 获取 websectiga 和 sec_poison_id"""
        url = "https://as.xiaohongshu.com/api/sec/v1/scripting"
        data = {"callFrom": "web", "callback": "seccallback"}

        async with self._session.post(url, json=data) as resp:
            res_json = await resp.json()

        sec_data = res_json.get("data", {})
        js_text = sec_data.get("data", "")
        sec_poison_id = sec_data.get("secPoisonId", "")

        websectiga = enc.decrypt_websectiga(js_text)
        self._cookies["websectiga"] = websectiga
        self._cookies["sec_poison_id"] = sec_poison_id
        self._apply_cookies()
        self._read_cookies()

    async def _fetch_gid(self):
        """POST 加密指纹到 webprofile 接口，获取 gid + acw_tc"""
        url = "https://as.xiaohongshu.com/api/sec/v1/shield/webprofile"
        gid_data = enc.generate_gid_data(self._fp)

        # webprofile 不需要签名头，直接 POST 即可
        request_data = json.dumps(gid_data, separators=(",", ":"))
        async with self._session.post(url, data=request_data) as resp:
            self._read_cookies()
            if resp.status != 200:
                logger.warning(f"webprofile 返回 {resp.status}，继续流程")

    async def _fetch_web_session(self):
        """游客激活，获取 web_session cookie"""
        url = "https://edith.xiaohongshu.com/api/sns/web/v1/login/activate"
        await self._signed_request("post", url, data={})
        self._read_cookies()

    # ==================== 签名请求 ====================

    async def _signed_request(self, method: str, url: str,
                              params: dict = None, data=None,
                              max_retries: int = 3) -> dict | None:
        """发送带签名头的请求"""
        cookies = self._read_cookies()
        a1 = cookies.get("a1", "")

        # 更新 loadts
        loadts = int(time.time() * 1000)
        self._cookies["loadts"] = str(loadts)
        self._apply_cookies()

        # 构造签名头
        url_path = urlparse(url).path
        extra_headers = {
            "x-xray-traceid": enc.encrypt_headers_xray(),
            "x-b3-traceid": enc.encrypt_header_xb3(),
            "x-s": enc.encrypt_headers_xs(a1, loadts, url_path, params, data),
            "x-t": str(int(time.time() * 1000)),
        }
        enc.update_fingerprint(self._fp, cookies, url)
        extra_headers["x-s-common"] = enc.encrypt_headers_xsc(a1, self._fp)

        # 构造完整 URL
        full_url = url
        if params:
            from urllib.parse import urlencode
            full_url += "?" + urlencode(params).replace("%2C", ",")

        # 序列化 body
        request_data = None
        if data is not None:
            request_data = json.dumps(data, separators=(",", ":"))

        headers = dict(self._session.headers)
        headers.update(extra_headers)

        for attempt in range(max_retries):
            try:
                async with self._session.request(
                    method.upper(), full_url,
                    data=request_data,
                    headers=headers,
                ) as resp:
                    self._read_cookies()
                    if resp.status != 200:
                        text = await resp.text()
                        logger.warning(f"XhsApi {resp.status} | {url} | {text[:200]}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(0.5)
                            continue
                        return None
                    try:
                        return await resp.json()
                    except Exception:
                        return {"_raw": await resp.text()}
            except aiohttp.ClientError as e:
                logger.warning(f"XhsApi 请求异常 ({attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5)
                else:
                    raise
        return None

    # ==================== 业务接口 ====================

    async def get_note_detail(self, note_id: str, xsec_token: str) -> dict | None:
        """获取笔记详情（无水印）

        返回结构: {"data": {"items": [{"note_card": {...}}]}}
        """
        if not self._initialized:
            await self.initialize()

        url = "https://edith.xiaohongshu.com/api/sns/web/v1/feed"
        data = {
            "source_note_id": note_id,
            "image_formats": ["jpg", "webp", "avif"],
            "extra": {"need_body_topic": "1"},
            "xsec_source": "pc_feed",
            "xsec_token": xsec_token,
        }
        return await self._signed_request("post", url, data=data)
