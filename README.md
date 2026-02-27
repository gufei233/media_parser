# 媒体解析插件


抖音和小红书链接解析插件（异步优化版），使用 aiohttp 实现完全异步请求，提升性能和稳定性。

由于抖音的风控，请参考`cloudflare_worker_v2.js`自行部署Cloudflare 代理。

---

## ⚙️ 配置说明

所有配置项均可在 AstrBot 管理面板中调整，也可直接编辑 `_conf_schema.json`。

### 核心配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled_sessions` | list | `[]` | 解析白名单，留空表示全局启用 |
| `debounce_interval` | int | `300` | 防抖时间间隔（秒），0 表示不启用 |
| `source_max_size` | int | `90` | 最大文件大小（MB） |
| `source_max_minute` | int | `15` | 最大视频时长（分钟） |
| `download_timeout` | int | `280` | 下载超时时间（秒） |
| `download_retry_times` | int | `3` | 下载失败重试次数 |
| `common_timeout` | int | `15` | 普通请求超时时间（秒） |
| `show_download_fail_tip` | bool | `true` | 是否提示下载失败信息 |
| `forward_threshold` | int | `3` | 消息合并转发阈值 |
| `douyin_info_render_mode` | string | `"image"` | 抖音信息渲染模式：`text` / `image` / `both` |
| `enable_cf_proxy` | bool | `false` | 是否启用 CF 代理 |
| `cf_proxy_url` | string | `""` | CF Workers 地址 |

### 配置示例

```json
{
  "enabled_sessions": ["group_123456", "group_789012"],
  "debounce_interval": 300,
  "source_max_size": 90,
  "source_max_minute": 15,
  "download_timeout": 280,
  "download_retry_times": 3,
  "douyin_info_render_mode": "image",
  "enable_cf_proxy": true,
  "cf_proxy_url": "https://your-worker.workers.dev"
}
```

---

## 📖 使用指南

### 基础使用

#### 1. 全局模式（默认）

当 `enabled_sessions` 为空时，所有群聊都启用解析。

```plaintext
用户: 看看这个视频 https://v.douyin.com/abc123/
Bot: [自动解析并发送视频]
```

#### 2. 白名单模式

只有白名单中的群聊会解析链接。

```plaintext
管理员: /开启解析
Bot: ✅ 解析已开启

用户: https://v.douyin.com/abc123/
Bot: [自动解析并发送视频]
```

### 管理员命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/开启解析` | 将当前会话加入白名单 | 在群聊中发送 |
| `/关闭解析` | 将当前会话移出白名单 | 在群聊中发送 |
| `/解析状态` | 查看插件配置和状态 | 任意位置发送 |

### 支持的平台

#### 抖音
- ✅ 普通视频
- ✅ 图集
- ✅ 实况图片（Live Photo）
- ✅ 视频封面
- ✅ 作者信息
- ✅ 点赞/评论/分享数据

#### 小红书
- ✅ 图文笔记
- ✅ 视频笔记
- ✅ 实况图片（Live Photo）
- ✅ 笔记封面
- ✅ 作者信息

---

## 🏗️ 技术架构

### 文件结构

```
media_parser/
├── _conf_schema.json       # 配置文件定义（AstrBot标准格式）
├── main.py                 # 主插件文件（异步版）
├── config.py               # 配置管理类
├── debounce.py             # 防抖器（含自动清理）
├── exceptions.py           # 异常类定义
├── async_dysk.py           # 异步抖音下载器（CookieJar管理）
├── async_xhs.py            # 异步小红书解析器
└── dysk.py                 # ABogus算法（同步版，供async_dysk使用）
```


### Cookie 工作流程

1. **初始化阶段** (`_init_tokens`)
   - 生成 msToken → 手动保存到 `_cookies["msToken"]`
   - 请求 ttwid API → **CookieJar 自动保存** ttwid cookie

2. **短链接重定向** (`_resolve_short_url`)
   - CF Worker 模式：手动传递 Cookie header（从 CookieJar + _cookies 合并）
   - 直连模式：CookieJar 自动传递 cookies
   - 响应：**CookieJar 自动保存** UIFID_TEMP、enter_pc_once 等

3. **API 请求** (`_fetch_detail_api`)
   - CF Worker 模式：手动传递完整 Cookie header
   - 直连模式：CookieJar 自动传递 cookies
   - 响应：**CookieJar 自动保存**新 cookies

4. **下载媒体** (`download_video`)
   - 使用同一个 session → **CookieJar 自动传递** cookies
   - 不需要手动设置 Cookie header

### 关键 Cookies 说明

| Cookie 名称 | 来源 | 作用 | 管理方式 |
|------------|------|------|---------|
| `msToken` | 本地生成 | 156位随机字符串，请求标识 | 手动（_cookies字典） |
| `ttwid` | ttwid API | 抖音用户追踪 ID，URL 编码 | CookieJar自动 |
| `UIFID_TEMP` | 短链接重定向 | 临时用户界面 ID | CookieJar自动 |
| `enter_pc_once` | 短链接重定向 | PC 端访问标记 | CookieJar自动 |

### 工作流程

```
消息接收
  ↓
白名单检查 ←─────────┐
  ↓                  │ 管理员命令
链接匹配             │ /开启解析
  ↓                  │ /关闭解析
防抖检查 ←───────────┘ /解析状态
  ↓
创建解析器实例（新实例，避免cookie混乱）
  ↓
异步解析（CookieJar自动管理Cookie）
  ├─ 初始化tokens（msToken + ttwid）
  ├─ 短链接重定向（自动保存cookies）
  └─ API请求（自动传递cookies）
  ↓
异步下载
  ├─ 图片下载（使用session，CookieJar自动处理）
  └─ 视频下载（使用session，CookieJar自动处理）
  ↓
发送消息
  ↓
关闭解析器（清理session和CookieJar）
```

## 项目参考
[TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)
