import requests
import re
import json
import time
import random
import string
from urllib.parse import urlencode, quote
from datetime import datetime
# 依赖库: pip install gmssl
from gmssl import func, sm3

# ==========================================
# 1. 常量定义
# ==========================================
USERAGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ==========================================
# 2. ABogus 算法 (保持不变)
# ==========================================
class ABogus:
    __filter = re.compile(r"%([0-9A-F]{2})")
    __arguments = [0, 1, 14]
    __ua_key = "\u0000\u0001\u000e"
    __end_string = "cus"
    __browser = "1536|742|1536|864|0|0|0|0|1536|864|1536|864|1536|742|24|24|Win32"
    __reg = [
        1937774191, 1226093241, 388252375, 3666478592,
        2842636476, 372324522, 3817729613, 2969243214,
    ]
    __str = {
        "s0": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=",
        "s1": "Dkdpgh4ZKsQB80/Mfvw36XI1R25+WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe=",
        "s2": "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe=",
        "s3": "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe",
        "s4": "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe",
    }

    def __init__(self, user_agent: str = USERAGENT):
        self.chunk = []
        self.size = 0
        self.reg = self.__reg[:]
        self.ua_code = self.generate_ua_code(user_agent)
        self.browser = self.__browser
        self.browser_len = len(self.browser)
        self.browser_code = self.char_code_at(self.browser)

    @classmethod
    def list_1(cls, random_num=None, a=170, b=85, c=45) -> list:
        return cls.random_list(random_num, a, b, 1, 2, 5, c & a)

    @classmethod
    def list_2(cls, random_num=None, a=170, b=85) -> list:
        return cls.random_list(random_num, a, b, 1, 0, 0, 0)

    @classmethod
    def list_3(cls, random_num=None, a=170, b=85) -> list:
        return cls.random_list(random_num, a, b, 1, 0, 5, 0)

    @staticmethod
    def random_list(a: float = None, b=170, c=85, d=0, e=0, f=0, g=0) -> list:
        r = a or (random.random() * 10000)
        v = [r, int(r) & 255, int(r) >> 8]
        s = v[1] & b | d; v.append(s)
        s = v[1] & c | e; v.append(s)
        s = v[2] & b | f; v.append(s)
        s = v[2] & c | g; v.append(s)
        return v[-4:]

    @staticmethod
    def from_char_code(*args):
        return "".join(chr(code) for code in args)

    @classmethod
    def generate_string_1(cls, random_num_1=None, random_num_2=None, random_num_3=None):
        return (cls.from_char_code(*cls.list_1(random_num_1)) +
                cls.from_char_code(*cls.list_2(random_num_2)) +
                cls.from_char_code(*cls.list_3(random_num_3)))

    def generate_string_2(self, url_params: str, method="GET", start_time=0, end_time=0) -> str:
        a = self.generate_string_2_list(url_params, method, start_time, end_time)
        e = self.end_check_num(a)
        a.extend(self.browser_code)
        a.append(e)
        return self.rc4_encrypt(self.from_char_code(*a), "y")

    def generate_ua_code(self, user_agent: str) -> list:
        u = self.rc4_encrypt(user_agent, self.__ua_key)
        u = self.generate_result(u, "s3")
        return self.sum(u)

    def generate_string_2_list(self, url_params: str, method="GET", start_time=0, end_time=0) -> list:
        start_time = start_time or int(time.time() * 1000)
        end_time = end_time or (start_time + random.randint(4, 8))
        params_array = self.generate_params_code(url_params)
        method_array = self.generate_method_code(method)
        return self.list_4(
            (end_time >> 24) & 255, params_array[21], self.ua_code[23],
            (end_time >> 16) & 255, params_array[22], self.ua_code[24],
            (end_time >> 8) & 255, (end_time >> 0) & 255,
            (start_time >> 24) & 255, (start_time >> 16) & 255,
            (start_time >> 8) & 255, (start_time >> 0) & 255,
            method_array[21], method_array[22],
            int(end_time / 256 / 256 / 256 / 256) >> 0,
            int(start_time / 256 / 256 / 256 / 256) >> 0,
            self.browser_len,
        )

    def compress(self, a):
        f = self.generate_f(a)
        i = self.reg[:]
        for o in range(64):
            c = self.de(i[0], 12) + i[4] + self.de(self.pe(o), o)
            c = c & 0xFFFFFFFF
            c = self.de(c, 7)
            s = (c ^ self.de(i[0], 12)) & 0xFFFFFFFF
            u = self.he(o, i[0], i[1], i[2])
            u = (u + i[3] + s + f[o + 68]) & 0xFFFFFFFF
            b = self.ve(o, i[4], i[5], i[6])
            b = (b + i[7] + c + f[o]) & 0xFFFFFFFF
            i[3] = i[2]; i[2] = self.de(i[1], 9); i[1] = i[0]; i[0] = u
            i[7] = i[6]; i[6] = self.de(i[5], 19); i[5] = i[4]
            i[4] = (b ^ self.de(b, 9) ^ self.de(b, 17)) & 0xFFFFFFFF
        for l in range(8): self.reg[l] = (self.reg[l] ^ i[l]) & 0xFFFFFFFF

    @classmethod
    def generate_f(cls, e):
        r = [0] * 132
        for t in range(16):
            r[t] = ((e[4 * t] << 24) | (e[4 * t + 1] << 16) | (e[4 * t + 2] << 8) | e[4 * t + 3]) & 0xFFFFFFFF
        for n in range(16, 68):
            a = r[n - 16] ^ r[n - 9] ^ cls.de(r[n - 3], 15)
            a = a ^ cls.de(a, 15) ^ cls.de(a, 23)
            r[n] = (a ^ cls.de(r[n - 13], 7) ^ r[n - 6]) & 0xFFFFFFFF
        for n in range(68, 132): r[n] = (r[n - 68] ^ r[n - 64]) & 0xFFFFFFFF
        return r

    @staticmethod
    def pad_array(arr, length=60):
        while len(arr) < length: arr.append(0)
        return arr

    def fill(self, length=60):
        size = 8 * self.size
        self.chunk.append(128)
        self.chunk = self.pad_array(self.chunk, length)
        for i in range(4): self.chunk.append((size >> 8 * (3 - i)) & 255)

    @staticmethod
    def list_4(a, b, c, d, e, f, g, h, i, j, k, m, n, o, p, q, r) -> list:
        return [44, a, 0, 0, 0, 0, 24, b, n, 0, c, d, 0, 0, 0, 1, 0, 239, e, o, f, g, 0, 0, 0, 0, h, 0, 0, 14, i, j, 0, k, m, 3, p, 1, q, 1, r, 0, 0, 0]

    @staticmethod
    def end_check_num(a: list):
        r = 0
        for i in a: r ^= i
        return r

    @classmethod
    def decode_string(cls, url_string): return cls.__filter.sub(cls.replace_func, url_string)

    @staticmethod
    def replace_func(match): return chr(int(match.group(1), 16))

    @staticmethod
    def de(e, r): r %= 32; return ((e << r) & 0xFFFFFFFF) | (e >> (32 - r))

    @staticmethod
    def pe(e): return 2043430169 if 0 <= e < 16 else 2055708042

    @staticmethod
    def he(e, r, t, n):
        if 0 <= e < 16: return (r ^ t ^ n) & 0xFFFFFFFF
        elif 16 <= e < 64: return (r & t | r & n | t & n) & 0xFFFFFFFF
        raise ValueError

    @staticmethod
    def ve(e, r, t, n):
        if 0 <= e < 16: return (r ^ t ^ n) & 0xFFFFFFFF
        elif 16 <= e < 64: return (r & t | ~r & n) & 0xFFFFFFFF
        raise ValueError

    @staticmethod
    def split_array(arr, chunk_size=64):
        return [arr[i : i + chunk_size] for i in range(0, len(arr), chunk_size)]

    @staticmethod
    def char_code_at(s): return [ord(char) for char in s]

    def write(self, e):
        self.size = len(e)
        if isinstance(e, str): e = self.char_code_at(self.decode_string(e))
        if len(e) <= 64: self.chunk = e
        else:
            chunks = self.split_array(e, 64)
            for i in chunks[:-1]: self.compress(i)
            self.chunk = chunks[-1]

    def reset(self): self.chunk = []; self.size = 0; self.reg = self.__reg[:]

    def sum(self, e, length=60):
        self.reset(); self.write(e); self.fill(length); self.compress(self.chunk)
        return self.reg_to_array(self.reg)

    @staticmethod
    def reg_to_array(a):
        o = [0] * 32
        for i in range(8):
            c = a[i]
            o[4 * i + 3] = 255 & c; c >>= 8; o[4 * i + 2] = 255 & c; c >>= 8
            o[4 * i + 1] = 255 & c; c >>= 8; o[4 * i] = 255 & c
        return o

    @classmethod
    def generate_result(cls, s, e="s4"):
        r = []
        for i in range(0, len(s), 3):
            if i + 2 < len(s): n = (ord(s[i]) << 16) | (ord(s[i + 1]) << 8) | ord(s[i + 2])
            elif i + 1 < len(s): n = (ord(s[i]) << 16) | (ord(s[i + 1]) << 8)
            else: n = ord(s[i]) << 16
            for j, k in zip(range(18, -1, -6), (0xFC0000, 0x03F000, 0x0FC0, 0x3F)):
                if j == 6 and i + 1 >= len(s): break
                if j == 0 and i + 2 >= len(s): break
                r.append(cls.__str[e][(n & k) >> j])
        r.append("=" * ((4 - len(r) % 4) % 4))
        return "".join(r)

    def generate_method_code(self, method: str = "GET") -> list:
        return self.sm3_to_array(self.sm3_to_array(method + self.__end_string))

    def generate_params_code(self, params: str) -> list:
        return self.sm3_to_array(self.sm3_to_array(params + self.__end_string))

    @classmethod
    def sm3_to_array(cls, data: str | bytes) -> list:
        b = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        h = sm3.sm3_hash(func.bytes_to_list(b))
        return [int(h[i : i + 2], 16) for i in range(0, len(h), 2)]

    @staticmethod
    def rc4_encrypt(plaintext, key):
        s = list(range(256)); j = 0
        for i in range(256):
            j = (j + s[i] + ord(key[i % len(key)])) % 256
            s[i], s[j] = s[j], s[i]
        i = 0; j = 0; cipher = []
        for k in range(len(plaintext)):
            i = (i + 1) % 256; j = (j + s[i]) % 256
            s[i], s[j] = s[j], s[i]
            t = (s[i] + s[j]) % 256
            cipher.append(chr(s[t] ^ ord(plaintext[k])))
        return "".join(cipher)

    def get_value(self, url_params: dict | str, method="GET"):
        string_1 = self.generate_string_1()
        string_2 = self.generate_string_2(
            urlencode(url_params, quote_via=quote) if isinstance(url_params, dict) else url_params,
            method
        )
        return self.generate_result(string_1 + string_2, "s4")

# ==========================================
# 3. 数据提取器 (保持不变)
# ==========================================
class Extractor:
    @staticmethod
    def safe_extract(data, path, default=None):
        keys = path.split('.')
        current = data
        try:
            for key in keys:
                if '[' in key and ']' in key:
                    k, idx = key[:-1].split('[')
                    current = current.get(k, [])[int(idx)]
                else:
                    current = current.get(key)
                if current is None: return default
            return current
        except Exception:
            return default

    @staticmethod
    def time_conversion(time_: int) -> str:
        second = time_ // 1000
        return f"{second // 3600:0>2d}:{second % 3600 // 60:0>2d}:{second % 3600 % 60:0>2d}"

    def extract_data(self, data_dict: dict):
        result = {
            "id": data_dict.get("aweme_id"),
            "desc": data_dict.get("desc", ""),
            "create_time": datetime.fromtimestamp(data_dict.get("create_time", 0)).strftime("%Y-%m-%d %H:%M:%S"),
            "author": {
                "nickname": self.safe_extract(data_dict, "author.nickname"),
                "uid": self.safe_extract(data_dict, "author.uid"),
                "sec_uid": self.safe_extract(data_dict, "author.sec_uid"),
                "avatar": self.safe_extract(data_dict, "author.avatar_thumb.url_list[0]")
            },
            "statistics": {
                "digg_count": self.safe_extract(data_dict, "statistics.digg_count"),
                "comment_count": self.safe_extract(data_dict, "statistics.comment_count"),
                "collect_count": self.safe_extract(data_dict, "statistics.collect_count"),
                "share_count": self.safe_extract(data_dict, "statistics.share_count"),
            },
            "music": {
                "author": self.safe_extract(data_dict, "music.author"),
                "title": self.safe_extract(data_dict, "music.title"),
                "url": self.safe_extract(data_dict, "music.play_url.url_list[0]"),
            }
        }

        images = data_dict.get("images")

        if images:
            has_live = any(i.get("video") for i in images)

            if has_live:
                result["type"] = "实况"
                result["downloads"] = []
                for i in images:
                    if i.get("video"):
                        video_url = self._get_best_video_url(i)
                        result["downloads"].append({
                            "type": "live_photo",
                            "image": self.safe_extract(i, "url_list[0]"),
                            "video": video_url
                        })
                    else:
                        result["downloads"].append(self.safe_extract(i, "url_list[0]"))
            else:
                result["type"] = "图集"
                result["downloads"] = [self.safe_extract(i, "url_list[0]") for i in images]
        else:
            result["type"] = "视频"
            duration_ms = self.safe_extract(data_dict, "video.duration", 0)
            result["duration"] = self.time_conversion(duration_ms)
            result["duration_seconds"] = duration_ms // 1000  # 添加秒数用于限制检查
            video_url = self._get_best_video_url(data_dict)
            cover_url = self.safe_extract(data_dict, "video.cover.url_list[0]")
            result["downloads"] = [{
                "type": "video",
                "url": video_url,
                "cover": cover_url
            }]

        return result

    def _get_best_video_url(self, data):
        bit_rate = self.safe_extract(data, "video.bit_rate", [])
        if not bit_rate:
            # 回退：取 url_list 最后一个（最稳定的CDN节点）
            url_list = self.safe_extract(data, "video.play_addr.url_list", [])
            return url_list[-1] if url_list else ""
        try:
            candidates = []
            for i in bit_rate:
                play_addr = i.get("play_addr", {})
                candidates.append((
                    i.get("FPS", 0),
                    i.get("bit_rate", 0),
                    play_addr.get("data_size", 0),
                    play_addr.get("height", 0),
                    play_addr.get("width", 0),
                    play_addr.get("url_list", [])
                ))
            candidates.sort(key=lambda x: (max(x[3], x[4]), x[0], x[1], x[2]))
            # 使用 url_list[-1]（最后一个CDN节点，最稳定）
            url_list = candidates[-1][-1] if candidates else []
            return url_list[-1] if url_list else ""
        except Exception:
            url_list = self.safe_extract(data, "video.play_addr.url_list", [])
            return url_list[-1] if url_list else ""

# ==========================================
# 4. 下载器核心
# ==========================================
class DouyinDownloader:
    def __init__(self, enable_cf_proxy=False, cf_proxy_url=""):
        self.session = requests.Session()
        # 默认 headers 使用 PC UA
        self.session.headers.update({
            "User-Agent": USERAGENT,
            "Referer": "https://www.douyin.com/",
        })
        self.ab = ABogus(USERAGENT)
        self.extractor = Extractor()
        self.enable_cf_proxy = enable_cf_proxy
        self.cf_proxy_url = cf_proxy_url.rstrip("/") if cf_proxy_url else ""
        
        print("正在初始化 (获取 ttwid/msToken)...")
        self._init_tokens()

    def _init_tokens(self):
        base_str = string.digits + string.ascii_letters
        ms_token = "".join(random.choice(base_str) for _ in range(156))
        self.session.cookies.set("msToken", ms_token, domain=".douyin.com")
        data = {"region": "cn", "aid": 1768, "needFid": False, "service": "www.ixigua.com", "migrate_info": {"ticket": "", "source": "node"}, "cbUrlProtocol": "https", "union": True}

        # 使用 CF 代理或直连
        if self.enable_cf_proxy and self.cf_proxy_url:
            url = f"{self.cf_proxy_url}/ttwid/ttwid/union/register/"
            # CF 代理时需要手动传递 Cookie
            resp = self.session.post(url, json=data, timeout=30)
            if resp.status_code == 200:
                # 从响应中提取 ttwid cookie 并设置到 session
                if 'set-cookie' in resp.headers or 'Set-Cookie' in resp.headers:
                    cookie_header = resp.headers.get('set-cookie') or resp.headers.get('Set-Cookie')
                    # 手动解析并设置 cookie
                    if cookie_header and 'ttwid=' in cookie_header:
                        ttwid_match = re.search(r'ttwid=([^;]+)', cookie_header)
                        if ttwid_match:
                            ttwid_value = ttwid_match.group(1)
                            self.session.cookies.set("ttwid", ttwid_value, domain=".douyin.com")
        else:
            url = "https://ttwid.bytedance.com/ttwid/union/register/"
            resp = self.session.post(url, json=data, timeout=30)

        if resp.status_code != 200:
            raise Exception(f"初始化 ttwid 失败: HTTP {resp.status_code}")

    def get_detail(self, url_input):
        url = url_input.strip()

        aweme_id = self._resolve_short_url(url)

        if not aweme_id:
            return None

        print(f"解析到 ID: {aweme_id}")

        params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "aweme_id": aweme_id,
            "update_version_code": "170400",
            "pc_client_type": "1",
            "version_code": "190500",
            "version_name": "19.5.0",
            "cookie_enabled": "true",
            "platform": "PC",
            "downlink": "10",
            "msToken": self.session.cookies.get("msToken")
        }

        params["a_bogus"] = self.ab.get_value(params)

        try:
            # 使用 CF 代理或直连
            if self.enable_cf_proxy and self.cf_proxy_url:
                api = f"{self.cf_proxy_url}/douyin/aweme/v1/web/aweme/detail/"
            else:
                api = "https://www.douyin.com/aweme/v1/web/aweme/detail/"

            self.session.headers.update({"User-Agent": USERAGENT})

            # 如果使用 CF 代理，手动添加 Cookie 到请求头
            if self.enable_cf_proxy and self.cf_proxy_url:
                # 获取所有 cookies 并构建 Cookie 头
                cookie_str = "; ".join([f"{k}={v}" for k, v in self.session.cookies.items()])
                headers_with_cookie = {"Cookie": cookie_str}
                resp = self.session.get(api, params=params, timeout=30, headers=headers_with_cookie)
            else:
                resp = self.session.get(api, params=params, timeout=30)

            if resp.status_code == 200:
                try:
                    resp_json = resp.json()

                    # 如果使用 CF 代理，响应会被 Base64 编码
                    if self.enable_cf_proxy and self.cf_proxy_url and isinstance(resp_json, dict) and 'encoding' in resp_json:
                        if resp_json.get('encoding') == 'base64':
                            import base64
                            decoded_text = base64.b64decode(resp_json['data']).decode('utf-8')
                            data = json.loads(decoded_text)
                        else:
                            data = resp_json
                    else:
                        data = resp_json

                    if data.get("aweme_detail"):
                        return self.extractor.extract_data(data["aweme_detail"])
                    else:
                        print(f"未获取到 aweme_detail")
                except Exception as e:
                    print(f"JSON 解析失败: {e}")
            else:
                print(f"API 请求失败: {resp.status_code}")
        except Exception as e:
            print(f"请求异常: {e}")
        return None

    def _resolve_short_url(self, url):
        """
        复刻原仓库逻辑：使用 HEAD 请求解析短链接
        """
        url_match = re.search(r'(https?://\S+)', url)
        if url_match:
            url = url_match.group(1)

        # 使用 HEAD 请求获取重定向后的 URL，增加超时和重试
        final_url = None
        for attempt in range(3):
            try:
                resp = self.session.head(url, allow_redirects=True, timeout=30)
                final_url = resp.url
                break
            except Exception as e:
                if attempt == 2:
                    raise Exception(f"链接解析失败(重试3次): {e}")
                time.sleep(1)

        if not final_url:
            raise Exception("链接解析失败: 无法获取重定向URL")

        pattern = re.compile(r'/(?:video|note|slides)/(\d+)')
        match = pattern.search(final_url)
        if match:
            return match.group(1)

        match = re.search(r'(?:modal_id|mid|aweme_id)=(\d+)', final_url)
        if match:
            return match.group(1)

        return None

    def download_video(self, url, save_path="video.mp4", logger_func=None):
        """下载视频，添加必要的 headers 绕过防盗链，支持重试"""
        def log(msg):
            if logger_func:
                logger_func(msg)
            else:
                print(msg)

        headers = {
            "User-Agent": USERAGENT,
            "Referer": "https://www.douyin.com/",
        }

        for attempt in range(3):
            try:
                if attempt > 0:
                    log(f"[下载] 重试 {attempt}/3")
                    time.sleep(2)

                log(f"[下载] 开始: {save_path}")
                resp = self.session.get(url, headers=headers, stream=True, timeout=60)
                log(f"[下载] 状态码: {resp.status_code}")

                if resp.status_code == 200:
                    total_size = 0
                    with open(save_path, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                total_size += len(chunk)
                    log(f"[下载] 完成: {save_path}, 大小: {total_size} bytes")
                    return True
                else:
                    log(f"[下载] 失败: HTTP {resp.status_code}")
            except Exception as e:
                log(f"[下载] 异常 (尝试 {attempt+1}/3): {e}")

        return False

if __name__ == "__main__":
    downloader = DouyinDownloader()
    while True:
        u = input("\n请输入抖音链接 (q退出): ")
        if u.lower() == 'q': break
        if not u: continue

        data = downloader.get_detail(u)
        if data:
            print(json.dumps(data, indent=4, ensure_ascii=False))

            # 询问是否下载
            if data.get('type') == '视频':
                choice = input("\n是否下载视频? (y/n): ")
                if choice.lower() == 'y':
                    video_url = data['downloads'][0]['url']
                    filename = f"{data['id']}.mp4"
                    downloader.download_video(video_url, filename)