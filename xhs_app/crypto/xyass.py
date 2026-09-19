"""xyass 签名核心加密原语（已逆向验证）.

已确认的算法链（2026-08-25）:
1. key16 = deviceId 前 16 字节 XOR MASK（逐字节）
2. round_keys = 定制 AES-128 key schedule(key16)  [自定义 RCON 表]
3. 块密码 = 标准 AES（S-box / Te / Td 表均为标准）
4. main_hmac = AES-CBC-encrypt(HEADER(16B) || key64(64B) || footer(16B), round_keys, IV=0)
   -> 即 CBC 解密 main_hmac 可得 key64（偏移 16 处，64 字节）

可选未解：f_key(deviceId) -> key64 的离线派生算法（native "类 AES"）。正常客户端从 vfc_code 响应取得 main_hmac，再由本模块解出 key64，不依赖该反向生成式。
"""

SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16"
)

# deviceId 前 16 字节的 XOR 掩码（0x19898 处 key schedule 输入变换）
MASK = bytes.fromhex("f1892131ff001123f1001356f1234890")

# 定制 key schedule 的 Rcon（0x1a098 静态表，10 个 32 位值，big-endian word）
RCON = [
    0x12310000,
    0x02000100,
    0x04020000,
    0x08020200,
    0x10102000,
    0x30020400,
    0x40002000,
    0x80002000,
    0x1B002000,
    0x36200200,
]

# main_hmac 明文首块（固定 header，16B）
HEADER = bytes.fromhex("3501323400020861667a666607176639")


def rotw(w):
    return ((w << 8) | (w >> 24)) & 0xFFFFFFFF


def subw(w):
    b = w.to_bytes(4, "big")
    return int.from_bytes(bytes(SBOX[x] for x in b), "big")


def key_expand(key16):
    """定制 AES-128 key schedule: 16B -> 176B (11 x 16) round keys."""
    w = [int.from_bytes(key16[i * 4 : i * 4 + 4], "big") for i in range(4)]
    for i in range(10):
        t = subw(rotw(w[-1]))
        w.append(w[-4] ^ t ^ RCON[i])
        w.append(w[-4] ^ w[-1])
        w.append(w[-4] ^ w[-1])
        w.append(w[-4] ^ w[-1])
    return b"".join(x.to_bytes(4, "big") for x in w)


def deviceid_key16(devid):
    """deviceId 前 16 字节 XOR MASK -> 16 字节 AES 主密钥。"""
    if not isinstance(devid, str):
        raise TypeError("devid must be str")
    encoded = devid.encode("utf-8")
    if len(encoded) != 36:
        raise ValueError(f"devid must be 36 bytes, got {len(encoded)}")
    return bytes(a ^ b for a, b in zip(encoded[:16], MASK))


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def _gmul(a, b):
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        b >>= 1
        a = _xtime(a)
    return r


def _sub_bytes(s):
    return bytes(SBOX[b] for b in s)


def _shift_rows(s):
    return bytes(
        [
            s[0],
            s[5],
            s[10],
            s[15],
            s[4],
            s[9],
            s[14],
            s[3],
            s[8],
            s[13],
            s[2],
            s[7],
            s[12],
            s[1],
            s[6],
            s[11],
        ]
    )


def _inv_shift_rows(s):
    return bytes(
        [
            s[0],
            s[13],
            s[10],
            s[7],
            s[4],
            s[1],
            s[14],
            s[11],
            s[8],
            s[5],
            s[2],
            s[15],
            s[12],
            s[9],
            s[6],
            s[3],
        ]
    )


def _mix_columns(s):
    o = bytearray(16)
    for c in range(4):
        a = s[c * 4 : c * 4 + 4]
        o[c * 4 + 0] = _gmul(a[0], 2) ^ _gmul(a[1], 3) ^ a[2] ^ a[3]
        o[c * 4 + 1] = a[0] ^ _gmul(a[1], 2) ^ _gmul(a[2], 3) ^ a[3]
        o[c * 4 + 2] = a[0] ^ a[1] ^ _gmul(a[2], 2) ^ _gmul(a[3], 3)
        o[c * 4 + 3] = _gmul(a[0], 3) ^ a[1] ^ a[2] ^ _gmul(a[3], 2)
    return bytes(o)


def _inv_mix_columns(s):
    o = bytearray(16)
    for c in range(4):
        a = s[c * 4 : c * 4 + 4]
        o[c * 4 + 0] = (
            _gmul(a[0], 14) ^ _gmul(a[1], 11) ^ _gmul(a[2], 13) ^ _gmul(a[3], 9)
        )
        o[c * 4 + 1] = (
            _gmul(a[0], 9) ^ _gmul(a[1], 14) ^ _gmul(a[2], 11) ^ _gmul(a[3], 13)
        )
        o[c * 4 + 2] = (
            _gmul(a[0], 13) ^ _gmul(a[1], 9) ^ _gmul(a[2], 14) ^ _gmul(a[3], 11)
        )
        o[c * 4 + 3] = (
            _gmul(a[0], 11) ^ _gmul(a[1], 13) ^ _gmul(a[2], 9) ^ _gmul(a[3], 14)
        )
    return bytes(o)


def _add_rk(s, rk):
    return bytes(a ^ b for a, b in zip(s, rk))


def aes_encrypt_block(block, rk):
    """标准 AES-128 加密（自定义 176B 轮密钥 rk）."""
    s = _add_rk(block, rk[0:16])
    for i in range(1, 10):
        s = _add_rk(_mix_columns(_shift_rows(_sub_bytes(s))), rk[i * 16 : (i + 1) * 16])
    s = _add_rk(_shift_rows(_sub_bytes(s)), rk[160:176])
    return s


def aes_decrypt_block(block, rk):
    """标准 AES-128 解密（自定义 176B 轮密钥 rk）。"""
    if len(block) != 16:
        raise ValueError(f"AES block must be 16 bytes, got {len(block)}")
    if len(rk) != 176:
        raise ValueError(f"AES-128 round keys must be 176 bytes, got {len(rk)}")
    s = _add_rk(block, rk[160:176])
    for i in range(9, 0, -1):
        s = _inv_shift_rows(s)
        s = bytes(_inv_sbox(b) for b in s)
        s = _add_rk(s, rk[i * 16 : (i + 1) * 16])
        s = _inv_mix_columns(s)
    s = _inv_shift_rows(s)
    s = bytes(_inv_sbox(b) for b in s)
    s = _add_rk(s, rk[0:16])
    return s


_INV_SBOX = [0] * 256
for _i, _v in enumerate(SBOX):
    _INV_SBOX[_v] = _i


def _inv_sbox(b):
    return _INV_SBOX[b]


def round_keys(devid):
    """deviceId -> 176B 轮密钥."""
    return key_expand(deviceid_key16(devid))


def decrypt_main_hmac(main_hmac, devid):
    """CBC 解密 main_hmac(96B) -> header(16B) || key64(64B) || footer(16B)。IV=0。"""
    if not isinstance(main_hmac, bytes):
        raise TypeError("main_hmac must be bytes")
    if len(main_hmac) != 96:
        raise ValueError(f"main_hmac must be 96 bytes, got {len(main_hmac)}")
    rk = round_keys(devid)
    C = [main_hmac[i * 16 : (i + 1) * 16] for i in range(6)]
    P = []
    prev = b"\x00" * 16
    for c in C:
        p = bytes(a ^ b for a, b in zip(aes_decrypt_block(c, rk), prev))
        P.append(p)
        prev = c
    return b"".join(P)


def encrypt_main_hmac(header_key64_footer, devid):
    """CBC 加密 header||key64||footer -> main_hmac(96B)。IV=0。"""
    if not isinstance(header_key64_footer, bytes):
        raise TypeError("header_key64_footer must be bytes")
    if len(header_key64_footer) != 96:
        raise ValueError(
            f"header_key64_footer must be 96 bytes, got {len(header_key64_footer)}"
        )
    rk = round_keys(devid)
    C = []
    prev = b"\x00" * 16
    for i in range(6):
        p = header_key64_footer[i * 16 : (i + 1) * 16]
        c = aes_encrypt_block(bytes(a ^ b for a, b in zip(p, prev)), rk)
        C.append(c)
        prev = c
    return b"".join(C)


if __name__ == "__main__":
    # 自测：key schedule 对 deviceId aaaaaaaa-bbbb-4ccc 的 w[0..4]
    key16 = deviceid_key16("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    assert key16.hex() == "90e840509e617042dc627134930e7cf3", key16.hex()
    rk = key_expand(key16)
    assert int.from_bytes(rk[16:20], "big") == 0x29C94D8C, rk[16:20].hex()
    print("key schedule self-test OK")

    # 自测：main_hmac 解密 -> key64
    import json
    from pathlib import Path

    fixture = (
        Path(__file__).resolve().parents[1] / "fixtures" / "main_hmac_vectors.json"
    )
    triples = json.loads(fixture.read_text(encoding="utf-8"))
    devid = "00000000-0000-0000-0000-000000000000"
    d = triples[devid]
    pt = decrypt_main_hmac(bytes.fromhex(d["hmac_hex"]), devid)
    assert pt[:16] == HEADER, pt[:16].hex()
    assert pt[16:80] == bytes.fromhex(d["key_hex"]), "key64 mismatch"
    print("main_hmac decrypt self-test OK (key64 recovered)")
