import importlib.util
import logging
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock


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
    command = staticmethod(identity_decorator)


class StarStub:
    def __init__(self, *_args, **_kwargs):
        pass


class ComponentStub:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


# Provide only the AstrBot surface used while defining/importing main.py.
astrbot = sys.modules.get("astrbot") or types.ModuleType("astrbot")
astrbot_api = sys.modules.get("astrbot.api") or types.ModuleType("astrbot.api")
astrbot_api.logger = logging.getLogger("test.main")
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

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE_NAME, package)

# Load the real Debouncer while stubbing unrelated parser dependencies.
debounce_spec = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.debounce", ROOT / "debounce.py"
)
debounce_module = importlib.util.module_from_spec(debounce_spec)
sys.modules[debounce_spec.name] = debounce_module
debounce_spec.loader.exec_module(debounce_module)
Debouncer = debounce_module.Debouncer

config_stub = types.ModuleType(f"{PACKAGE_NAME}.config")
config_stub.MediaParserConfig = object
sys.modules[config_stub.__name__] = config_stub

async_dysk_stub = types.ModuleType(f"{PACKAGE_NAME}.async_dysk")
async_dysk_stub.AsyncDouyinDownloader = object
sys.modules[async_dysk_stub.__name__] = async_dysk_stub

async_xhs_stub = types.ModuleType(f"{PACKAGE_NAME}.async_xhs")
async_xhs_stub.AsyncXiaohongshuParser = object
sys.modules[async_xhs_stub.__name__] = async_xhs_stub

utils_stub = types.ModuleType(f"{PACKAGE_NAME}.utils")
utils_stub.normalize_text = lambda value, default="": default if value is None else str(value)
sys.modules[utils_stub.__name__] = utils_stub

main_spec = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.main", ROOT / "main.py"
)
main_module = importlib.util.module_from_spec(main_spec)
sys.modules[main_spec.name] = main_module
main_spec.loader.exec_module(main_module)
MediaParserPlugin = main_module.MediaParserPlugin


class FakeConfig:
    douyin_info_render_mode = "none"
    max_duration = 0
    show_download_fail_tip = True


class FakeEvent:
    unified_msg_origin = "session"

    def plain_result(self, value):
        return value

    def get_sender_id(self):
        return "sender"

    def get_sender_name(self):
        return "name"


class MainDebounceTests(unittest.IsolatedAsyncioTestCase):
    URL = "https://v.douyin.com/j2WRM0fsHL4/"

    def make_plugin(self, detail_result):
        plugin = object.__new__(MediaParserPlugin)
        plugin.cfg = FakeConfig()
        plugin.debouncer = Debouncer(300)
        plugin.dy_downloader = types.SimpleNamespace(
            get_detail=AsyncMock(return_value=detail_result)
        )
        plugin._sync_downloader_config = lambda: None
        return plugin

    async def consume_parse(self, plugin, reservation=None):
        event = FakeEvent()
        return [
            result
            async for result in plugin.parse_douyin(
                event, self.URL, debounce_reservation=reservation
            )
        ]

    async def test_failed_detail_releases_link_reservation(self):
        plugin = self.make_plugin(None)
        is_duplicate, reservation = plugin.debouncer.reserve_link(
            "session", self.URL
        )
        self.assertFalse(is_duplicate)

        results = await self.consume_parse(plugin, reservation)

        self.assertEqual(len(results), 1)
        self.assertFalse(plugin.debouncer.hit_link("session", self.URL))

    async def test_successful_detail_keeps_link_reservation(self):
        plugin = self.make_plugin({"downloads": []})
        is_duplicate, reservation = plugin.debouncer.reserve_link(
            "session", self.URL
        )
        self.assertFalse(is_duplicate)

        results = await self.consume_parse(plugin, reservation)

        self.assertEqual(results, [])
        self.assertTrue(plugin.debouncer.hit_link("session", self.URL))


if __name__ == "__main__":
    unittest.main()
