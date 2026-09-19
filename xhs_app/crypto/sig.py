"""固化 SIG 生成函数，并用全部样本端到端验证。"""

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(ROOT)

# ---- 加载 GF(2) 仿射矩阵 ----
with open(
    os.path.join(PROJECT_ROOT, "fixtures", "sig_matrix_android.json"), encoding="utf-8"
) as matrix_file:
    _matrix = json.load(matrix_file)
_NBYTES = _matrix["nbytes"]
_NBITS = _NBYTES * 8
_ROWS = _matrix["rows"]  # 128 行，每行 129 列（128 输入 bit + 1 常量）


def _gf2_apply(digest16):
    """对 digest 前 16 字节做仿射变换，返回 16 字节。"""
    v = int.from_bytes(digest16, "big") | (1 << _NBITS)
    out = bytearray(16)
    for ob, row in enumerate(_ROWS):
        s = 0
        for j, c in enumerate(row):
            if c:
                s ^= (v >> j) & 1
        if s:
            out[ob // 8] |= 1 << (7 - ob % 8)
    return bytes(out)


def build_canonical(method, path, query, body_bytes, mua):
    body_sha = hashlib.sha256(body_bytes).hexdigest()
    return f"{method}\n{path}\n{query}\n{body_sha}\n{mua}"


def compute_sig(method, path, query, body_bytes, mua):
    """根据请求信息 + MUA 计算 x-mini-sig（32 字节 hex = 64 字符）。"""
    canonical = build_canonical(method, path, query, body_bytes, mua)
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    front = _gf2_apply(digest[:16])
    return (front + digest[16:]).hex()


# ---- 端到端验证 ----
def verify_pairs(pairs, label):
    ok = 0
    for sample in pairs:
        body = bytes.fromhex(sample.get("body_hex", ""))
        got = compute_sig(
            sample.get("method", "GET"),
            sample["path"],
            sample.get("query", ""),
            body,
            sample["mua"],
        )
        if got == sample["sig"]:
            ok += 1
    print(f"{label}: {ok}/{len(pairs)} 完全一致")
    return ok


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fixture = os.path.join(ROOT, "fixtures", "sig_imagefeed_vectors.json")
    with open(fixture, encoding="utf-8") as vector_file:
        pairs = json.load(vector_file)
    print(f"独立 imagefeed 抓包样本: {len(pairs)}")
    ok = verify_pairs(pairs, "独立抓包端到端验证")
    if ok != len(pairs):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
