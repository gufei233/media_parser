import asyncio
import importlib.util
import logging
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE_NAME = "media_parser_xhs_app_testpkg"

# Provide only the AstrBot surface used while importing xhs_app_async.
astrbot = sys.modules.get("astrbot") or types.ModuleType("astrbot")
astrbot_api = sys.modules.get("astrbot.api") or types.ModuleType("astrbot.api")
astrbot_api.logger = logging.getLogger("test.xhs_app_async")
astrbot.api = astrbot_api
sys.modules["astrbot"] = astrbot
sys.modules["astrbot.api"] = astrbot_api

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE_NAME, package)

spec = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.xhs_app_async", ROOT / "xhs_app_async.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

AsyncXhsAppParser = module.AsyncXhsAppParser
_best_video_url = module._best_video_url


class BestVideoTests(unittest.TestCase):
    def test_prefers_audio_then_h265_then_bitrate(self):
        url = _best_video_url(
            [
                {"url": "low", "videoCodec": "h265", "audioCodec": "", "bitrate": 900},
                {
                    "url": "h264a",
                    "videoCodec": "h264",
                    "audioCodec": "aac",
                    "bitrate": 100,
                },
                {
                    "url": "h265a",
                    "videoCodec": "h265",
                    "audioCodec": "aac",
                    "bitrate": 200,
                },
                {
                    "url": "h265a-big",
                    "videoCodec": "h265",
                    "audioCodec": "aac",
                    "bitrate": 500,
                },
            ]
        )
        self.assertEqual(url, "h265a-big")

    def test_empty_details(self):
        self.assertIsNone(_best_video_url([]))
        self.assertIsNone(_best_video_url(None))


class MapResultTests(unittest.TestCase):
    def setUp(self):
        self.parser = AsyncXhsAppParser()

    def test_video_note_single_stream_and_cover(self):
        note = {
            "noteId": "v1",
            "title": "视频",
            "content": "desc",
            "contentType": "video",
            "author": {"name": "a", "id": "u1", "avatar": "https://x/a.jpg"},
            "images": ["https://x/cover.jpg"],
            "videoDetails": [
                {
                    "url": "https://x/v-264.mp4",
                    "videoCodec": "h264",
                    "audioCodec": "aac",
                    "bitrate": 100,
                },
                {
                    "url": "https://x/v-265.mp4",
                    "videoCodec": "h265",
                    "audioCodec": "aac",
                    "bitrate": 300,
                },
            ],
            "videos": ["https://x/v-264.mp4", "https://x/v-265.mp4"],
            "shareUrl": "https://www.xiaohongshu.com/explore/v1",
        }
        result = self.parser._map_result(note)
        self.assertEqual(result["contentType"], "video")
        self.assertEqual(result["videos"], ["https://x/v-265.mp4"])
        self.assertEqual(result["video"], "https://x/v-265.mp4")
        self.assertEqual(result["images"], [])
        self.assertEqual(result["cover"], "https://x/cover.jpg")
        self.assertFalse(result["isLivePhoto"])
        self.assertEqual(result["source"], "app_api")

    def test_live_note_keeps_image_order_and_live_videos(self):
        note = {
            "noteId": "l1",
            "title": "实况",
            "content": "",
            "contentType": "live",
            "isLivePhoto": True,
            "author": {"name": "a", "id": "u1", "avatar": ""},
            "images": ["https://x/1.jpg", "https://x/2.jpg", "https://x/3.jpg"],
            "livePhotos": [
                {"url": "https://x/live1.mp4"},
                {"url": "https://x/live2.mp4"},
            ],
            "videos": ["https://x/live1.mp4", "https://x/live2.mp4"],
            "imageDetails": [
                {"url": "https://x/1.jpg", "isLivePhoto": True},
                {"url": "https://x/2.jpg", "isLivePhoto": False},
                {"url": "https://x/3.jpg", "isLivePhoto": True},
            ],
        }
        result = self.parser._map_result(note)
        self.assertEqual(result["contentType"], "image")
        self.assertTrue(result["isLivePhoto"])
        self.assertEqual(
            result["images"], ["https://x/1.jpg", "https://x/2.jpg", "https://x/3.jpg"]
        )
        self.assertEqual(
            result["videos"], ["https://x/live1.mp4", "https://x/live2.mp4"]
        )
        # 配对：图1→live1，图2 无实况，图3→live2
        self.assertEqual(
            result["livePairs"],
            [
                {"image": "https://x/1.jpg", "video": "https://x/live1.mp4"},
                {"image": "https://x/2.jpg", "video": ""},
                {"image": "https://x/3.jpg", "video": "https://x/live2.mp4"},
            ],
        )

    def test_image_note_without_cover_duplication(self):
        note = {
            "noteId": "i1",
            "title": "图文",
            "content": "",
            "contentType": "image",
            "author": {"name": "a", "id": "u1", "avatar": ""},
            "images": ["https://x/1.jpg", "https://x/2.jpg"],
        }
        result = self.parser._map_result(note)
        # main.py 会遍历 images 发送；cover 置空避免第一张图重复发送
        self.assertIsNone(result["cover"])
        self.assertEqual(result["images"], ["https://x/1.jpg", "https://x/2.jpg"])
        self.assertEqual(result["videos"], [])

    def test_parse_error_passthrough(self):
        async def run():
            return await self.parser.parse("这不是一个有效链接!!!")

        result = asyncio.run(run())
        self.assertTrue(result.get("error"))
        self.assertIn("message", result)


if __name__ == "__main__":
    unittest.main()
