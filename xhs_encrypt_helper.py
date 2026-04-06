"""
从 RedCrack 项目提取的小红书加密辅助模块。
用于生成请求 edith.xiaohongshu.com API 所需的 Cookie 和 Header 签名。
"""
import time
import random
import hashlib
import zlib
import json
import base64
import uuid
import math
import secrets
import urllib.parse

from Crypto.Cipher import DES, ARC4


# ==================== 基础工具 ====================

def crc32_encode(data: str) -> int:
    return zlib.crc32(data.encode())

def md5_encode(data: str) -> str:
    return hashlib.md5(data.encode()).hexdigest()

def unsigned_right_shift(value, shift):
    return (value & 0xFFFFFFFF) >> shift


# ==================== 自定义编码 ====================

def triplet_to_base64(a, c):
    return c[(a >> 18) & 63] + c[(a >> 12) & 63] + c[(a >> 6) & 63] + c[a & 63]

def encode_chunk(a, e, r, c):
    d = []
    for f in range(e, r, 3):
        c_val = ((a[f] << 16) & 0xff0000) + ((a[f + 1] << 8) & 0xff00) + (a[f + 2] & 0xff)
        d.append(triplet_to_base64(c_val, c))
    return ''.join(d)

def b64_encode(a, alphabet):
    r = len(a)
    d = r % 3
    f = []
    s = 16383
    u = 0
    l = r - d
    while u < l:
        end = min(u + s, l)
        f.append(encode_chunk(a, u, end, alphabet))
        u += s
    if d == 1:
        e = a[r - 1]
        f.append(alphabet[e >> 2] + alphabet[(e << 4) & 63] + "==")
    elif d == 2:
        e = (a[r - 2] << 8) + a[r - 1]
        f.append(alphabet[e >> 10] + alphabet[(e >> 4) & 63] + alphabet[(e << 2) & 63] + "=")
    return ''.join(f)

def encode_utf8(a):
    result = []
    i = 0
    while i < len(a):
        c = a[i]
        if c == '%':
            result.append(int(a[i+1:i+3], 16))
            i += 3
        else:
            result.append(ord(c))
            i += 1
    return result


# ==================== 配置常量 ====================

APP_ID = "xhs-pc-web"
LANGUAGE_VERSION = "4.2.6"
ARTIFACT_VERSION = "6.3.0"
OS_SYSTEM = "Windows"
PLAT_FROM_CODE = 5
BASE64_TABLE = "ZmserbBoHQtNP+wOcza/LpngG8yJq42KWYj0DSfdikx3VT16IlUAFM97hECvuRX5"
BASE58_TABLE = "NOPQRStuvwxWXYZabcyz012DEFTKLMdefghijkl4563GHIJBC7mnop89+/AUVqrsOPQefghijkABCDEFGuvwz0123456789xy"
XOR_KEY = [175, 87, 43, 149, 202, 101, 178, 217, 236, 118, 187, 93, 46, 151, 203, 101, 50, 153, 204, 102, 51, 153, 204, 102, 51, 153, 204, 230, 115, 57, 156, 206, 103, 51, 25, 12, 6, 3, 1, 0, 0, 0, 0, 0, 128, 64, 32, 144, 72, 36, 18, 137, 196, 226, 113, 56, 28, 14, 7, 3, 1, 128, 64, 160, 80, 40, 20, 138, 197, 98, 49, 24, 12, 6, 131, 193, 96, 48, 152, 76, 38, 147, 201, 100, 178, 89, 172, 86, 171, 213, 234, 245, 250, 253, 126, 63, 159, 79, 39, 147, 73, 164, 210, 233, 116, 58, 157, 78, 39, 147, 73, 164, 210, 233, 244, 122, 61, 30, 143, 71, 35, 145, 72, 164]
B1_RC4_KEY = b"xhswebmplfbt"
DES_KEY = b"zbp30y86"
A1_VALID_CHARS = "abcdefghijklmnopqrstuvwxyz1234567890"


# ==================== Cookie 生成 ====================

def generate_a1_and_webid():
    """生成 a1 和 webId cookie"""
    hex_data = hex(int(time.time() * 1000))[2:]
    random_string = ''.join(random.choices(A1_VALID_CHARS, k=30))
    text = hex_data + random_string + str(PLAT_FROM_CODE) + "0" + "000"
    crc32 = crc32_encode(text)
    a1 = (text + str(crc32))[:52]
    webId = md5_encode(a1)
    return a1, webId


def decrypt_websectiga(js_text: str) -> str:
    """从 /api/sec/v1/scripting 响应中解密 websectiga"""
    import re as _re
    b = _re.search(r'"b":"(.*?)",', js_text).group(1)
    d = json.loads(_re.search(r'"d":(.*?)\}\)', js_text).group(1))
    # Base64 解码并转为逻辑列表
    encoded_str = b
    padding = len(encoded_str) % 4
    if padding:
        encoded_str += '=' * (4 - padding)
    decoded_str = base64.b64decode(encoded_str).decode('utf-8')
    result = []
    current_chunk = []
    for char in decoded_str:
        if len(current_chunk) == 5:
            result.append(current_chunk)
            current_chunk = []
        current_chunk.append(ord(char) - 1)
    if current_chunk:
        result.append(current_chunk)
    target = result[d[92]:d[93]+1]
    key = [d[target[675+i][2]] for i in range(0, 128, 2)]
    decode_key = [chr(key[i+j]) for i in range(56, -1, -8) for j in range(8)]
    return "".join(decode_key)


# ==================== GID 加密 ====================

def generate_gid_data(fp: dict):
    """生成获取 gid 所需的加密 data"""
    fp_json = json.dumps(fp, separators=(',', ':'), ensure_ascii=False)
    fp_b64 = base64.b64encode(fp_json.encode())
    pad_len = 8 - len(fp_b64) % 8
    padded = fp_b64 + b'\x00' * pad_len
    cipher = DES.new(DES_KEY, DES.MODE_ECB)
    ciphertext = cipher.encrypt(padded)
    return {
        "platform": OS_SYSTEM,
        "profileData": ciphertext.hex(),
        "sdkVersion": LANGUAGE_VERSION,
        "svn": "2"
    }


# ==================== 指纹生成 ====================

def generate_fingerprint(cookies: dict, user_agent: str) -> dict:
    """生成浏览器指纹（80+ 字段）"""
    cookie_string = "; ".join(f"{k}={v}" for k, v in cookies.items())
    vendor = "Google Inc. (NVIDIA)"
    renderer = "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x0000250F) Direct3D11 vs_5_0 ps_5_0, D3D11)"
    width, height = "1920", "1080"
    fp = {
        "x1": user_agent, "x2": "false", "x3": "zh-CN",
        "x4": "24", "x5": "8", "x6": "24",
        "x7": f"{vendor},{renderer}",
        "x8": "12", "x9": f"{width};{height}",
        "x10": f"{width};{int(height)-48}",
        "x11": "-480", "x12": "Asia/Shanghai",
        "x13": "true", "x14": "true", "x15": "true",
        "x16": "false", "x17": "false", "x18": "un",
        "x19": "Win32", "x20": "",
        "x21": "PDF Viewer,Chrome PDF Viewer,Chromium PDF Viewer,Microsoft Edge PDF Viewer,WebKit built-in PDF",
        "x22": hashlib.md5(secrets.token_bytes(32)).hexdigest(),
        "x23": "false", "x24": "false", "x25": "false",
        "x26": "false", "x27": "false",
        "x28": "0,false,false", "x29": "4,7,8",
        "x30": "swf object not loaded",
        "x33": "0", "x34": "0", "x35": "0",
        "x36": f"{random.randint(1, 20)}",
        "x37": "0|0|0|0|0|0|0|0|0|1|0|0|0|0|0|0|0|0|1|0|0|0|0|0",
        "x38": "0|0|1|0|1|0|0|0|0|0|1|0|1|0|1|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0",
        "x39": 0, "x40": "0", "x41": "0",
        "x42": "3.4.3", "x43": "742cc32c",
        "x44": f"{int(time.time() * 1000)}",
        "x45": "__SEC_CAV__1-1-1-1-1|__SEC_WSA__|",
        "x46": "false", "x47": "1|0|0|0|0|0",
        "x48": "", "x49": "{list:[],type:}",
        "x50": "", "x51": "", "x52": "",
        "x55": "380,380,360,400,380,400,420,380,400,400,360,360,440,420",
        "x56": f"{vendor}|{renderer}|{hashlib.md5(secrets.token_bytes(32)).hexdigest()}|35",
        "x57": cookie_string,
        "x58": "180", "x59": "2", "x60": "63", "x61": "1291",
        "x62": "2047", "x63": "0", "x64": "0", "x65": "0",
        "x66": {"referer": "", "location": "https://www.xiaohongshu.com/explore", "frame": 0},
        "x67": "1|0", "x68": "0",
        "x69": "326|1292|30", "x70": ["location"],
        "x71": "true", "x72": "complete", "x73": "1191",
        "x74": "0|0|0", "x75": "Google Inc.", "x76": "true",
        "x77": "1|1|1|1|1|1|1|1|1|1",
        "x78": {"x": 0, "y": 2400, "left": 0, "right": 290.828125, "bottom": 2418, "height": 18, "top": 2400, "width": 290.828125, "font": 'system-ui, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji", -apple-system, "Segoe UI", Roboto, Ubuntu, Cantarell, "Noto Sans", sans-serif, BlinkMacSystemFont, "Helvetica Neue", Arial, "PingFang SC", "PingFang TC", "PingFang HK", "Microsoft Yahei", "Microsoft JhengHei"'},
        "x82": "_0x17a2|_0x1954",
        "x31": "124.04347527516074",
        "x79": "144|599565058866",
        "x53": hashlib.md5(secrets.token_bytes(32)).hexdigest(),
        "x54": "10311144241322244122",
        "x80": "1|[object FileSystemDirectoryHandle]",
    }
    return fp

def update_fingerprint(fp: dict, cookies: dict, url: str):
    """每次请求前更新指纹的动态字段"""
    cookie_string = "; ".join(f"{k}={v}" for k, v in cookies.items())
    fp.update({
        "x39": 0,
        "x44": f"{time.time() * 1000}",
        "x57": cookie_string,
        "x66": {"referer": "https://www.xiaohongshu.com/explore", "location": url, "frame": 0}
    })


# ==================== Header 签名 ====================

_xray_seq = math.floor(random.random() * math.pow(2, 23))

def encrypt_header_xb3() -> str:
    return ''.join(random.choices("abcdef0123456789", k=16))

def encrypt_headers_xray() -> str:
    global _xray_seq
    _xray_seq += 1
    part1 = hex(int(time.time() * 1000) << 23 | _xray_seq)[2:].zfill(16)
    high32 = math.floor(random.random() * math.pow(2, 32))
    low32 = math.floor(random.random() * math.pow(2, 32))
    part2 = hex((high32 << 32) | low32)[2:].zfill(16)
    return part1 + part2

def _base58_encode(data: bytes) -> str:
    num = int.from_bytes(data, byteorder='big')
    encoded = []
    while num > 0:
        num, remainder = divmod(num, 58)
        encoded.append(BASE58_TABLE[remainder])
    leading_zeros = len(data) - len(data.lstrip(b'\x00'))
    encoded.extend([BASE58_TABLE[0]] * leading_zeros)
    return ''.join(reversed(encoded))

def _encrypt_x3(cookie_a1, cookie_loadts, uri="", params=None, data=None):
    if params:
        query_string = urllib.parse.urlencode(params).replace('%2C', ',')
        uri = f"{uri}?{query_string}"
    if data is not None:
        uri = uri + json.dumps(data, separators=(",", ":"))
    md5_url = hashlib.md5(uri.encode()).hexdigest()
    p1 = [119, 104, 96, 41]
    rn = int(random.random() * 4294967295)
    p2 = list(rn.to_bytes(4, byteorder='little'))
    ts = int(time.time() * 1000)
    bl = list(ts.to_bytes(8, byteorder='little'))
    bl[0] = (sum(bl[1:5]) & 255) + sum(bl[5:8]) & 0xFF
    p3 = [i ^ 41 for i in bl]
    p4 = list(cookie_loadts.to_bytes(8, byteorder='little'))
    num = int(random.random() * 99) + 1
    p5 = list(num.to_bytes(4, byteorder='little'))
    p6 = list((1293).to_bytes(4, byteorder='little'))
    p7 = list(len(uri.encode("utf-8")).to_bytes(4, byteorder='little'))
    p8 = [b ^ (rn & 255) for b in bytes.fromhex(md5_url)][0:8]
    ba = list(cookie_a1.encode('utf-8'))
    p9 = [len(ba)] + ba
    ba2 = list(APP_ID.encode('utf-8'))
    p10 = [len(ba2)] + ba2
    p11 = [1, (rn & 255) ^ 115, 249, 83, 103, 103, 201, 181, 131, 99, 94, 7, 68, 250, 132, 21]
    combined = p1 + p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9 + p10 + p11
    encrypted = [i ^ j for i, j in zip(combined, XOR_KEY)]
    return "mns0101_" + _base58_encode(bytes(encrypted))

def encrypt_headers_xs(cookie_a1, cookie_loadts, uri="", params=None, data=None):
    p = {
        'x0': LANGUAGE_VERSION, 'x1': APP_ID, 'x2': OS_SYSTEM,
        'x3': _encrypt_x3(cookie_a1, cookie_loadts, uri, params, data),
        'x4': "" if data is None else "object"
    }
    encoded = b64_encode(
        encode_utf8(urllib.parse.quote(json.dumps(p, separators=(",", ":")), safe="-_.!~*'()")),
        BASE64_TABLE
    )
    return "XYS_" + encoded

def _diy_mrc(e):
    def jsint(num):
        return num % (2**32) if num >= 2**31 else num - 2**32
    mrc_list = []
    for i in range(255, -1, -1):
        j = i
        for _ in range(8, 0, -1):
            j = unsigned_right_shift(j, 1) ^ 0xedb88320 if j & 1 else unsigned_right_shift(j, 1)
        mrc_list.insert(0, unsigned_right_shift(j, 0))
    i_val = -1
    for r in e:
        i_val = mrc_list[255 & i_val ^ ord(r)] ^ unsigned_right_shift(i_val, 8)
    return -1 ^ jsint(i_val) ^ 0xedb88320

def _encrypt_b1(fp):
    b1_fp = {k: fp[k] for k in ["x33","x34","x35","x36","x37","x38","x39","x42","x43","x44","x45","x46","x48","x49","x50","x51","x52","x82"]}
    b1_json = json.dumps(b1_fp, separators=(',', ':'), ensure_ascii=False)
    cipher = ARC4.new(B1_RC4_KEY)
    ciphertext = cipher.encrypt(b1_json.encode('utf-8')).decode('latin1')
    encoded_url = urllib.parse.quote(ciphertext, safe="!*'()~_-")
    b = []
    for c in encoded_url.split('%')[1:]:
        chars = list(c)
        b.append(int(''.join(chars[:2]), 16))
        [b.append(ord(j)) for j in chars[2:]]
    return b64_encode(bytearray(b), "ZmserbBoHQtNP+wOcza/LpngG8yJq42KWYj0DSfdikx3VT16IlUAFM97hECvuRX5")

def encrypt_headers_xsc(cookie_a1, fp):
    localStorage_b1 = _encrypt_b1(fp)
    source_text = {
        's0': PLAT_FROM_CODE, 's1': '',
        'x0': "1", 'x1': LANGUAGE_VERSION, 'x2': OS_SYSTEM,
        'x3': APP_ID, 'x4': ARTIFACT_VERSION,
        'x5': cookie_a1, 'x6': '', 'x7': '',
        'x8': localStorage_b1,
        'x9': int(_diy_mrc("" + "" + localStorage_b1)),
        'x10': fp["x39"], 'x11': "normal"
    }
    encoded = b64_encode(
        encode_utf8(urllib.parse.quote(json.dumps(source_text, separators=(",", ":"), ensure_ascii=False), safe="-_.!~*'()")),
        BASE64_TABLE
    )
    return encoded
