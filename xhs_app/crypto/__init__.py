"""Android 9.43.1 签名算法工具模块。"""

from .mua import PurePythonMuaSigner, compose_mua, decode_part1, encode_part1
from .s1 import build_s1, build_s1_raw
from .shield import compute_shield
from .sig import build_canonical, compute_sig
from .ssk import decrypt_ssk, generate_keypair
from .xyass import decrypt_main_hmac, encrypt_main_hmac

__all__ = [
    "PurePythonMuaSigner",
    "build_canonical",
    "build_s1",
    "build_s1_raw",
    "compose_mua",
    "compute_shield",
    "compute_sig",
    "decode_part1",
    "decrypt_main_hmac",
    "decrypt_ssk",
    "encode_part1",
    "encrypt_main_hmac",
    "generate_keypair",
]
