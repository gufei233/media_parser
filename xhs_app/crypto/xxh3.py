"""Pure Python seeded XXH3-128 for arbitrary byte-string lengths.

Adapted from xxHash v0.8.3, Copyright (c) 2012-2023 Yann Collet.
BSD-2-Clause; see XXHASH_LICENSE.txt. No native hash extension is used.
"""

MASK = (1 << 64) - 1
P1 = 0x9E3779B185EBCA87
P2 = 0xC2B2AE3D27D4EB4F
P4 = 0x85EBCA77C2B2AE63
SECRET = bytes.fromhex(
    "b8fe6c3923a44bbe7c01812cf721ad1cded46de9839097db7240a4a4b7b3671fcb79e64eccc0e578825ad07dccff7221b8084674f743248ee03590e6813a264c3c2852bb91c300cb88d0658b1b532ea371644897a20df94e3819ef46a9deacd8a8fa763fe39c343ff9dcbbc7c70b4f1d8a51e04bcdb45931c89f7ec9d9787364eac5ac8334d3ebc3c581a0fffa1363eb170ddd51b7f0da49d316552629d4689e2b16be587d47a1fc8ff8b8d17ad031ce45cb3a8f95160428afd7fbcabb4b407e"
)


def _read(b: bytes, pos: int) -> int:
    return int.from_bytes(b[pos : pos + 8], "little")


def _avalanche(x: int) -> int:
    x &= MASK
    x ^= x >> 37
    x = x * 0x165667919E3779F9 & MASK
    return x ^ (x >> 32)


def xxh3_128_medium(data: bytes, seed: int = 0) -> int:
    """Return high64<<64|low64, rejecting lengths outside 17..240."""
    n = len(data)
    if not 17 <= n <= 240:
        raise ValueError("medium XXH3-128 currently supports 17..240 bytes")
    seed &= MASK
    low, high = n * P1 & MASK, 0

    def mix16(pos: int, sec: int, seed: int) -> int:
        a = _read(data, pos) ^ ((_read(SECRET, sec) + seed) & MASK)
        b = _read(data, pos + 8) ^ ((_read(SECRET, sec + 8) - seed) & MASK)
        product = a * b
        return (product & MASK) ^ (product >> 64)

    def mix32(a: int, b: int, sec: int, sd: int) -> None:
        nonlocal low, high
        low = ((low + mix16(a, sec, sd)) & MASK) ^ (
            (_read(data, b) + _read(data, b + 8)) & MASK
        )
        high = ((high + mix16(b, sec + 16, sd)) & MASK) ^ (
            (_read(data, a) + _read(data, a + 8)) & MASK
        )

    if n <= 128:
        for i in range((n - 1) // 32, -1, -1):
            mix32(16 * i, n - 16 * (i + 1), 32 * i, seed)
    else:
        for i in range(32, 160, 32):
            mix32(i - 32, i - 16, i - 32, seed)
        low, high = _avalanche(low), _avalanche(high)
        for i in range(160, n + 1, 32):
            mix32(i - 32, i - 16, 3 + i - 160, seed)
        mix32(n - 16, n - 32, 136 - 17 - 16, -seed & MASK)
    result_low = _avalanche(low + high)
    result_high = -_avalanche(low * P1 + high * P4 + (n - seed) * P2) & MASK
    return result_high << 64 | result_low


P3 = 0x165667B19E3779F9
P5 = 0x27D4EB2F165667C5
P32_1 = 0x9E3779B1
P32_2 = 0x85EBCA77
P32_3 = 0xC2B2AE3D


def _swap(x: int, size: int) -> int:
    return int.from_bytes(x.to_bytes(size, "little"), "big")


def _avalanche64(x: int) -> int:
    x &= MASK
    x = ((x ^ (x >> 33)) * P2) & MASK
    x = ((x ^ (x >> 29)) * P3) & MASK
    return x ^ (x >> 32)


def _short(data: bytes, seed: int) -> int:
    n = len(data)
    if n == 0:
        low = _avalanche64(seed ^ _read(SECRET, 64) ^ _read(SECRET, 72))
        high = _avalanche64(seed ^ _read(SECRET, 80) ^ _read(SECRET, 88))
    elif n < 4:
        combined = (data[0] << 16) | (data[n >> 1] << 24) | data[-1] | (n << 8)
        swapped = _swap(combined, 4)
        rotated = ((swapped << 13) | (swapped >> 19)) & 0xFFFFFFFF
        flip_l = (
            int.from_bytes(SECRET[:4], "little") ^ int.from_bytes(SECRET[4:8], "little")
        ) + seed
        flip_h = (
            int.from_bytes(SECRET[8:12], "little")
            ^ int.from_bytes(SECRET[12:16], "little")
        ) - seed
        low = _avalanche64(combined ^ (flip_l & MASK))
        high = _avalanche64(rotated ^ (flip_h & MASK))
    elif n <= 8:
        seed ^= _swap(seed & 0xFFFFFFFF, 4) << 32
        value = int.from_bytes(data[:4], "little") | (
            int.from_bytes(data[-4:], "little") << 32
        )
        flip = ((_read(SECRET, 16) ^ _read(SECRET, 24)) + seed) & MASK
        product = (value ^ flip) * (P1 + (n << 2))
        low, high = product & MASK, product >> 64
        high = (high + (low << 1)) & MASK
        low ^= high >> 3
        low = ((low ^ (low >> 35)) * 0x9FB21C651E98DF25) & MASK
        low ^= low >> 28
        high = _avalanche(high)
    else:
        flip_l = ((_read(SECRET, 32) ^ _read(SECRET, 40)) - seed) & MASK
        flip_h = ((_read(SECRET, 48) ^ _read(SECRET, 56)) + seed) & MASK
        head, tail = _read(data, 0), _read(data, n - 8)
        product = (head ^ tail ^ flip_l) * P1
        low, high = product & MASK, product >> 64
        low = (low + ((n - 1) << 54)) & MASK
        tail ^= flip_h
        high = (high + tail + (tail & 0xFFFFFFFF) * (P32_2 - 1)) & MASK
        low ^= _swap(high, 8)
        product = low * P2
        low = _avalanche(product & MASK)
        high = _avalanche((product >> 64) + high * P2)
    return (high << 64) | low


def _long(data: bytes, seed: int) -> int:
    if seed:
        parts = []
        for i in range(0, 192, 16):
            parts.append(((_read(SECRET, i) + seed) & MASK).to_bytes(8, "little"))
            parts.append(((_read(SECRET, i + 8) - seed) & MASK).to_bytes(8, "little"))
        secret = b"".join(parts)
    else:
        secret = SECRET
    acc = [P32_3, P1, P2, P3, P4, P32_2, P5, P32_1]

    def stripe(pos: int, sec: int) -> None:
        for lane in range(8):
            value = _read(data, pos + 8 * lane)
            keyed = value ^ _read(secret, sec + 8 * lane)
            acc[lane ^ 1] = (acc[lane ^ 1] + value) & MASK
            acc[lane] = (acc[lane] + (keyed & 0xFFFFFFFF) * (keyed >> 32)) & MASK

    n = len(data)
    blocks = (n - 1) // 1024
    for block in range(blocks):
        for j in range(16):
            stripe(block * 1024 + j * 64, j * 8)
        for lane in range(8):
            value = acc[lane]
            acc[lane] = (
                (value ^ (value >> 47) ^ _read(secret, 128 + lane * 8)) * P32_1
            ) & MASK
    for j in range(((n - 1) - blocks * 1024) // 64):
        stripe(blocks * 1024 + j * 64, j * 8)
    stripe(n - 64, 121)

    def merge(sec: int, start: int) -> int:
        total = start
        for i in range(4):
            product = (acc[2 * i] ^ _read(secret, sec + 16 * i)) * (
                acc[2 * i + 1] ^ _read(secret, sec + 16 * i + 8)
            )
            total += (product & MASK) ^ (product >> 64)
        return _avalanche(total)

    return merge(117, ~(n * P2)) << 64 | merge(11, n * P1)


def xxh3_128(data: bytes, seed: int = 0) -> int:
    """Return the unsigned 128-bit digest (high64 followed by low64).

    Seed is reduced modulo 2**64, as in the xxHash C API. This is a one-shot
    implementation, with no runtime dependency on xxhash/unidbg/native code.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    if type(seed) is not int:
        raise TypeError("seed must be an integer")
    data = bytes(data)
    seed &= MASK
    if len(data) <= 16:
        return _short(data, seed)
    if len(data) <= 240:
        return xxh3_128_medium(data, seed)
    return _long(data, seed)
