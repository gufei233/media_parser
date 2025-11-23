import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

try:
    from .dysk import DouyinDownloader
    from .xhs import XiaohongshuParser
except ImportError:
    from dysk import DouyinDownloader
    from xhs import XiaohongshuParser

@register("media_parser", "Author", "抖音小红书链接解析插件", "1.0.0")
class MediaParserPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.xhs_parser = XiaohongshuParser()
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.dy_downloader = None
        self.dy_downloader_time = 0

        self.dy_patterns = [
            r'https?://v\.douyin\.com/[a-zA-Z0-9_-]+/?',
            r'https?://(?:www\.)?douyin\.com/[^\s]+',
            r'https?://(?:www\.)?iesdouyin\.com/[^\s]+'
        ]
        self.xhs_patterns = [
            r'https?://(?:www\.)?xiaohongshu\.com/[^\s]+',
            r'https?://xhslink\.com/[^\s]+'
        ]

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def parse_media_link(self, event: AstrMessageEvent):
        if not event.is_private_chat() and not event.is_at_or_wake_command:
            return

        text = event.message_str

        dy_url = None
        for pattern in self.dy_patterns:
            match = re.search(pattern, text)
            if match:
                dy_url = match.group(0)
                break

        xhs_url = None
        for pattern in self.xhs_patterns:
            match = re.search(pattern, text)
            if match:
                xhs_url = match.group(0)
                break

        if dy_url:
            async for result in self.parse_douyin(event, dy_url):
                yield result
            event.stop_event()
        elif xhs_url:
            async for result in self.parse_xiaohongshu(event, xhs_url):
                yield result
            event.stop_event()

    def _parse_douyin_sync(self, url):
        import sys
        from io import StringIO
        import time

        max_retries = 5
        retry_delay = 5

        for attempt in range(max_retries):
            old_stdout = sys.stdout
            sys.stdout = captured_output = StringIO()

            try:
                current_time = time.time()

                # 首次尝试或重试时，检查是否需要重新创建实例
                enable_cf = self.config.get("enable_cf_proxy", False)
                cf_url = self.config.get("cf_proxy_url", "")

                if attempt == 0:
                    # 首次尝试：复用 downloader 实例，但每 5 分钟重新创建一次
                    if self.dy_downloader is None or (current_time - self.dy_downloader_time) > 300:
                        logger.info("创建新的 DouyinDownloader 实例")
                        self.dy_downloader = DouyinDownloader(enable_cf_proxy=enable_cf, cf_proxy_url=cf_url)
                        self.dy_downloader_time = current_time
                    else:
                        logger.info("复用现有的 DouyinDownloader 实例")
                else:
                    # 重试时：强制重新创建实例
                    logger.info(f"第 {attempt + 1} 次重试，重新创建 DouyinDownloader 实例")
                    self.dy_downloader = DouyinDownloader(enable_cf_proxy=enable_cf, cf_proxy_url=cf_url)
                    self.dy_downloader_time = current_time

                result = self.dy_downloader.get_detail(url)

                output = captured_output.getvalue()
                sys.stdout = old_stdout

                logger.info(f"dysk.py 输出长度: {len(output)}")
                if output:
                    logger.info(f"dysk.py 输出:\n{output}")
                else:
                    logger.warning("dysk.py 没有任何输出")

                logger.info(f"解析结果: {result is not None}")

                # 如果解析成功，返回结果
                if result is not None:
                    return (result, self.dy_downloader)

                # 如果解析失败且还有重试次数
                if attempt < max_retries - 1:
                    logger.warning(f"解析返回 None，{retry_delay} 秒后进行第 {attempt + 2} 次尝试")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"已重试 {max_retries} 次，仍然解析失败")
                    return (None, self.dy_downloader)

            except Exception as e:
                sys.stdout = old_stdout
                import traceback
                logger.error(f"第 {attempt + 1} 次尝试同步解析失败: {e}\n{traceback.format_exc()}")

                if attempt < max_retries - 1:
                    logger.info(f"{retry_delay} 秒后进行第 {attempt + 2} 次尝试")
                    time.sleep(retry_delay)
                else:
                    raise
            finally:
                sys.stdout = old_stdout

        return (None, self.dy_downloader)

    async def _send_media_async(self, event, dy_downloader, images, video_links):
        """异步后台任务：下载并发送媒体文件"""
        import tempfile
        import os

        logger.info(f"开始下载媒体文件: {len(images)}张图片, {len(video_links)}个视频")
        loop = asyncio.get_event_loop()

        # 立即下载所有文件（避免链接过期）
        downloaded_images = []
        for i, img_url in enumerate(images):
            try:
                logger.info(f"下载图片 {i+1}/{len(images)}")
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
                temp_path = temp_file.name
                temp_file.close()

                success = await loop.run_in_executor(
                    self.executor,
                    dy_downloader.download_video,
                    img_url,
                    temp_path,
                    logger.info
                )
                logger.info(f"图片 {i+1} 下载{'成功' if success else '失败'}")
                downloaded_images.append((success, temp_path, img_url))
            except Exception as e:
                logger.error(f"图片下载异常: {e}")
                downloaded_images.append((False, None, img_url))

        downloaded_videos = []
        for i, video_url in enumerate(video_links):
            try:
                logger.info(f"下载视频 {i+1}/{len(video_links)}")
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
                temp_path = temp_file.name
                temp_file.close()

                success = await loop.run_in_executor(
                    self.executor,
                    dy_downloader.download_video,
                    video_url,
                    temp_path,
                    logger.info
                )
                logger.info(f"视频 {i+1} 下载{'成功' if success else '失败'}")
                downloaded_videos.append((success, temp_path, video_url))
            except Exception as e:
                logger.error(f"视频下载异常: {e}")
                downloaded_videos.append((False, None, video_url))

        # 延迟发送
        for success, temp_path, img_url in downloaded_images:
            try:
                await asyncio.sleep(2)
                if success and temp_path and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    result = event.make_result()
                    result.chain = [Comp.Image.fromFileSystem(temp_path)]
                    await event.send(result)
                    try:
                        os.unlink(temp_path)
                    except:
                        pass
                else:
                    await event.send(event.image_result(img_url))
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except:
                            pass
            except Exception as e:
                logger.error(f"图片发送失败: {e}")

        for success, temp_path, video_url in downloaded_videos:
            try:
                await asyncio.sleep(3)
                if success and temp_path and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    result = event.make_result()
                    result.chain = [Comp.Video.fromFileSystem(temp_path)]
                    await event.send(result)
                    try:
                        os.unlink(temp_path)
                    except:
                        pass
                else:
                    await event.send(event.plain_result(f"视频链接: {video_url}"))
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except:
                            pass
            except Exception as e:
                logger.error(f"视频发送失败: {e}")

    async def parse_douyin(self, event: AstrMessageEvent, url: str):
        try:
            logger.info(f"开始解析抖音链接: {url}")
            loop = asyncio.get_event_loop()
            result, dy_downloader = await loop.run_in_executor(self.executor, self._parse_douyin_sync, url)

            if not result:
                logger.error("dysk.py 返回 None，可能被风控")
                yield event.plain_result(f"解析失败，请直接打开链接查看:\n{url}")
                return

            uin = event.get_sender_id()
            name = event.get_sender_name()

            nodes = []

            author = result.get('author') or {}
            info_text = f"id: {result.get('id', '')}\ndesc: {result.get('desc', '')}\ncreate_time: {result.get('create_time', '')}\nnickname: {author.get('nickname', '')}"
            nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Plain(info_text)]))

            music = result.get('music') or {}
            music_text = f"uid: {author.get('uid', '')}\nauthor: {music.get('author', '')}\ntitle: {music.get('title', '')}\nurl: {music.get('url', '')}"
            nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Plain(music_text)]))

            stats = result.get('statistics') or {}
            stats_text = f"digg_count: {stats.get('digg_count', 0)}\ncomment_count: {stats.get('comment_count', 0)}\ncollect_count: {stats.get('collect_count', 0)}\nshare_count: {stats.get('share_count', 0)}"
            nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Plain(stats_text)]))

            type_text = f"type: {result.get('type', '')}"
            duration_str = result.get('duration', '')

            if result.get('type') == '视频' and duration_str:
                type_text += f"\nduration: {duration_str}"

            nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Plain(type_text)]))

            yield event.chain_result([Comp.Nodes(nodes=nodes)])

            downloads = result.get('downloads', [])
            images = []
            video_links = []

            for item in downloads:
                if isinstance(item, str):
                    images.append(item)
                elif isinstance(item, dict):
                    if item.get('type') == 'video':
                        if item.get('cover'):
                            images.append(item['cover'])
                        video_links.append(item['url'])
                    elif item.get('type') == 'live_photo':
                        if item.get('image'):
                            images.append(item['image'])
                        if item.get('video'):
                            video_links.append(item['video'])

            logger.info(f"准备发送媒体: {len(images)}张图片, {len(video_links)}个视频")
            if images or video_links:
                task = asyncio.create_task(self._send_media_async(event, dy_downloader, images, video_links))
                # 确保任务不会被取消
                task.add_done_callback(lambda t: logger.info("媒体发送任务完成") if not t.exception() else logger.error(f"媒体发送任务异常: {t.exception()}"))
            else:
                logger.warning("没有媒体文件需要发送")

        except Exception as e:
            import traceback
            error_msg = f"抖音解析失败: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            yield event.plain_result(f"解析失败: {str(e)}")

    async def parse_xiaohongshu(self, event: AstrMessageEvent, url: str):
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(self.executor, self.xhs_parser.parse, url)

            if result.get('error'):
                yield event.plain_result(f"解析失败: {result.get('message', '未知错误')}")
                return

            uin = event.get_sender_id()
            name = event.get_sender_name()

            nodes = []
            nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Plain(f"title: {result.get('title', '小红书内容')}")]))
            nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Plain(f"content: {result.get('content', '')}")]))

            yield event.chain_result([Comp.Nodes(nodes=nodes)])

            if result.get('cover'):
                yield event.chain_result([Comp.Image.fromURL(result['cover'])])

            if result.get('images'):
                for img_url in result['images']:
                    yield event.chain_result([Comp.Image.fromURL(img_url)])

            if result.get('videos'):
                for video_url in result['videos']:
                    yield event.chain_result([Comp.Video.fromURL(video_url)])

        except Exception as e:
            logger.error(f"小红书解析失败: {e}")
            yield event.plain_result(f"解析失败: {str(e)}")
