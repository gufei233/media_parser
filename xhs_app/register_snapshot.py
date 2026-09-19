"""Register snapshot primitives and an explicit-input builder.

JNI IDs, attestation bytes, filesystem facts and optional file collectors are
environment inputs. This module does not query the host OS, and TinyProbe JNI
hashCode IDs are not claimed to equal Android ART method IDs.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import struct
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

MAP_METHOD_DESCRIPTORS = (
    "java/util/Map->size()I",
    "java/util/Map->get(Ljava/lang/Object;)Ljava/lang/Object;",
    "java/util/Map->put(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
    "java/util/Map->remove(Ljava/lang/Object;)Ljava/lang/Object;",
    "java/util/Map->keySet()Ljava/util/Set;",
    "java/util/Map->values()Ljava/util/Collection;",
    "java/util/Map->entrySet()Ljava/util/Set;",
)


def java_string_hashcode(value: str) -> int:
    """Unsigned 32-bit Java String.hashCode over UTF-16 code units."""
    raw = value.encode("utf-16-be", errors="surrogatepass")
    result = 0
    for (unit,) in struct.iter_unpack(">H", raw):
        result = (31 * result + unit) & 0xFFFFFFFF
    return result


def x137_from_method_ids(method_ids: Sequence[int]) -> str:
    """Hash the seven captured 32-bit JNI IDs in Map descriptor order."""
    if len(method_ids) != 7:
        raise ValueError("x137 requires exactly seven method IDs")
    if any(type(v) is not int or not 0 <= v <= 0xFFFFFFFF for v in method_ids):
        raise ValueError("method IDs must be unsigned 32-bit integers")
    return hashlib.md5(struct.pack("<7I", *method_ids)).hexdigest().upper()


def tinyprobe_x137() -> str:
    """Reproduce the unidbg oracle IDs; NOT an Android ART ID allocator."""
    return x137_from_method_ids(
        [java_string_hashcode(v) for v in MAP_METHOD_DESCRIPTORS]
    )


def tinyprobe_attestation_bytes(device_id: str) -> bytes:
    """TinyProbe HwAttestationManager.getDeviceID simulation; not a TEE formula."""
    return hashlib.sha256(device_id.encode("utf-8")).digest()


def x147_from_attestation_bytes(value: bytes) -> str:
    """Observed snapshot UTF-8 conversion, with replacement of invalid bytes."""
    return value.decode("utf-8", errors="replace")


def x86_from_input(data: bytes, *, seed: int) -> str:
    """Proven hash composition; caller supplies observed/environment-derived seed.

    All input lengths are supported; no fallback to a native extension.
    """
    import zlib

    from .crypto.xxh3 import xxh3_128

    return f"{xxh3_128(data, seed):032X}{zlib.crc32(data):08X}"


# Observed immediate at libtiny 9.43.1 +0x2e02bc/+0x2e02c4;
# dynamically confirmed at the store +0x2e03e4.
REGISTER_X86_SEED = 0x48FA5412

# Order read by +0x320c40; successful entries use one-based decimal keys.
X165_PATHS = (
    "/data/system",
    "/data/data/",
    "/data/data/com.android.shell",
    "/data/system/install_sessions",
    "/data/data/com.google.android.webview",
    "/data/data/com.google.android.gms",
    "/dev/__properties__/u:object_r:radio_prop:s0",
    "/dev/__properties__/u:object_r:ffs_prop:s0",
    "/dev/__properties__/u:object_r:debuggerd_prop:s0",
    "/dev/fd/1",
    "/dev/fd/0",
    "/sdcard",
)


@dataclass(frozen=True)
class StatFacts:
    """AArch64 lstat facts; none of these is inferred from a device ID."""

    ctime_seconds: int
    ctime_nanoseconds: int
    inode: int
    device: int


@dataclass(frozen=True)
class StatFsFacts:
    """Filesystem facts, with fsid split into two little-endian u32 words."""

    fsid_low: int
    fsid_high: int
    fs_type: int


def filesystem_snapshot_value(
    stat: StatFacts | None, fs: StatFsFacts | None
) -> str | None:
    """Compose native %ld%09ld-%lu-%lu-%s-%lu or omit on both failures.

    Inputs represent actual/declared environment facts, not host-Windows stat.
    Python intentionally does not query unrelated local paths.
    """
    if stat is None and fs is None:
        return None
    sec, ns, ino, dev = (
        (0, 0, 0, 0)
        if stat is None
        else (stat.ctime_seconds, stat.ctime_nanoseconds, stat.inode, stat.device)
    )

    def signed64(x: int) -> int:
        x &= (1 << 64) - 1
        return x - (1 << 64) if x >= 1 << 63 else x

    fsid = (
        "*"
        if fs is None
        else f"{fs.fsid_low & 0xFFFFFFFF:08x}{fs.fsid_high & 0xFFFFFFFF:08x}"
    )
    ftype = 0 if fs is None else fs.fs_type & ((1 << 64) - 1)
    return f"{signed64(sec)}{signed64(ns):09d}-{ino & ((1 << 64) - 1)}-{dev & ((1 << 64) - 1)}-{fsid}-{ftype}"


def build_x165(
    facts: Mapping[str, tuple[StatFacts | None, StatFsFacts | None]],
) -> dict[str, str] | None:
    """Build the ordered-path map, omitting paths where both queries failed.

    Require an explicit success/failure record for every path, rather than
    silently interpreting missing input as query failure.
    """
    missing = set(X165_PATHS) - facts.keys()
    if missing:
        raise ValueError(f"missing filesystem facts for {sorted(missing)}")
    result = {}
    for index, path in enumerate(X165_PATHS, 1):
        value = filesystem_snapshot_value(*facts[path])
        if value is not None:
            result[str(index)] = value
    return result or None


def register_x86_from_parts(parts: Sequence[bytes]) -> str:
    """Hash ordered native collector chunks with the proven version seed.

    Baseline parts are android_id, OAID, attestation bytes, resolution. Other
    collector successes insert further chunks at source-defined positions;
    callers must retain the observed order (SoC serial precedes resolution).
    """
    return x86_from_input(b"".join(parts), seed=REGISTER_X86_SEED)


SYS_IDENTITY_MAX_BYTES = 200
BOOT_ID_MAX_CHARS = 35
PUBLIC_SOURCE_SDK = 28


def normalize_sys_identity_bytes(
    raw: bytes | str, *, max_len: int = SYS_IDENTITY_MAX_BYTES
) -> bytes:
    """Strip surrounding ASCII whitespace and cap length; observed on soc/mmc/ufs."""
    data = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    return data.strip()[:max_len]


def normalize_boot_id(raw: bytes | str, *, max_chars: int = BOOT_ID_MAX_CHARS) -> str:
    """Drop trailing newlines and keep the first 35 characters; exact reader still open."""
    text = (
        raw.decode("utf-8", errors="replace")
        if isinstance(raw, (bytes, bytearray))
        else str(raw)
    )
    return text.rstrip("\r\n")[:max_chars]


def sdcard_only_filesystem_facts(
    stat: StatFacts | None,
    fs: StatFsFacts | None,
) -> dict[str, tuple[StatFacts | None, StatFsFacts | None]]:
    """Declare all 12 x165 paths, with only /sdcard succeeding."""
    facts = {path: (None, None) for path in X165_PATHS}
    facts["/sdcard"] = (stat, fs)
    return facts


def _optional_sys_identity(raw: bytes | str | None) -> bytes | None:
    if raw is None:
        return None
    value = normalize_sys_identity_bytes(raw)
    return value or None


@dataclass(frozen=True)
class RegisterSnapshotInputs:
    """Declared device/environment facts for one register snapshot.

    Optional file bodies are raw collector inputs; the builder applies the
    proven normalizers. Unresolved collectors stay omitted unless supplied.
    """

    android_id: str
    oaid: str
    attestation_bytes: bytes
    resolution: str
    manufacturer: str
    cpu_abilist: str
    product_name: str
    boot_hardware: str
    source_dir: str
    sdk_int: int
    total_storage: int
    free_storage: int
    filesystem_facts: Mapping[str, tuple[StatFacts | None, StatFsFacts | None]]
    method_ids: Sequence[int]
    missing_file_error: str | None
    id_provider: str
    public_source_dir: str | None = None
    x148: str = ""
    soc_serial: bytes | str | None = None
    mmc_cid: bytes | str | None = None
    ufs_id: bytes | str | None = None
    boot_id: bytes | str | None = None
    cpuinfo_x76: int | None = None
    include_xiaomi_oaid_copies: bool = True
    include_attestation: bool = True
    settings: Mapping[str, str] | None = None
    kernel_uuid: bytes | str | None = None


SETTINGS_FIELD_MAP = (
    ("gcbooster_uuid", "x133", True),
    ("uuid", "x158", False),
    ("pps_oaid", "x169", True),
    ("bluetooth_address", "x63", False),
    ("key_mqs_uuid", "x132", False),
    ("ad_aaid", "x134", False),
    ("mi_health_id", "x157", False),
    ("persist.sys.oppo.opmuuid", "x159", False),
    ("com.vivo.pushservice.client_id", "x160", False),
    ("iRoamingKey", "x161", False),
    ("com.vivo.pushservice.back_up", "x162", False),
    ("ZHVzY2Lk", "x163", False),
)


def x170_from_oem_classes(*, xiaomi_id_provider: bool) -> str:
    """Observed 9.43.1 labels: IdProviderImpl present -> id_provider, else vivo.

    Hiding additional vivo/oppo class-name tokens did not change the fallback
    in this oracle. Do not treat this as a complete OEM table.
    """
    return "id_provider" if xiaomi_id_provider else "vivo"


def x86_parts_from_inputs(inputs: RegisterSnapshotInputs) -> list[bytes]:
    """Native XXH update order: identity, optional SoC serial, resolution, mmc, ufs."""
    soc = _optional_sys_identity(inputs.soc_serial)
    mmc = _optional_sys_identity(inputs.mmc_cid)
    ufs = _optional_sys_identity(inputs.ufs_id)
    parts = [inputs.android_id.encode("ascii")]
    settings = dict(inputs.settings or {})
    hashed_settings = []
    for key, _field, hashed in SETTINGS_FIELD_MAP:
        if hashed and key in settings and settings[key] != "":
            hashed_settings.append(normalize_sys_identity_bytes(settings[key]))
    parts.extend(hashed_settings)
    if inputs.include_xiaomi_oaid_copies:
        parts.append(inputs.oaid.encode("ascii"))
    if inputs.include_attestation:
        parts.append(bytes(inputs.attestation_bytes))
    if soc is not None:
        parts.append(soc)
    parts.append(inputs.resolution.encode("ascii"))
    if mmc is not None:
        parts.append(mmc)
    if ufs is not None:
        parts.append(ufs)
    return parts


def build_register_snapshot(inputs: RegisterSnapshotInputs) -> dict[str, object]:
    """Assemble a conditional-schema snapshot from declared facts.

    24 keys are the TinyProbe SDK>=28 baseline, not a fixed schema. x252/x253
    appear at SDK 28+. Optional sys/proc collectors add keys only when present.
    """
    if not isinstance(inputs.attestation_bytes, (bytes, bytearray)):
        raise TypeError("attestation_bytes must be bytes")
    public_source_dir = (
        inputs.source_dir
        if inputs.public_source_dir is None
        else inputs.public_source_dir
    )
    soc = _optional_sys_identity(inputs.soc_serial)
    mmc = _optional_sys_identity(inputs.mmc_cid)
    ufs = _optional_sys_identity(inputs.ufs_id)
    snapshot: dict[str, object] = {
        "x137": x137_from_method_ids(inputs.method_ids),
        "x165": build_x165(inputs.filesystem_facts),
        "x170": inputs.id_provider,
        "x171": inputs.oaid,
        "x20": inputs.manufacturer,
        "x21": inputs.cpu_abilist,
        "x231": inputs.total_storage,
        "x232": inputs.total_storage,
        "x236": inputs.free_storage,
        "x237": inputs.free_storage,
        "x25": inputs.product_name,
        "x46": inputs.android_id,
        "x49": inputs.source_dir,
        "x77": inputs.boot_hardware,
        "x86": register_x86_from_parts(x86_parts_from_inputs(inputs)),
    }
    if inputs.missing_file_error is not None:
        snapshot["x128"] = inputs.missing_file_error
    if inputs.include_attestation:
        snapshot["x147"] = x147_from_attestation_bytes(bytes(inputs.attestation_bytes))
        snapshot["x148"] = inputs.x148
    if inputs.include_xiaomi_oaid_copies:
        snapshot["x173"] = inputs.oaid
        snapshot["x174"] = inputs.oaid
        snapshot["x175"] = inputs.oaid
        snapshot["x181"] = inputs.oaid
    for key, field, _hashed in SETTINGS_FIELD_MAP:
        settings = dict(inputs.settings or {})
        if key in settings and settings[key] != "":
            snapshot[field] = normalize_sys_identity_bytes(settings[key]).decode(
                "utf-8", errors="replace"
            )
    if inputs.kernel_uuid is not None:
        snapshot["x110"] = normalize_boot_id(inputs.kernel_uuid)
    if soc is not None:
        snapshot["x149"] = soc.decode("utf-8", errors="replace")
    if mmc is not None:
        snapshot["x151"] = mmc.decode("utf-8", errors="replace")
    if ufs is not None:
        snapshot["x153"] = ufs.decode("utf-8", errors="replace")
    if inputs.sdk_int >= PUBLIC_SOURCE_SDK:
        snapshot["x252"] = public_source_dir
        snapshot["x253"] = public_source_dir
    if inputs.boot_id is not None:
        snapshot["x47"] = normalize_boot_id(inputs.boot_id)
    if inputs.cpuinfo_x76 is not None:
        snapshot["x76"] = inputs.cpuinfo_x76
    return snapshot


def device_id_from_android_id(android_id: str) -> str:
    """Same MD5-name UUID the client uses; not a register-snapshot hash."""
    digest = bytearray(hashlib.md5(android_id.encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x3F) | 0x30
    digest[8] = (digest[8] & 0xBF) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def _seed_bytes(seed: bytes, label: str, length: int) -> bytes:
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


def _b64url_token(seed: bytes, label: str, length: int = 22) -> str:
    raw = (
        base64.urlsafe_b64encode(_seed_bytes(seed, label, 16))
        .decode("ascii")
        .rstrip("=")
    )
    return raw[:length]


def _seed_hex(seed: bytes, label: str, chars: int) -> str:
    return _seed_bytes(seed, label, (chars + 1) // 2).hex()[:chars]


def _uuid_text(seed: bytes, label: str) -> str:
    return str(uuid.UUID(bytes=_seed_bytes(seed, label, 16)))


def _apk_source_dir(seed: bytes) -> str:
    return (
        f"/data/app/~~{_b64url_token(seed, 'apk-dir')}/"
        f"com.xingin.xhs-{_b64url_token(seed, 'apk-id')}"
    )


def _synthetic_sdcard_facts(
    seed: bytes,
) -> dict[str, tuple[StatFacts | None, StatFsFacts | None]]:
    """Seeded /sdcard lstat+statfs only. Never reads the host filesystem."""
    return sdcard_only_filesystem_facts(
        StatFacts(
            ctime_seconds=_seed_int(
                seed, "sdcard-ctime-sec", 1_600_000_000, 1_900_000_000
            ),
            ctime_nanoseconds=_seed_int(seed, "sdcard-ctime-ns", 0, 999_999_999),
            inode=_seed_int(seed, "sdcard-ino", 1, 1_000_000),
            device=_seed_int(seed, "sdcard-dev", 1, 0xFFFF),
        ),
        StatFsFacts(
            fsid_low=int.from_bytes(_seed_bytes(seed, "sdcard-fsid-lo", 4), "little"),
            fsid_high=int.from_bytes(_seed_bytes(seed, "sdcard-fsid-hi", 4), "little"),
            fs_type=61267,
        ),
    )


@dataclass(frozen=True)
class SyntheticRegisterDevice:
    """A seed-derived identity plus the snapshot built from it.

    `policy` records emulator-only substitutions so they cannot be mistaken
    for recovered ART/TEE formulas.
    """

    seed: str
    android_id: str
    oaid: str
    device_id: str
    inputs: RegisterSnapshotInputs
    snapshot: dict[str, object]
    policy: Mapping[str, str]


def synthesize_register_device(
    seed: str | bytes | None = None,
    *,
    preset: str | object = "xiaomi-mi6-lineage-15",
    optional_files: str = "android",
    jni_id_policy: str = "tinyprobe",
    attestation_policy: str = "tinyprobe",
    omit_x128: bool = False,
    android_id: str | None = None,
    oaid: str | None = None,
    device_id: str | None = None,
) -> SyntheticRegisterDevice:
    """Build one synthetic phone identity and its register snapshot.

    This does not read host files. Hardware/build fields come from the MUA
    preset. JNI method IDs and attestation bytes follow named policies.
    `optional_files='baseline'` omits SoC/boot collectors (TinyProbe empty
    tree). `optional_files='android'` adds typical Android boot_id and
    Qualcomm SoC serial, still generated from the seed.
    """
    if optional_files not in ("android", "baseline"):
        raise ValueError("optional_files must be 'android' or 'baseline'")
    if jni_id_policy != "tinyprobe":
        raise ValueError("only tinyprobe JNI ID policy is implemented")
    if attestation_policy != "tinyprobe":
        raise ValueError("only tinyprobe attestation policy is implemented")
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
    seed_bytes = seed_text.encode("utf-8")
    try:
        from .device_info import get_device_preset
    except ImportError:
        from device_info import get_device_preset
    device_preset = get_device_preset(preset)
    android = (
        _seed_hex(seed_bytes, "android-id", 16) if android_id is None else android_id
    )
    oaid_value = _seed_hex(seed_bytes, "oaid", 32) if oaid is None else oaid
    device = device_id_from_android_id(android) if device_id is None else device_id
    total_storage = device_preset.total_storage_bytes
    free_storage = (
        total_storage * _seed_int(seed_bytes, "free-storage-percent", 31, 72) // 100
    )
    soc_serial = None
    boot_id = None
    if optional_files == "android":
        soc_serial = _seed_hex(seed_bytes, "soc-serial", 16)
        boot_id = _uuid_text(seed_bytes, "boot-id")
    xiaomi_provider = device_preset.manufacturer == "Xiaomi"
    inputs = RegisterSnapshotInputs(
        android_id=android,
        oaid=oaid_value,
        attestation_bytes=tinyprobe_attestation_bytes(device),
        resolution=device_preset.display,
        manufacturer=device_preset.manufacturer,
        cpu_abilist=device_preset.supported_abis,
        product_name=device_preset.product,
        boot_hardware=device_preset.hardware,
        source_dir=_apk_source_dir(seed_bytes),
        sdk_int=device_preset.sdk_int,
        total_storage=total_storage,
        free_storage=free_storage,
        filesystem_facts=_synthetic_sdcard_facts(seed_bytes),
        method_ids=[java_string_hashcode(v) for v in MAP_METHOD_DESCRIPTORS],
        missing_file_error=None if omit_x128 else "No such file or directory",
        id_provider=x170_from_oem_classes(xiaomi_id_provider=xiaomi_provider),
        x148="",
        soc_serial=soc_serial,
        boot_id=boot_id,
        include_xiaomi_oaid_copies=xiaomi_provider,
        include_attestation=True,
    )
    return SyntheticRegisterDevice(
        seed=seed_text,
        android_id=android,
        oaid=oaid_value,
        device_id=device,
        inputs=inputs,
        snapshot=build_register_snapshot(inputs),
        policy={
            "jni_id_policy": jni_id_policy,
            "attestation_policy": attestation_policy,
            "optional_files": optional_files,
            "preset": device_preset.name,
            "x170": "xiaomi-idprovider" if xiaomi_provider else "fallback-vivo",
            "x128": "omit-if-getprop-and-sh"
            if omit_x128
            else "strerror-if-getprop-or-sh-missing",
            "x148": "empty-when-attestation-present",
        },
    )


def _cli() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Synthesize one register snapshot from a seed; never reads host files.",
    )
    parser.add_argument(
        "--seed", default="", help="stable identity seed; random when omitted"
    )
    parser.add_argument("--preset", default="xiaomi-mi6-lineage-15")
    parser.add_argument(
        "--optional-files", choices=("android", "baseline"), default="android"
    )
    parser.add_argument("--android-id")
    parser.add_argument("--oaid")
    parser.add_argument("--device-id")
    args = parser.parse_args()
    device = synthesize_register_device(
        args.seed or None,
        preset=args.preset,
        optional_files=args.optional_files,
        android_id=args.android_id,
        oaid=args.oaid,
        device_id=args.device_id,
    )
    print(
        json.dumps(
            {
                "seed": device.seed,
                "android_id": device.android_id,
                "oaid": device.oaid,
                "device_id": device.device_id,
                "policy": dict(device.policy),
                "snapshot": device.snapshot,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
