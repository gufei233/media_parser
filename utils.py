"""Shared text utilities for mojibake detection and repair."""

import re
from typing import Any, Tuple


# ---------- Latin-path mojibake markers ----------
_LATIN_MARKERS = (
    "Ã", "Â", "â", "å", "ä", "ç", "é", "è",
    "ê", "ë", "ì", "í", "î", "ï", "ð", "ñ",
    "ò", "ó", "ô", "õ", "ö", "ù", "ú", "û",
    "ü", "ý", "þ", "€", "™", "\xa0",
)

# ---------- GBK-path mojibake markers ----------
_GBK_MARKERS = (
    "锛", "銆", "鈥", "鈻", "鎴", "鐨", "鍦",
    "涓", "鏄", "浣", "鍙", "瀵", "璇", "鎵",
    "鍒", "绗", "澶", "鍥", "鏂", "鏃", "鍐",
    "寮", "闂", "閮",
)

# ---------- Common CJK characters for quality scoring ----------
_COMMON_CJK = set(
    "的一是不了人我在有他这为之大来以个中上们到说国和地也子时道出而要于就下得可你年生会那后能对着事其里所去行过家十用发天如然作方成者多日都三小军二无同么经当起与好看学进种将还分此心前面又定见只主没公从知全工"
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def count_cjk(text: str) -> int:
    return len(_CJK_RE.findall(text))


def mojibake_score(text: str) -> int:
    """Score how likely text contains latin-path mojibake."""
    if not text:
        return 0
    return sum(text.count(ch) for ch in _LATIN_MARKERS)


def gbk_mojibake_score(text: str) -> int:
    """Score how likely text contains GBK-path mojibake."""
    if not text:
        return 0
    return sum(text.count(ch) for ch in _GBK_MARKERS)


def common_cjk_score(text: str) -> int:
    if not text:
        return 0
    return sum(1 for ch in text if ch in _COMMON_CJK)


def text_quality(text: str) -> Tuple[int, int, int, int]:
    """Return (quality, cjk_count, common_count, bad_count)."""
    cjk = count_cjk(text)
    common = common_cjk_score(text)
    latin_bad = mojibake_score(text)
    gbk_bad = gbk_mojibake_score(text)
    bad = latin_bad * 2 + gbk_bad * 3 + text.count("\xa0") * 4
    quality = common * 4 + cjk - bad
    return quality, cjk, common, bad


def repair_mojibake_text(text: str) -> str:
    """Try to recover mojibake text from common wrong decoding paths."""
    if not text:
        return text

    best = text
    best_q = text_quality(text)
    if mojibake_score(text) == 0 and gbk_mojibake_score(text) == 0:
        return text

    for source_enc in ("latin1", "cp1252", "gb18030", "gbk"):
        try:
            candidate = text.encode(source_enc).decode("utf-8")
        except Exception:
            continue

        # Some payloads are double-garbled; try one extra pass.
        try:
            second = candidate.encode(source_enc).decode("utf-8")
            if text_quality(second)[0] > text_quality(candidate)[0]:
                candidate = second
        except Exception:
            pass

        cand_q = text_quality(candidate)
        if cand_q[0] >= best_q[0] + 2 or (
            cand_q[0] > best_q[0] and cand_q[3] < best_q[3]
        ):
            best = candidate
            best_q = cand_q

    return best


def normalize_text(value: Any, default: str = "") -> str:
    """Normalize and repair a text value."""
    if value is None:
        return default
    text = repair_mojibake_text(str(value))
    text = text.replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text or default


def result_mojibake_score(result: dict) -> int:
    """Score overall mojibake in a parsed result dict."""
    if not isinstance(result, dict):
        return 0
    author = result.get("author") or {}
    music = result.get("music") or {}
    fields = [
        result.get("desc"),
        result.get("type"),
        author.get("nickname"),
        music.get("title"),
        music.get("author"),
    ]
    total = 0
    for v in fields:
        if v:
            total += mojibake_score(str(v)) + gbk_mojibake_score(str(v))
    return total


def decode_text_bytes(raw: bytes) -> str:
    """Decode bytes trying common encodings."""
    if not raw:
        return ""
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")
