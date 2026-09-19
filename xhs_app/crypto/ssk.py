#!/usr/bin/env python3
"""小红书 9.43.1 SSK 客户端密码学实现。

当前客户端使用 POST /api/sns/v1/user/activate：请求携带临时
client_public_key_base64，响应 data.ssk 为 base64(iv[12] ||
AES-256-GCM(ciphertext+tag))。shared key = X25519(client_private,
SERVER_PUBLIC_KEY)，解密结果为 32B main_ssk。

历史 cold_start_config 曾出现相同公钥参数，因此保留 build_cold_start_url()
作为研究辅助；它不是 xhs_app_client.py 当前 bootstrap 的实际 SSK 入口。
"""

from __future__ import annotations

import base64
import os

P = 2**255 - 19
BASE_POINT = b"\x09" + b"\x00" * 31
SERVER_PUBLIC_KEY = bytes.fromhex(
    "2abd22ca0b02bb78a760935708be24e09bb369843669f235c5bc1cec11fab26f"
)


def _clamp(k: bytes) -> bytes:
    k = bytearray(k)
    k[0] &= 0xF8
    k[31] = (k[31] & 0x7F) | 0x40
    return bytes(k)


def _x25519_scalar_mult(k_int: int, u_int: int) -> int:
    x1 = u_int
    x2, z2 = 1, 0
    x3, z3 = u_int, 1
    swap = 0
    a24 = 121665
    for t in range(254, -1, -1):
        kt = (k_int >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        A = (x2 + z2) % P
        AA = (A * A) % P
        B = (x2 - z2) % P
        BB = (B * B) % P
        E = (AA - BB) % P
        C = (x3 + z3) % P
        D = (x3 - z3) % P
        DA = (D * A) % P
        CB = (C * B) % P
        x3 = ((DA + CB) ** 2) % P
        z3 = ((DA - CB) ** 2 * x1) % P
        x2 = (AA * BB) % P
        z2 = (E * (AA + a24 * E)) % P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return (x2 * pow(z2, P - 2, P)) % P


def x25519(
    private_key: bytes, peer_public: bytes, *, all_zero_check: bool = False
) -> bytes:
    private_key = _clamp(private_key)
    if len(private_key) != 32 or len(peer_public) != 32:
        raise ValueError("X25519 keys must be 32 bytes")
    k = int.from_bytes(private_key, "little")
    u = int.from_bytes(peer_public, "little") & ((1 << 255) - 1)
    out = _x25519_scalar_mult(k, u).to_bytes(32, "little")
    if all_zero_check and out == b"\x00" * 32:
        raise ValueError("X25519 shared key is all zero")
    return out


def public_key(private_key: bytes) -> bytes:
    return x25519(private_key, BASE_POINT)


def generate_keypair() -> tuple[bytes, bytes]:
    priv = _clamp(os.urandom(32))
    return priv, public_key(priv)


def shared_secret_with_server(private_key: bytes) -> bytes:
    return x25519(private_key, SERVER_PUBLIC_KEY, all_zero_check=True)


def _b64_decode(s: str) -> bytes:
    s = s.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def decrypt_ssk(private_key: bytes, encrypted_ssk_base64: str) -> bytes:
    """返回 32 字节 main_ssk 明文。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    shared = shared_secret_with_server(private_key)
    try:
        blob = _b64_decode(encrypted_ssk_base64)
        if len(blob) < 28:
            raise ValueError("encrypted SSK too short")
        nonce = blob[:12]
        ct_tag = blob[12:]
        out = AESGCM(shared).decrypt(nonce, ct_tag, None)
        if len(out) != 32:
            raise ValueError(f"decrypted SSK length {len(out)} != 32")
        return out
    finally:
        # shared 是敏感值，尽力清除
        pass


def client_public_key_base64(public: bytes, *, url_encoded: bool = False) -> str:
    s = base64.b64encode(public).decode("ascii")  # 对应 Base64.NO_WRAP
    if url_encoded:
        from urllib.parse import quote

        return quote(s, safe="")
    return s


def build_cold_start_url(
    public: bytes,
    *,
    need_user_info: bool = True,
    has_displayed_translation_guide: bool = False,
) -> str:
    pub = client_public_key_base64(public, url_encoded=True)
    return (
        "https://edith.xiaohongshu.com/api/sns/v1/system/cold_start_config"
        f"?need_user_info={'true' if need_user_info else 'false'}"
        f"&has_displayed_translation_guide={'true' if has_displayed_translation_guide else 'false'}"
        f"&client_public_key_base64={pub}"
    )


def _self_test() -> None:
    # RFC 7748 测试向量 1
    k = bytes.fromhex(
        "a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4"
    )
    u = bytes.fromhex(
        "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c"
    )
    got = x25519(k, u)
    want = bytes.fromhex(
        "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"
    )
    assert got == want, "RFC7748 vector failed"

    # AES-256-GCM 往返
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = bytes(range(32))
    nonce = b"0" * 12
    ct = AESGCM(key).encrypt(nonce, b"x" * 32, None)
    assert AESGCM(key).decrypt(nonce, ct, None) == b"x" * 32

    priv, pub = generate_keypair()
    assert len(priv) == 32 and len(pub) == 32
    assert public_key(priv) == pub
    print("self-test ok")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--gen", action="store_true", help="生成临时 X25519 密钥对并打印")
    p.add_argument("--decrypt", metavar="ENCRYPTED_SSK_B64")
    p.add_argument(
        "--priv", metavar="PRIV_HEX", help="32 字节 X25519 私钥 hex，配合 --decrypt"
    )
    a = p.parse_args()

    if a.self_test:
        _self_test()
    elif a.gen:
        priv, pub = generate_keypair()
        print("PRIV_HEX", priv.hex())
        print("PUB_B64", client_public_key_base64(pub))
        print("URL", build_cold_start_url(pub))
    elif a.decrypt:
        if not a.priv:
            raise SystemExit("--decrypt 需要 --priv")
        priv = bytes.fromhex(a.priv)
        ssk = decrypt_ssk(priv, a.decrypt)
        print("MAIN_SSK", base64.b64encode(ssk).decode("ascii"))
    else:
        p.print_help()
