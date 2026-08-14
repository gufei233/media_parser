import base64
import importlib.util
import json
import logging
import os
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


ROOT = pathlib.Path(__file__).resolve().parents[1]

# async_dysk imports AstrBot's logger, but parser unit tests do not need AstrBot.
astrbot = types.ModuleType("astrbot")
astrbot_api = types.ModuleType("astrbot.api")
astrbot_api.logger = logging.getLogger("test.async_dysk")
astrbot.api = astrbot_api
sys.modules.setdefault("astrbot", astrbot)
sys.modules.setdefault("astrbot.api", astrbot_api)

# Loading the module as a package keeps its existing relative imports intact.
package = types.ModuleType("media_parser_testpkg")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("media_parser_testpkg", package)

# Isolate network-flow tests from the optional SM3 implementation. The real
# signature implementation is covered by runtime dependency checks, not these
# HTTP state-machine tests.
dysk_stub = types.ModuleType("media_parser_testpkg.dysk")
dysk_stub.USERAGENT = "Mozilla/5.0 test"


class StubABogus:
    def __init__(self, _user_agent):
        pass

    def get_value(self, _params):
        return "test-signature"


class StubExtractor:
    def extract_data(self, detail):
        return detail


dysk_stub.ABogus = StubABogus
dysk_stub.Extractor = StubExtractor
sys.modules.setdefault("media_parser_testpkg.dysk", dysk_stub)

spec = importlib.util.spec_from_file_location(
    "media_parser_testpkg.async_dysk", ROOT / "async_dysk.py"
)
async_dysk = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = async_dysk
spec.loader.exec_module(async_dysk)
AsyncDouyinDownloader = async_dysk.AsyncDouyinDownloader

SHORT_URL = "https://v.douyin.com/j2WRM0fsHL4/"
AWEME_ID = "7669412584596006833"
VIDEO_URL = f"https://www.douyin.com/video/{AWEME_ID}"


class FakeResponse:
    def __init__(
        self,
        *,
        status=200,
        url="https://example.invalid/",
        history=(),
        headers=None,
        body=b"",
    ):
        self.status = status
        self.url = url
        self.history = history
        self.headers = headers or {}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        return self._body


class FakeContent:
    def __init__(self, chunks):
        self._chunks = tuple(chunks)

    async def iter_chunked(self, _size):
        for chunk in self._chunks:
            yield chunk


class FakeDownloadResponse(FakeResponse):
    def __init__(self, *, body_chunks, content_range):
        super().__init__(
            status=206,
            headers={"Content-Range": content_range},
        )
        self.content_length = sum(len(chunk) for chunk in body_chunks)
        self.content = FakeContent(body_chunks)


class FakeSession:
    def __init__(self, *, heads=(), gets=()):
        self._heads = list(heads)
        self._gets = list(gets)
        self.head_calls = []
        self.get_calls = []

    def head(self, url, **kwargs):
        self.head_calls.append((url, kwargs))
        item = self._heads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        item = self._gets.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class DouyinUrlTests(unittest.IsolatedAsyncioTestCase):
    SHARE_TEXT = (
        "3.00 02/26 Z@M.JI :9pm Vyt:/ 一滴一滴刺痛我的心 "
        f"{SHORT_URL} 复制此链接，打开Dou音搜索！"
    )

    def make_downloader(self, retries=0):
        return AsyncDouyinDownloader(download_retry_times=retries)

    def test_extracts_url_from_full_share_text(self):
        url = AsyncDouyinDownloader._extract_http_url(self.SHARE_TEXT)
        self.assertEqual(url, SHORT_URL)

    async def test_head_404_redirect_history_still_yields_id(self):
        history = FakeResponse(
            status=302,
            url=SHORT_URL,
            headers={
                "Location": f"https://www.iesdouyin.com/share/video/{AWEME_ID}/"
            },
        )
        head = FakeResponse(
            status=404,
            url=f"{VIDEO_URL}?previous_page=web_code_link",
            history=(history,),
        )
        session = FakeSession(heads=(head,))
        downloader = self.make_downloader(retries=0)
        downloader._get_session = AsyncMock(return_value=session)

        result = await downloader._resolve_short_url(
            SHORT_URL
        )

        self.assertEqual(result, AWEME_ID)
        self.assertEqual(len(session.head_calls), 1)
        self.assertEqual(len(session.get_calls), 0)

    async def test_head_without_id_falls_back_to_get(self):
        session = FakeSession(
            heads=(
                FakeResponse(
                    status=405,
                    url="https://www.douyin.com/",
                ),
            ),
            gets=(
                FakeResponse(
                    status=200,
                    url=VIDEO_URL,
                ),
            ),
        )
        downloader = self.make_downloader(retries=0)
        downloader._get_session = AsyncMock(return_value=session)

        result = await downloader._resolve_short_url(
            SHORT_URL
        )

        self.assertEqual(result, AWEME_ID)
        self.assertEqual(len(session.head_calls), 1)
        self.assertEqual(len(session.get_calls), 1)

    async def test_transient_get_status_uses_configured_retry(self):
        session = FakeSession(
            heads=(FakeResponse(status=405),),
            gets=(
                FakeResponse(status=503),
                FakeResponse(status=200, url=VIDEO_URL),
            ),
        )
        downloader = self.make_downloader(retries=1)
        downloader._get_session = AsyncMock(return_value=session)

        with patch.object(async_dysk.asyncio, "sleep", AsyncMock()):
            result = await downloader._resolve_short_url(SHORT_URL)

        self.assertEqual(result, AWEME_ID)
        self.assertEqual(len(session.get_calls), 2)

    async def test_non_transient_get_status_does_not_retry(self):
        session = FakeSession(
            heads=(FakeResponse(status=405),),
            gets=(
                FakeResponse(status=404),
                FakeResponse(status=200, url=VIDEO_URL),
            ),
        )
        downloader = self.make_downloader(retries=1)
        downloader._get_session = AsyncMock(return_value=session)

        result = await downloader._resolve_short_url(SHORT_URL)

        self.assertIsNone(result)
        self.assertEqual(len(session.get_calls), 1)

    async def test_zero_retries_still_makes_one_get_attempt(self):
        session = FakeSession(
            heads=(OSError("HEAD unavailable"),),
            gets=(OSError("GET unavailable"),),
        )
        downloader = self.make_downloader(retries=0)
        downloader._get_session = AsyncMock(return_value=session)

        result = await downloader._resolve_short_url(
            SHORT_URL
        )

        self.assertIsNone(result)
        self.assertEqual(len(session.head_calls), 1)
        self.assertEqual(len(session.get_calls), 1)

    async def test_full_url_still_primes_cookies(self):
        session = FakeSession(
            heads=(FakeResponse(status=404, url=VIDEO_URL),)
        )
        downloader = self.make_downloader(retries=0)
        downloader._get_session = AsyncMock(return_value=session)

        result = await downloader._resolve_short_url(VIDEO_URL)

        self.assertEqual(result, AWEME_ID)
        self.assertEqual(len(session.head_calls), 1)
        self.assertEqual(len(session.get_calls), 0)


class DouyinDetailFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_cf_failure_does_not_bypass_proxy(self):
        downloader = AsyncDouyinDownloader(
            enable_cf_proxy=True,
            cf_proxy_url="https://worker.example",
            download_retry_times=0,
        )
        downloader._ensure_tokens = AsyncMock()
        downloader._resolve_short_url = AsyncMock(
            return_value=AWEME_ID
        )
        downloader._fetch_detail_api = AsyncMock(return_value=None)

        result = await downloader.get_detail(SHORT_URL)

        self.assertIsNone(result)
        downloader._fetch_detail_api.assert_awaited_once()
        call = downloader._fetch_detail_api.await_args
        self.assertFalse(call.kwargs.get("force_direct", False))

    async def test_valid_cf_result_does_not_retry_direct(self):
        downloader = AsyncDouyinDownloader(
            enable_cf_proxy=True,
            cf_proxy_url="https://worker.example",
        )
        downloader._ensure_tokens = AsyncMock()
        downloader._resolve_short_url = AsyncMock(
            return_value=AWEME_ID
        )
        expected = {"id": AWEME_ID, "downloads": []}
        downloader._fetch_detail_api = AsyncMock(return_value=expected)

        result = await downloader.get_detail(
            SHORT_URL
        )

        self.assertEqual(result, expected)
        downloader._fetch_detail_api.assert_awaited_once()


class DouyinDetailResponseTests(unittest.IsolatedAsyncioTestCase):
    def make_downloader(self, response):
        downloader = AsyncDouyinDownloader()
        downloader._get_session = AsyncMock(
            return_value=FakeSession(gets=(response,))
        )
        return downloader

    async def test_direct_json_response_is_extracted(self):
        detail = {"id": AWEME_ID, "downloads": []}
        body = json.dumps(
            {"status_code": 0, "aweme_detail": detail}
        ).encode()
        downloader = self.make_downloader(FakeResponse(status=200, body=body))

        result = await downloader._fetch_detail_api(
            AWEME_ID, {"a_bogus": "test"}
        )

        self.assertEqual(result, detail)

    async def test_cf_base64_response_is_extracted(self):
        detail = {"id": AWEME_ID, "downloads": []}
        upstream = json.dumps(
            {"status_code": 0, "aweme_detail": detail}
        ).encode()
        body = json.dumps(
            {
                "encoding": "base64",
                "data": base64.b64encode(upstream).decode("ascii"),
            }
        ).encode()
        downloader = self.make_downloader(FakeResponse(status=200, body=body))

        result = await downloader._fetch_detail_api(
            AWEME_ID, {"a_bogus": "test"}
        )

        self.assertEqual(result, detail)

    async def test_invalid_payload_type_returns_none(self):
        downloader = self.make_downloader(
            FakeResponse(status=200, body=b"[]")
        )

        result = await downloader._fetch_detail_api(
            AWEME_ID, {"a_bogus": "test"}
        )

        self.assertIsNone(result)


class DouyinDownloadRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_to_bytes_retries_transient_status(self):
        session = FakeSession(
            gets=(
                FakeResponse(status=503),
                FakeResponse(status=200, body=b"image"),
            )
        )
        downloader = AsyncDouyinDownloader(download_retry_times=1)
        downloader._get_session = AsyncMock(return_value=session)

        with patch.object(async_dysk.asyncio, "sleep", AsyncMock()):
            result = await downloader.download_to_bytes(
                "https://example.invalid/cover.jpg"
            )

        self.assertEqual(result, b"image")
        self.assertEqual(len(session.get_calls), 2)

    async def test_partial_download_respects_attempt_limit(self):
        session = FakeSession(
            gets=(
                FakeDownloadResponse(
                    body_chunks=(b"partial",),
                    content_range="bytes 0-6/100",
                ),
            )
        )
        downloader = AsyncDouyinDownloader(download_retry_times=0)
        downloader._get_session = AsyncMock(return_value=session)
        output_path = str(ROOT / "unused-partial-output.mp4")

        try:
            result = await downloader.download_video(
                "https://example.invalid/video.mp4", output_path
            )
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

        self.assertFalse(result)
        self.assertEqual(len(session.get_calls), 1)

    async def test_zero_retries_still_starts_download_once(self):
        response = FakeResponse(status=503)
        session = FakeSession(gets=(response,))
        downloader = AsyncDouyinDownloader(download_retry_times=0)
        downloader._get_session = AsyncMock(return_value=session)

        result = await downloader.download_video(
            "https://example.invalid/video.mp4",
            "unused-test-output.mp4",
        )

        self.assertFalse(result)
        self.assertEqual(len(session.get_calls), 1)


if __name__ == "__main__":
    unittest.main()
