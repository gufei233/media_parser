import requests
import json
import re
import time
import math
import random
from urllib.parse import urlparse, urljoin

class DouyinParseError(Exception):
    def __init__(self, message, code='PARSE_ERROR', original_error=None):
        super().__init__(message)
        self.code = code
        self.original_error = original_error
        self.timestamp = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())

class DouyinParser:
    def __init__(self):
        # 默认配置 (对应 DouyinParser.defaultConfig)
        self.config = {
            'timeout': 15, # 秒
            'retries': 3,
            'retry_delay': 1,
            'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15'
        }
        
    def log(self, level, message, data=None):
        # 简易日志实现
        timestamp = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
        # print(f"[{timestamp}] [{level.upper()}] {message} {json.dumps(data, ensure_ascii=False) if data else ''}")

    def validate_and_clean_url(self, url):
        if not url:
            raise DouyinParseError('URL不能为空', 'INVALID_URL')
        
        # 提取实际链接 (对应 validateAndCleanUrl 中的正则)
        link_patterns = [
            r'(https?://v\.douyin\.com/[a-zA-Z0-9_-]+/?),?',
            r'(https?://(?:www\.)?douyin\.com/[^\s]+)',
            r'(https?://(?:www\.)?iesdouyin\.com/[^\s]+)'
        ]
        
        for pattern in link_patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return url

    def follow_redirects(self, url):
        # 对应 followRedirects 方法
        self.log('debug', '开始处理重定向', {'url': url})
        current_url = url
        redirect_count = 0
        max_redirects = 10
        
        # 模拟 TS 中的手动重定向处理
        headers = {
            'User-Agent': self.config['user_agent'],
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
        }

        while redirect_count < max_redirects:
            try:
                # 使用 HEAD 请求，禁止自动跳转以模拟 manual redirect
                resp = requests.head(current_url, headers=headers, timeout=self.config['timeout'], allow_redirects=False)
                
                if 300 <= resp.status_code < 400 and 'Location' in resp.headers:
                    location = resp.headers['Location']
                    if location:
                        if not location.startswith('http'):
                            current_url = urljoin(current_url, location)
                        else:
                            current_url = location
                        redirect_count += 1
                        self.log('debug', f'重定向 {redirect_count}', {'to': current_url})
                        continue
                
                if 200 <= resp.status_code < 300:
                    return current_url
                
                break
            except Exception as e:
                self.log('warn', '重定向处理失败', {'error': str(e)})
                return url
                
        return current_url

    def detect_live_photo(self, url):
        # 对应 detectLivePhoto 方法
        self.log('debug', '开始Live图检测', {'url': url})
        
        # 步骤1: 处理重定向 (TS 中这里有独立的 followLivePhotoRedirects，逻辑类似但 UA 不同)
        # 为了严格 1:1，这里复刻 followLivePhotoRedirects 的逻辑
        current_url = url
        try:
            for _ in range(5):
                headers = {
                    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
                }
                resp = requests.head(current_url, headers=headers, timeout=self.config['timeout'], allow_redirects=False)
                if 300 <= resp.status_code < 400 and 'Location' in resp.headers:
                    current_url = resp.headers['Location']
                else:
                    break
        except:
            pass

        final_url = current_url
        
        # 提取视频ID (对应 TS 第 583 行: const videoIdMatch = currentUrl.match(/(?:video|slides)\/(\d+)/);)
        # 注意：原代码只匹配 video 和 slides，不匹配 note
        video_id_match = re.search(r'/(?:video|slides)/(\d+)', final_url)
        video_id = video_id_match.group(1) if video_id_match else None

        if not video_id:
            return {'success': False, 'isLivePhoto': False, 'error': '无法获取视频ID'}

        # 步骤3: 调用 API
        api_result = self.call_slides_info_api(video_id, final_url)
        
        # 步骤4: 检查是否包含 Live 图数据
        has_live_photo = api_result['success'] and self.check_if_live_photo(api_result.get('data'))
        
        if not has_live_photo:
            # 逻辑与 TS 一致：如果不包含 Live 数据，且路径是 slides，报错；否则视为普通视频
            is_slides = '/slides/' in final_url
            if is_slides:
                return {'success': False, 'isLivePhoto': True, 'error': 'API调用失败'}
            else:
                return {'success': True, 'isLivePhoto': False}

        # 步骤5: 解析响应
        extracted = self.parse_live_photo_api_response(api_result['data'])
        
        return {
            'success': True,
            'isLivePhoto': True,
            'videoId': video_id,
            'title': extracted['title'],
            'author': extracted['author'],
            'videos': extracted['videos'],
            'rawApiData': api_result['data']
        }

    def call_slides_info_api(self, video_id, referer_url=None):
        # 对应 callSlidesInfoAPI 方法
        try:
            web_id = self.generate_web_id()
            
            # 构造 URL
            api_url = "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/"
            params = {
                'reflow_source': 'reflow_page',
                'web_id': web_id,
                'device_id': web_id,
                'aweme_ids': f"[{video_id}]",
                'request_source': '200',
                'a_bogus': self.generate_a_bogus()
            }
            
            headers = {
                'accept': 'application/json, text/plain, */*',
                'accept-language': 'zh-CN,zh;q=0.9',
                'agw-js-conv': 'str',
                'referer': referer_url or f"https://www.iesdouyin.com/share/slides/{video_id}/",
                'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
            }
            
            resp = requests.get(api_url, params=params, headers=headers, timeout=self.config['timeout'])
            if resp.status_code != 200:
                raise Exception(f"API请求失败: {resp.status_code}")
                
            return {'success': True, 'data': resp.json()}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def fetch_from_html_parser(self, url):
        # 对应 fetchFromHTMLParser 方法 (TS 第 425 行)
        self.log('debug', '开始HTML解析方法', {'url': url})
        
        # 完全复刻原代码的 Headers，包括硬编码的 Cookie
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 13; V2166BA Build/TP1A.220624.014; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/121.0.6167.71 MQQBrowser/6.2 TBS/047205 Mobile Safari/537.36',
            'Cookie': 'ttwid=1%7Chf7h6KY-9QJzBZPLTeMn9TvQ3FjVPiUOGO1TvdN2ypk%7C1727744584%7Ca13c6d514bfb4de5703116a1278df7d0e7ac2331a3ea22dc5a6d5a5416916944;_tea_utm_cache_1243={%22utm_source%22:%22copy%22%2C%22utm_medium%22:%22android%22%2C%22utm_campaign%22:%22client_share%22}',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
        }
        
        resp = requests.get(url, headers=headers, timeout=self.config['timeout'])
        html_content = resp.text
        
        # 复刻字符串截取逻辑
        router_data_start = html_content.find('window._ROUTER_DATA = ')
        if router_data_start == -1:
            raise Exception('未找到 window._ROUTER_DATA')
            
        json_start = router_data_start + 22
        substring = html_content[json_start:]
        json_end = substring.find('}</script>')
        
        if json_end == -1:
            raise Exception('未找到JSON结束标记')
            
        json_string = substring[:json_end+1]
        router_data = json.loads(json_string)
        
        loader_data = router_data.get('loaderData')
        if not loader_data:
            raise Exception('未找到 loaderData')
            
        video_data = None
        content_type = ''
        
        # 复刻 key 遍历逻辑，使用 TS 的 optional chaining 语义
        for key in loader_data.keys():
            val = loader_data[key]
            if val is None: continue # 模拟 TS 的 ?.
            
            if 'video' in key and val.get('videoInfoRes', {}).get('item_list'):
                video_data = val['videoInfoRes']['item_list'][0]
                content_type = 'video'
                break
            if 'note' in key and val.get('videoInfoRes', {}).get('item_list'):
                video_data = val['videoInfoRes']['item_list'][0]
                content_type = 'note'
                break
                
        if not video_data:
            raise Exception('未找到视频或图集数据')
            
        return {
            'item_list': [video_data],
            '_source': 'html_parser',
            '_content_type': content_type
        }

    def format_html_parser_response(self, api_response, original_url):
        # 对应 formatHTMLParserResponse 方法 (TS 第 546 行)
        item = api_response['item_list'][0]
        content_type = api_response['_content_type']
        
        video_url = None
        # 提取视频链接 (仅 video 类型)
        if content_type == 'video' and item.get('video', {}).get('play_addr', {}).get('uri'):
            uri = item['video']['play_addr']['uri']
            video_url = f"https://www.douyin.com/aweme/v1/play/?video_id={uri}&ratio=1040p"
            
        images = []
        # 提取图片 (仅 note 类型)
        if content_type == 'note' and item.get('images'):
            for img in item['images']:
                url_list = img.get('url_list', [])
                if url_list:
                    # 优先 JPEG
                    jpeg_url = next((u for u in url_list if '.jpeg' in u or '.jpg' in u), None)
                    selected = jpeg_url if jpeg_url else url_list[0]
                    images.append(selected)
                    
        # 构造标签
        tags = ""
        if item.get('text_extra'):
            tags = " ".join([f"#{x.get('hashtag_name')}" for x in item['text_extra'] if x.get('hashtag_name')])

        # 构造结果 (TS 第 577 行)
        # 注意：原 TS 代码在这里完全没有提取实况视频的逻辑
        return {
            'title': item.get('desc', '抖音内容'),
            'author': {
                'name': item.get('author', {}).get('nickname', '抖音用户'),
                'avatar': item.get('author', {}).get('avatar_medium', {}).get('url_list', [''])[0],
                'id': item.get('author', {}).get('unique_id') or item.get('author', {}).get('sec_uid')
            },
            'content': item.get('desc', '抖音内容'),
            'description': item.get('desc', '抖音内容'),
            'video': video_url,
            'video_download_url': video_url,
            'original_video_url': original_url,
            'cover': self.select_best_cover_url(item.get('video', {}).get('cover', {}).get('url_list')),
            'stats': {
                'likes': item.get('statistics', {}).get('digg_count', 0),
                'comments': item.get('statistics', {}).get('comment_count', 0),
                'collects': item.get('statistics', {}).get('collect_count', 0),
                'shares': item.get('statistics', {}).get('share_count', 0)
            },
            'images': images if images else None,
            'original_url': original_url,
            'parsed_at': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
            '_raw': item,
            '_source': 'html_parser'
        }

    def format_live_photo_response(self, live_photo_result, original_url, raw_api_data):
        # 对应 formatLivePhotoResponse 方法
        # 提取封面
        cover_url = None
        if raw_api_data and raw_api_data.get('aweme_details'):
            video_cover = raw_api_data['aweme_details'][0].get('video', {}).get('cover', {}).get('url_list')
            cover_url = self.select_best_cover_url(video_cover)
            
        live_photo_video_urls = [v['url'] for v in live_photo_result['videos']]
        
        return {
            'title': live_photo_result.get('title', '抖音实况图片'),
            'author': {
                'name': live_photo_result.get('author', '抖音用户'),
                'avatar': '',
                'id': live_photo_result.get('videoId')
            },
            'content': live_photo_result.get('title', '抖音实况图片'),
            'description': live_photo_result.get('title', '抖音实况图片'),
            'video': live_photo_video_urls[0] if live_photo_video_urls else None,
            'video_download_url': live_photo_video_urls[0] if live_photo_video_urls else None,
            'original_video_url': original_url,
            'cover': cover_url,
            'stats': {'likes': 0, 'comments': 0, 'collects': 0, 'shares': 0}, # TS 代码中这里也是 0
            'tags': '实况图片',
            'original_url': original_url,
            'parsed_at': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
            'videos': live_photo_video_urls,
            'livePhotos': live_photo_result['videos'],
            'isLivePhoto': True,
            '_raw': raw_api_data or live_photo_result,
            '_source': 'live_photo_extractor'
        }

    def parse(self, url):
        # 对应 parse 方法 (TS 第 270 行)
        start_time = time.time()
        
        clean_url = self.validate_and_clean_url(url)
        full_url = self.follow_redirects(clean_url)
        
        # 尝试 LivePhoto 检测
        live_photo_result = self.detect_live_photo(full_url)
        
        if live_photo_result.get('isLivePhoto') and live_photo_result.get('success'):
            return self.format_live_photo_response(live_photo_result, url, live_photo_result.get('rawApiData'))
            
        # 降级到 HTML 解析
        extracted_data = self.fetch_from_html_parser(full_url)
        return self.format_html_parser_response(extracted_data, url)

    # === 辅助函数 ===
    
    def generate_web_id(self):
        return '75' + str(math.floor(random.random() * 100000000000000))

    def generate_a_bogus(self):
        chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        return ''.join(random.choice(chars) for _ in range(64))

    def check_if_live_photo(self, data):
        # 对应 checkIfLivePhoto
        try:
            if not data or not data.get('aweme_details'): return False
            images = data['aweme_details'][0].get('images', [])
            if not images: return False
            # 检查是否有 video 字段
            return any(img.get('video', {}).get('play_addr', {}).get('url_list') for img in images)
        except:
            return False

    def parse_live_photo_api_response(self, data):
        # 对应 parseLivePhotoApiResponse
        videos = []
        try:
            detail = data['aweme_details'][0]
            title = detail.get('desc') or detail.get('preview_title') or ''
            author = detail.get('author', {}).get('nickname', '')
            
            for img in detail.get('images', []):
                video = img.get('video')
                if video and video.get('play_addr', {}).get('url_list'):
                    videos.append({
                        'url': video['play_addr']['url_list'][0],
                        'duration': video.get('duration', 0),
                        'width': video.get('width', 0),
                        'height': video.get('height', 0),
                        'fileSize': video['play_addr'].get('data_size', 0),
                        'fileHash': video['play_addr'].get('file_hash', '')
                    })
            return {'title': title, 'author': author, 'videos': videos}
        except:
            return {'videos': []}

    def select_best_cover_url(self, url_list):
        if not url_list: return None
        jpeg_url = next((u for u in url_list if '.jpeg' in u or '.jpg' in u), None)
        return jpeg_url if jpeg_url else url_list[0]

# 主入口
if __name__ == "__main__":
    import sys
    # 支持直接输入或命令行参数
    try:
        print("请输入抖音链接:")
        # 读取输入，兼容多行粘贴（实际上 input 只能读一行，这里只处理一行）
        url_input = sys.stdin.readline().strip()
        if not url_input:
            url_input = input().strip()
            
        parser = DouyinParser()
        result = parser.parse(url_input)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, indent=2, ensure_ascii=False))