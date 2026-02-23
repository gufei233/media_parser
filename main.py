"""
媒体解析插件主文件 - 完全异步版本
支持解析抖音和小红书链接
"""
import re
import os
import asyncio
import tempfile
import traceback
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

try:
    from .config import MediaParserConfig
    from .debounce import Debouncer
    from .async_dysk import AsyncDouyinDownloader
    from .async_xhs import AsyncXiaohongshuParser
except ImportError:
    from config import MediaParserConfig
    from debounce import Debouncer
    from async_dysk import AsyncDouyinDownloader
    from async_xhs import AsyncXiaohongshuParser


@register("media_parser", "Author", "抖音小红书链接解析插件（异步优化版）", "2.1.2")
class MediaParserPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)

        # 配置管理
        self.cfg = MediaParserConfig(config)

        # 防抖器（使用 lambda 实现动态配置）
        self.debouncer = Debouncer(lambda: self.cfg.debounce_interval)

        # ========== 异步解析器 ==========
        # 小红书解析器
        self.xhs_parser = AsyncXiaohongshuParser()

        # 抖音下载器（每次请求时创建新实例，避免 session 复用问题）

        # 链接匹配正则
        self.dy_patterns = [
            r"https?://v\.douyin\.com/[a-zA-Z0-9_-]+/?",
            r"https?://(?:www\.)?douyin\.com/[^\s]+",
            r"https?://(?:www\.)?iesdouyin\.com/[^\s]+",
        ]
        self.xhs_patterns = [
            r"https?://(?:www\.)?xiaohongshu\.com/[^\s]+",
            r"https?://xhslink\.com/[^\s]+",
        ]

        logger.info("媒体解析插件初始化完成（异步版）")
        logger.info(f"白名单会话数: {len(self.cfg.enabled_sessions)}")
        logger.info(f"防抖时间: {self.cfg.debounce_interval}秒")
        logger.info(f"最大文件大小: {self.cfg.source_max_size}MB")
        logger.info(f"最大视频时长: {self.cfg.source_max_minute}分钟")

    async def terminate(self):
        """插件卸载时清理资源"""
        logger.info("正在清理资源...")
        if self.xhs_parser:
            await self.xhs_parser.close()
        logger.info("资源清理完成")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def parse_media_link(self, event: AstrMessageEvent):
        """媒体链接解析入口"""
        # ========== 白名单过滤 ==========
        umo = event.unified_msg_origin
        if not self.cfg.is_session_enabled(
            umo, event.is_admin(), event.is_at_or_wake_command
        ):
            return

        text = event.message_str

        # ========== 匹配链接 ==========
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

        # 没有匹配到链接
        if not dy_url and not xhs_url:
            return

        # ========== 防抖检查 ==========
        check_url = dy_url or xhs_url
        if self.debouncer.hit_link(umo, check_url):
            logger.warning(f"[链接防抖] 链接 {check_url} 在防抖时间内，跳过解析")
            return

        # ========== 解析处理 ==========
        if dy_url:
            async for result in self.parse_douyin(event, dy_url):
                yield result
            event.stop_event()
        elif xhs_url:
            async for result in self.parse_xiaohongshu(event, xhs_url):
                yield result
            event.stop_event()

    # ==================== 抖音解析（完全异步）====================

    async def parse_douyin(self, event: AstrMessageEvent, url: str):
        """解析抖音链接（异步版本）"""
        try:
            logger.info(f"开始解析抖音链接: {url}")

            # 每次创建新的下载器实例，避免 session 复用问题
            dy_downloader = AsyncDouyinDownloader(
                enable_cf_proxy=self.cfg.enable_cf_proxy,
                cf_proxy_url=self.cfg.cf_proxy_url,
                download_retry_times=self.cfg.download_retry_times,
                download_timeout=self.cfg.download_timeout,
                common_timeout=self.cfg.common_timeout,
                max_size=self.cfg.max_size,
                max_duration=self.cfg.max_duration,
            )

            try:
                # 异步解析
                result = await dy_downloader.get_detail(url)

                if not result:
                    logger.error("解析返回 None，可能被风控")
                    yield event.plain_result(f"解析失败，请直接打开链接查看:\n{url}")
                    return

                uin = event.get_sender_id()
                name = event.get_sender_name()

                # ========== 构造合并转发消息 ==========
                nodes = []

                author = result.get("author") or {}
                info_text = f"id: {result.get('id', '')}\ndesc: {result.get('desc', '')}\ncreate_time: {result.get('create_time', '')}\nnickname: {author.get('nickname', '')}"
                nodes.append(
                    Comp.Node(uin=uin, name=name, content=[Comp.Plain(info_text)])
                )

                music = result.get("music") or {}
                music_text = f"uid: {author.get('uid', '')}\nauthor: {music.get('author', '')}\ntitle: {music.get('title', '')}\nurl: {music.get('url', '')}"
                nodes.append(
                    Comp.Node(uin=uin, name=name, content=[Comp.Plain(music_text)])
                )

                stats = result.get("statistics") or {}
                stats_text = f"digg_count: {stats.get('digg_count', 0)}\ncomment_count: {stats.get('comment_count', 0)}\ncollect_count: {stats.get('collect_count', 0)}\nshare_count: {stats.get('share_count', 0)}"
                nodes.append(
                    Comp.Node(uin=uin, name=name, content=[Comp.Plain(stats_text)])
                )

                type_text = f"type: {result.get('type', '')}"
                duration_str = result.get("duration", "")

                if result.get("type") == "视频" and duration_str:
                    type_text += f"\nduration: {duration_str}"

                nodes.append(
                    Comp.Node(uin=uin, name=name, content=[Comp.Plain(type_text)])
                )

                yield event.chain_result([Comp.Nodes(nodes=nodes)])

                # ========== 检查视频时长限制 ==========
                duration_seconds = result.get("duration_seconds", 0)
                if result.get("type") == "视频" and duration_seconds > 0:
                    if self.cfg.max_duration and duration_seconds > self.cfg.max_duration:
                        max_minutes = self.cfg.max_duration / 60
                        actual_minutes = duration_seconds / 60
                        warning_msg = f"⚠️ 视频时长 {actual_minutes:.1f} 分钟超过限制 {max_minutes:.1f} 分钟，不下载视频"
                        logger.warning(warning_msg)
                        if self.cfg.show_download_fail_tip:
                            yield event.plain_result(warning_msg)
                        return

                # ========== 提取媒体链接 ==========
                downloads = result.get("downloads", [])
                images = []
                video_links = []

                for item in downloads:
                    if isinstance(item, str):
                        images.append(item)
                    elif isinstance(item, dict):
                        if item.get("type") == "video":
                            if item.get("cover"):
                                images.append(item["cover"])
                            video_links.append(item["url"])
                        elif item.get("type") == "live_photo":
                            if item.get("image"):
                                images.append(item["image"])
                            if item.get("video"):
                                video_links.append(item["video"])

                logger.info(f"准备发送媒体: {len(images)}张图片, {len(video_links)}个视频")

                # ========== 异步下载并发送 ==========
                if images or video_links:
                    await self._send_media_async(
                        event, dy_downloader, images, video_links
                    )
                else:
                    logger.warning("没有媒体文件需要发送")

            finally:
                # 关闭下载器
                await dy_downloader.close()

        except Exception as e:
            error_msg = f"抖音解析失败: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            if self.cfg.show_download_fail_tip:
                yield event.plain_result(f"解析失败: {str(e)}")

    async def _send_media_async(self, event, dy_downloader, images, video_links):
        """异步下载并发送媒体文件"""
        logger.info(f"开始下载媒体文件: {len(images)}张图片, {len(video_links)}个视频")

        # 下载所有图片
        for i, img_url in enumerate(images):
            temp_path = None
            try:
                logger.info(f"下载图片 {i+1}/{len(images)}")

                # 创建临时文件
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                temp_path = temp_file.name
                temp_file.close()

                # 异步下载
                success = await dy_downloader.download_video(img_url, temp_path)

                # 延迟发送
                await asyncio.sleep(2)

                if success and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    result = event.make_result()
                    result.chain = [Comp.Image.fromFileSystem(temp_path)]
                    await event.send(result)
                    logger.info(f"图片 {i+1} 发送成功")
                else:
                    if self.cfg.show_download_fail_tip:
                        await event.send(event.plain_result(f"图片下载失败: {img_url}"))
                    logger.warning(f"图片 {i+1} 下载失败")

            except Exception as e:
                logger.error(f"图片 {i+1} 处理异常: {e}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except Exception as e:
                        logger.warning(f"清理临时文件失败: {temp_path}, {e}")

        # 下载所有视频
        for i, video_url in enumerate(video_links):
            temp_path = None
            try:
                logger.info(f"下载视频 {i+1}/{len(video_links)}")

                # 创建临时文件
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                temp_path = temp_file.name
                temp_file.close()

                # 异步下载
                success = await dy_downloader.download_video(video_url, temp_path)

                # 延迟发送
                await asyncio.sleep(3)

                # 视频文件需要额外验证：文件大小必须大于 10KB
                min_video_size = 10 * 1024
                if success and os.path.exists(temp_path):
                    file_size = os.path.getsize(temp_path)
                    if file_size >= min_video_size:
                        result = event.make_result()
                        result.chain = [Comp.Video.fromFileSystem(temp_path)]
                        await event.send(result)
                        logger.info(f"视频 {i+1} 发送成功, 大小: {file_size} bytes")
                    else:
                        logger.warning(f"视频 {i+1} 文件过小 ({file_size} bytes)，可能不完整，跳过发送")
                        if self.cfg.show_download_fail_tip:
                            await event.send(event.plain_result(f"视频下载不完整，请直接访问链接"))
                else:
                    if self.cfg.show_download_fail_tip:
                        await event.send(event.plain_result(f"视频链接: {video_url}"))
                    logger.warning(f"视频 {i+1} 下载失败")

            except Exception as e:
                logger.error(f"视频 {i+1} 处理异常: {e}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except Exception as e:
                        logger.warning(f"清理临时文件失败: {temp_path}, {e}")

    # ==================== 小红书解析（完全异步）====================

    async def parse_xiaohongshu(self, event: AstrMessageEvent, url: str):
        """解析小红书链接（异步版本）"""
        try:
            logger.info(f"开始解析小红书链接: {url}")

            # 异步解析
            result = await self.xhs_parser.parse(url)

            if result.get("error"):
                error_msg = result.get("message", "未知错误")
                logger.error(f"小红书解析失败: {error_msg}")
                if self.cfg.show_download_fail_tip:
                    yield event.plain_result(f"解析失败: {error_msg}")
                return

            uin = event.get_sender_id()
            name = event.get_sender_name()

            # ========== 构造合并转发消息 ==========
            nodes = []
            nodes.append(
                Comp.Node(
                    uin=uin,
                    name=name,
                    content=[
                        Comp.Plain(f"title: {result.get('title', '小红书内容')}")
                    ],
                )
            )
            nodes.append(
                Comp.Node(
                    uin=uin,
                    name=name,
                    content=[Comp.Plain(f"content: {result.get('content', '')}")],
                )
            )

            yield event.chain_result([Comp.Nodes(nodes=nodes)])

            # ========== 发送封面 ==========
            if result.get("cover"):
                yield event.chain_result([Comp.Image.fromURL(result["cover"])])

            # ========== 发送图片 ==========
            if result.get("images"):
                for img_url in result["images"]:
                    yield event.chain_result([Comp.Image.fromURL(img_url)])

            # ========== 发送视频 ==========
            if result.get("videos"):
                for video_url in result["videos"]:
                    yield event.chain_result([Comp.Video.fromURL(video_url)])

        except Exception as e:
            error_msg = f"小红书解析失败: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            if self.cfg.show_download_fail_tip:
                yield event.plain_result(f"解析失败: {str(e)}")

    # ==================== 管理员命令 ====================

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("开启解析")
    async def enable_parser(self, event: AstrMessageEvent):
        """开启当前会话的解析"""
        umo = event.unified_msg_origin
        if umo not in self.cfg.enabled_sessions:
            self.cfg.add_enabled_session(umo)
            yield event.plain_result("✅ 解析已开启")
        else:
            yield event.plain_result("✅ 解析已开启，无需重复开启")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("关闭解析")
    async def disable_parser(self, event: AstrMessageEvent):
        """关闭当前会话的解析"""
        umo = event.unified_msg_origin
        if umo in self.cfg.enabled_sessions:
            self.cfg.remove_enabled_session(umo)
            yield event.plain_result("❌ 解析已关闭")
        elif len(self.cfg.enabled_sessions) == 0:
            yield event.plain_result("ℹ️ 解析白名单为空时，全局开启解析")
        else:
            yield event.plain_result("❌ 解析已关闭，无需重复关闭")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("解析状态")
    async def parser_status(self, event: AstrMessageEvent):
        """查看当前插件状态"""
        umo = event.unified_msg_origin
        is_enabled = self.cfg.is_session_enabled(
            umo, event.is_admin(), event.is_at_or_wake_command
        )

        status_text = f"""📊 媒体解析插件状态（异步版）

🎯 当前会话: {'✅ 已开启' if is_enabled else '❌ 已关闭'}
📋 白名单数量: {len(self.cfg.enabled_sessions)} 个会话
⏱️ 防抖时间: {self.cfg.debounce_interval} 秒
📦 最大文件大小: {self.cfg.source_max_size} MB
⏰ 最大视频时长: {self.cfg.source_max_minute} 分钟
🔄 下载重试次数: {self.cfg.download_retry_times} 次
☁️ CF 代理: {'✅ 已启用' if self.cfg.enable_cf_proxy else '❌ 未启用'}
"""
        yield event.plain_result(status_text)
