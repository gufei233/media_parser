"""Pure-Python x-mini-s1 generator for Android 9.43.1.

The implementation mirrors the observed VM pipeline: SHA-256 word framing,
four digest-lane formatters, two CRC-32 checksums, and the app's keyed
AES-like CBC transform. It has no native/runtime dependency.
"""

from __future__ import annotations

import base64
import hashlib
import struct
import time

DIGEST_WORD_ORDER: tuple[int, ...] = (7, 0, 2, 5, 6, 3, 1, 4)
CRC32_REFLECTED_POLYNOMIAL = 0xEDB88320
THIRD_LANE_NIBBLE_SBOX: tuple[int, ...] = (
    0xB,
    0x2,
    0xF,
    0x6,
    0x8,
    0x9,
    0xE,
    0x3,
    0x1,
    0xD,
    0x7,
    0x5,
    0xC,
    0xA,
    0x0,
    0x4,
)

S1_FRAME_MARKER = b"\x00"
S1_VERSION = b"\x01"
S1_STATIC_WORD = bytes.fromhex("51244a19")
S1_SEED_TRAILER = bytes.fromhex("170e")
S1_CBC_IV = bytes.fromhex("6e2052fb01f05fd238ad54534bd00831")


def _gf_multiply(left: int, right: int) -> int:
    result = 0
    for _ in range(8):
        if right & 1:
            result ^= left
        left = ((left << 1) ^ 0x11B) & 0xFF if left & 0x80 else (left << 1) & 0xFF
        right >>= 1
    return result


def _gf_inverse(value: int) -> int:
    if value == 0:
        return 0
    result = 1
    base = value
    exponent = 254
    while exponent:
        if exponent & 1:
            result = _gf_multiply(result, base)
        base = _gf_multiply(base, base)
        exponent >>= 1
    return result


def _rotate_byte(value: int, count: int) -> int:
    return ((value << count) | (value >> (8 - count))) & 0xFF


def _build_aes_sbox() -> bytes:
    values = []
    for value in range(256):
        inverse = _gf_inverse(value)
        values.append(
            inverse
            ^ _rotate_byte(inverse, 1)
            ^ _rotate_byte(inverse, 2)
            ^ _rotate_byte(inverse, 3)
            ^ _rotate_byte(inverse, 4)
            ^ 0x63
        )
    return bytes(values)


_AES_SBOX = _build_aes_sbox()

# Nine fused SubBytes input masks recovered from the active 9.43.1 VM tables.
_S1_ROUND_XOR_KEYS: tuple[bytes, ...] = tuple(
    bytes.fromhex(value)
    for value in (
        "786873306f6832697534616e376f6732",
        "5a3241711e764471e0d4fb95096b0c3e",
        "43713041275115398f5bfa6ff3c8c4fa",
        "1e6f5f1e27766300174cf798864b8f75",
        "ed82ddc30e781b293c70b32b4539b6c3",
        "52d00dce760e1578cabaddf62717a162",
        "2bfbf638faf4e18c2d9711e7789c3d5f",
        "93689ea6fe0aeb0467f0ad4a819ba6f9",
        "fa920caa767c97888c7ca1eb61bf19e0",
    )
)
_S1_FINAL_XOR_A = bytes.fromhex("69fbf75dee92059829554ea58b130aea")
_S1_FINAL_XOR_C = bytes.fromhex("34cf3865648a181d73d6ffaa5f55bf34")


def build_nested_input(canonical: bytes, counter: int) -> bytes:
    if not 0 <= counter <= 0xFFFFFFFF:
        raise ValueError("counter must fit uint32")
    return canonical + struct.pack("<I", counter)


def permute_sha256_words(digest: bytes) -> bytes:
    if len(digest) != 32:
        raise ValueError("SHA-256 digest must be 32 bytes")
    words = tuple(digest[offset : offset + 4] for offset in range(0, 32, 4))
    return b"".join(words[index] for index in DIGEST_WORD_ORDER)


def nibble_swap(data: bytes) -> bytes:
    return bytes((value >> 4) | ((value & 0x0F) << 4) for value in data)


def initial_vm_digest_state(canonical: bytes, counter: int) -> bytes:
    return permute_sha256_words(
        hashlib.sha256(build_nested_input(canonical, counter)).digest()
    )


def state_at_first_lane_store(canonical: bytes, counter: int) -> bytes:
    state = initial_vm_digest_state(canonical, counter)
    return nibble_swap(state[:8]) + state[8:]


def crc32_internal(data: bytes) -> int:
    state = 0xFFFFFFFF
    for value in data:
        state ^= value
        for _ in range(8):
            state = (
                (state >> 1) ^ CRC32_REFLECTED_POLYNOMIAL if state & 1 else state >> 1
            )
    return state & 0xFFFFFFFF


def crc32(data: bytes) -> int:
    return crc32_internal(data) ^ 0xFFFFFFFF


def format_first_lane_block(block: bytes) -> bytes:
    if len(block) != 8:
        raise ValueError("lane block must be exactly 8 bytes")
    swapped = nibble_swap(block)
    mask = crc32_internal(swapped) & 0xFF
    return bytes(value ^ mask for value in swapped)


def format_second_lane_block(block: bytes) -> bytes:
    if len(block) != 8:
        raise ValueError("lane block must be exactly 8 bytes")
    cross_nibble = ((block[0] << 4) | (block[7] >> 4)) ^ (
        (block[6] << 4) | (block[1] >> 4)
    )
    masked = bytes(value ^ (cross_nibble & 0xFF) for value in block)
    return bytes(
        value if index in (3, 5) else nibble_swap(bytes((value,)))[0]
        for index, value in enumerate(masked)
    )


def format_third_lane_block(block: bytes) -> bytes:
    if len(block) != 8:
        raise ValueError("lane block must be exactly 8 bytes")
    substituted = bytes(
        transformed
        for value in block
        for transformed in (
            THIRD_LANE_NIBBLE_SBOX[value >> 4],
            THIRD_LANE_NIBBLE_SBOX[value & 0x0F],
        )
    )
    mask = crc32_internal(substituted) & 0xFF
    return bytes(value ^ mask for value in block)


def format_fourth_lane_block(block: bytes) -> bytes:
    if len(block) != 8:
        raise ValueError("lane block must be exactly 8 bytes")
    constant = 0xAE
    first = block[0] ^ constant
    first_swapped = nibble_swap(bytes((first,)))[0]
    second = block[1] ^ first_swapped ^ constant
    second_swapped = nibble_swap(bytes((second,)))[0]
    mask = first_swapped ^ second_swapped ^ constant
    return bytes(
        (
            first_swapped ^ second_swapped ^ first,
            second_swapped ^ second,
            *(value ^ mask for value in block[2:]),
        )
    )


def format_digest_lane(digest_state: bytes) -> bytes:
    if len(digest_state) != 32:
        raise ValueError("digest state must be exactly 32 bytes")
    return b"".join(
        (
            format_first_lane_block(digest_state[0:8]),
            format_second_lane_block(digest_state[8:16]),
            format_third_lane_block(digest_state[16:24]),
            format_fourth_lane_block(digest_state[24:32]),
        )
    )


def digest_lane(canonical: bytes, counter: int) -> bytes:
    return format_digest_lane(initial_vm_digest_state(canonical, counter))


def _transpose_4x4(block: bytes) -> bytes:
    if len(block) != 16:
        raise ValueError("block must be exactly 16 bytes")
    return bytes(block[row * 4 + column] for column in range(4) for row in range(4))


def _shift_rows_row_major(state: bytes) -> bytes:
    return b"".join(
        state[row * 4 + row : row * 4 + 4] + state[row * 4 : row * 4 + row]
        for row in range(4)
    )


def _mix_column(column: bytes) -> bytes:
    a, b, c, d = column
    return bytes(
        (
            _gf_multiply(a, 2) ^ _gf_multiply(b, 3) ^ c ^ d,
            a ^ _gf_multiply(b, 2) ^ _gf_multiply(c, 3) ^ d,
            a ^ b ^ _gf_multiply(c, 2) ^ _gf_multiply(d, 3),
            _gf_multiply(a, 3) ^ b ^ c ^ _gf_multiply(d, 2),
        )
    )


def _mix_columns_row_major(state: bytes) -> bytes:
    transposed = _transpose_4x4(state)
    mixed = b"".join(
        _mix_column(transposed[offset : offset + 4]) for offset in range(0, 16, 4)
    )
    return _transpose_4x4(mixed)


def encrypt_s1_block(block: bytes) -> bytes:
    """Encrypt one 16-byte block with the VM's fused AES-like transform."""
    state = _shift_rows_row_major(_transpose_4x4(block))
    for round_key in _S1_ROUND_XOR_KEYS:
        state = bytes(_AES_SBOX[value ^ mask] for value, mask in zip(state, round_key))
        state = _shift_rows_row_major(_mix_columns_row_major(state))
    state = bytes(
        _AES_SBOX[value ^ first_mask] ^ last_mask
        for value, first_mask, last_mask in zip(state, _S1_FINAL_XOR_A, _S1_FINAL_XOR_C)
    )
    return _transpose_4x4(state)


def encrypt_s1_cbc(payload: bytes) -> bytes:
    if not payload or len(payload) % 16:
        raise ValueError("S1 CBC payload must be a non-empty 16-byte multiple")
    previous = S1_CBC_IV
    output = bytearray()
    for offset in range(0, len(payload), 16):
        block = bytes(
            left ^ right for left, right in zip(payload[offset : offset + 16], previous)
        )
        previous = encrypt_s1_block(block)
        output.extend(previous)
    return bytes(output)


def build_s1_raw(
    canonical: bytes, counter: int, *, timestamp_s: int | None = None
) -> bytes:
    if timestamp_s is None:
        timestamp_s = int(time.time())
    if not 0 <= timestamp_s <= 0xFFFFFFFF:
        raise ValueError("timestamp_s must fit uint32")
    lane = digest_lane(canonical, counter)
    seed = (
        struct.pack("<I", timestamp_s)
        + S1_STATIC_WORD
        + b"\x00\x00"
        + lane
        + S1_SEED_TRAILER
    )
    if len(seed) != 44:
        raise AssertionError("S1 seed framing regression")
    first_crc = crc32(seed).to_bytes(4, "big")
    ciphertext = encrypt_s1_cbc(seed + b"\x04" * 4)
    source = S1_VERSION + ciphertext + first_crc
    second_crc = crc32(source).to_bytes(4, "big")
    raw = S1_FRAME_MARKER + struct.pack("<I", counter) + source + second_crc
    if len(raw) != 62:
        raise AssertionError("S1 output framing regression")
    return raw


def build_s1(
    canonical: bytes | str, counter: int, *, timestamp_s: int | None = None
) -> str:
    if isinstance(canonical, str):
        canonical = canonical.encode("utf-8")
    return base64.b64encode(
        build_s1_raw(canonical, counter, timestamp_s=timestamp_s)
    ).decode("ascii")
