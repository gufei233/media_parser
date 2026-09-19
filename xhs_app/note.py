"""把 homefeed / imagefeed / detailfeed 响应整理成稳定字段。"""

from __future__ import annotations

import io
from typing import Any


def _v(obj: Any, *names: str, default=None):
    if not isinstance(obj, dict):
        return default
    for name in names:
        value = obj.get(name)
        if value not in (None, ""):
            return value
    return default


def _https(url: Any):
    if isinstance(url, str) and url.startswith("http://"):
        return "https://" + url[7:]
    return url if isinstance(url, str) else ""


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False


def _append_unique(items: list, value: Any) -> None:
    if value and value not in items:
        items.append(value)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def image_to_jpg(raw: bytes) -> bytes:
    """把 HEIF/HEIC/WEBP 转成 JPEG。"""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(raw))
    except Exception:
        import pillow_heif

        pillow_heif.register_heif_opener()
        image = Image.open(io.BytesIO(raw))
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


def download_image(url: str, session=None) -> bytes:
    import httpx as _httpx

    if session is not None:
        raw = session.get(url, timeout=60).content
    else:
        raw = _httpx.get(url, timeout=60, follow_redirects=True).content
    return image_to_jpg(raw)


def _user(obj: dict) -> dict:
    user = obj.get("user") if isinstance(obj.get("user"), dict) else {}
    return {
        "name": str(_v(user, "nickname", "nickName", "name", default="") or ""),
        "id": str(_v(user, "userid", "user_id", "userId", "id", default="") or ""),
        "avatar": _https(_v(user, "image", "avatar", default="") or ""),
        "redId": str(_v(user, "red_id", "redId", default="") or ""),
        "followed": _bool_value(_v(user, "followed", default=False)),
    }


def _topics(note: dict) -> list[dict]:
    topics = []
    seen = set()
    for topic in list(_as_list(note.get("hash_tag"))) + list(
        _as_list(note.get("topics"))
    ):
        if not isinstance(topic, dict):
            continue
        name = str(_v(topic, "name", "title", default="") or "")
        topic_id = str(_v(topic, "id", default="") or "")
        key = (topic_id, name)
        if not any(key) or key in seen:
            continue
        seen.add(key)
        topics.append(
            {
                "id": topic_id,
                "name": name,
                "type": str(_v(topic, "type", default="") or ""),
                "link": str(_v(topic, "link", default="") or ""),
            }
        )
    return topics


def _stream_variants(stream: Any) -> list[tuple[bool, bool, str, dict]]:
    if not isinstance(stream, dict):
        return []
    out = []
    for codec in ("h265", "h264", "av1", "h266"):
        for variant in _as_list(stream.get(codec)):
            if not isinstance(variant, dict):
                continue
            master = _https(
                _v(variant, "master_url", "masterUrl", "url", default="") or ""
            )
            if not master:
                continue
            has_audio = bool(_v(variant, "audio_codec", "audioCodec"))
            out.append((has_audio, codec == "h265", codec, variant))
    return out


def _video_detail(variant: dict, *, kind: str, codec: str = "") -> dict:
    audio_codec = str(_v(variant, "audio_codec", "audioCodec", default="") or "")
    return {
        "url": _https(_v(variant, "master_url", "masterUrl", "url", default="") or ""),
        "kind": kind,
        "videoCodec": str(
            _v(variant, "video_codec", "videoCodec", default=codec) or codec
        ),
        "audioCodec": audio_codec,
        "width": _int_value(_v(variant, "width", default=0)),
        "height": _int_value(_v(variant, "height", default=0)),
        "durationMs": _int_value(_v(variant, "duration", default=0)),
        "bitrate": _int_value(_v(variant, "avg_bitrate", "bitrate", default=0)),
        "qualityType": str(
            _v(variant, "quality_type", "qualityType", default="") or ""
        ),
        "size": _int_value(_v(variant, "size", default=0)),
    }


def _audio_track(variant: dict, url: str) -> dict:
    return {
        "url": url,
        "master_url": url,
        "codec": str(_v(variant, "audio_codec", "audioCodec", default="") or "")
        or "aac",
        "audio_codec": str(_v(variant, "audio_codec", "audioCodec", default="") or "")
        or "aac",
        "durationMs": _int_value(
            _v(variant, "audio_duration", "audioDuration", default=0)
        ),
        "channels": _int_value(
            _v(variant, "audio_channels", "audioChannels", default=0)
        ),
        "hasSoundtrack": True,
    }


def _collect_live_photo(image: dict, result: dict) -> None:
    live = _v(image, "live_photo", "livePhoto", default={}) or {}
    media = _v(live, "media", default={}) or {}
    stream = _v(media, "stream", default={}) or {}
    candidates = _stream_variants(stream)
    if not candidates:
        return
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, codec, variant = candidates[0]
    detail = _video_detail(variant, kind="live_photo", codec=codec)
    if not detail["url"]:
        return
    _append_unique(result["videos"], detail["url"])
    result["videoDetails"].append(detail)
    result["livePhotos"].append(detail)
    if detail["audioCodec"]:
        result["audioTracks"].append(_audio_track(variant, detail["url"]))


def _collect_video_root(video_root: Any, result: dict) -> None:
    if not isinstance(video_root, dict) or not video_root:
        return
    media = _v(video_root, "media", default={}) or {}
    stream = _v(media, "stream", default={}) or {}
    stream_found = False
    for has_audio, _h265, codec, variant in _stream_variants(stream):
        detail = _video_detail(variant, kind="video", codec=codec)
        if not detail["url"]:
            continue
        stream_found = True
        _append_unique(result["videos"], detail["url"])
        result["videoDetails"].append(detail)
        if has_audio and not any(
            track.get("url") == detail["url"] for track in result["audioTracks"]
        ):
            result["audioTracks"].append(_audio_track(variant, detail["url"]))
    audio_stream = media.get("audio_stream") or {}
    for aac in _as_list(audio_stream.get("AAC")):
        if not isinstance(aac, dict):
            continue
        master = _https(_v(aac, "master_url", "masterUrl", "url", default="") or "")
        if master:
            result["audioTracks"].append(_audio_track(aac, master))
    if stream_found:
        return
    direct = _https(_v(video_root, "url", "master_url", "masterUrl", default="") or "")
    if direct:
        _append_unique(result["videos"], direct)
        duration = _int_value(_v(video_root, "duration", default=0))
        if 0 < duration < 10000:
            duration *= 1000
        result["videoDetails"].append(
            {
                "url": direct,
                "kind": "video",
                "videoCodec": "",
                "audioCodec": "",
                "width": _int_value(_v(video_root, "width", default=0)),
                "height": _int_value(_v(video_root, "height", default=0)),
                "durationMs": duration,
                "bitrate": 0,
                "qualityType": "",
                "size": 0,
            }
        )
    for item in _as_list(
        video_root.get("url_info_list") or video_root.get("urlInfoList")
    ):
        if isinstance(item, dict):
            _append_unique(
                result["videos"],
                _https(_v(item, "url", "master_url", "masterUrl", default="") or ""),
            )


def classify_content(note: dict) -> str:
    if note.get("livePhotos") or any(
        item.get("kind") == "live_photo" for item in note.get("videoDetails") or []
    ):
        return "live"
    if note.get("type") == "video" or (note.get("videos") and not note.get("images")):
        return "video"
    if note.get("images"):
        return "image"
    return "text"


def summarize_note(note: dict) -> str:
    author = (note.get("author") or {}).get("name") or "-"
    title = note.get("title") or note.get("displayTitle") or "(无标题)"
    kind = note.get("contentType") or classify_content(note)
    counts = note.get("counts") or {}
    extra = []
    if note.get("images"):
        extra.append(f"图{len(note['images'])}")
    if note.get("videos"):
        extra.append(f"视频{len(note['videos'])}")
    if note.get("livePhotos"):
        extra.append(f"实况{len(note['livePhotos'])}")
    liked = counts.get("liked")
    if liked:
        extra.append(f"赞{liked}")
    bits = " ".join(extra)
    return f"[{kind}] {title} | {author}" + (f" | {bits}" if bits else "")


def parse_note_object(note: dict, *, source: str, note_id: str = "") -> dict:
    content = str(_v(note, "desc", "content", default="") or "").strip()
    raw_title = str(_v(note, "title", default="") or "").strip()
    display_title = str(
        _v(note, "display_title", "displayTitle", default="") or ""
    ).strip()
    fallback = next((line.strip() for line in content.splitlines() if line.strip()), "")
    raw_type = str(_v(note, "type", default="normal") or "normal")
    result = {
        "noteId": str(_v(note, "id", "note_id", "noteId", default=note_id) or note_id),
        "title": raw_title or display_title or fallback[:80] or "小红书内容",
        "rawTitle": raw_title,
        "displayTitle": display_title,
        "author": _user(note),
        "content": content,
        "desc": content,
        "type": raw_type,
        "modelType": str(_v(note, "model_type", "modelType", default="") or ""),
        "counts": {
            "liked": _int_value(
                _v(
                    note,
                    "liked_count",
                    "likedCount",
                    "likes",
                    "nice_count",
                    "niced",
                    default=0,
                )
            ),
            "collected": _int_value(
                _v(note, "collected_count", "collectedCount", default=0)
            ),
            "comments": _int_value(
                _v(note, "comments_count", "commentsCount", default=0)
            ),
            "shares": _int_value(
                _v(note, "shared_count", "share_count", "sharedCount", default=0)
            ),
            "views": _int_value(_v(note, "view_count", "viewCount", default=0)),
        },
        "liked": _bool_value(_v(note, "inlikes", "liked", default=False)),
        "createdAt": _int_value(
            _v(note, "time", "timestamp", "create_time", "createTime", default=0)
        ),
        "updatedAt": _int_value(
            _v(note, "last_update_time", "lastUpdateTime", "update_time", default=0)
        ),
        "ipLocation": str(_v(note, "ip_location", "ipLocation", default="") or ""),
        "topics": _topics(note),
        "shareUrl": str(
            _v(
                note.get("share_info")
                if isinstance(note.get("share_info"), dict)
                else {},
                "link",
                default="",
            )
            or ""
        ),
        "xsecToken": str(_v(note, "xsec_token", "xsecToken", default="") or ""),
        "hasMusic": _bool_value(_v(note, "has_music", "hasMusic", default=False)),
        "isAds": _bool_value(_v(note, "is_ads", "isAds", default=False)),
        "cursorScore": str(_v(note, "cursor_score", "cursorScore", default="") or ""),
        "images": [],
        "imageDetails": [],
        "videos": [],
        "videoDetails": [],
        "livePhotos": [],
        "audioTracks": [],
        "source": source,
    }

    for image in _as_list(
        note.get("images_list") or note.get("image_list") or note.get("imageList")
    ):
        if not isinstance(image, dict):
            continue
        img_url = _v(
            image, "url_size_large", "urlSizeLarge", "original", "url", default=""
        )
        if not img_url:
            levels = _v(image, "url_multi_level", "urlMultiLevel", default={}) or {}
            img_url = _v(levels, "high", "medium", "low", default="")
        img_url = _https(img_url)
        live = _v(image, "live_photo", "livePhoto", default={}) or {}
        if img_url:
            _append_unique(result["images"], img_url)
            result["imageDetails"].append(
                {
                    "url": img_url,
                    "width": _int_value(_v(image, "width", default=0)),
                    "height": _int_value(_v(image, "height", default=0)),
                    "fileId": str(
                        _v(image, "fileid", "file_id", "fileId", default="") or ""
                    ),
                    "isLivePhoto": bool(live),
                }
            )
        _collect_live_photo(image, result)

    _collect_video_root(note.get("video_info_v2") or {}, result)
    _collect_video_root(note.get("video") or {}, result)

    if result["videos"]:
        result["video"] = result["videos"][0]
    if result["images"]:
        result["cover"] = result["images"][0]
    if result["audioTracks"]:
        result["audio"] = result["audioTracks"][0]
        result["audioUrl"] = result["audioTracks"][0]["url"]
        result["hasSoundtrack"] = True
    result["contentType"] = classify_content(result)
    result["isLivePhoto"] = result["contentType"] == "live"
    return result


def parse_imagefeed(payload: dict, note_id: str = "") -> dict:
    """把 imagefeed / detailfeed 响应归一化。"""
    if not isinstance(payload, dict):
        return {"error": True, "message": "empty payload"}
    roots = payload.get("data") or []
    if isinstance(roots, dict) and isinstance(roots.get("preload_map"), dict):
        preload_map = roots["preload_map"]
        selected = preload_map.get(note_id) if note_id else None
        if not isinstance(selected, dict):
            selected = next(
                (value for value in preload_map.values() if isinstance(value, dict)),
                None,
            )
        if not selected:
            return {"error": True, "message": "empty preload_map"}
        roots = [{"note_list": [selected]}]
    if isinstance(roots, dict):
        roots = [roots]
    if not isinstance(roots, list) or not roots:
        return {
            "error": True,
            "message": f"no data: code={payload.get('code')} msg={payload.get('msg')}",
        }
    entry = next((item for item in roots if isinstance(item, dict)), None)
    if not entry:
        return {"error": True, "message": "bad entry"}
    notes = entry.get("note_list") or entry.get("noteList") or []
    if isinstance(notes, dict):
        notes = [notes]
    note = next((item for item in notes if isinstance(item, dict)), None)
    if not note:
        return {"error": True, "message": "no note_list"}
    if isinstance(entry.get("user"), dict) and not isinstance(note.get("user"), dict):
        note = dict(note)
        note["user"] = entry["user"]
    return parse_note_object(note, source="app_imagefeed", note_id=note_id)


def parse_search_notes(payload: dict) -> list[dict]:
    data = payload.get("data") if isinstance(payload, dict) else None
    items = []
    if isinstance(data, dict):
        items = data.get("items") or []
    notes = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        note = item.get("note") if isinstance(item.get("note"), dict) else item
        parsed = parse_note_object(note, source="app_search")
        if parsed.get("noteId"):
            notes.append(parsed)
    return notes


def parse_user_info(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"error": True, "message": "empty payload"}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if payload.get("code") not in (0, None) and not data.get("userid"):
        return {
            "error": True,
            "message": payload.get("msg") or "user info failed",
            "code": payload.get("code"),
        }
    interactions = {
        item.get("type"): item
        for item in (data.get("interactions") or [])
        if isinstance(item, dict)
    }
    return {
        "userId": str(_v(data, "userid", "user_id", "id", default="") or ""),
        "name": str(_v(data, "nickname", "name", default="") or ""),
        "redId": str(_v(data, "red_id", "redId", default="") or ""),
        "avatar": _https(_v(data, "imageb", "image", "avatar", default="") or ""),
        "desc": str(_v(data, "desc", default="") or ""),
        "ipLocation": str(_v(data, "ip_location", "ipLocation", default="") or ""),
        "gender": _int_value(_v(data, "gender", default=0)),
        "fans": _int_value(
            _v(data, "fans", default=0) or (interactions.get("fans") or {}).get("count")
        ),
        "follows": _int_value(
            _v(data, "follows", default=0)
            or (interactions.get("follows") or {}).get("count")
        ),
        "liked": _int_value(_v(data, "liked", default=0)),
        "collected": _int_value(_v(data, "collected", default=0)),
        "shareLink": str(_v(data, "share_link", default="") or ""),
        "banner": _https(
            (
                (data.get("banner_info") or {})
                if isinstance(data.get("banner_info"), dict)
                else {}
            ).get("image")
            or ""
        ),
    }


def parse_user_posted(payload: dict) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    notes_raw = []
    cursor = ""
    has_more = False
    if isinstance(data, dict):
        notes_raw = data.get("notes") or []
        cursor = str(data.get("cursor") or "")
        has_more = bool(data.get("has_more") or data.get("hasMore"))
        if not cursor and notes_raw and isinstance(notes_raw[-1], dict):
            cursor = str(notes_raw[-1].get("cursor") or notes_raw[-1].get("id") or "")
    notes = []
    for note in notes_raw if isinstance(notes_raw, list) else []:
        if isinstance(note, dict):
            parsed = parse_note_object(note, source="app_user_posted")
            if parsed.get("noteId"):
                notes.append(parsed)
    return {"notes": notes, "cursor": cursor, "hasMore": has_more}


def parse_homefeed(payload: dict) -> list[dict]:
    """把 homefeed 响应整理成可供选择的笔记摘要。"""
    if not isinstance(payload, dict):
        return []
    items = payload.get("data") or []
    if not isinstance(items, list):
        return []
    notes = []
    for item in items:
        if not isinstance(item, dict):
            continue
        note = parse_note_object(item, source="app_homefeed")
        if note.get("noteId"):
            notes.append(note)
    return notes
