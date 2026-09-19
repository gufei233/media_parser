"""Android 9.43.1 x-mini-mua 的离线生成及会话材料初始化。

native 读取 /dev/urandom 的 64B 作为 s，再读 32B 作为 X25519 原始
scalar；曲线计算时执行标准 clamp。给定相同熵输入及设备画像，可逐字节
重建 MUA。零输入 95 字段画像由 device_info.DeviceInfoGenerator 提供；
S1 由同目录 `xhs_s1` 纯 Python 生成。register 字段 ``d`` 与 MUA Part2
共用 zlib/AES-CBC/Base64URL 外层；明文可由 `register_snapshot.build_register_snapshot`
从声明事实重建，signer 仍要求注入 `register_profile`。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import time
import zlib
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .ssk import (
    generate_keypair as _generate_x25519_keypair,
)
from .ssk import (
    public_key as _x25519_public_key,
)
from .ssk import (
    x25519 as _x25519,
)


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    value = value.strip()
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def decode_part1(mua: str) -> OrderedDict[str, object]:
    """解码 MUA 第一段，并保留原始字段顺序。"""
    first = mua.split(".", 1)[0]
    raw = b64url_decode(first)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=OrderedDict)
    if not isinstance(value, OrderedDict):
        raise ValueError("MUA Part1 必须是 JSON object")
    return value


def encode_part1(profile: Mapping[str, object]) -> str:
    """按传入顺序编码 Part1；不自动排序，避免改变签名输入。"""
    raw = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
    return b64url_encode(raw.encode("utf-8"))


def compose_mua(part1: Mapping[str, object] | str, part2: str) -> str:
    first = part1 if isinstance(part1, str) else encode_part1(part1)
    return f"{first}.{part2}."


def build_part1(
    *,
    counter: int,
    public_key_hex: str,
    random_material_hex: str,
    app_id: str = "ECFAAF01",
    platform: str = "a",
    state: Mapping[str, object] | None = None,
    uid: str | None = None,
    version: str = "2.9.99",
) -> OrderedDict[str, object]:
    """构造真机观察到的 Part1 字段顺序。"""
    if len(public_key_hex) != 64 or len(random_material_hex) != 128:
        raise ValueError("k 必须 32B hex，s 必须 64B hex")
    profile: OrderedDict[str, object] = OrderedDict()
    profile["a"] = app_id
    profile["c"] = int(counter)
    profile["k"] = public_key_hex.lower()
    profile["p"] = platform
    profile["s"] = random_material_hex.lower()
    profile["t"] = OrderedDict(
        state
        or {
            "c": 0,
            "d": 0,
            "f": 0,
            "s": 4098,
            "t": 0,
            "tt": [],
        }
    )
    if uid is not None:
        profile["u"] = uid
    profile["v"] = version
    return profile


def canonical_device_info(profile: Mapping[str, object]) -> bytes:
    """将 x0~x305 设备画像按 native 的紧凑 JSON 形式序列化。

    Frida 样本显示字段按字符串键排序（x0, x1, x10, ...），因此这里
    使用 Python 的字典排序来复现该顺序；调用方应先把动态字段写入 profile。
    """
    ordered = OrderedDict((str(k), profile[k]) for k in sorted(profile, key=str))
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def compress_device_info(profile: Mapping[str, object], *, raw: bool = False) -> bytes:
    payload = canonical_device_info(profile)
    if raw:
        compressor = zlib.compressobj(level=6, wbits=-zlib.MAX_WBITS)
        return compressor.compress(payload) + compressor.flush()
    return zlib.compress(payload, level=6)


def _pkcs7(data: bytes, block_size: int = 16) -> bytes:
    pad = block_size - (len(data) % block_size)
    return data + bytes([pad]) * pad


def aes_cbc_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    if len(key) not in (16, 24, 32) or len(iv) != 16:
        raise ValueError("AES-CBC key 必须为 16/24/32B，IV 必须 16B")
    padded = _pkcs7(data, 16)
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()
    except Exception:
        from Crypto.Cipher import AES

        return AES.new(key, AES.MODE_CBC, iv).encrypt(padded)


def aes_cbc_decrypt(data: bytes, key: bytes, iv: bytes, *, unpad: bool = True) -> bytes:
    """解密 Part2 校准样本；默认校验并移除 PKCS#7。"""
    if len(data) == 0 or len(data) % 16:
        raise ValueError("AES-CBC 密文长度必须为正的 16B 倍数")
    if len(key) not in (16, 24, 32) or len(iv) != 16:
        raise ValueError("AES-CBC key 必须为 16/24/32B，IV 必须 16B")
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        plain = decryptor.update(data) + decryptor.finalize()
    except Exception:
        from Crypto.Cipher import AES

        plain = AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    if not unpad:
        return plain
    pad = plain[-1]
    if pad < 1 or pad > 16 or plain[-pad:] != bytes([pad]) * pad:
        raise ValueError("AES-CBC 明文不是有效 PKCS#7")
    return plain[:-pad]


def decrypt_part2(
    part2: str, *, key: bytes, iv: bytes, raw_deflate: bool = False
) -> dict[str, object]:
    """解密一段 native MUA Part2，返回原始压缩流和画像 JSON。

    该函数只用于校准/证据验证，不会在缺少 key/iv 时猜测或回退。
    """
    cipher = b64url_decode(part2)
    compressed = aes_cbc_decrypt(cipher, key, iv)
    if raw_deflate:
        raw = zlib.decompress(compressed, wbits=-zlib.MAX_WBITS)
    else:
        raw = zlib.decompress(compressed)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=OrderedDict)
    if not isinstance(value, Mapping):
        raise ValueError("Part2 明文不是 JSON object")
    return {"ciphertext": cipher, "compressed": compressed, "json": value, "raw": raw}


def encrypt_part2(
    profile: Mapping[str, object], *, key: bytes, iv: bytes, raw_deflate: bool = False
) -> str:
    """完成已确认的 Part2 外层：JSON -> zlib -> PKCS7 -> AES-CBC -> Base64URL。"""
    compressed = compress_device_info(profile, raw=raw_deflate)
    return b64url_encode(aes_cbc_encrypt(compressed, key, iv))


def encrypt_register_d(
    profile: Mapping[str, object],
    *,
    key: bytes,
    iv: bytes,
    raw_deflate: bool = False,
) -> str:
    """Encode register JSON field ``d``.

    Native 9.43.1 empty-identity register uses the same outer transform as MUA
    Part2: canonical JSON -> zlib(level=6) -> PKCS#7 -> AES-CBC(slotA[:16],
    slotA[16:]) -> Base64URL. ``d`` is not a second Part2 string; it encrypts a
    distinct 24-field snapshot. This function does not invent those fields.
    """
    return encrypt_part2(profile, key=key, iv=iv, raw_deflate=raw_deflate)


def build_register_android_body(
    profile: Mapping[str, object],
    *,
    counter: int,
    public_key_hex: str,
    random_material_hex: str,
    key: bytes,
    iv: bytes,
    device_id: str = "",
    rr: str = "0",
    sid: str = "",
    uid: str = "",
    app_id: str = "ECFAAF01",
    platform: str = "a",
    version: str = "2.9.99",
    raw_deflate: bool = False,
) -> bytes:
    """Assemble the compact register/android JSON body, including field ``d``.

    Field order matches native: ``a,c,d,e,k,p,s,v``. ``e`` is the plain map
    ``{device_id, rr, sid, uid}``; first-launch captured samples use empty
    identity strings and ``rr="0"``.
    """
    if len(public_key_hex) != 64 or len(random_material_hex) != 128:
        raise ValueError("k 必须 32B hex，s 必须 64B hex")
    body: OrderedDict[str, object] = OrderedDict()
    body["a"] = app_id
    body["c"] = int(counter)
    body["d"] = encrypt_register_d(profile, key=key, iv=iv, raw_deflate=raw_deflate)
    body["e"] = OrderedDict(
        (
            ("device_id", device_id),
            ("rr", str(rr)),
            ("sid", sid),
            ("uid", uid),
        )
    )
    body["k"] = public_key_hex.lower()
    body["p"] = platform
    body["s"] = random_material_hex.lower()
    body["v"] = version
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fe51_to_bytes(limbs: bytes | tuple[int, int, int, int, int]) -> bytes:
    """Serialize libtiny's five 51-bit limbs like its native FE_TOBYTES.

    Native evidence: +0x5f1980 consumes five little-endian uint64 limbs and
    writes the canonical 32-byte little-endian field element.  Inputs may be
    temporarily unreduced; the final reduction is modulo 2**255-19.
    """
    if isinstance(limbs, (bytes, bytearray, memoryview)):
        raw = bytes(limbs)
        if len(raw) < 40:
            raise ValueError("FE limbs buffer must contain at least 40 bytes")
        h = struct.unpack("<5Q", raw[:40])
    else:
        if len(limbs) != 5:
            raise ValueError("FE limbs must contain five values")
        h = tuple(int(x) for x in limbs)
    # The native routine normalizes the 51-bit radix representation.  Modulo
    # reduction is equivalent for arbitrary carry-bearing intermediate limbs.
    value = sum(int(x) << (51 * i) for i, x in enumerate(h))
    return (value % (2**255 - 19)).to_bytes(32, "little")


def derive_mua_material_from_fe(limbs: bytes | tuple[int, int, int, int, int]) -> bytes:
    """Return the exact 32-byte MUA material emitted from FE limbs.

    This is the confirmed native boundary used for both ``k`` and ``slotA``;
    the caller still needs to obtain the corresponding native five-limb input.
    """
    return fe51_to_bytes(limbs)


# MUA-specific Curve25519 peer recovered from two independent native
# sessions. This is distinct from the SSK bootstrap public key.
MUA_SERVER_PUBLIC_KEY = bytes.fromhex(
    "c9b68adc9a607bd108c7b7bba0dd6b5a9eba716fb6027b52c861b838cae63637"
)


def derive_mua_session_material(
    private_key: bytes,
    *,
    server_public_key: bytes = MUA_SERVER_PUBLIC_KEY,
) -> dict[str, bytes]:
    """Derive the native MUA ``k`` and Part2 AES material from one scalar.

    Native evidence across two sessions shows:
      k      = X25519(private_key, basepoint9)
      slotA  = X25519(private_key, MUA_SERVER_PUBLIC_KEY)
      AES key/iv = slotA[:16]/slotA[16:]
    ``s`` is a separate 64-byte session field and is intentionally not
    synthesized here.
    """
    if len(private_key) != 32 or len(server_public_key) != 32:
        raise ValueError("MUA Curve25519 inputs must be 32 bytes")
    k = x25519_public(private_key)
    slot_a = derive_shared(private_key, server_public_key)
    return {"private_key": bytes(private_key), "k": k, "slotA": slot_a}


def _read_session_entropy(random_bytes: Callable[[int], bytes], length: int) -> bytes:
    value = random_bytes(length)
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("session entropy source must return bytes")
    value = bytes(value)
    if len(value) != length:
        raise ValueError(
            f"session entropy source returned {len(value)} bytes, expected {length}"
        )
    return value


def generate_mua_session_material(
    *,
    random_bytes: Callable[[int], bytes] | None = None,
    server_public_key: bytes = MUA_SERVER_PUBLIC_KEY,
) -> dict[str, bytes]:
    """Initialize one MUA material epoch, without native code or captured secrets.

    Observed native order: entropy(64) -> s; entropy(32) -> raw private_key.
    The scalar is clamped by X25519, not by the entropy reader; retaining raw
    bytes makes the native input reproducible. Returns private_key, s, k, slotA.
    The default uses OS entropy. Injectable entropy is for deterministic tests.

    INIT_LITE and CONFIG each create an epoch in the current probe; CONFIG's
    epoch is used by subsequent sign calls. Do not regenerate per request.
    The separate CONFIG 4-byte read is not an input to this material derivation.
    """
    source = os.urandom if random_bytes is None else random_bytes
    s = _read_session_entropy(source, 64)
    private = _read_session_entropy(source, 32)
    return {
        **derive_mua_session_material(private, server_public_key=server_public_key),
        "s": s,
    }


def build_mua_from_session_material(
    profile: Mapping[str, object],
    *,
    private_key: bytes,
    random_material: bytes,
    counter: int = 1,
    app_id: str = "ECFAAF01",
    platform: str = "a",
    version: str = "2.9.99",
    state: Mapping[str, object] | None = None,
    oaid: str | None = None,
    raw_deflate: bool = False,
) -> str:
    """Build native-shaped MUA from an explicit session scalar and 64B s."""
    if len(random_material) != 64:
        raise ValueError("random_material must be 64 bytes")
    material = derive_mua_session_material(private_key)
    part1: OrderedDict[str, object] = OrderedDict(
        [
            ("a", app_id),
            ("c", int(counter)),
            ("k", material["k"].hex()),
            ("p", platform),
            ("s", bytes(random_material).hex()),
        ]
    )
    if state is not None:
        part1["t"] = OrderedDict(state)
    if oaid:
        part1["u"] = "00000000" + oaid
    part1["v"] = version
    part2 = encrypt_part2(
        profile,
        key=material["slotA"][:16],
        iv=material["slotA"][16:],
        raw_deflate=raw_deflate,
    )
    return compose_mua(part1, part2)


def x25519_keypair() -> tuple[bytes, bytes]:
    """生成 MUA 会话级 X25519 私钥、公钥；公钥返回原始 32 字节。"""
    return _generate_x25519_keypair()


def x25519_public(private_key: bytes) -> bytes:
    """计算 X25519 公钥，供 Part1 的 k 字段使用。"""
    return _x25519_public_key(private_key)


def derive_shared(private_key: bytes, server_public_key: bytes) -> bytes:
    """计算 MUA 的 X25519 shared；不接受全零结果。"""
    return _x25519(private_key, server_public_key, all_zero_check=True)


def derive_key_iv(
    shared: bytes,
    *,
    key_mode: str = "prefix16",
    iv_mode: str = "suffix16",
) -> tuple[bytes, bytes]:
    """把 32B shared 拆成 AES key/IV。

    prefix16/suffix16 已通过 native AES-CBC 样本逐字节验证：shared[0:16] 为 key，shared[16:32] 为 IV。
    另提供 sha256 标签模式，便于把不同样本的拆分规则做成可重复实验，而不是
    在生产代码里散落硬编码。
    """
    if len(shared) != 32:
        raise ValueError("X25519 shared 必须为 32B")

    def material(mode: str, *, label: bytes = b"") -> bytes:
        if mode == "prefix16":
            return shared[:16]
        if mode == "suffix16":
            return shared[16:]
        if mode == "sha256":
            return hashlib.sha256(shared).digest()[:16]
        if mode == "sha256-label":
            return hashlib.sha256(shared + label).digest()[:16]
        if mode in ("direct", "direct16"):
            # 显式校准模式：shared 参数实际是 native 捕获的 key||iv。
            # 保留该模式是为了逐字节对照，不把捕获值伪装成可推导规则。
            return shared[:16] if label == b"key" else shared[16:]
        raise ValueError(f"未知 shared 派生模式: {mode}")

    key = material(key_mode, label=b"key")
    iv = material(iv_mode, label=b"iv")
    return key, iv


def encrypt_part2_from_shared(
    profile: Mapping[str, object],
    *,
    shared: bytes,
    key_mode: str = "prefix16",
    iv_mode: str = "suffix16",
    raw_deflate: bool = False,
) -> str:
    """使用已获得的 shared 生成 Part2；用于 native 对照和纯 Python 回归。"""
    key, iv = derive_key_iv(shared, key_mode=key_mode, iv_mode=iv_mode)
    return encrypt_part2(profile, key=key, iv=iv, raw_deflate=raw_deflate)


@dataclass
class PurePythonMuaSigner:
    """纯 Python MUA/SIG 生成器。

    该类只负责已经可验证的密码和编码链路。server_public_key、设备画像和
    shared 拆分规则由调用方注入；缺少这些输入时显式失败，绝不静默退回 unidbg。
    x25519_private_key 在 signer 生命周期内复用，以匹配真机进程级 k/s。
    """

    server_public_key: bytes | None
    device_info: (
        Mapping[str, object] | Callable[[Mapping[str, object]], Mapping[str, object]]
    )
    gid: str = ""
    app_id: str = "ECFAAF01"
    platform: str = "a"
    version: str = "2.9.99"
    key_mode: str = "prefix16"
    iv_mode: str = "suffix16"
    raw_deflate: bool = False
    x25519_private_key: bytes | None = None
    random_material: bytes | None = None
    default_mua_state: Mapping[str, object] | None = None
    # 校准模式：当 native 已经捕获 shared 但服务端公钥尚未定位时，直接注入
    # 32B shared（或按请求上下文返回 shared），用于逐字节验证 Part2。
    shared_secret: bytes | Callable[[Mapping[str, object]], bytes] | None = None
    # 校准模式：native 已直接捕获 AES key||iv 时注入 32B；生产上优先使用
    # server_public_key/shared_secret，避免把一次性材料误当成长期密钥。
    key_iv: bytes | Callable[[Mapping[str, object]], bytes] | None = None
    # First-launch register ``d`` encrypts a conditional snapshot with the same
    # AES material as MUA Part2. Packing is restored. `build_register_snapshot`
    # can fill the JSON from declared facts; this signer still requires an
    # injected register_profile and will not invent missing collectors.
    register_profile: (
        Mapping[str, object]
        | Callable[[Mapping[str, object]], Mapping[str, object]]
        | None
    ) = None
    register_identity: Mapping[str, object] | None = None

    # 注册 HTTP 与 GID 解析已在 XhsAppClient.register_gid。没有注入
    # register_profile 时仍不能发注册。
    supports_register: bool = False

    def __post_init__(self) -> None:
        if self.server_public_key is not None and len(self.server_public_key) != 32:
            raise ValueError("server_public_key 必须为 32B")
        if (
            self.server_public_key is None
            and self.shared_secret is None
            and self.key_iv is None
        ):
            raise ValueError("必须提供 server_public_key、shared_secret 或 key_iv")
        if (
            isinstance(self.shared_secret, (bytes, bytearray))
            and len(self.shared_secret) != 32
        ):
            raise ValueError("shared_secret 必须为 32B")
        if isinstance(self.key_iv, (bytes, bytearray)) and len(self.key_iv) != 32:
            raise ValueError("key_iv 必须为 32B（16B key + 16B iv）")
        if self.key_iv is not None and self.shared_secret is not None:
            raise ValueError("key_iv 与 shared_secret 只能二选一")
        if self.key_iv is not None and self.server_public_key is not None:
            raise ValueError("key_iv 校准模式不能同时指定 server_public_key")
        if self.x25519_private_key is not None and len(self.x25519_private_key) != 32:
            raise ValueError("x25519_private_key 必须为 32B")
        if self.random_material is not None and len(self.random_material) != 64:
            raise ValueError("random_material 必须为 64B")
        # Match the native entropy order. Preserve raw scalar bytes; X25519 clamps.
        if self.random_material is None:
            self.random_material = _read_session_entropy(os.urandom, 64)
        if self.x25519_private_key is None:
            self.x25519_private_key = _read_session_entropy(os.urandom, 32)
        if self.register_profile is not None:
            self.supports_register = True

    def _aes_key_iv(self, context: Mapping[str, object]) -> tuple[bytes, bytes]:
        if self.key_iv is not None:
            key_iv = (
                self.key_iv(context) if callable(self.key_iv) else bytes(self.key_iv)
            )
            if len(key_iv) != 32:
                raise ValueError("key_iv builder 必须返回 32B")
            return key_iv[:16], key_iv[16:]
        if self.shared_secret is not None:
            shared = (
                self.shared_secret(context)
                if callable(self.shared_secret)
                else bytes(self.shared_secret)
            )
            if len(shared) != 32:
                raise ValueError("shared_secret builder 必须返回 32B")
            return derive_key_iv(shared, key_mode=self.key_mode, iv_mode=self.iv_mode)
        shared = derive_shared(self.x25519_private_key, self.server_public_key)
        return derive_key_iv(shared, key_mode=self.key_mode, iv_mode=self.iv_mode)

    @property
    def public_key(self) -> bytes:
        """当前会话的 MUA 公钥（Part1.k 对应的原始 32 字节）。"""
        return x25519_public(self.x25519_private_key)

    def export_state(self) -> dict[str, str]:
        """导出必须跨进程复用的会话材料；不导出服务端公钥等配置。"""
        return {
            "mua_x25519_private_key": self.x25519_private_key.hex(),
            "mua_random_material": self.random_material.hex(),
        }

    def sign(
        self,
        *,
        gid: str | None = None,
        oaid: str = "",
        method: str,
        host: str = "",
        path: str,
        query: str = "",
        body: bytes = b"",
        include_mua_profile: bool = True,
        mua_state: Mapping[str, object] | None = None,
        mua_counter: int = 1,
        uptime: int | None = None,
        **_: object,
    ) -> dict[str, str]:
        del host
        if not include_mua_profile:
            raise ValueError("PurePythonMuaSigner 需要 include_mua_profile=True")
        is_register = str(path).endswith("/register/android")
        effective_gid = self.gid if gid is None else gid
        if is_register:
            if self.register_profile is None:
                raise ValueError("纯 Python register 需要 24 字段 register_profile")
            effective_gid = effective_gid or ""
        elif not effective_gid:
            raise ValueError("纯 Python MUA 需要已注册 gid")
        pub = x25519_public(self.x25519_private_key)
        state = dict(
            self.default_mua_state
            or {
                "c": 0,
                "d": 0,
                "f": 0,
                "s": 4098,
                "t": 0,
                "tt": [],
            }
        )
        if mua_state:
            state.update(mua_state)
        if uptime is not None and "t" not in (mua_state or {}):
            state["t"] = int(uptime)
        part1 = build_part1(
            counter=mua_counter,
            public_key_hex=pub.hex(),
            random_material_hex=self.random_material.hex(),
            app_id=self.app_id,
            platform=self.platform,
            state=state,
            uid=None if is_register else (("00000000" + oaid) if oaid else None),
            version=self.version,
        )
        context = {
            "gid": effective_gid,
            "oaid": oaid,
            "counter": mua_counter,
            "method": method.upper(),
            "path": path,
            "query": query,
            "uptime": uptime,
        }
        key, iv = self._aes_key_iv(context)
        profile = (
            self.device_info(context)
            if callable(self.device_info)
            else self.device_info
        )
        if not isinstance(profile, Mapping):
            raise TypeError("device_info builder 必须返回 Mapping")
        part2 = encrypt_part2(profile, key=key, iv=iv, raw_deflate=self.raw_deflate)
        mua = compose_mua(part1, part2)
        register_body = b""
        if is_register:
            identity = dict(self.register_identity or {})
            register_snapshot = (
                self.register_profile(context)
                if callable(self.register_profile)
                else self.register_profile
            )
            if not isinstance(register_snapshot, Mapping):
                raise TypeError("register_profile builder 必须返回 Mapping")
            register_body = build_register_android_body(
                register_snapshot,
                counter=mua_counter,
                public_key_hex=pub.hex(),
                random_material_hex=self.random_material.hex(),
                key=key,
                iv=iv,
                device_id=str(identity.get("device_id", "")),
                rr=str(identity.get("rr", "0")),
                sid=str(identity.get("sid", "")),
                uid=str(identity.get("uid", "")),
                app_id=self.app_id,
                platform=self.platform,
                version=self.version,
                raw_deflate=self.raw_deflate,
            )
            body = register_body
        from .s1 import build_s1
        from .sig import build_canonical, compute_sig

        sig = compute_sig(method.upper(), path, query, body, mua)
        canonical = build_canonical(method.upper(), path, query, body, mua)
        result = {
            "gid": effective_gid,
            "mua": mua,
            "sig": sig,
            "native_sig": sig,
            "s1": build_s1(canonical, mua_counter),
            "counter": str(mua_counter),
            "mua_part1": encode_part1(part1),
            "mua_part2": part2,
            "register_body_b64": "",
        }
        if register_body:
            result["register_body_b64"] = base64.b64encode(register_body).decode(
                "ascii"
            )
        return result


def build_s1_placeholder(
    sig: str, counter: int, *, timestamp_ms: int | None = None
) -> str:
    """保留给旧测试的非算法占位值。生产路径使用 `xhs_s1.build_s1`。"""
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    digest = hashlib.sha256(f"{sig}:{counter}:{timestamp_ms}".encode()).digest()
    blob = (
        struct.pack("<Q", timestamp_ms)
        + struct.pack("<I", counter)
        + digest
        + b"xhs-s1"
    )
    return base64.b64encode(blob).decode("ascii")
