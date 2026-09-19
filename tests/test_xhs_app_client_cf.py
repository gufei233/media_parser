"""XHS 客户端 CF Worker 反代路由的单元测试。"""

import base64
import importlib.util
import json
import logging
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]

# xhs_app 包本身不依赖 astrbot，但保持与其它测试一致的桩环境。
astrbot = sys.modules.get("astrbot") or types.ModuleType("astrbot")
astrbot_api = sys.modules.get("astrbot.api") or types.ModuleType("astrbot.api")
astrbot_api.logger = logging.getLogger("test.xhs_client_cf")
astrbot.api = astrbot_api
sys.modules["astrbot"] = astrbot
sys.modules["astrbot.api"] = astrbot_api

package = types.ModuleType("media_parser_xhs_cf_testpkg")
package.__path__ = [str(ROOT / "xhs_app")]
sys.modules.setdefault(package.__name__, package)

spec = importlib.util.spec_from_file_location(
    f"{package.__name__}.xhs_app_client", ROOT / "xhs_app" / "xhs_app_client.py"
)
client_mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = client_mod
spec.loader.exec_module(client_mod)
XhsAppClient = client_mod.XhsAppClient


class _FakeUrllibResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


class CfProxyRoutingTest(unittest.TestCase):
    def make_client(self, cf_proxy_url=None):
        client = XhsAppClient(cf_proxy_url=cf_proxy_url)
        client._bootstrapped = True
        # signer=None 时 xmini 不参与；shield 走 shield_signer 分支返回固定值
        client.shield_signer = types.SimpleNamespace(sign=lambda **_: "test-shield")
        return client

    def test_imagefeed_rerouted_via_cf_proxy(self):
        captured = {}

        def fake(url, headers):
            captured["url"] = url
            return 200, {"code": 0}

        client = self.make_client("https://w.example/")
        client._urllib_get_json = fake

        client.get_note_imagefeed("123")

        self.assertTrue(
            captured["url"].startswith(
                "https://w.example/xhs/api/sns/v1/note/imagefeed?note_id=123"
            )
        )

    def test_imagefeed_direct_without_cf(self):
        captured = {}

        def fake(url, headers):
            captured["url"] = url
            return 200, {"code": 0}

        client = self.make_client()
        client._urllib_get_json = fake

        client.get_note_imagefeed("123")

        self.assertTrue(
            captured["url"].startswith(
                "https://edith.xiaohongshu.com/api/sns/v1/note/imagefeed?note_id=123"
            )
        )

    def test_base64_wrapper_unwrapped(self):
        inner = json.dumps({"code": 0, "data": [{"x": 1}]}).encode("utf-8")
        wrapper = json.dumps(
            {"data": base64.b64encode(inner).decode("ascii"), "encoding": "base64"}
        ).encode("utf-8")

        client = self.make_client()
        with patch.object(
            client_mod.urllib.request,
            "urlopen",
            lambda *_a, **_k: _FakeUrllibResponse(wrapper),
        ):
            status, payload = client._urllib_get_json(
                "https://w.example/xhs/api/sns/v1/note/imagefeed", {}
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"][0]["x"], 1)


if __name__ == "__main__":
    unittest.main()
