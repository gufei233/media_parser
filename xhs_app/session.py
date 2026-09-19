"""匿名会话：设备档案复用、风控后轮换、笔记拉取。"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import httpx

from .crypto.mua import MUA_SERVER_PUBLIC_KEY, PurePythonMuaSigner
from .device_info import (
    DeviceInfoBuilder,
    DeviceInfoGenerator,
    get_device_preset,
    load_profile_sample,
)
from .device_pool import DevicePool
from .note import parse_imagefeed
from .register_snapshot import synthesize_register_device
from .xhs_app_client import XhsAppClient

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
NOTE_ID_RE = re.compile(r"^[0-9a-zA-Z]{16,32}$")
PROXY = os.environ.get("XHS_PROXY", "")
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_POOL_ROOT = PROJECT_ROOT / "xhs_app_data" / "device_pool"


def extract_url(text: str):
    for pat in (
        r"https?://xhslink\.(?:com|cn)/[^\s]+",
        r"https?://(?:www\.)?xiaohongshu\.com/[^\s]+",
    ):
        match = re.search(pat, text)
        if match:
            return match.group(0).rstrip("，。；,.!！\\~）)】]")
    return None


def extract_note_id(text: str) -> str:
    raw = (text or "").strip()
    if NOTE_ID_RE.match(raw):
        return raw
    url = extract_url(raw) or raw
    for pat in (
        r"/(?:explore|discovery/item|item|notes?)/([0-9a-zA-Z]+)",
        r"[?&]note_id=([0-9a-zA-Z]+)",
    ):
        match = re.search(pat, url)
        if match:
            return match.group(1)
    return ""


def resolve_short(url: str):
    response = httpx.get(
        url,
        headers={"User-Agent": MOBILE_UA},
        proxy=PROXY or None,
        follow_redirects=True,
        timeout=30,
    )
    response.raise_for_status()
    final = str(response.url)
    return extract_note_id(final), final


def extract_note(payload: dict) -> dict:
    result = parse_imagefeed(payload)
    if result.get("error"):
        return result
    result["live_photos"] = list(result.get("livePhotos") or [])
    return result


class ClientManager:
    """管理持久化设备、匿名会话刷新和一次性设备轮换。"""

    def __init__(
        self, pool_root: str | Path | None = None, cf_proxy_url: str | None = None
    ):
        root = (
            Path(pool_root)
            if pool_root
            else Path(os.environ.get("XHS_POOL_ROOT", str(DEFAULT_POOL_ROOT)))
        )
        self.pool = DevicePool(root)
        self.client = None
        self.profile_path = None
        self.cf_proxy_url = (cf_proxy_url or "").strip().rstrip("/") or None

    def set_cf_proxy_url(self, cf_proxy_url: str | None):
        """更新 CF 反代地址并同步到已存在的客户端实例。"""
        self.cf_proxy_url = (cf_proxy_url or "").strip().rstrip("/") or None
        if self.client is not None:
            self.client.cf_proxy_url = self.cf_proxy_url

    @staticmethod
    def _make_python_mua_signer(profile_path: Path):
        config_name = os.environ.get("XHS_MUA_CONFIG", "").strip()
        if config_name:
            config_path = Path(config_name)
            if not config_path.is_absolute():
                config_path = PROJECT_ROOT / config_path
            config = json.loads(config_path.read_text(encoding="utf-8"))
        else:
            config = {}
        saved = {}
        if profile_path.exists():
            saved = json.loads(profile_path.read_text(encoding="utf-8"))
        pub_hex = str(config.get("server_public_key_hex") or "").strip()
        pub_b64 = str(config.get("server_public_key_b64") or "").strip()
        if pub_hex:
            server_public_key = bytes.fromhex(pub_hex)
        elif pub_b64:
            server_public_key = base64.b64decode(pub_b64)
        elif (
            str(config.get("shared_secret_hex") or "").strip()
            or str(config.get("key_iv_hex") or "").strip()
        ):
            server_public_key = None
        else:
            server_public_key = MUA_SERVER_PUBLIC_KEY
        builder_state = saved.get("mua_device_info_state") or {}
        if config.get("device_info_path"):
            info_path = Path(str(config["device_info_path"]))
            if not info_path.is_absolute():
                info_path = PROJECT_ROOT / info_path
            device_info = load_profile_sample(info_path)
            builder = DeviceInfoBuilder(
                device_info,
                persisted=builder_state,
                install_time_ms=saved.get("install_time_ms")
                or saved.get("install_time"),
                uptime_base=int(saved.get("uptime_base") or 1),
                uptime_anchor=saved.get("uptime_anchor"),
                identity_seed=str(
                    saved.get("device_id") or saved.get("android_id") or ""
                ),
            )
        elif isinstance(config.get("device_info"), dict) and config["device_info"]:
            builder = DeviceInfoBuilder(
                config["device_info"],
                persisted=builder_state,
                install_time_ms=saved.get("install_time_ms")
                or saved.get("install_time"),
                uptime_base=int(saved.get("uptime_base") or 1),
                uptime_anchor=saved.get("uptime_anchor"),
                identity_seed=str(
                    saved.get("device_id") or saved.get("android_id") or ""
                ),
            )
        else:
            generated_seed = (
                str(config.get("device_seed") or "")
                or str(saved.get("device_seed") or "")
                or str(builder_state.get("identity_seed") or "")
                or None
            )
            preset_name = str(
                config.get("device_preset")
                or os.environ.get("XHS_DEVICE_PRESET")
                or saved.get("device_preset")
                or "xiaomi-mi6-lineage-15"
            )
            builder = DeviceInfoGenerator(
                generated_seed,
                preset=preset_name,
                persisted=builder_state,
            )
        if isinstance(builder, DeviceInfoGenerator):
            register_seed = builder.seed_text
            register_preset = builder.preset
        else:
            register_seed = (
                str(saved.get("device_seed") or "")
                or str(builder.identity_seed or "")
                or str(saved.get("android_id") or "")
                or None
            )
            register_preset = str(
                config.get("device_preset")
                or saved.get("device_preset")
                or "xiaomi-mi6-lineage-15"
            )
        register_device = synthesize_register_device(
            register_seed,
            preset=register_preset,
            optional_files=str(config.get("register_files") or "android"),
            android_id=str(saved["android_id"]) if saved.get("android_id") else None,
            oaid=str(saved["oaid"]) if saved.get("oaid") else None,
            device_id=str(saved["device_id"]) if saved.get("device_id") else None,
        )
        private_hex = str(saved.get("mua_x25519_private_key") or "").strip()
        random_hex = str(saved.get("mua_random_material") or "").strip()
        shared_hex = str(config.get("shared_secret_hex") or "").strip()
        shared_secret = bytes.fromhex(shared_hex) if shared_hex else None
        key_iv_hex = str(config.get("key_iv_hex") or "").strip()
        key_iv = bytes.fromhex(key_iv_hex) if key_iv_hex else None
        signer = PurePythonMuaSigner(
            server_public_key=server_public_key,
            device_info=builder.build,
            gid=saved.get("gid", ""),
            key_mode=str(config.get("key_mode", "prefix16")),
            iv_mode=str(config.get("iv_mode", "suffix16")),
            raw_deflate=bool(config.get("raw_deflate", False)),
            x25519_private_key=bytes.fromhex(private_hex) if private_hex else None,
            random_material=bytes.fromhex(random_hex) if random_hex else None,
            shared_secret=shared_secret,
            key_iv=key_iv,
            default_mua_state=config.get("mua_state")
            if isinstance(config.get("mua_state"), dict)
            else None,
            register_profile=register_device.snapshot,
            register_identity={"device_id": "", "rr": "0", "sid": "", "uid": ""},
        )
        signer._device_info_builder = builder
        signer._register_device = register_device
        signer._device_preset = get_device_preset(register_preset)
        return signer

    def _make_client(self, profile_path: Path):
        mua_backend = os.environ.get("XHS_MUA_BACKEND", "python").strip().lower()
        if mua_backend not in ("python", ""):
            raise RuntimeError(
                f"不支持的 XHS_MUA_BACKEND: {mua_backend}（插件仅内置 python 实现）"
            )
        signer = ClientManager._make_python_mua_signer(profile_path)
        client = XhsAppClient(
            timeout=30,
            signer=signer,
            shield_signer=None,
            profile_path=str(profile_path),
            proxy=PROXY or None,
            cf_proxy_url=self.cf_proxy_url,
        )
        return client

    def _bootstrap_profile(self, profile_path: Path):
        client = self._make_client(profile_path)
        try:
            client.bootstrap()
        except Exception:
            client.close()
            raise
        self.profile_path = profile_path
        self.pool.set_active(profile_path)
        self.client = client
        return client

    def get(self):
        if self.client is not None and getattr(self.client, "_bootstrapped", False):
            if self.profile_path is None or not self.pool.is_cooldown(
                self.profile_path
            ):
                return self.client
            self.client.close()
            self.client = None
        active = self.pool.active_profile()
        if active is None or self.pool.is_cooldown(active):
            active = self.pool.new_profile_path()
        return self._bootstrap_profile(active)

    def refresh_session(self):
        current = self.profile_path or self.pool.active_profile()
        if current is None:
            return self.get()
        if self.pool.is_cooldown(current):
            return self.rotate_device(
                code="cooldown",
                message="attempted to refresh a cooldown device",
                stage="refresh_session",
            )
        if self.client is not None:
            self.client.close()
        self.client = None
        return self._bootstrap_profile(current)

    def rotate_device(self, *, code=None, message="", stage=""):
        old = self.profile_path or self.pool.active_profile()
        if old is not None:
            self.pool.mark_cooldown(old, code=code, message=message, stage=stage)
        if self.client is not None:
            self.client.close()
        self.client = None
        fresh = self.pool.new_profile_path()
        return self._bootstrap_profile(fresh)

    def record_failure(self, *, code=None, message="", stage=""):
        current = self.profile_path or self.pool.active_profile()
        if current is not None:
            self.pool.record_failure(current, code=code, message=message, stage=stage)


_manager = ClientManager()


def configure_cf_proxy(cf_proxy_url: str | None):
    """为模块级管理器设置 CF 反代地址（imagefeed 绕行被风控 IP 时使用）。"""
    _manager.set_cf_proxy_url(cf_proxy_url)


def get_client():
    return _manager.get()


def refresh_client():
    return _manager.refresh_session()


def fetch_imagefeed(client, note_id):
    return client.get_note_imagefeed(note_id)


def request_note(client, note_id: str):
    return fetch_imagefeed(client, note_id), "imagefeed"


RISK_CODES = {-100, 300011}


def is_risk_payload(payload: dict | None) -> bool:
    return bool(payload) and payload.get("code") in RISK_CODES


def recover_from_minus_100(note_id: str, payload: dict, endpoint: str):
    recovery = []
    _manager.record_failure(
        code=payload.get("code"), message=payload.get("msg", ""), stage=endpoint
    )
    try:
        client = _manager.refresh_session()
        payload, endpoint = request_note(client, note_id)
        recovery.append("session_refresh")
    except Exception as exc:
        recovery.append("session_refresh_error:" + str(exc)[:120])
        client = None
    if is_risk_payload(payload):
        old_profile = str(_manager.profile_path) if _manager.profile_path else ""
        try:
            client = _manager.rotate_device(
                code=payload.get("code"), message=payload.get("msg", ""), stage=endpoint
            )
            payload, endpoint = request_note(client, note_id)
            recovery.append("device_rotate")
        except Exception as exc:
            recovery.append("device_rotate_error:" + str(exc)[:120])
        if old_profile:
            recovery.append("old_profile=" + old_profile)
    return payload, endpoint, recovery


def extract_fields(text: str, client=None, include_raw: bool = False) -> dict:
    url = extract_url(text)
    if url:
        try:
            note_id, final = resolve_short(url)
        except Exception as exc:
            return {
                "ok": False,
                "stage": "resolve_short",
                "shortUrl": url,
                "message": str(exc),
            }
        if not note_id:
            return {
                "ok": False,
                "stage": "resolve_short",
                "shortUrl": url,
                "finalUrl": final,
                "message": "未找到 note_id",
            }
    else:
        note_id = extract_note_id(text)
        if not note_id:
            return {
                "ok": False,
                "stage": "input",
                "message": "未识别到小红书链接或 note_id",
            }
        url = ""
        final = ""

    active_client = client or get_client()
    payload, endpoint = request_note(active_client, note_id)
    recovery = []
    if is_risk_payload(payload):
        payload, endpoint, recovery = recover_from_minus_100(note_id, payload, endpoint)
    if not payload or payload.get("code") != 0:
        return {
            "ok": False,
            "stage": "imagefeed",
            "shortUrl": url,
            "finalUrl": final,
            "noteId": note_id,
            "code": payload.get("code") if payload else None,
            "message": payload.get("msg") if payload else "empty response",
            "endpoint": endpoint,
            "recovery": recovery,
        }

    result = extract_note(payload)
    if result.get("error"):
        return {
            "ok": False,
            "stage": "extract",
            "shortUrl": url,
            "finalUrl": final,
            "noteId": note_id,
            "message": result.get("message"),
        }
    result.update(
        {
            "ok": True,
            "apiCode": payload.get("code"),
            "apiSuccess": payload.get("success"),
            "shortUrl": url,
            "finalUrl": final,
            "endpoint": endpoint,
            "recovery": recovery,
        }
    )
    if include_raw:
        result["raw"] = payload
    return result
