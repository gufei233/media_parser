"""小红书 Android 9.43.1 匿名会话与内容接口客户端。

已实现：deviceId、vfc_code/main_hmac 获取与解密、activate/SSK、完整
70B/118B Shield、动态 signer 接口、imagefeed/detailfeed 解析。

默认 MUA/S1 走 PurePythonMuaSigner（`XHS_MUA_BACKEND=unidbg` 才用 libtiny）。
`register_gid()` POST `/api/v1/register/android` 并解析 `data.g`。Python
signer 在注入 `register_profile` 后 `supports_register=True`。`get_homefeed()`
走动态 x-mini，不再注入过期抓包常量。
"""

import base64
import binascii
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import httpx

from .crypto import shield as _shield
from .crypto import ssk as _ssk
from .crypto import xyass as _crypto

APP_BUILD = "9431801"
VERSION_NAME = "9.43.1"
UA = (
    "Dalvik/2.1.0 (Linux; U; Android 15; MI 6 Build/BP1A.250505.005) "
    "Resolution/1080*1920 Version/9.43.1 Build/9431801 "
    "Device/(Xiaomi;MI 6) discover/9.43.1 NetType/WiFi"
)
REGISTER_UA = "okhttp/3.14.9.056"

# Historical captured x-mini values remain diagnostic-only. Production request
# paths must call signer.sign(); they must not inject these strings.
X_MINI_S1 = "AAcAAAABwkF+b2ZmWCs5jDJ7Yl/3MA0RFIP+cchYdVz9bxUDT0GUThNz8oTzjHWV6UGxBxgiA3s6ZMmcHNY="
X_MINI_SIG = "304bd4d09c13c62ff9924780d3a05324ad97a440b849483ab586c4314b248a30"
X_MINI_MUA = (
    "eyJhIjoiRUNGQUFGMDEiLCJjIjo3LCJrIjoiNjIzMGQ1MWNmOTQ0MGVjYzNjNzA3NDA5"
    "ZjRhODI5MjQ5MWUyMzE3ZDcwOWU5YzY2NDBhZDBhODMyMTZhYmEx"
)


def name_uuid_from_bytes(name: bytes) -> str:
    """android_id -> deviceId（MD5 摘要 -> UUID v3 名称空间）。"""
    md5_hash = hashlib.md5(name).digest()
    b = list(md5_hash)
    b[6] = (b[6] & 0x3F) | 0x30
    b[8] = (b[8] & 0xBF) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


class XhsAppClient:
    """小红书 App 端匿名会话客户端。

    用法：
        client = XhsAppClient()                      # 随机 android_id
        client.bootstrap()                           # vfc_code + activate
        note = client.get_note_imagefeed(note_id)    # 图文 / 实况（含 AAC 音轨）
        feed = client.get_homefeed()                 # 视频推荐流
    """

    def __init__(
        self,
        android_id: str = None,
        timeout: int = 30,
        signer=None,
        profile_path: str = None,
        shield_signer=None,
        proxy: str = None,
    ):
        self.profile_path = Path(profile_path) if profile_path else None
        profile = {}
        if self.profile_path and self.profile_path.exists():
            profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
        identity = getattr(signer, "_register_device", None)
        if not isinstance(getattr(identity, "android_id", None), str):
            identity = None
        preset = getattr(signer, "_device_preset", None)
        if preset is not None and not hasattr(preset, "dalvik_user_agent"):
            preset = None
        if identity is not None and not android_id and not profile.get("android_id"):
            self.android_id = identity.android_id
            self.device_id = identity.device_id
            self.oaid = profile.get("oaid") or identity.oaid
        else:
            self.android_id = (
                android_id or profile.get("android_id") or secrets.token_hex(8)
            )
            self.device_id = profile.get("device_id") or name_uuid_from_bytes(
                self.android_id.encode()
            )
            self.oaid = profile.get("oaid") or (
                identity.oaid if identity is not None else secrets.token_hex(16)
            )
        self.timeout = timeout
        self.signer = signer
        self.shield_signer = shield_signer
        self.session = httpx.Client(verify=False, proxy=proxy or None)
        self.launch_id = int(time.time())
        self.install_time = profile.get("install_time") or time.strftime(
            "%Y-%m-%d %H:%M:%S", time.gmtime()
        )
        self.uptime_base = int(profile.get("uptime_base", 4702066))
        self.uptime_anchor = float(profile.get("uptime_anchor", time.time()))
        self.nqe_score = 90
        self.live_net_bandwidth = "25130412"
        self.live_net_networktype = "HighSpeed-HighLatency-LowLoss-LowStable"
        self.imagefeed_trace_page = "note_detail_r10"
        self.app_build = (
            str(preset.app_version_code) if preset is not None else APP_BUILD
        )
        self.version_name = preset.app_version if preset is not None else VERSION_NAME
        self.user_agent = preset.dalvik_user_agent() if preset is not None else UA
        # libtiny 的 MUA 顶层 c 是设备级递增序号。真机同一进程会持续递增；
        # Python 新设备第一次 register 对齐 captured c=2；已有 GID 的档案不改。
        default_counter = 2 if identity is not None and not profile.get("gid") else 3
        self.mua_counter = int(profile.get("mua_counter", default_counter))

        # 会话状态
        self.main_hmac = ""  # vfc_code 响应头 xy-ter-str（base64）
        self.key64 = b""  # main_hmac 解密 [16:80]
        self.main_ssk = b""  # activate 响应 ssk 解密（32B）
        self.sid = ""
        self.did = profile.get("did") or secrets.token_hex(16)
        self.userid = ""
        self.gid = profile.get("gid") or ""
        self.id_token = ""  # activate 响应里的 id_token（登录令牌）
        self.media_drm_id = (
            profile.get("media_drm_id")
            or hashlib.sha256(
                (self.android_id + ":" + self.oaid).encode("utf-8")
            ).hexdigest()
        )
        self._bootstrapped = False

    def _save_profile(self) -> None:
        """持久化注册设备画像；GID 必须与 Android ID/OAID/did 成套复用。"""
        if self.profile_path is None:
            return
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "android_id": self.android_id,
            "device_id": self.device_id,
            "oaid": self.oaid,
            "did": self.did,
            "gid": self.gid,
            "mua_counter": self.mua_counter,
            "install_time": self.install_time,
            "uptime_base": self.uptime_base,
            "uptime_anchor": self.uptime_anchor,
        }
        identity = getattr(self.signer, "_register_device", None)
        if identity is not None:
            payload["device_seed"] = identity.seed
            payload["register_policy"] = dict(identity.policy)
        preset = getattr(self.signer, "_device_preset", None)
        if preset is not None:
            payload["device_preset"] = preset.name
        export_state = getattr(self.signer, "export_state", None)
        if export_state is not None:
            payload.update(export_state())
        builder = getattr(self.signer, "_device_info_builder", None)
        builder_state = getattr(builder, "state", None)
        if builder_state is not None:
            payload["mua_device_info_state"] = builder_state
        if self.media_drm_id:
            payload["media_drm_id"] = self.media_drm_id
        temporary = self.profile_path.with_suffix(self.profile_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.profile_path)

    def _consume_mua_counter(self) -> int:
        """取走一个 MUA 序号并立即持久化，失败/重试也不能复用旧序号。"""
        value = int(self.mua_counter)
        self.mua_counter = value + 1
        self._save_profile()
        return value

    def _current_uptime_ms(self) -> int:
        """让虚拟设备 uptime 随现实时间前进，而不是每次签名回到固定值。"""
        elapsed = max(0.0, time.time() - self.uptime_anchor)
        return self.uptime_base + int(elapsed * 1000)

    def load_captured_anonymous_session(
        self, request_file: str, android_id: str = None, keys_file: str = None
    ) -> None:
        """导入真机已注册匿名会话；后续请求仍由 Python/unidbg 重新签名。"""
        captured = json.loads(Path(request_file).read_text(encoding="utf-8"))
        headers = {key.lower(): value for key, value in captured["headers"].items()}
        common = urllib.parse.parse_qs(
            headers["xy-common-params"], keep_blank_values=True
        )
        self.android_id = android_id or self.android_id
        self.device_id = common["deviceId"][0]
        if android_id and name_uuid_from_bytes(android_id.encode()) != self.device_id:
            raise ValueError("android_id 与抓包 deviceId 不匹配")
        self.sid = common["sid"][0]
        self.did = common["did"][0]
        self.gid = headers.get("x-mini-gid") or common["gid"][0]
        self.id_token = common.get("id_token", [""])[0]
        self.launch_id = int(common.get("launch_id", [str(int(time.time()))])[0])
        self.nqe_score = int(common.get("nqe_score", ["90"])[0] or 90)
        self.live_net_bandwidth = headers.get(
            "xy-live-net-bandwidth", self.live_net_bandwidth
        )
        self.live_net_networktype = headers.get(
            "xy-live-net-networktype", self.live_net_networktype
        )
        mua_head = headers["x-mini-mua"].split(".", 1)[0]
        mua_head += "=" * ((4 - len(mua_head) % 4) % 4)
        profile = json.loads(base64.urlsafe_b64decode(mua_head))
        uid = str(profile.get("u") or "")
        if not uid.startswith("00000000") or len(uid) <= 8:
            raise ValueError("抓包 MUA 不含可用 OAID")
        self.oaid = uid[8:]
        if keys_file:
            keys = json.loads(Path(keys_file).read_text(encoding="utf-8"))
            self.main_ssk = base64.b64decode(keys["main_ssk"])
            # main_hmac 以 vfc_code 当前下发值为准；手机 s.xml 可能保留旧值。
            self.fetch_main_hmac()
        else:
            self.fetch_main_hmac()
        self._bootstrapped = True

    # ------------------------------------------------------------------ #
    # 底层：带 shield 的请求
    # ------------------------------------------------------------------ #
    def _build_common_params(
        self, t: int, *, with_sid: bool, holder_ctry: str = "GB", fill_cpu: bool = False
    ) -> str:
        sid = urllib.parse.quote(self.sid) if with_sid else ""
        # 基础模板先留空动态字段；具体接口在签名前按真实抓包补齐。
        active_ctry = "CN" if fill_cpu else ""
        cpu_name = (
            urllib.parse.quote_plus("Qualcomm Technologies, Inc MSM8998")
            if fill_cpu
            else ""
        )
        id_token = urllib.parse.quote(self.id_token, safe="")
        return (
            f"fid=&gid={self.gid}"
            "&device_model=phone&tz=Asia%2FShanghai&channel=JTdCJTdE"
            f"&versionName={self.version_name}"
            f"&deviceId={self.device_id}&platform=android&sid={sid}&identifier_flag=0"
            "&cpu_abi=&nqe_score=&project_id=ECFAAF&x_trace_page_current=&lang=zh-Hans"
            "&app_id=ECFAAF01&uis=dark&teenager=0"
            f"&active_ctry={active_ctry}&cpu_name={cpu_name}&dlang=zh"
            "&data_ctry=CN&SUE=1"
            f"&launch_id={self.launch_id}&id_token={id_token}&device_level=&origin_channel=JTdCJTdE"
            "&overseas_channel=0&mlanguage=zh_cn&folder_type=none&auto_trans=0"
            f"&t={t}&build={self.app_build}&holder_ctry={holder_ctry}&did={self.did}"
        )

    def _shield_for(
        self,
        url: str,
        body: str = "",
        *,
        with_sid: bool,
        content_type: str = "application/x-www-form-urlencoded",
        with_mini: bool = False,
        xmini: dict = None,
        scene: str = "fs=1&point=-1",
        direction: str = "",
        platform_device_id: str = None,
        holder_ctry: str = "GB",
        fill_cpu: bool = False,
        sign_shield: bool = True,
    ):
        """构造一次请求的 headers（含 shield 签名）。

        activate 与内容接口的 header 不同，通过 scene/direction/platform_device_id/
        holder_ctry/fill_cpu 分别对齐真实抓包。
        """
        t = int(time.time())
        common = self._build_common_params(
            t, with_sid=with_sid, holder_ctry=holder_ctry, fill_cpu=fill_cpu
        )
        headers = {
            "xy-scene": scene,
            "x-legacy-did": self.device_id,
            "x-legacy-fid": "",
            "x-legacy-sid": self.sid if with_sid else "",
            "x-mini-gid": self.gid,
            "xy-common-params": common,
            "user-agent": self.user_agent,
            "referer": "https://app.xhs.cn/",
        }
        if direction:
            headers["xy-direction"] = direction
        if xmini:
            headers["x-mini-gid"] = xmini.get("gid") or self.gid
            headers["x-mini-s1"] = xmini["s1"]
            headers["x-mini-sig"] = xmini["sig"]
            headers["x-mini-mua"] = xmini["mua"]
        elif with_mini:
            raise RuntimeError(
                "生产路径禁止注入过期 X_MINI_*；请传入 signer.sign() 的 xmini"
            )
        if sign_shield:
            headers["shield"] = self._compute_shield(url, headers, body)
        headers["xy-platform-info"] = (
            f"platform=android&build={self.app_build}&deviceId={platform_device_id or self.device_id}"
        )
        headers["Content-Type"] = content_type
        return headers

    def _compute_shield(self, url: str, headers: dict, body: str = "") -> str:
        if self.shield_signer is not None and self.main_ssk:
            return self.shield_signer.sign(
                device_id=self.device_id,
                main_hmac=self.main_hmac,
                main_ssk=self.main_ssk,
                url=url,
                headers=headers,
                body=body if body else None,
            )
        return _shield.compute_shield(
            self.device_id,
            self.main_hmac,
            url,
            headers=headers,
            body=body,
            main_ssk=self.main_ssk,
        )

    def _urllib_get_json(self, url: str, headers: dict) -> tuple[int, dict]:
        """使用与已验证手机桥接一致的 urllib TLS/HTTP 请求路径。"""
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
        return status, json.loads(raw.decode("utf-8"))

    # ------------------------------------------------------------------ #
    # 会话引导
    # ------------------------------------------------------------------ #
    def fetch_main_hmac(self) -> str:
        """GET vfc_code，从响应头 xy-ter-str 取 main_hmac。"""
        url = (
            "https://edith.xiaohongshu.com/api/sns/v1/system_service/vfc_code"
            "?phone=13800138000&zone=1&type=login"
        )
        headers = self._shield_for(url, with_sid=False)
        r = self.session.get(url, headers=headers, timeout=self.timeout)
        hmac = r.headers.get("xy-ter-str") or r.headers.get("Xy-Ter-Str")
        if not hmac:
            raise RuntimeError(
                f"vfc_code 无 xy-ter-str，HTTP {r.status_code}: {r.text[:200]}"
            )
        try:
            raw = base64.b64decode(hmac, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise RuntimeError("vfc_code xy-ter-str 不是有效 Base64") from exc
        if len(raw) != 96:
            raise RuntimeError(
                f"vfc_code xy-ter-str 解码长度应为 96B，实际 {len(raw)}B"
            )
        pt = _crypto.decrypt_main_hmac(raw, self.device_id)
        if len(pt) != 96 or pt[:16] != _crypto.HEADER:
            prefix = pt[:16].hex() if isinstance(pt, bytes) else "<non-bytes>"
            raise RuntimeError(f"vfc_code main_hmac 明文头不匹配: {prefix}")
        self.main_hmac = hmac
        self.key64 = pt[16:80]
        return hmac

    def register_gid(self) -> str:
        """POST 真实 register/android，取得 56 位 GID。"""
        if self.signer is None:
            raise RuntimeError("register_gid 需要配置 signer")
        if not getattr(self.signer, "supports_register", False):
            raise RuntimeError("当前 signer 不支持 register/android")
        if len(self.gid) == 56:
            return self.gid
        path = "/api/v1/register/android"
        signed = self.signer.sign(
            gid="",
            oaid=self.oaid,
            android_id=self.android_id,
            device_id=self.device_id,
            media_drm_id=self.media_drm_id,
            method="POST",
            host="as.xiaohongshu.com",
            path=path,
            query="",
            include_mua_profile=True,
            mua_counter=self._consume_mua_counter(),
            uptime=self._current_uptime_ms(),
        )
        encoded_body = signed.get("register_body_b64") or ""
        if not encoded_body:
            raise RuntimeError("signer 未导出 register/android 请求体")
        body = base64.b64decode(encoded_body)
        # 注册发生在 GID/SID 建立前；xy-common-params 中 gid/sid 保持为空。
        old_gid = self.gid
        self.gid = ""
        common = self._build_common_params(int(time.time()), with_sid=False)
        self.gid = old_gid
        headers = {
            "x-mini-s1": signed["s1"],
            "x-mini-sig": signed["sig"],
            "x-mini-mua": signed["mua"],
            "xy-common-params": common,
            "content-type": "application/json",
            "user-agent": REGISTER_UA,
        }
        response = self.session.post(
            "https://as.xiaohongshu.com" + path,
            headers=headers,
            content=body,
            timeout=self.timeout,
        )
        payload = response.json()
        data = payload.get("data") or {}
        gid = data.get("g") or ""
        if response.status_code != 200 or not gid:
            raise RuntimeError(
                f"register/android 失败，HTTP {response.status_code}: {response.text[:300]}"
            )
        self.gid = gid
        self._save_profile()
        return gid

    def activate(self) -> dict:
        """POST activate，拿 sid/did/userid/gid + 解密 ssk。"""
        if not self.main_hmac:
            raise RuntimeError("请先 fetch_main_hmac()")
        priv, pub = _ssk.generate_keypair()
        pub_b64 = _ssk.client_public_key_base64(pub, url_encoded=True)
        install_time = self.install_time
        extra = urllib.parse.quote(
            json.dumps({"systemFontSize": 1, "em": -1}, separators=(",", ":"))
        )
        body = (
            "idfa=&idfv="
            + f"&android_id={self.android_id}"
            + "&channel=&pasteboard=&category="
            + f"&oaid={self.oaid}"
            + "&android_version=35&mac=&gaid=&attribution_id=&imei_encrypted="
            + f"&install_time={urllib.parse.quote(install_time)}"
            + "&install_first_open=true&open_url=&after_register=false&last_login_user_id="
            + f"&extra_data={extra}"
            + "&source=0"
            + f"&did_cur_value={self.did or secrets.token_hex(16)}&did_last_value=default-currentDid&did_cur_source=LOCAL_GEN"
            + f"&client_public_key_base64={pub_b64}"
        )
        url = "https://edith.xiaohongshu.com/api/sns/v1/user/activate"
        xmini = None
        if self.signer is not None:
            xmini = self.signer.sign(
                gid=self.gid,
                oaid=self.oaid,
                android_id=self.android_id,
                media_drm_id=self.media_drm_id,
                method="POST",
                host="edith.xiaohongshu.com",
                path="/api/sns/v1/user/activate",
                query="",
                body=body.encode("utf-8"),
                include_mua_profile=True,
                mua_counter=self._consume_mua_counter(),
                uptime=self._current_uptime_ms(),
            )
        headers = self._shield_for(
            url, body, with_sid=False, with_mini=False, xmini=xmini
        )
        r = self.session.post(
            url, headers=headers, content=body.encode("utf-8"), timeout=self.timeout
        )
        j = r.json()
        if not isinstance(j, dict):
            raise RuntimeError(f"activate 返回非对象 JSON，HTTP {r.status_code}")
        data = j.get("data") or {}
        if not isinstance(data, dict):
            raise RuntimeError(
                f"activate data 不是对象，HTTP {r.status_code}: {r.text[:400]}"
            )
        ssk_b64 = data.get("ssk")
        if not ssk_b64:
            raise RuntimeError(f"activate 无 ssk，HTTP {r.status_code}: {r.text[:400]}")
        session_value = data.get("session")
        if session_value in (None, ""):
            raise RuntimeError(
                f"activate 无 session，HTTP {r.status_code}: {r.text[:400]}"
            )
        effective_gid = self.gid or data.get("gid") or ""
        if not effective_gid:
            raise RuntimeError(
                "activate 未返回 gid，且本地没有已注册 gid；不能用随机字符串替代服务端身份"
            )
        main_ssk = _ssk.decrypt_ssk(priv, ssk_b64)
        self.main_ssk = main_ssk
        self.sid = "session." + str(session_value)
        self.did = data.get("did_cur_value") or self.did
        self.userid = data.get("userid") or ""
        self.gid = effective_gid
        self.id_token = (
            data.get("id_token")
            or data.get("session_token")
            or data.get("token")
            or data.get("x_id_token")
            or ""
        )
        self._bootstrapped = True
        self._save_profile()
        return {
            "code": j.get("code"),
            "success": j.get("success"),
            "sid": self.sid,
            "did": self.did,
        }

    def fetch_cold_start_id_token(self) -> str:
        """同步匿名 cold_start_config 中下发的 id_token。"""
        if self.signer is None:
            return self.id_token
        path = "/api/sns/v1/system/cold_start_config"
        query = "need_user_info=true&has_displayed_translation_guide=false"
        # 2026-08-28 当前 App 实测域名为 edith；旧 HAR 中的 rec 已过时。
        url = f"https://edith.xiaohongshu.com{path}?{query}"
        xmini = self.signer.sign(
            gid=self.gid,
            oaid=self.oaid,
            android_id=self.android_id,
            media_drm_id=self.media_drm_id,
            method="GET",
            host="edith.xiaohongshu.com",
            path=path,
            query=query,
            include_mua_profile=True,
            mua_counter=self._consume_mua_counter(),
            uptime=self._current_uptime_ms(),
        )
        headers = self._shield_for(
            url,
            with_sid=True,
            xmini=xmini,
            scene="fs=0&point=601",
            platform_device_id=self.device_id,
            holder_ctry="CN",
            fill_cpu=True,
            sign_shield=False,
        )
        headers.pop("Content-Type", None)
        headers.pop("x-legacy-fid", None)
        common = headers["xy-common-params"]
        common = common.replace(
            "cpu_abi=&nqe_score=", f"cpu_abi=arm64-v8a&nqe_score={self.nqe_score}"
        )
        common = common.replace(
            "x_trace_page_current=&lang=", "x_trace_page_current=explore_feed&lang="
        )
        common = common.replace(
            "device_level=&origin_channel=", "device_level=2&origin_channel="
        )
        headers["xy-common-params"] = common
        headers["x-b3-traceid"] = secrets.token_hex(8)
        headers["x-xray-traceid"] = secrets.token_hex(16)
        headers["x-xhs-ext-failover"] = "128"
        headers["x-xhs-ext-dnsisolatetag"] = "0"
        headers["shield"] = self._compute_shield(url, headers, "")
        self.last_cold_request = {"url": url, "headers": dict(headers)}
        status, payload = self._urllib_get_json(url, headers)
        tags = ((payload.get("data") or {}).get("user_info") or {}).get(
            "user_tags"
        ) or {}
        token = tags.get("id_token") if isinstance(tags, dict) else ""
        if status != 200 or payload.get("code") != 0 or not token:
            raise RuntimeError(
                f"cold_start_config 无 id_token，HTTP {status}: "
                f"{json.dumps(payload, ensure_ascii=False)[:300]}"
            )
        self.id_token = token
        return token

    def bootstrap(self) -> dict:
        """一步完成会话引导。"""
        self.fetch_main_hmac()
        if self.signer is not None and getattr(self.signer, "supports_register", True):
            self.register_gid()
        elif self.signer is not None and not self.gid:
            raise RuntimeError(
                "当前 signer 不支持 register/android，必须先导入已注册 gid"
            )
        result = self.activate()
        if self.signer is not None:
            self.fetch_cold_start_id_token()
        return result

    # ------------------------------------------------------------------ #
    # 内容接口
    # ------------------------------------------------------------------ #
    def get_note_imagefeed(self, note_id: str) -> dict:
        """GET /api/sns/v1/note/imagefeed —— 图文 / 实况（含 live_photo AAC 音轨）。

        2026-08-28 已验证：LocalUnidbgSigner + LocalUnidbgShieldSigner 可在桌面匿名会话中
        返回真实内容。MUA Part2 的设备画像必须与外层请求头一致。
        """
        if not self._bootstrapped:
            self.bootstrap()
        url = (
            f"https://edith.xiaohongshu.com/api/sns/v1/note/imagefeed?note_id={note_id}"
            "&page=1&has_ads_tag=false&num=5&fetch_mode=1&source=&source_scene=&ads_track_id=&ads_track_url=&from_rec_local=false"
            "&extra_params=%7B%22co_author_id%22%3A%22%22%2C%22is_out_of_china%22%3A0%2C"
            "%22longtext_collection%22%3A0%2C%22screen_height%22%3A1920%2C%22screen_width%22%3A1080%2C%22track_info%22%3A%22%22%7D"
        )
        xmini = None
        if self.signer is not None:
            xmini = self.signer.sign(
                gid=self.gid,
                oaid=self.oaid,
                android_id=self.android_id,
                media_drm_id=self.media_drm_id,
                method="GET",
                host="edith.xiaohongshu.com",
                path="/api/sns/v1/note/imagefeed",
                query=url.split("?", 1)[1],
                include_mua_profile=True,
                mua_counter=self._consume_mua_counter(),
                uptime=self._current_uptime_ms(),
            )
        headers = self._shield_for(
            url,
            with_sid=True,
            with_mini=False,
            xmini=xmini,
            scene="fs=0&point=2565",
            direction="70",
            platform_device_id=self.device_id,
            holder_ctry="CN",
            fill_cpu=True,
            sign_shield=False,
        )
        headers.pop("Content-Type", None)
        headers.pop("x-legacy-fid", None)
        common = headers["xy-common-params"]
        common = common.replace(
            "cpu_abi=&nqe_score=", f"cpu_abi=arm64-v8a&nqe_score={self.nqe_score}"
        )
        common = common.replace(
            "x_trace_page_current=&lang=",
            f"x_trace_page_current={self.imagefeed_trace_page}&lang=",
        )
        common = common.replace(
            "device_level=&origin_channel=", "device_level=2&origin_channel="
        )
        headers["xy-common-params"] = common
        headers["xy-live-net-bandwidth"] = self.live_net_bandwidth
        headers["xy-live-net-networktype"] = self.live_net_networktype
        headers["x-b3-traceid"] = secrets.token_hex(8)
        headers["x-xray-traceid"] = secrets.token_hex(16)
        headers["x-xhs-ext-failover"] = "128"
        headers["x-xhs-ext-dnsisolatetag"] = "0"
        headers["shield"] = self._compute_shield(url, headers, "")
        self.last_request = {"url": url, "headers": dict(headers)}
        self.last_response_status, payload = self._urllib_get_json(url, headers)
        return payload

    def get_homefeed(self) -> dict:
        """GET /api/sns/v6/homefeed —— 视频推荐流。

        使用最终 URL 的 path/query 调用 signer；Shield 在写入动态 x-mini 之后计算。
        """
        if not self._bootstrapped:
            self.bootstrap()
        if self.signer is None:
            raise RuntimeError("homefeed 需要 signer，不能回退到过期 X_MINI_*")
        geo = base64.b64encode(
            b'{"latitude":20.982105,"longitude":111.313309}'
        ).decode()
        query = (
            f"oid=homefeed_recommend&cursor_score=&geo={geo}"
            "&page_size=20&session_id=&refresh_type=1&source=homefeed_recommend"
            "&unread_begin_note_id=&unread_end_note_id=&unread_note_count=0&category="
        )
        url = f"https://rec.xiaohongshu.com/api/sns/v6/homefeed?{query}"
        xmini = self.signer.sign(
            gid=self.gid,
            oaid=self.oaid,
            android_id=self.android_id,
            media_drm_id=self.media_drm_id,
            method="GET",
            host="rec.xiaohongshu.com",
            path="/api/sns/v6/homefeed",
            query=query,
            include_mua_profile=True,
            mua_counter=self._consume_mua_counter(),
            uptime=self._current_uptime_ms(),
        )
        headers = self._shield_for(url, with_sid=True, with_mini=False, xmini=xmini)
        self.last_homefeed_request = {
            "url": url,
            "headers": dict(headers),
            "query": query,
        }
        r = self.session.get(url, headers=headers, timeout=self.timeout)
        return r.json()

    def _signed_get(
        self,
        host: str,
        path: str,
        query: str,
        *,
        scene: str = "fs=0&point=0",
        direction: str = "70",
        fill_cpu: bool = True,
    ) -> dict:
        if not self._bootstrapped:
            self.bootstrap()
        if self.signer is None:
            raise RuntimeError("该接口需要 signer")
        url = f"https://{host}{path}"
        if query:
            url = f"{url}?{query}"
        xmini = self.signer.sign(
            gid=self.gid,
            oaid=self.oaid,
            android_id=self.android_id,
            media_drm_id=self.media_drm_id,
            method="GET",
            host=host,
            path=path,
            query=query,
            include_mua_profile=True,
            mua_counter=self._consume_mua_counter(),
            uptime=self._current_uptime_ms(),
        )
        headers = self._shield_for(
            url,
            with_sid=True,
            with_mini=False,
            xmini=xmini,
            scene=scene,
            direction=direction,
            platform_device_id=self.device_id,
            holder_ctry="CN",
            fill_cpu=fill_cpu,
            sign_shield=False,
        )
        headers.pop("Content-Type", None)
        headers.pop("x-legacy-fid", None)
        common = headers["xy-common-params"]
        common = common.replace(
            "cpu_abi=&nqe_score=", f"cpu_abi=arm64-v8a&nqe_score={self.nqe_score}"
        )
        common = common.replace(
            "device_level=&origin_channel=", "device_level=2&origin_channel="
        )
        headers["xy-common-params"] = common
        headers["x-b3-traceid"] = secrets.token_hex(8)
        headers["x-xray-traceid"] = secrets.token_hex(16)
        headers["shield"] = self._compute_shield(url, headers, "")
        self.last_request = {"url": url, "headers": dict(headers)}
        _, payload = self._urllib_get_json(url, headers)
        return payload

    def search_notes(self, keyword: str, *, page: int = 1, page_size: int = 20) -> dict:
        keyword = urllib.parse.quote(keyword, safe="")
        search_id = secrets.token_hex(12)
        session_id = secrets.token_hex(12)
        geo = base64.b64encode(
            b'{"latitude":30.311741,"longitude":120.227697}'
        ).decode()
        query = (
            f"keyword={keyword}&page={page}&page_pos=0&page_size={page_size}"
            f"&search_id={search_id}&session_id={session_id}&source=explore_feed"
            f"&scene=history&allow_rewrite=1&geo={geo}&is_out_of_china=0"
            "&location_permission=0&request_trigger_from=pre_request"
        )
        return self._signed_get(
            "so.xiaohongshu.com",
            "/api/sns/v10/search/notes",
            query,
            scene="fs=0&point=309",
        )

    def get_user_info(self, user_id: str) -> dict:
        query = f"user_id={user_id}&new_page_exp=1&profile_page_head_exp=1"
        return self._signed_get(
            "edith.xiaohongshu.com",
            "/api/sns/v3/user/info",
            query,
            scene="fs=0&point=0",
        )

    def get_user_posted(self, user_id: str, *, cursor: str = "", num: int = 20) -> dict:
        query = (
            f"user_id={user_id}&num={num}&page_size={num}&source=user_profile"
            f"&use_cursor=1&pin_note_id=&cursor={urllib.parse.quote(cursor)}"
        )
        return self._signed_get(
            "edith.xiaohongshu.com",
            "/api/sns/v4/note/user/posted",
            query,
            scene="fs=0&point=789",
        )

    def close(self):
        """关闭 HTTP 会话及由客户端持有的 signer 进程。"""
        self.session.close()
        closed = set()
        for component in (self.signer, self.shield_signer):
            if component is None or id(component) in closed:
                continue
            closed.add(id(component))
            close = getattr(component, "close", None)
            if close is not None:
                close()


from .note import parse_imagefeed

if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    note_id = sys.argv[1] if len(sys.argv) > 1 else None
    client = XhsAppClient()
    print(f"android_id = {client.android_id}")
    print(f"deviceId   = {client.device_id}")

    client.fetch_main_hmac()
    print(f"main_hmac  = {client.main_hmac[:28]}...")
    print(f"key64      = {client.key64.hex()[:32]}...")

    act = client.activate()
    print(f"activate   = code={act['code']} sid={client.sid[:24]}...")

    if note_id:
        payload = client.get_note_imagefeed(note_id)
        note = parse_imagefeed(payload, note_id)
        print("\n=== 笔记解析结果 ===")
        print(json.dumps(note, ensure_ascii=False, indent=2))
    else:
        feed = client.get_homefeed()
        body = json.dumps(feed, ensure_ascii=False)
        aac_count = body.count('"audio_codec":"aac"')
        print(
            f"homefeed code={feed.get('code')} master_url 数={body.count('master_url')} aac 数={aac_count}"
        )
    client.close()
