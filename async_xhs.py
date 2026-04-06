"""
异步版本的小红书解析器
保持原有解析逻辑，使用 aiohttp 替代 requests
"""
import re
import json
import time
import asyncio
import traceback
from urllib.parse import urlparse, parse_qs
from typing import Optional, Dict

import aiohttp
from astrbot.api import logger

from .xhs_api_client import XhsApiClient


class AsyncXiaohongshuParser:
    """异步小红书解析器"""

    def __init__(self):
        # 配置常量
        self.config = {
            'timeout': 15,
            'max_retries': 3,
            'retry_delay': 1
        }
        self.user_agent_desktop = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
        self.user_agent_mobile = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        # 默认使用 mobile UA（小红书对移动端分享链接不做 xsec Cookie 校验）
        self.user_agent = self.user_agent_mobile

        # 正则表达式模式
        self.patterns = {
            'image_url': re.compile(r'https://sns-[a-z0-9-]+\.xhscdn\.com/[^"\'\s]+'),
            'video_url': [
                re.compile(r'https://sns-video[^"\'\s]+\.mp4'),
                re.compile(r'https://v\.xhscdn\.com[^"\'\s]+'),
                re.compile(r'"masterUrl":"([^"]+)"'),
                re.compile(r'"url":"(https://v\.xhscdn\.com[^"]+)"')
            ],
            'title': [
                re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"', re.I),
                re.compile(r'<title[^>]*>(.*?)<\/title>', re.I),
                re.compile(r'"title":"([^"]+)"', re.I)
            ],
            'author': [
                re.compile(r'"nickname":"([^"]+)"', re.I),
                re.compile(r'"nickName":"([^"]+)"', re.I),
                re.compile(r'<meta\s+name="author"\s+content="([^"]+)"', re.I)
            ],
            'content': [
                re.compile(r'"desc":"([^"]+)"', re.I),
                re.compile(r'"content":"([^"]+)"', re.I),
                re.compile(r'"text":"([^"]+)"', re.I)
            ],
            'note_id': [
                re.compile(r'/item/([a-zA-Z0-9]+)'),
                re.compile(r'"noteId":"([a-zA-Z0-9]+)"')
            ],
            'og_image': [
                re.compile(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\'][^>]*>', re.I),
                re.compile(r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\'][^>]*>', re.I),
                re.compile(r'<meta[^>]*og:image[^>]*content=["\']([^"\']+)["\'][^>]*>', re.I),
                re.compile(r'content=["\']([^"\']*xhscdn[^"\']*)["\']', re.I)
            ]
        }

        # Session 延迟创建
        self._session: Optional[aiohttp.ClientSession] = None
        # API 客户端（无水印，主路径）
        self._api_client: Optional[XhsApiClient] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config['timeout'])
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """关闭 session"""
        if self._session and not self._session.closed:
            await self._session.close()
        if self._api_client:
            await self._api_client.close()
            self._api_client = None

    # ==================== 工具函数 ====================

    def clean_text(self, text):
        if not text:
            return ""
        text = re.sub(r'\s*-\s*小红书', '', text)
        text = text.replace(r'\n', ' ').replace(r'&[a-z]+;', ' ')
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def clean_url(self, url):
        if not url:
            return ''
        return (url.replace(r'\u002F', '/')
                   .replace(r'\u0026', '&')
                   .replace(r'\u003D', '=')
                   .replace(r'\u003F', '?')
                   .replace(r'\u003A', ':')
                   .replace(r'\"', '"')
                   .strip('"'))

    @staticmethod
    def _is_valid_http_url(url: str) -> bool:
        if not isinstance(url, str) or not url:
            return False
        try:
            parsed = urlparse(url.strip())
        except Exception:
            return False
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _decode_html_bytes(raw: bytes) -> str:
        if not raw:
            return ""
        for enc in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", errors="replace")

    # ==================== 内容提取函数 ====================

    def extract_title(self, html):
        for pattern in self.patterns['title']:
            match = pattern.search(html)
            if match and match.group(1):
                title = self.clean_text(match.group(1))
                if title and title != '小红书':
                    return title
        return '小红书内容'

    def extract_author(self, html):
        for pattern in self.patterns['author']:
            match = pattern.search(html)
            if match and match.group(1):
                author = self.clean_text(match.group(1))
                if author:
                    return author
        return '未知作者'

    def extract_content(self, html):
        for pattern in self.patterns['content']:
            match = pattern.search(html)
            if match and match.group(1):
                content = match.group(1).replace(r'\n', '\n').replace(r'\t', '\t').replace(r'\"', '"')
                if content:
                    return content
        return ''

    def extract_note_id(self, html, url):
        # 先从URL中提取
        for pattern in self.patterns['note_id']:
            match = pattern.search(url)
            if match and match.group(1):
                return match.group(1)
        # 再从HTML中提取
        for pattern in self.patterns['note_id']:
            match = pattern.search(html)
            if match and match.group(1):
                return match.group(1)
        return ''

    @staticmethod
    def _extract_nowatermark_url(url: str) -> str:
        """从移动端 CDN URL 中提取图片 ID，构造无水印 URL。

        移动端格式: http://sns-webpic-qc.xhscdn.com/DATE/SIGN/IMAGE_ID!suffix
        无水印格式: https://sns-img-qc.xhscdn.com/IMAGE_ID
        """
        # 去掉 !suffix
        clean = url.split('!')[0] if '!' in url else url
        # 提取最后一段路径作为 image_id（可能带子目录如 notes_pre_post/ID）
        try:
            from urllib.parse import urlparse as _urlparse
            path = _urlparse(clean).path
            # 路径格式: /DATE/SIGN/IMAGE_ID  或  /DATE/SIGN/notes_pre_post/IMAGE_ID
            parts = path.strip('/').split('/')
            if len(parts) >= 3:
                # 取最后1-2段（可能有子目录）
                image_path = '/'.join(parts[2:])  # 跳过 DATE 和 SIGN
                return f"https://sns-img-qc.xhscdn.com/{image_path}"
        except Exception:
            pass
        return url

    def extract_images(self, html):
        images = []
        # 方式1：从 og:image meta 标签提取（桌面端 SSR 页面）
        for pattern in self.patterns['og_image']:
            for match in pattern.finditer(html):
                url = self.clean_url(match.group(1))
                if url and 'http' in url and url not in images:
                    images.append(url)
            if len(images) > 0:
                break

        # 方式2：从移动端轮播图结构提取（onix-carousel-item 内的 <img>）
        # 这些才是当前笔记的图片，而非页面底部推荐流的图片
        if not images:
            carousel_imgs = re.findall(
                r'class="onix-carousel-item"[^>]*>.*?<img[^>]*src=["\']([^"\'\s]+)["\']',
                html, re.DOTALL
            )
            for url in carousel_imgs:
                # 转换为无水印 URL
                nowm_url = self._extract_nowatermark_url(url)
                if nowm_url not in images:
                    images.append(nowm_url)

        return images

    def extract_videos(self, html):
        video_urls = set()
        for pattern in self.patterns['video_url']:
            for match in pattern.finditer(html):
                raw_url = match.group(1) if match.lastindex and match.lastindex >= 1 else match.group(0)
                url = self.clean_url(raw_url)
                if url and 'http' in url and ('.mp4' in url or 'xhscdn' in url):
                    # 优先选择无水印版本
                    if '_259.mp4' not in url:
                        video_urls.add(url)
        return list(video_urls)

    # ==================== 深度 JSON 分析逻辑 ====================

    def extract_all_json_data(self, html):
        result = {
            'scriptJsonData': [],
            'livePhotoData': {
                'videos': [],
                'wbDftImages': [],
                'wbPrvImages': []
            }
        }

        # 提取 script 中的 JSON 数据
        script_matches = re.finditer(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)

        for script_match in script_matches:
            content = re.sub(r'<script[^>]*>', '', script_match.group(0))
            content = re.sub(r'</script>', '', content)

            # 查找可能的 JSON 对象
            json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
            json_matches = re.finditer(json_pattern, content)

            for json_match in json_matches:
                json_str = json_match.group(0)
                if len(json_str) > 50:
                    try:
                        parsed = json.loads(json_str)

                        # 检查 Live 图数据
                        if 'imageScene' in parsed or 'h264' in parsed or 'h265' in parsed:
                            if 'h264' in parsed and isinstance(parsed['h264'], list) and len(parsed['h264']) > 0:
                                video_data = parsed['h264'][0]
                                if 'masterUrl' in video_data:
                                    result['livePhotoData']['videos'].append({
                                        'url': video_data['masterUrl'],
                                        'backupUrls': video_data.get('backupUrls', []),
                                        'jsonIndex': 0
                                    })
                            elif 'imageScene' in parsed and 'url' in parsed:
                                if parsed['imageScene'] == 'WB_DFT':
                                    result['livePhotoData']['wbDftImages'].append({
                                        'url': parsed['url'],
                                        'imageScene': 'WB_DFT',
                                        'jsonIndex': 0
                                    })
                                elif parsed['imageScene'] == 'WB_PRV':
                                    result['livePhotoData']['wbPrvImages'].append({
                                        'url': parsed['url'],
                                        'imageScene': 'WB_PRV',
                                        'jsonIndex': 0
                                    })

                        # 保存所有 JSON 对象
                        str_dump = json.dumps(parsed)
                        if any(k in str_dump for k in ['video', 'image', 'title', 'WB_']):
                            result['scriptJsonData'].append({'data': parsed})

                    except Exception:
                        pass
        return result

    def analyze_live_photo_groups(self, live_photo_data):
        videos = live_photo_data['videos']
        wb_dft = live_photo_data['wbDftImages']
        wb_prv = live_photo_data['wbPrvImages']

        groups = []
        max_len = max(len(videos), len(wb_dft), len(wb_prv))

        for i in range(max_len):
            group = {}
            if i < len(wb_prv):
                group['wbPrv'] = wb_prv[i]
            if i < len(wb_dft):
                group['wbDft'] = wb_dft[i]
            if i < len(videos):
                group['video'] = videos[i]
                group['videos'] = [videos[i]]

            if group:
                groups.append(group)

        return groups

    def analyze_media_structure(self, extracted_data):
        live_data = extracted_data['livePhotoData']
        script_json = extracted_data['scriptJsonData']

        regular_images = [item for item in script_json if item['data'].get('livePhoto') is False]
        live_groups = self.analyze_live_photo_groups(live_data)

        return {
            'regularImages': len(regular_images),
            'livePhotoGroups': len(live_groups),
            'totalGroups': len(regular_images) + len(live_groups),
            'liveGroups': live_groups,
            'regularImageDetails': regular_images
        }

    def extract_type_from_url(self, url):
        try:
            parsed = urlparse(url)
            query = parsed.query
            if 'type=' in query:
                match = re.search(r'type=([^&]+)', query)
                return match.group(1) if match else None
            return None
        except Exception:
            return None

    def has_live_photo_data(self, html):
        matches = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', html)
        for json_str in matches:
            if len(json_str) > 50:
                try:
                    parsed = json.loads(json_str)
                    if 'h264' in parsed and isinstance(parsed['h264'], list) and len(parsed['h264']) > 0:
                        return True
                except Exception:
                    pass
        return False

    def determine_note_type(self, final_url, html):
        type_param = self.extract_type_from_url(final_url)

        if type_param == 'video':
            return {'contentType': 'video', 'isLivePhoto': False}

        if type_param == 'normal':
            has_live = self.has_live_photo_data(html)
            if has_live:
                return {'contentType': 'image', 'isLivePhoto': True}
            else:
                return {'contentType': 'image', 'isLivePhoto': False}

        # 回退方案
        has_live = self.has_live_photo_data(html)
        if has_live:
            return {'contentType': 'image', 'isLivePhoto': True}
        else:
            return {'contentType': 'image', 'isLivePhoto': False}

    # ==================== 主流程 ====================

    def _build_headers(self):
        """构建模拟浏览器的请求头"""
        return {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Upgrade-Insecure-Requests': '1',
        }

    @staticmethod
    def _is_404_response(final_url, html) -> bool:
        """检测是否落入 404 页面"""
        return '/404' in final_url or 'errorCode=' in final_url

    async def resolve_short_link(self, url: str) -> tuple[str | None, str | None, str | None]:
        """解析短链，提取 note_id 和 xsec_token。

        返回 (note_id, xsec_token, final_url)。
        使用移动端 UA 跟随重定向获得最终 URL。
        """
        session = await self._get_session()
        headers = self._build_headers()
        try:
            async with session.get(url, headers=headers, allow_redirects=True) as resp:
                final_url = str(resp.url)
        except Exception as e:
            logger.warning(f"解析短链失败: {e}")
            return None, None, None

        # 从 URL 提取 note_id
        note_id = None
        for pattern in self.patterns['note_id']:
            m = pattern.search(final_url)
            if m:
                note_id = m.group(1)
                break

        # 提取 xsec_token
        xsec_token = None
        parsed = urlparse(final_url)
        qs = parse_qs(parsed.query)
        if 'xsec_token' in qs:
            xsec_token = qs['xsec_token'][0]

        return note_id, xsec_token, final_url

    async def _parse_via_api(self, note_id: str, xsec_token: str) -> dict | None:
        """通过 edith API 获取笔记详情（无水印），返回解析结果 dict 或 None"""
        try:
            if not self._api_client:
                self._api_client = XhsApiClient()
            await self._api_client.initialize()

            res = await self._api_client.get_note_detail(note_id, xsec_token)
            if not res or "data" not in res:
                logger.warning(f"API 返回无数据: {res}")
                return None

            items = res.get("data", {}).get("items", [])
            if not items:
                logger.warning("API 返回空 items")
                return None

            note_card = items[0].get("note_card", {})
            if not note_card:
                return None

            return self._build_result_from_api(note_card, note_id)

        except Exception as e:
            logger.warning(f"API 方式解析失败: {e}")
            logger.debug(traceback.format_exc())
            return None

    def _build_result_from_api(self, note_card: dict, note_id: str) -> dict:
        """从 API 返回的 note_card 构建标准结果"""
        # 基础字段
        user = note_card.get("user", {})
        title = note_card.get("title", "小红书内容")
        desc = note_card.get("desc", "")
        note_type = note_card.get("type", "normal")  # "normal" or "video"

        result = {
            "title": title,
            "author": {
                "name": user.get("nickname", "未知作者"),
                "id": user.get("user_id", ""),
                "avatar": user.get("avatar", ""),
            },
            "content": desc,
            "noteId": note_id,
            "originalUrl": f"https://www.xiaohongshu.com/explore/{note_id}",
            "images": [],
            "videos": [],
            "cover": None,
            "contentType": "video" if note_type == "video" else "image",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "source": "api",
        }

        # 图片提取（无水印）
        image_list = note_card.get("image_list", [])
        for img in image_list:
            # info_list 包含多种格式，取第一个可用的
            info_list = img.get("info_list", [])
            url = img.get("url_default", "") or img.get("url", "")
            if info_list:
                # 优先选 jpg/webp 大图
                for info in info_list:
                    if info.get("url"):
                        url = info["url"]
                        break
            if url:
                if not url.startswith("http"):
                    url = "https://sns-img-qc.xhscdn.com/" + url
                result["images"].append(url)

        # 视频提取
        video_info = note_card.get("video", {})
        if video_info:
            # consumer 结构下有 origin_video_key
            consumer = video_info.get("consumer", {})
            origin_key = consumer.get("origin_video_key", "")
            if origin_key:
                result["videos"].append(f"https://sns-video-bd.xhscdn.com/{origin_key}")

            # media 结构
            media = video_info.get("media", {})
            stream = media.get("stream", {})
            for quality in ("h264", "h265", "av1"):
                streams = stream.get(quality, [])
                for s in streams:
                    master_url = s.get("master_url", "")
                    if master_url:
                        result["videos"].append(master_url)
                        break
                if result["videos"]:
                    break

        if result["videos"]:
            result["video"] = result["videos"][0]

        # 封面
        if result["contentType"] == "video" and result["images"]:
            result["cover"] = result["images"][0]
            result["images"] = []
        elif result["images"]:
            result["cover"] = result["images"][0]

        # Live Photo 处理
        if image_list:
            live_videos = []
            for img in image_list:
                stream = img.get("stream", {})
                for quality in ("h264", "h265"):
                    vlist = stream.get(quality, [])
                    if vlist and isinstance(vlist, list) and len(vlist) > 0:
                        master = vlist[0].get("master_url", "")
                        if master:
                            live_videos.append(master)
                        break
            if live_videos and result["contentType"] != "video":
                result["isLivePhoto"] = True
                result["isGroupedContent"] = True
                result["videos"] = live_videos
                result["video"] = live_videos[0]

        return result

    async def fetch_with_retry(self, url):
        """异步请求，带重试。
        使用移动端 UA 绕过小红书桌面端的 xsec Cookie 校验机制。
        小红书对移动端分享链接（app_platform=ios）使用移动端 UA 时不做额外校验。
        """
        if not self._is_valid_http_url(url):
            raise Exception(f"URL无效: {url}")

        session = await self._get_session()
        headers = self._build_headers()

        for attempt in range(self.config['max_retries'] + 1):
            try:
                async with session.get(url, headers=headers, allow_redirects=True) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status}")
                    if resp.content_length and resp.content_length > 8 * 1024 * 1024:
                        raise Exception("响应体过大，已拒绝解析")
                    raw = await resp.read()
                    html = self._decode_html_bytes(raw)
                    final_url = str(resp.url)

                    # 检测是否被重定向到 404 页面
                    if self._is_404_response(final_url, html):
                        raise Exception("页面返回 404，笔记可能不存在或已被删除")

                    return html, final_url

            except Exception as e:
                if attempt < self.config['max_retries']:
                    logger.warning(f"小红书请求第 {attempt+1} 次失败: {e}，重试中...")
                    await asyncio.sleep(self.config['retry_delay'] * (attempt + 1))
                else:
                    raise Exception(f"请求失败: {str(e)}")

    async def parse(self, url):
        """解析小红书链接（主入口）

        策略：使用移动端 HTML 解析 + CDN URL 重写去除水印。
        API 方式因 JSVMP 签名机制更新暂不可用，保留代码待后续修复。
        """
        try:
            return await self._parse_via_html(url)
        except Exception as e:
            logger.error(f"小红书解析异常: {e}")
            logger.error(traceback.format_exc())
            return {'error': True, 'message': str(e)}

    async def _parse_via_html(self, url):
        """通过移动端 HTML 解析（fallback，有水印）"""
        try:
            html, final_url = await self.fetch_with_retry(url)

            if 'internal error' in html or '验证码' in html or 'captcha' in html:
                return {'error': True, 'message': '页面返回错误或需要验证码'}

            # 基础提取
            result = {
                'title': self.extract_title(html),
                'author': {
                    'name': self.extract_author(html),
                    'id': self.extract_note_id(html, final_url),
                    'avatar': ''
                },
                'content': self.extract_content(html),
                'noteId': self.extract_note_id(html, final_url),
                'originalUrl': final_url,
                'images': self.extract_images(html),
                'videos': self.extract_videos(html),
                'cover': None,
                'contentType': 'text',
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
                'source': 'html',
            }

            if result['videos']:
                result['video'] = result['videos'][0]

            # 智能分析
            extracted_data = self.extract_all_json_data(html)
            media_analysis = self.analyze_media_structure(extracted_data)
            result['mediaAnalysis'] = media_analysis

            # 确定类型
            note_type_result = self.determine_note_type(final_url, html)
            result['contentType'] = note_type_result['contentType']
            is_live_photo = note_type_result['isLivePhoto']

            # 逻辑判断
            all_videos = result['videos']

            if len(all_videos) > 0 or media_analysis['livePhotoGroups'] > 0:
                if result['contentType'] == 'video' and not is_live_photo:
                    if all_videos:
                        result['video'] = all_videos[0]
                        result['videos'] = [all_videos[0]]

                    if len(result['images']) > 0:
                        cover_image = result['images'][0]
                        result['coverImage'] = cover_image
                        result['cover'] = cover_image
                        result['images'] = []
                        result['originalImageCount'] = len(result['images'])
                else:
                    # Live图判断逻辑
                    is_real_live = False
                    if result['contentType'] == 'video' and not is_live_photo:
                        is_real_live = False
                    else:
                        is_real_live = (
                            media_analysis['livePhotoGroups'] > 1 or
                            (media_analysis['livePhotoGroups'] > 0 and media_analysis['regularImages'] > 0 and result['contentType'] != 'video') or
                            (media_analysis['livePhotoGroups'] > 0 and result['contentType'] == 'image') or
                            is_live_photo
                        )

                    if is_real_live:
                        live_photo_videos = [v['url'] for v in extracted_data['livePhotoData']['videos']]
                        live_photo_videos = [self.clean_url(v) for v in live_photo_videos if v]

                        result['videos'] = live_photo_videos
                        result['video'] = live_photo_videos[0] if live_photo_videos else None
                        result['isLivePhoto'] = True
                        result['isGroupedContent'] = True

                        if result['images']:
                            result['cover'] = result['images'][0]
                    else:
                        if all_videos:
                            result['video'] = all_videos[0]
                            if result['contentType'] == 'video':
                                result['videos'] = [all_videos[0]]
                            else:
                                result['videos'] = all_videos

                        if result['contentType'] == 'video' and result['images']:
                            cover_image = result['images'][0]
                            result['coverImage'] = cover_image
                            result['cover'] = cover_image
                            result['images'] = []

            # 处理图文笔记封面
            if result['contentType'] == 'image' and not result.get('isLivePhoto') and result['images']:
                result['cover'] = result['images'][0]

            if result['contentType'] == 'image' and not result['images'] and not result.get('videos'):
                result['contentType'] = 'text'

            return result

        except Exception as e:
            logger.error(f"小红书解析异常: {e}")
            logger.error(traceback.format_exc())
            return {'error': True, 'message': str(e)}


# ========== 测试函数 ==========
async def test_parser():
    """测试异步解析器"""
    parser = AsyncXiaohongshuParser()

    test_url = input("请输入小红书链接: ")

    try:
        result = await parser.parse(test_url)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        await parser.close()


if __name__ == "__main__":
    asyncio.run(test_parser())
