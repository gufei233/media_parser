"""
异步版本的小红书解析器
保持原有解析逻辑，使用 aiohttp 替代 requests
"""
import re
import json
import time
import asyncio
import traceback
from urllib.parse import urlparse
from typing import Optional, Dict

import aiohttp
from astrbot.api import logger


class AsyncXiaohongshuParser:
    """异步小红书解析器"""

    def __init__(self):
        # 配置常量
        self.config = {
            'timeout': 15,
            'max_retries': 3,
            'retry_delay': 1
        }
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"

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

    def extract_images(self, html):
        images = []
        # 尝试所有正则模式
        for pattern in self.patterns['og_image']:
            for match in pattern.finditer(html):
                url = self.clean_url(match.group(1))
                if url and 'http' in url and url not in images:
                    images.append(url)
            if len(images) > 0:
                break
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

    async def fetch_with_retry(self, url):
        """异步请求，带重试"""
        if not self._is_valid_http_url(url):
            raise Exception(f"URL无效: {url}")

        session = await self._get_session()

        for attempt in range(self.config['max_retries'] + 1):
            try:
                headers = {
                    'User-Agent': self.user_agent,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Cache-Control': 'no-cache'
                }

                async with session.get(url, headers=headers, allow_redirects=True) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status}")
                    if resp.content_length and resp.content_length > 8 * 1024 * 1024:
                        raise Exception("响应体过大，已拒绝解析")
                    raw = await resp.read()
                    html = self._decode_html_bytes(raw)
                    final_url = str(resp.url)
                    return html, final_url

            except Exception as e:
                if attempt < self.config['max_retries']:
                    await asyncio.sleep(self.config['retry_delay'] * (attempt + 1))
                else:
                    raise Exception(f"请求失败: {str(e)}")

    async def parse(self, url):
        """解析小红书链接（主入口）"""
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
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
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
