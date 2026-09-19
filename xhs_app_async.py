"""
小红书 App 端签名解析的异步适配层。

底层复用 xhs_app 包（Android 9.43.1 MUA/SIG/S1/Shield 纯 Python 实现 +
匿名会话管理）。签名与 HTTP 为同步实现，这里通过线程池执行并串行化，
对外提供 asyncio 接口；输出字段与 async_xhs 的 HTML 解析保持一致，
供 main.py 直接消费。
"""

import asyncio
import time
import traceback
from pathlib import Path

from astrbot.api import logger

try:
    from .xhs_app import session as app_session
    from .xhs_app.session import ClientManager
except ImportError:  # 插件以扁平方式加载
    from xhs_app import session as app_session
    from xhs_app.session import ClientManager


def _best_video_url(details: list) -> str | None:
    """视频笔记的多码流去重：优先带音轨、h265、高码率的一条。"""
    candidates = [d for d in details or [] if isinstance(d, dict) and d.get("url")]

    def score(d: dict):
        codec = str(d.get("videoCodec") or "")
        return (
            bool(d.get("audioCodec")),
            1 if "265" in codec or "hevc" in codec.lower() else 0,
            int(d.get("bitrate") or 0),
        )

    if not candidates:
        return None
    return max(candidates, key=score)["url"]


class AsyncXhsAppParser:
    """App 端签名解析器（异步包装）。"""

    def __init__(self, pool_root: str | Path | None = None):
        self._lock = asyncio.Lock()
        self._manager: ClientManager | None = None
        self._pool_root = pool_root

    def _ensure_manager(self) -> ClientManager:
        if self._manager is None:
            self._manager = ClientManager(pool_root=self._pool_root)
            # session.extract_fields 的风控轮换使用模块级 _manager，
            # 这里替换成带自定义池目录的实例。
            app_session._manager = self._manager
        return self._manager

    async def close(self):
        if self._manager is None:
            return
        manager, self._manager = self._manager, None
        client = manager.client

        def _close():
            try:
                if client is not None:
                    client.close()
            except Exception as exc:
                logger.debug(f"xhs_app client close error: {exc}")

        try:
            await asyncio.to_thread(_close)
        except Exception as exc:
            logger.debug(f"xhs_app close error: {exc}")

    # ==================== 结果映射 ====================

    @staticmethod
    def _map_result(note: dict) -> dict:
        """把 xhs_app 的笔记字段映射为插件标准结果。"""
        content_type = str(note.get("contentType") or "image")
        is_live = content_type == "live" or bool(note.get("isLivePhoto"))
        images = list(note.get("images") or [])
        result = {
            "title": str(note.get("title") or "小红书内容"),
            "author": {
                "name": (note.get("author") or {}).get("name") or "未知作者",
                "id": (note.get("author") or {}).get("id") or "",
                "redId": (note.get("author") or {}).get("redId") or "",
                "avatar": (note.get("author") or {}).get("avatar") or "",
            },
            "content": str(note.get("content") or note.get("desc") or ""),
            "noteId": str(note.get("noteId") or ""),
            "originalUrl": str(note.get("shareUrl") or ""),
            "createdAt": int(note.get("createdAt") or 0),
            "topics": [
                t.get("name")
                for t in (note.get("topics") or [])
                if isinstance(t, dict) and t.get("name")
            ],
            "images": [],
            "videos": [],
            "livePairs": [],
            "cover": None,
            "contentType": "video" if content_type == "video" else "image",
            "isLivePhoto": is_live,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "source": "app_api",
            "counts": note.get("counts") or {},
            "ipLocation": note.get("ipLocation") or "",
            "isAds": bool(note.get("isAds")),
        }

        if content_type == "video":
            # 视频笔记：images 只是封面，取一条最优视频流，避免同一视频重复发送
            best = _best_video_url(note.get("videoDetails") or [])
            if not best:
                videos = note.get("videos") or []
                best = videos[0] if videos else None
            if best:
                result["videos"] = [best]
                result["video"] = best
            if images:
                result["cover"] = images[0]
        else:
            # 图文 / 实况：全部图片按序保留
            result["images"] = images
            if is_live:
                live_details = [
                    d
                    for d in (note.get("livePhotos") or note.get("live_photos") or [])
                    if isinstance(d, dict) and d.get("url")
                ]
                result["videos"] = [d["url"] for d in live_details]
                # 配对：livePhotos 与 imageDetails 按同一图片顺序产生，
                # 第 i 个带 isLivePhoto 的图片对应第 i 个实况视频。
                image_details = note.get("imageDetails") or []
                live_index = 0
                for position, detail in enumerate(image_details):
                    image_url = (
                        images[position]
                        if position < len(images)
                        else detail.get("url")
                    )
                    video_url = ""
                    if detail.get("isLivePhoto") and live_index < len(live_details):
                        video_url = live_details[live_index]["url"]
                        live_index += 1
                    result["livePairs"].append({"image": image_url, "video": video_url})
            if result["videos"]:
                result["video"] = result["videos"][0]

        return result

    # ==================== 主入口 ====================

    async def parse(self, text: str) -> dict:
        """解析分享口令 / 短链 / 笔记链接 / note_id。"""
        try:
            async with self._lock:
                result = await asyncio.to_thread(
                    app_session.extract_fields, text, None, False
                )
        except Exception as exc:
            logger.error(f"XhsApp parse exception: {exc}")
            logger.debug(traceback.format_exc())
            return {"error": True, "message": str(exc)}

        if not result.get("ok"):
            message = str(result.get("message") or "解析失败")
            code = result.get("code")
            if code is not None:
                message = f"{message} (code={code})"
            logger.warning(
                f"XhsApp parse failed: stage={result.get('stage')} {message}"
            )
            return {"error": True, "message": message}

        mapped = self._map_result(result)
        logger.info(
            f"XhsApp parsed noteId={mapped['noteId']} type={mapped['contentType']} "
            f"images={len(mapped['images'])} videos={len(mapped['videos'])} "
            f"live={mapped['isLivePhoto']}"
        )
        return mapped


# ========== 测试 ==========
async def _test():
    parser = AsyncXhsAppParser()
    try:
        text = input("请输入小红书分享口令或链接: ").strip()
        import json

        print(json.dumps(await parser.parse(text), ensure_ascii=False, indent=2))
    finally:
        await parser.close()


if __name__ == "__main__":
    asyncio.run(_test())
