"""小红书 Android 9.43.1 Shield 的纯 Python 实现。

覆盖 70B 基础形态，以及 main_ssk 激活后的 118B 完整形态。LW 扩展由
两个可选 TLV 经同一 RC4 流后半段加密得到。native nonce 为
atomic_inc(counter) XOR trunc32(sp+0x20)；固定该 4 字节时可逐字节复现
libxyass.so。nonce=None 时使用 os.urandom(4)，因为可移植路径没有栈地址。
"""

import base64
import binascii
import hashlib
import os
from urllib.parse import urlsplit

from .md5 import hmac_md5

# ---- 常量（9.43.1 版本固定，与 deviceId/mainHmac 无关）----
RC4_KEY = "逆向违法，即刻停止！Reverse engineering is illegal. Cease now!".encode()

MASK36 = bytes.fromhex(
    "606b39fd3c39443008502297d736827cf3903c05d0e77e0737f77066f77460435bd69068"
)
MASK16 = bytes.fromhex("b3661b2425c8d1cf9674e8218b30b91a")

HDR_PREFIX = bytes.fromhex("8dfe00055569")
HDR_SUFFIX = bytes.fromhex("a6d7a54f949cf960")
EMPTY_HF = bytes.fromhex("9a89")  # 空 mainHmac 时头部 2 字节标志
NONEMPTY_HF = bytes.fromhex(
    "9e8a"
)  # 非空 mainHmac 时头部 2 字节标志（常量，非 HMAC 派生）

PLAIN_BYTE0 = 0xC1
PLAIN_BYTE37 = 0xF4

APP_BUILD = "9431801"


def build_source(url, headers, device_id, body=""):
    """按 libxyass 白名单顺序构造 HMAC source。"""
    parts = urlsplit(url)
    path_query = parts.path + parts.query  # 去 "?"（host/scheme 已由 path 去掉）
    common = headers.get("xy-common-params", "")
    direction = headers.get("xy-direction", "")
    bandwidth = headers.get("xy-live-net-bandwidth", "")
    network_type = headers.get("xy-live-net-networktype", "")
    scene = headers.get("xy-scene", "")
    platform_info = f"platform=android&build={APP_BUILD}&deviceId={device_id}"
    return (
        path_query
        + common
        + direction
        + bandwidth
        + network_type
        + platform_info
        + scene
        + body
    ).encode("utf-8")


def rc4(key, data):
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) & 0xFF
        S[i], S[j] = S[j], S[i]
    i = j = 0
    out = bytearray()
    for b in data:
        i = (i + 1) & 0xFF
        j = (j + S[i]) & 0xFF
        S[i], S[j] = S[j], S[i]
        out.append(b ^ S[(S[i] + S[j]) & 0xFF])
    return bytes(out)


def _normalize_main_ssk(main_ssk):
    """返回 native SharedPreferences 中使用的 main_ssk Base64 ASCII。"""
    if not main_ssk:
        return ""
    if isinstance(main_ssk, bytes):
        if len(main_ssk) != 32:
            raise ValueError(f"main_ssk bytes must be 32 bytes, got {len(main_ssk)}")
        return base64.b64encode(main_ssk).decode("ascii")
    if not isinstance(main_ssk, str):
        raise TypeError("main_ssk must be bytes, str, or empty")
    try:
        decoded = base64.b64decode(main_ssk, validate=True)
    except Exception as exc:
        raise ValueError("main_ssk string must be valid Base64") from exc
    if len(decoded) != 32:
        raise ValueError(f"main_ssk Base64 must decode to 32 bytes, got {len(decoded)}")
    return main_ssk


def build_lw_extension(main_ssk, request_hmac, nonce=None):
    """构造 raw Shield 的可选 SSK 扩展密文。

    Args:
        main_ssk: 32B 原始 SSK，或其 44 字节 Base64 字符串。
        request_hmac: 当前请求的 16B custom HMAC-MD5 原值。为空时 native
            不生成任何 LW 扩展，保持 70B 冷启动形态。
        nonce: 可注入的 4B native challenge（atomic_inc XOR sp+0x20）；
            None 时使用 os.urandom(4)。

    Returns:
        main_ssk 与 request_hmac 均非空时固定为 48B 密文；否则为空。
    """
    ssk_b64 = _normalize_main_ssk(main_ssk)
    # Native 4bec0 only enters the SSK extension branch when both the
    # request HMAC field and main_ssk-derived proof are present.
    if not ssk_b64 or not request_hmac:
        return b""
    if nonce is None:
        nonce = os.urandom(4)
    if not isinstance(nonce, bytes) or len(nonce) != 4:
        raise ValueError("nonce must be exactly 4 bytes")
    if not isinstance(request_hmac, bytes):
        raise TypeError("request_hmac must be bytes")
    if len(request_hmac) != 16:
        raise ValueError(
            "request_hmac must be exactly 16 bytes when main_ssk is present"
        )

    ssk_proof = nonce + hashlib.sha1(ssk_b64.encode("ascii") + nonce).digest()
    request_proof = hashlib.sha1(request_hmac + ssk_proof).digest()
    extension_plaintext = bytearray(b"\x04\x14" + request_proof)
    extension_plaintext += b"\x05\x18" + ssk_proof

    # libxyass 对完整内部明文从 offset 0 开始跑 RC4。基础 raw 的前 2B
    # 是后处理插入的，因此 raw[70:] 对应 RC4 keystream[68:]。
    stream_offset = 68
    keystream = rc4(RC4_KEY, bytes(stream_offset + len(extension_plaintext)))
    keystream = keystream[stream_offset:]
    return bytes(a ^ b for a, b in zip(extension_plaintext, keystream))


def compute_shield(
    device_id, main_hmac, url, headers=None, body="", main_ssk="", ssk_nonce=None
):
    """计算 Android 9.43.1 Shield。

    main_hmac 是服务器 xy-ter-str 返回的 Base64；main_ssk 可传 32B 原值
    或 Base64。ssk_nonce 用于 native fixture 重放，生产调用保持 None。
    """
    headers = headers or {}
    if not isinstance(device_id, str):
        raise TypeError("device_id must be str")
    devid = device_id.encode("utf-8")
    if len(devid) != 36:
        raise ValueError(f"deviceId must be 36 bytes, got {len(devid)}")

    request_hmac = b""
    if main_hmac:
        from .xyass import HEADER, decrypt_main_hmac

        try:
            raw = base64.b64decode(main_hmac, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ValueError("main_hmac must be valid Base64") from exc
        if len(raw) != 96:
            raise ValueError(f"main_hmac must decode to 96 bytes, got {len(raw)}")
        pt = decrypt_main_hmac(raw, device_id)
        if len(pt) != 96 or pt[:16] != HEADER:
            raise ValueError("main_hmac plaintext header mismatch")
        key64 = pt[16:80]
        source = build_source(url, headers, device_id, body)
        request_hmac, _ = hmac_md5(key64, source)
        hf = NONEMPTY_HF
        tail = bytes(a ^ b for a, b in zip(request_hmac, MASK16))
    else:
        hf = EMPTY_HF
        tail = MASK16

    plaintext = bytearray()
    plaintext.append(PLAIN_BYTE0)
    plaintext += bytes(a ^ b for a, b in zip(devid, MASK36))
    plaintext.append(PLAIN_BYTE37)
    plaintext += tail
    if len(plaintext) != 54:
        raise RuntimeError(
            f"internal Shield plaintext length mismatch: {len(plaintext)}"
        )

    header = HDR_PREFIX + hf + HDR_SUFFIX
    if len(header) != 16:
        raise RuntimeError(f"internal Shield header length mismatch: {len(header)}")

    payload = header + rc4(RC4_KEY, bytes(plaintext))
    payload += build_lw_extension(main_ssk, request_hmac, nonce=ssk_nonce)
    return "XY" + base64.b64encode(payload).decode()


if __name__ == "__main__":
    devid = "4ee6f1f8-75e5-313d-993b-e5a8aaedbaae"
    url = "https://edith.xiaohongshu.com/api/sns/v1/system_service/vfc_code?phone=13800138000&zone=1&type=login"

    # 空 hmac
    s_empty = compute_shield(
        devid, "", url, {"xy-common-params": "", "xy-direction": "90"}
    )
    expect_empty = "XYjf4ABVVpmomm16VPlJz5YE0FodlR1qnMmVnCszuzcoCJfJOhpEN1aN/UnJcBJa1K0LUqi0v+Qdey0hyMHeAio6PLpd74zQ=="
    print("empty  :", s_empty)
    print("  match:", s_empty == expect_empty)

    # 非空 hmac
    main_hmac = "mW0Arab0NW5XncN47vtuo7bibdVSjqrhg5l/mVZXLUFpQsF/6LC4Dc/mLbFOp5q3iw+UFWEDnDJMFhI4k645wqOja5zFGRMmohOrS1i1hY4Un1un6rc6SEHqGBJcFujX"
    s_non = compute_shield(
        devid, main_hmac, url, {"xy-common-params": "", "xy-direction": "90"}
    )
    expect_non = "XYjf4ABVVpnoqm16VPlJz5YE0FodlR1qnMmVnCszuzcoCJfJOhpEN1aN/UnJcBJa1K0LUqi0v+36qMKls9oiStch2gsOasRw=="
    print("nonempty:", s_non)
    print("  match:", s_non == expect_non)
