import importlib.util
import logging
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE_NAME = "media_parser_main_testpkg"


def identity_decorator(*_args, **_kwargs):
    return lambda value: value


class FilterStub:
    class EventMessageType:
        ALL = object()

    class PermissionType:
        ADMIN = object()

    event_message_type = staticmethod(identity_decorator)
    permission_type = staticmethod(identity_decorator)


class StarStub:
    def __init__(self, *_args, **_kwargs):
        pass


class ComponentStub:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


astrbot = sys.modules.get("astrbot") or types.ModuleType("astrbot")
astrbot_api = sys.modules.get("astrbot.api") or types.ModuleType("astrbot.api")
astrbot_api.logger = logging.getLogger("test.main_live")
astrbot_api.AstrBotConfig = dict
astrbot.api = astrbot_api
sys.modules["astrbot"] = astrbot
sys.modules["astrbot.api"] = astrbot_api

event_module = types.ModuleType("astrbot.api.event")
event_module.filter = FilterStub()
event_module.AstrMessageEvent = object
sys.modules.setdefault("astrbot.api.event", event_module)

star_module = types.ModuleType("astrbot.api.star")
star_module.Context = object
star_module.Star = StarStub
star_module.register = identity_decorator
sys.modules.setdefault("astrbot.api.star", star_module)

components = types.ModuleType("astrbot.api.message_components")
for component_name in ("Node", "Nodes", "Plain", "Image", "Video"):
    setattr(components, component_name, ComponentStub)
sys.modules.setdefault("astrbot.api.message_components", components)

config_stub = types.ModuleType(f"{PACKAGE_NAME}.config")
config_stub.MediaParserConfig = object
sys.modules[config_stub.__name__] = config_stub

async_dysk_stub = types.ModuleType(f"{PACKAGE_NAME}.async_dysk")
async_dysk_stub.AsyncDouyinDownloader = object
sys.modules[async_dysk_stub.__name__] = async_dysk_stub

async_xhs_stub = types.ModuleType(f"{PACKAGE_NAME}.async_xhs")
async_xhs_stub.AsyncXiaohongshuParser = object
async_xhs_stub._suffix_from_url = lambda url, default=".bin": default
async_xhs_stub._suffix_from_bytes = lambda raw, kind, fallback: fallback
sys.modules[async_xhs_stub.__name__] = async_xhs_stub

utils_stub = types.ModuleType(f"{PACKAGE_NAME}.utils")
utils_stub.normalize_text = lambda value, default="": (
    default if value is None else str(value)
)
sys.modules[utils_stub.__name__] = utils_stub

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE_NAME, package)

main_spec = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.main", ROOT / "main.py"
)
main_module = importlib.util.module_from_spec(main_spec)
sys.modules[main_spec.name] = main_module
main_spec.loader.exec_module(main_module)

_live_forward_segments = main_module._live_forward_segments


class LiveForwardSegmentsTest(unittest.TestCase):
    def test_plain_then_live_grouping(self):
        """3 静图 + 4 实况：静图一条转发，实况段静图视频交错一条转发。"""
        pairs = [
            {"image": "s1", "video": ""},
            {"image": "s2", "video": ""},
            {"image": "s3", "video": ""},
            {"image": "l1", "video": "v1"},
            {"image": "l2", "video": "v2"},
            {"image": "l3", "video": "v3"},
            {"image": "l4", "video": "v4"},
        ]
        segments = _live_forward_segments(pairs)
        self.assertEqual(len(segments), 2)
        self.assertEqual(
            segments[0],
            [("image", "s1"), ("image", "s2"), ("image", "s3")],
        )
        self.assertEqual(
            segments[1],
            [
                ("image", "l1"),
                ("video", "v1"),
                ("image", "l2"),
                ("video", "v2"),
                ("image", "l3"),
                ("video", "v3"),
                ("image", "l4"),
                ("video", "v4"),
            ],
        )

    def test_mixed_order_keeps_original_sequence(self):
        """实况/静图交替时按原始顺序切分成多段，不重排。"""
        pairs = [
            {"image": "l1", "video": "v1"},
            {"image": "s1", "video": ""},
            {"image": "l2", "video": "v2"},
        ]
        segments = _live_forward_segments(pairs)
        self.assertEqual(
            segments,
            [
                [("image", "l1"), ("video", "v1")],
                [("image", "s1")],
                [("image", "l2"), ("video", "v2")],
            ],
        )

    def test_all_plain_single_segment(self):
        pairs = [{"image": f"s{i}", "video": ""} for i in range(3)]
        segments = _live_forward_segments(pairs)
        self.assertEqual(
            segments, [[("image", "s0"), ("image", "s1"), ("image", "s2")]]
        )

    def test_video_only_pair(self):
        segments = _live_forward_segments([{"image": "", "video": "v1"}])
        self.assertEqual(segments, [[("video", "v1")]])

    def test_empty_input(self):
        self.assertEqual(_live_forward_segments(None), [])
        self.assertEqual(_live_forward_segments([]), [])


if __name__ == "__main__":
    unittest.main()
