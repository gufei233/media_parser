"""小红书 9.43.1 shield 的魔改 MD5 —— 纯 Python 实现（无 unicorn、无 SO 依赖）。

完全逆向自 libxyass.so 的 init(0x8c6e8)+compress(0x8ca08)，已与 native oracle 逐字节对齐。

魔改点（相对标准 MD5）：
1. IV 反序：a0=0x10325476 b0=0x98badcfe c0=0xefcdab89 d0=0x67452301
2. 左移 10 处（S 表魔改）
3. T 表：0xa01d0（=8.77 的 T_mod 前 64 值），并对 K16/K19/K29 做键控 mask：
       K16 = T16 & 0xff00ff00
       K19 = T19 & 0xff0011ff
       K29 = T29 & 0xff110010
   再交换 K39<->K40、K41<->K42
4. 第 40-43 轮（i=39..42）g 下标交换：10/13/0/3 -> 13/10/3/0
5. 第 40-44 轮入参错位（pin 重排），i=39..62 的 state 更新为 [B,C,D,new_a]，
   i=63 恢复标准 [D,new_a,B,C]
"""

import struct

# ---- T 表（libxyass.so 偏移 0xa01d0 的前 64 个 u32，=8.77 T_mod）----
T_A01D0 = [
    0xE9C9B756,
    0xD71BA479,
    0x241081DB,
    0x681088D9,
    0x9B14F7AF,
    0xFF1F5BB1,
    0x881CD7BE,
    0x66666122,
    0xF6666193,
    0xA619639E,
    0x49140921,
    0xC11DCEEE,
    0xF51C0FAF,
    0x4717C62A,
    0xA9104613,
    0xFD169501,
    0xF61E2562,
    0x02741453,
    0xD221E691,
    0xE213FBC9,
    0x2261CDE6,
    0xF2D50D97,
    0x425A14ED,
    0xA277E905,
    0xF277A3F9,
    0x626F12D9,
    0x922A4C9A,
    0xC040B340,
    0x265E5A51,
    0xE9F6C7AA,
    0xD63F105D,
    0xC35707D6,
    0xFFFC3942,
    0x977CD691,
    0xA4BCEA44,
    0x4BDCCFA9,
    0xBEBCBC70,
    0x288C7EC6,
    0xF6CC4B60,
    0xEAAC27FA,
    0xD4EC1095,
    0xD9DCD039,
    0xE6DC88E5,
    0x048C1D05,
    0x1FA27CF9,
    0x6D9D6122,
    0xC4AC5665,
    0xFDE5391C,
    0xF4292244,
    0xAB9423A7,
    0xF593A039,
    0x655B59C3,
    0x452AFF97,
    0xF5EF247D,
    0x85845DD1,
    0x850CCC92,
    0xF99926E0,
    0xF9997E4F,
    0xA9994314,
    0xC5537E82,
    0x450811A1,
    0x450811A6,
    0xBD3AF235,
    0xEB86D391,
]


# ---- 生成键控 K 表 ----
def _keyed_k(tbl):
    K = list(tbl)
    K[16] = tbl[16] & 0xFF00FF00
    K[19] = tbl[19] & 0xFF0011FF
    K[29] = tbl[29] & 0xFF110010
    K[39], K[40] = K[40], K[39]
    K[41], K[42] = K[42], K[41]
    return K


K = _keyed_k(T_A01D0)

# ---- 魔改左移表（10 处）----
S = [7, 12, 17, 22] * 4 + [5, 9, 14, 20] * 4 + [4, 11, 16, 23] * 4 + [6, 10, 15, 21] * 4
S[0] = 6
S[1] = 13
S[3] = 21
S[7] = 20
S[10] = 16
S[13] = 13
S[39] = 4
S[40] = 23
S[41] = 16
S[42] = 11

# ---- 第 40-43 轮 g 下标交换 ----
MAGIC_G = {39: 13, 40: 10, 41: 3, 42: 0}

# ---- 第 40-44 轮入参错位（state 重排索引）----
PIN = {
    39: (3, 0, 1, 2),
    40: (0, 1, 2, 3),
    41: (0, 1, 2, 3),
    42: (0, 1, 2, 3),
    43: (1, 2, 0, 3),
    44: (1, 3, 0, 2),
}
for _i in range(45, 64):
    PIN[_i] = (2, 3, 0, 1)

IV = [0x10325476, 0x98BADCFE, 0xEFCDAB89, 0x67452301]  # 反序


def _F(b, c, d):
    return (b & c) | (~b & d)


def _G(b, c, d):
    return (d & b) | (~d & c)


def _H(b, c, d):
    return b ^ c ^ d


def _I(b, c, d):
    return c ^ (b | ~d)


def _rol(x, n):
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _round_fn(i):
    if i < 16:
        return _F, i
    if i < 32:
        return _G, (5 * i + 1) % 16
    if i < 48:
        return _H, (3 * i + 5) % 16
    return _I, (7 * i) % 16


def compress(state, M):
    """单块 64 字节（16 个 u32）压缩。state=[a,b,c,d]，返回新 [a,b,c,d]。"""
    a, b, c, d = state
    for i in range(64):
        f, g = _round_fn(i)
        if i in MAGIC_G:
            g = MAGIC_G[i]
        if i <= 38:
            A, B, C, D = a, b, c, d
        else:
            p = PIN[i]
            A, B, C, D = (
                [a, b, c, d][p[0]],
                [a, b, c, d][p[1]],
                [a, b, c, d][p[2]],
                [a, b, c, d][p[3]],
            )
        ror_in = (A + f(B, C, D) + M[g] + K[i]) & 0xFFFFFFFF
        new_a = (B + _rol(ror_in, S[i])) & 0xFFFFFFFF
        if i <= 38 or i == 63:
            a, b, c, d = D, new_a, B, C
        else:
            a, b, c, d = B, C, D, new_a
    return [a, b, c, d]


def md5(data):
    """完整魔改 MD5（含 padding，支持多块），返回 16 字节 digest。"""
    blk = bytearray(data)
    bitlen = (len(data) * 8) & 0xFFFFFFFFFFFFFFFF
    blk.append(0x80)
    while len(blk) % 64 != 56:
        blk.append(0)
    blk += struct.pack("<Q", bitlen)
    state = list(IV)
    for off in range(0, len(blk), 64):
        M = list(struct.unpack("<16I", bytes(blk[off : off + 64])))
        res = compress(state, M)
        state = [(state[j] + res[j]) & 0xFFFFFFFF for j in range(4)]
    return struct.pack("<4I", *state)


def hmac_md5(key64, msg):
    """HMAC-MD5（魔改 MD5）。key64 = 64 字节密钥。返回 (outer, inner) 各 16 字节。"""
    ipad = bytes(b ^ 0x36 for b in key64)
    opad = bytes(b ^ 0x5C for b in key64)
    inner = md5(ipad + msg)
    outer = md5(opad + inner)
    return outer, inner


if __name__ == "__main__":
    # 自测
    z = md5(b"00000000-0000-0000-0000-000000000000")
    print("md5(zero-uuid) =", z.hex(), "(expect 94d3c51d13033535c7ca0a1183028c1d)")
    key64 = bytes.fromhex(
        "8803de49bfd6525d96bf235636bc10565e7ddee177fb314ea3044d0a7790d2d0b28562a9b820e1d1bcf2c30b9d6f7247eee86c6e91f939cf2999ac6013765754"
    )
    src = b"/api/sns/v1/system_service/vfc_codephone=13800138000&zone=1&type=login90platform=android&build=9431801&deviceId=4ee6f1f8-75e5-313d-993b-e5a8aaedbaae"
    hmac, inner = hmac_md5(key64, src)
    print("inner =", inner.hex(), "(expect 12e1acb424b105a0f74ddd2eb4d8252c)")
