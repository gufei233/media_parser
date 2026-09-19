"""纯 Python MUA 设备画像输入层。

DeviceInfoBuilder 保留真机快照兼容路径；DeviceInfoGenerator 不读取快照，
从 seed、AndroidDevicePreset 与显式 runtime state 生成完整 95 字段画像。
当前只有 Xiaomi MI 6 / Lineage 15 预设。静态 Build/硬件值属于预设，不是
每次随机生成真实机型；已验证 schema、自洽加密回读和持久化，尚未完成
新 native 会话逐字段对照与服务端长期验收。
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path


def load_profile_sample(path: str | Path) -> dict[str, object]:
    """读取直接画像 JSON 或 Frida `_deflate_hook.json` 记录数组。"""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        isinstance(value, dict)
        and "profile" in value
        and isinstance(value["profile"], dict)
    ):
        value = value["profile"]
    if isinstance(value, list):
        candidates = [
            row for row in value if isinstance(row, dict) and row.get("json_asc")
        ]
        if not candidates:
            raise ValueError(f"{path} 中没有可用 json_asc 画像")
        value = json.loads(
            max(candidates, key=lambda row: int(row.get("avail_in", 0) or 0))[
                "json_asc"
            ]
        )
    if not isinstance(value, dict):
        raise TypeError("设备画像必须是 JSON object")
    return {str(k): v for k, v in value.items()}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _seed_bytes(seed: bytes, label: str, length: int) -> bytes:
    """Domain-separated deterministic bytes without mutable PRNG state."""
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(
            hashlib.sha256(
                seed + b"\x00" + label.encode("utf-8") + counter.to_bytes(4, "big")
            ).digest()
        )
        counter += 1
    return bytes(output[:length])


def _seed_int(seed: bytes, label: str, lower: int, upper: int) -> int:
    if upper < lower:
        raise ValueError("upper must be >= lower")
    width = upper - lower + 1
    return lower + int.from_bytes(_seed_bytes(seed, label, 8), "big") % width


@dataclass(frozen=True)
class AndroidDevicePreset:
    """A field-compatible Android hardware/build preset for all 95 slots."""

    name: str = "xiaomi-mi6-lineage-15"
    package_name: str = "com.xingin.xhs"
    app_version: str = "9.43.1"
    app_version_code: int = 9431801
    manufacturer: str = "Xiaomi"
    brand: str = "Xiaomi"
    model: str = "MI 6"
    device: str = "sagit"
    product: str = "lineage_sagit"
    board: str = "msm8998"
    hardware: str = "msm8998"
    build_id: str = "BP1A.250505.005"
    build_host: str = "e563fef06ff1"
    build_incremental: str = "0feb95543f"
    build_tags: str = "release-keys"
    build_type: str = "userdebug"
    android_release: str = "15"
    sdk_int: int = 35
    security_patch: str = "2026-08-01"
    build_fingerprint: str = (
        "Xiaomi/sagit/sagit:8.0.0/OPR1.170623.027/V9.2.3.0.OCAMIEK:user/release-keys"
    )
    supported_abis: str = "arm64-v8a,armeabi-v7a,armeabi"
    machine: str = "aarch64"
    kernel_release: str = "4.4.302-perf+"
    kernel_version: str = "#1 SMP PREEMPT Fri Aug 14 08:50:37 UTC 2026"
    baseband: str = "AT20-0506_2249_2f010e6,AT20-0506_2249_2f010e6"
    display: str = "1080,1920,480"
    locale_country: str = "cn"
    sim_operator_name: str = "giffgaff"
    sim_operator_numeric: str = "23410"
    total_memory_bytes: int = 8 * 1024**3
    total_storage_bytes: int = 128 * 1024**3
    build_time_ms: int = 1786696554000

    def dalvik_user_agent(self) -> str:
        """Outer UA matching this preset's MUA Part2 model/build/resolution."""
        width, height, _dpi = str(self.display).split(",")
        return (
            f"Dalvik/2.1.0 (Linux; U; Android {self.android_release}; "
            f"{self.model} Build/{self.build_id}) "
            f"Resolution/{width}*{height} Version/{self.app_version} "
            f"Build/{self.app_version_code} "
            f"Device/({self.manufacturer};{self.model}) "
            f"discover/{self.app_version} NetType/WiFi"
        )


ANDROID_DEVICE_PRESETS: dict[str, AndroidDevicePreset] = {
    "xiaomi-mi6-lineage-15": AndroidDevicePreset(),
    "google-pixel6-aosp-15": AndroidDevicePreset(
        name="google-pixel6-aosp-15",
        manufacturer="Google",
        brand="google",
        model="Pixel 6",
        device="oriole",
        product="oriole",
        board="oriole",
        hardware="oriole",
        build_id="AP4A.250205.002",
        build_host="abfarm-release",
        build_incremental="12932220",
        build_tags="release-keys",
        build_type="user",
        android_release="15",
        sdk_int=35,
        security_patch="2026-08-01",
        build_fingerprint="google/oriole/oriole:15/AP4A.250205.002/12932220:user/release-keys",
        kernel_release="5.10.198-android13",
        kernel_version="#1 SMP PREEMPT Fri Aug 14 08:50:37 UTC 2026",
        baseband="",
        display="1080,2400,420",
        locale_country="us",
        sim_operator_name="",
        sim_operator_numeric="",
        total_memory_bytes=8 * 1024**3,
        total_storage_bytes=128 * 1024**3,
        build_time_ms=1786696554000,
    ),
}


def get_device_preset(value: str | AndroidDevicePreset) -> AndroidDevicePreset:
    if isinstance(value, AndroidDevicePreset):
        return value
    try:
        return ANDROID_DEVICE_PRESETS[str(value)]
    except KeyError as exc:
        raise ValueError(f"未知设备预设: {value}") from exc


class DeviceInfoBuilder:
    """按设备档案生成 MUA Part2 的画像。

    `base` 是真机采集模板；`state` 允许调用方覆盖电池、网络、USB 等状态。
    `persisted` 用于保存跨请求不应变化的 x146/x185/x269 等值。
    """

    def __init__(
        self,
        base: Mapping[str, object],
        *,
        persisted: Mapping[str, object] | None = None,
        install_time_ms: int | None = None,
        uptime_base: int = 0,
        uptime_anchor: float | None = None,
        identity_seed: str = "",
    ) -> None:
        self.base = copy.deepcopy(dict(base))
        self.persisted = dict(persisted or {})
        self.identity_seed = str(
            identity_seed or self.persisted.get("identity_seed") or ""
        )
        self.install_time_ms = int(
            install_time_ms
            or self.persisted.get("install_time_ms")
            or self.base.get("x3")
            or _now_ms()
        )
        self.uptime_base = int(uptime_base or self.persisted.get("uptime_base") or 1)
        self.uptime_anchor = float(
            uptime_anchor or self.persisted.get("uptime_anchor") or time.time()
        )
        # x146 是跨请求持久化字段。若调用方提供设备身份，优先从身份派生，
        # 避免多个 Python 进程因首次启动时机不同而各自产生不可复现值；
        # 没有身份时才使用一次随机值，并通过 state 持久化。
        if not self.persisted.get("x146"):
            if self.base.get("x146"):
                self.persisted["x146"] = self.base["x146"]
            elif self.identity_seed:
                self.persisted["x146"] = hashlib.sha256(
                    ("x146:" + self.identity_seed).encode("utf-8")
                ).hexdigest()[:56]
            else:
                self.persisted["x146"] = secrets.token_hex(28)
        self.persisted.setdefault("x185", self.base.get("x185") or "IiGgSsKkCVvEeP")
        self.persisted.setdefault("x269", self.base.get("x269") or 1230768000000)

    @property
    def state(self) -> dict[str, object]:
        return dict(self.persisted)

    def build(self, context: Mapping[str, object] | None = None) -> dict[str, object]:
        context = dict(context or {})
        out = copy.deepcopy(self.base)
        now = _now_ms()

        out["x3"] = self.install_time_ms
        out["x4"] = self.install_time_ms
        out["x146"] = self.persisted["x146"]
        out["x185"] = self.persisted["x185"]
        out["x269"] = self.persisted["x269"]
        out["x87"] = now
        out["x243"] = now
        out["x260"] = now
        out["x289"] = now + 1
        out["x44"] = now
        out["x6"] = int(context.get("launch_time_ms") or out.get("x6") or now)
        out["x72"] = int(context.get("wifi_connected_ms") or max(0, now - 145000))
        out["x73"] = int(context.get("wifi_dhcp_ms") or max(0, now - 55000))
        out["x234"] = context.get(
            "file_times", out.get("x234", {"1": 0, "2": 0, "3": 0})
        )
        out["x293"] = int(context.get("free_storage") or out.get("x293") or 132146402)
        for key in (
            "x32",
            "x33",
            "x34",
            "x35",
            "x36",
            "x37",
            "x38",
            "x39",
            "x41",
            "x43",
            "x45",
            "x78",
            "x79",
            "x80",
            "x290",
            "x301",
            "x305",
        ):
            if key in context:
                out[key] = context[key]
        if not isinstance(out.get("x146"), str) or len(str(out["x146"])) < 32:
            out["x146"] = hashlib.sha256(str(out.get("x146", "")).encode()).hexdigest()[
                :56
            ]
        return out


def builder_state(builder: DeviceInfoBuilder) -> dict[str, object]:
    return {
        "install_time_ms": builder.install_time_ms,
        "uptime_base": builder.uptime_base,
        "uptime_anchor": builder.uptime_anchor,
        "identity_seed": builder.identity_seed,
        **builder.state,
    }


@dataclass(frozen=True)
class RuntimeDeviceState:
    """Explicit per-run Android state; never mutates persistent identity fields."""

    now_ms: int | None = None
    uptime: int | None = None
    launch_time_ms: int | None = None
    wifi_connected_ms: int | None = None
    wifi_dhcp_ms: int | None = None
    free_storage: int | None = None
    total_storage: int | None = None
    available_memory: int | None = None
    network: str | None = None
    battery_status: int | None = None
    battery_level: int | None = None
    battery_scale: int | None = None
    battery_plugged: int | None = None
    process_id: int | None = None
    thread_id: int | None = None
    brightness: int | None = None
    usb_config: str | None = None
    file_times: Mapping[str, object] | None = None
    # Explicit native profile keys for fields whose semantic mapping is not yet
    # stable. This avoids inventing values while still allowing a captured
    # runtime snapshot to be replayed exactly.
    profile_overrides: Mapping[str, object] = field(default_factory=dict)

    def as_context(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for key in (
            "now_ms",
            "uptime",
            "launch_time_ms",
            "wifi_connected_ms",
            "wifi_dhcp_ms",
            "free_storage",
            "total_storage",
            "available_memory",
            "network",
            "battery_status",
            "battery_level",
            "battery_scale",
            "battery_plugged",
            "process_id",
            "thread_id",
            "brightness",
            "usb_config",
            "file_times",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        # x43 is the confirmed network-type slot used by existing builder
        # callers; all other uncertain fields must be supplied explicitly in
        # profile_overrides rather than guessed here.
        if self.network is not None:
            out["x43"] = self.network
        out.update(self.profile_overrides)
        return out


class DeviceInfoGenerator:
    """Generate the complete 95-field Android MUA profile without snapshots.

    Persistent identity is derived from ``seed``. Runtime values come from
    ``RuntimeDeviceState`` or the injected clock. The generator performs no
    filesystem, Android, native-library, or network reads.
    """

    PROFILE_KEYS = frozenset(
        {
            "x0",
            "x1",
            "x2",
            "x3",
            "x4",
            "x5",
            "x6",
            "x7",
            "x8",
            "x9",
            "x10",
            "x11",
            "x12",
            "x13",
            "x14",
            "x15",
            "x16",
            "x17",
            "x18",
            "x19",
            "x20",
            "x21",
            "x22",
            "x23",
            "x24",
            "x25",
            "x26",
            "x27",
            "x28",
            "x29",
            "x30",
            "x31",
            "x32",
            "x33",
            "x34",
            "x35",
            "x36",
            "x37",
            "x38",
            "x39",
            "x40",
            "x41",
            "x42",
            "x43",
            "x44",
            "x45",
            "x70",
            "x72",
            "x73",
            "x78",
            "x79",
            "x80",
            "x87",
            "x92",
            "x93",
            "x98",
            "x120",
            "x131",
            "x146",
            "x185",
            "x186",
            "x187",
            "x194",
            "x202",
            "x203",
            "x206",
            "x207",
            "x231",
            "x232",
            "x234",
            "x235",
            "x236",
            "x237",
            "x238",
            "x242",
            "x243",
            "x247",
            "x258",
            "x259",
            "x260",
            "x261",
            "x263",
            "x264",
            "x267",
            "x269",
            "x272",
            "x289",
            "x290",
            "x293",
            "x296",
            "x301",
            "x302",
            "x303",
            "x304",
            "x305",
        }
    )

    def __init__(
        self,
        seed: str | bytes | None = None,
        *,
        preset: str | AndroidDevicePreset = "xiaomi-mi6-lineage-15",
        persisted: Mapping[str, object] | None = None,
        created_at_ms: int | None = None,
        clock_ms: Callable[[], int] = _now_ms,
    ) -> None:
        if seed is None:
            seed_text = secrets.token_hex(32)
        elif isinstance(seed, bytes):
            if not seed:
                raise ValueError("seed must not be empty")
            seed_text = bytes(seed).hex()
        else:
            seed_text = str(seed)
            if not seed_text:
                raise ValueError("seed must not be empty")
        # Persist and derive from the same textual representation so a random
        # first-run seed rehydrates identically in the next Python process.
        seed_bytes = seed_text.encode("utf-8")
        self.seed = seed_bytes
        self.seed_text = seed_text
        self.preset = get_device_preset(preset)
        self.clock_ms = clock_ms
        self.persisted = dict(persisted or {})
        created = int(
            created_at_ms or self.persisted.get("created_at_ms") or self.clock_ms()
        )
        self.persisted.setdefault("schema", "xhs-generated-device-v1")
        self.persisted.setdefault("preset", self.preset.name)
        self.persisted.setdefault("identity_seed", self.seed_text)
        self.persisted.setdefault("created_at_ms", created)
        self.persisted.setdefault(
            "install_time_ms",
            created - _seed_int(self.seed, "install-age-days", 7, 120) * 86_400_000,
        )
        self.persisted.setdefault(
            "boot_time_ms",
            created - _seed_int(self.seed, "boot-age-minutes", 25, 72 * 60) * 60_000,
        )
        self.persisted.setdefault("x146", _seed_bytes(self.seed, "x146", 28).hex())
        self.persisted.setdefault("x269", 1230768000000)
        self.persisted.setdefault(
            "process_id", _seed_int(self.seed, "process-id", 12000, 29999)
        )
        self.persisted.setdefault(
            "thread_id",
            int(self.persisted["process_id"])
            + _seed_int(self.seed, "thread-delta", 1, 64),
        )
        self.persisted.setdefault(
            "uid", _seed_int(self.seed, "android-uid", 10000, 19999)
        )
        self.persisted.setdefault(
            "file_times",
            {
                "1": int(self.persisted["install_time_ms"]) - 67648,
                "2": int(self.persisted["install_time_ms"]) - 70281,
                "3": int(self.persisted["install_time_ms"]) - 70435,
            },
        )

    @property
    def state(self) -> dict[str, object]:
        return copy.deepcopy(self.persisted)

    def _default_free_storage(self, total: int) -> int:
        percent = _seed_int(self.seed, "free-storage-percent", 31, 72)
        return total * percent // 100

    def build(
        self,
        state: RuntimeDeviceState | Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        context = (
            state.as_context()
            if isinstance(state, RuntimeDeviceState)
            else dict(state or {})
        )
        preset = self.preset

        def value(name: str, default: object) -> object:
            current = context.get(name)
            return default if current is None else current

        now = int(value("now_ms", self.clock_ms()))
        boot = int(self.persisted["boot_time_ms"])
        uptime = max(1, int(value("uptime", now - boot)))
        launch = int(value("launch_time_ms", now - 1300))
        network = str(value("network", "wifi"))
        total_storage = int(value("total_storage", preset.total_storage_bytes))
        free_storage = int(
            value("free_storage", self._default_free_storage(total_storage))
        )
        available_memory = int(value("available_memory", 132146402))
        battery_level = int(value("battery_level", 99))
        battery_scale = int(value("battery_scale", 100))
        battery_status = int(value("battery_status", 2))
        battery_plugged = int(value("battery_plugged", 2))
        pid = int(value("process_id", self.persisted["process_id"]))
        tid = int(value("thread_id", self.persisted["thread_id"]))
        usb_config = str(value("usb_config", "adb"))
        wifi_connected = int(value("wifi_connected_ms", now - min(uptime, 145000)))
        wifi_dhcp = int(value("wifi_dhcp_ms", now - min(uptime, 55000)))
        if network != "wifi":
            wifi_connected = -1
            wifi_dhcp = -1

        profile: dict[str, object] = {
            "x0": preset.package_name,
            "x1": preset.app_version,
            "x2": preset.app_version_code,
            "x3": int(self.persisted["install_time_ms"]),
            "x4": int(self.persisted["install_time_ms"]),
            "x5": "JTdCJTdE",
            "x6": launch,
            "x7": preset.hardware,
            "x8": preset.build_time_ms,
            "x9": (
                f"{preset.product}-{preset.build_type} {preset.android_release} "
                f"{preset.build_id} {preset.build_incremental}"
            ),
            "x10": preset.build_fingerprint,
            "x11": preset.build_host,
            "x12": preset.build_id,
            "x13": preset.build_tags,
            "x14": preset.build_type,
            "x15": preset.build_incremental,
            "x16": preset.android_release,
            "x17": preset.sdk_int,
            "x18": preset.security_patch,
            "x19": preset.board,
            "x20": preset.manufacturer,
            "x21": preset.supported_abis,
            "x22": preset.device,
            "x23": preset.brand,
            "x24": preset.model,
            "x25": preset.product,
            "x26": preset.machine,
            "x27": preset.kernel_release,
            "x28": preset.kernel_version,
            "x29": preset.baseband,
            "x30": preset.display,
            "x31": 55,
            "x32": 1,
            "x33": battery_status,
            "x34": battery_scale,
            "x35": battery_level,
            "x36": battery_plugged,
            "x37": 0,
            "x38": 1 if network == "wifi" else 0,
            "x39": usb_config,
            "x40": 5,
            "x41": preset.sim_operator_name,
            "x42": preset.sim_operator_numeric,
            "x43": network,
            "x44": now,
            "x45": int(value("brightness", 21)),
            "x70": 3 if network == "wifi" else 1,
            "x72": wifi_connected,
            "x73": wifi_dhcp,
            "x78": int(self.persisted["uid"]),
            "x79": pid,
            "x80": tid,
            "x87": max(0, now - 1027),
            "x92": preset.sdk_int,
            "x93": 3,
            "x98": "0",
            "x120": "0",
            "x131": "0",
            "x146": str(self.persisted["x146"]),
            "x185": "IiGgSsKkCVvEeP",
            "x186": -1,
            "x187": -1,
            "x194": "1",
            "x202": "1",
            "x203": "1",
            "x206": 0,
            "x207": 0,
            "x231": total_storage,
            "x232": total_storage,
            "x234": copy.deepcopy(value("file_times", self.persisted["file_times"])),
            "x235": 3275987,
            "x236": free_storage,
            "x237": free_storage,
            "x238": preset.locale_country,
            "x242": [],
            "x243": max(0, now - 1063),
            "x247": {
                "0": 53.333333333333336,
                "1": 71.42857142857143,
                "2": 71.42857142857143,
                "3": 32.0,
                "4": 85.71428571428571,
                "5": 71.42857142857143,
            },
            "x258": 0,
            "x259": 0,
            "x260": now,
            "x261": 1,
            "x263": 0,
            "x264": 0,
            "x267": 1,
            "x269": int(self.persisted["x269"]),
            "x272": 0,
            "x289": now + 19,
            "x290": 1,
            "x293": max(0, min(free_storage, available_memory)),
            "x296": "",
            "x301": "LOADED,ABSENT",
            "x302": "",
            "x303": "",
            "x304": 2,
            "x305": -3,
        }
        profile.update(dict(context.get("profile_overrides") or {}))
        # Mapping callers may supply native slots directly, preserving the same
        # override behavior as DeviceInfoBuilder without coupling to snapshots.
        profile.update({k: v for k, v in context.items() if str(k).startswith("x")})
        missing = self.PROFILE_KEYS.difference(profile)
        extra = set(profile).difference(self.PROFILE_KEYS)
        if missing or extra:
            raise RuntimeError(
                f"generated device profile schema mismatch: missing={sorted(missing)}, extra={sorted(extra)}"
            )
        return profile


@dataclass
class DeviceProfileSnapshot:
    """Portable persistent identity + image template + last runtime state."""

    base: dict[str, object]
    persisted: dict[str, object] = field(default_factory=dict)
    runtime: dict[str, object] = field(default_factory=dict)
    schema: str = "xhs-device-profile-v1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "base": copy.deepcopy(self.base),
            "persisted": copy.deepcopy(self.persisted),
            "runtime": copy.deepcopy(self.runtime),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> DeviceProfileSnapshot:
        if "base" in value:
            base = value["base"]
            if not isinstance(base, Mapping):
                raise TypeError("snapshot.base must be an object")
            return cls(
                dict(base),
                dict(value.get("persisted") or {}),
                dict(value.get("runtime") or {}),
                str(value.get("schema") or "xhs-device-profile-v1"),
            )
        # Accept a raw native profile as a convenient import format.
        return cls({str(k): v for k, v in value.items()})

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> DeviceProfileSnapshot:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise TypeError("device snapshot must be a JSON object")
        return cls.from_dict(value)

    def builder(self, **kwargs: object) -> DeviceInfoBuilder:
        return DeviceInfoBuilder(self.base, persisted=self.persisted, **kwargs)

    def build(
        self, state: RuntimeDeviceState | Mapping[str, object] | None = None
    ) -> dict[str, object]:
        context = (
            state.as_context()
            if isinstance(state, RuntimeDeviceState)
            else dict(state or self.runtime)
        )
        builder = self.builder()
        result = builder.build(context)
        self.persisted = builder.state
        self.runtime = context
        return result


def load_device_snapshot(path: str | Path) -> DeviceProfileSnapshot:
    return DeviceProfileSnapshot.load(path)


def save_device_snapshot(path: str | Path, snapshot: DeviceProfileSnapshot) -> None:
    snapshot.save(path)
