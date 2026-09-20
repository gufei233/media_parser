"""Xiaohongshu CDN download helpers."""

import importlib.util
import logging
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE_NAME = "media_parser_xhs_dl_testpkg"

astrbot = sys.modules.get("astrbot") or types.ModuleType("astrbot")
astrbot_api = sys.modules.get("astrbot.api") or types.ModuleType("astrbot.api")
astrbot_api.logger = logging.getLogger("test.xhs_download")
astrbot.api = astrbot_api
sys.modules["astrbot"] = astrbot
sys.modules["astrbot.api"] = astrbot_api

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE_NAME, package)

xhs_app_async_stub = types.ModuleType(f"{PACKAGE_NAME}.xhs_app_async")
xhs_app_async_stub.AsyncXhsAppParser = object
sys.modules[xhs_app_async_stub.__name__] = xhs_app_async_stub

client_pkg = types.ModuleType(f"{PACKAGE_NAME}.xhs_app")
client_pkg.__path__ = [str(ROOT / "xhs_app")]
sys.modules.setdefault(client_pkg.__name__, client_pkg)
client_mod = types.ModuleType(f"{PACKAGE_NAME}.xhs_app.xhs_app_client")
client_mod.UA = "Dalvik/2.1.0 discover/9.43.1"
sys.modules[client_mod.__name__] = client_mod

spec = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.async_xhs", ROOT / "async_xhs.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class SuffixTests(unittest.TestCase):
    def test_url_known_ext(self):
        self.assertEqual(module._suffix_from_url("https://x/a.heic?q=1"), ".heic")
        self.assertEqual(module._suffix_from_url("https://x/noext", ".jpg"), ".jpg")

    def test_bytes_jpeg_png_heic_mp4(self):
        self.assertEqual(
            module._suffix_from_bytes(b"\xff\xd8\xff\xe0xxxx", "image", ".bin"), ".jpg"
        )
        self.assertEqual(
            module._suffix_from_bytes(b"\x89PNG\r\n\x1a\nxxxx", "image", ".bin"), ".png"
        )
        heic = b"\x00\x00\x00\x18ftypheicxxxx"
        self.assertEqual(module._suffix_from_bytes(heic, "image", ".jpg"), ".heic")
        mp4 = b"\x00\x00\x00\x18ftypisomxxxx"
        self.assertEqual(module._suffix_from_bytes(mp4, "video", ".bin"), ".mp4")


if __name__ == "__main__":
    unittest.main()
