# 杯子音乐网 - 只专注好听|安静的音乐

一个**纯爬虫架构**的音乐网站：没有数据库，页面数据实时抓取自源站（2t58.com），
抓取结果自动缓存到本地文件，避免每次访问都去打扰源站。

- 微信: duyanbz
- tg: <https://t.me/xiaoying1216>

---

## 一、功能特性

- **首页**：热门歌手 / 歌曲飙升榜 / 流行趋势榜 三大板块
- **歌手**：歌手大全（分类筛选）、歌手详情（作品列表 + 分页）
- **歌曲**：歌曲详情页、在线播放、歌词同步滚动、每日推荐
- **搜索**：关键词搜索（中文/空格自动处理）
- **榜单**：33 个热门榜单（名称已本地化改写，规避与源站雷同）
- **歌单**：歌单精选（分类筛选）、歌单详情
- **MV**：映像MV大全（分类筛选）、MV 详情（DPlayer 多清晰度播放）
- **下载**：MP3 / 歌词 / 打包 ZIP（后端代理转发，破解 CDN 防盗链）
- **播放体验**：全局底部播放条、播放记忆续播、倍速播放、刷新自动续播
- **缓存**：全站数据缓存（1-3 小时可配，按页面类型独立控制），详见下文「缓存机制」
- **SEO**：sitemap.xml、robots.txt、动态 title/keywords/description、全站友情链接（小影 API，服务端渲染）

## 二、技术架构

```
浏览器
  │  HTTP 请求
  ▼
Django（6.1）
  ├── 视图层  Web/views/request.py      ← 每个页面/接口一个视图，异常自动降级空数据
  ├── 爬虫层  SpiderServices/Music_2t58 ← 实时抓取源站数据（requests + lxml + AES解密）
  ├── 缓存层  Django FileBasedCache      ← 爬虫结果缓存到本地 cache/ 目录（零依赖）
  ├── 模板层  Web/templates              ← Django 模板 + layui + jPlayer/DPlayer
  └── 中间件  middlewares/request_detect.py（请求身份识别，默认关闭）
```

关键点：

- **无数据库**：`settings.py` 中 `DATABASES` 被注释，所有内容来自爬虫实时抓取 + 缓存。
- **人机验证**：源站（2t58.com）有"安全人机验证"，需在 `.env` 配置有效的
  `MUSIC_2T58_PHPSESSID`（浏览器手动通过验证后从 Cookie 复制，过期需更新）。
- **播放链接解密**：源站播放链接是 AES-ECB 加密的，爬虫内置解密逻辑，前端不参与。

## 三、目录结构

```
BeiZiMusic/
├── BeiZiMusic/                  # Django 项目配置
│   ├── settings.py              # 全局配置（含缓存配置 CACHES）
│   └── urls.py                  # 根路由
├── Web/
│   ├── views/
│   │   ├── request.py           # 全部页面/接口视图（含下载代理、sitemap）
│   │   └── urls.py              # 前端路由
│   ├── templates/               # 页面模板（index/song/singer/...）
│   ├── static/                  # 静态资源（css/js/images/layui）
│   └── services/friend_links.py # 小影 API 友情链接（1 小时缓存）
├── SpiderServices/Music_2t58/
│   └── main.py                  # 2t58.com 爬虫（12 个 fetch_xxx 方法，全部带缓存）
├── middlewares/
│   └── request_detect.py        # 请求身份识别中间件（默认关闭）
├── cache/                       # 爬虫缓存文件（运行时自动生成，已 gitignore）
├── media/                       # 图标等媒体文件
├── .env                         # 环境变量配置（数据库/爬虫/缓存等，已 gitignore）
├── manage.py
└── requirements.txt
```

## 四、快速开始

```bash
# 1. 创建虚拟环境（Windows）
python -m venv .venv

# 2. 安装依赖
.venv\Scripts\pip install -r requirements.txt

# 3. 配置 .env（参考第五节；没有 .env 时程序用默认值运行，但爬虫需要 PHPSESSID）

# 4. 启动服务
.venv\Scripts\python manage.py runserver 127.0.0.1:8000
```

浏览器访问 <http://127.0.0.1:8000> 即可。

## 五、环境变量配置（.env）

在项目根目录创建 `.env` 文件（已 gitignore，不会提交），内容如下：

```ini
# ---- Django 核心 ----
SECRET_KEY=django-insecure-xxx
DEBUG=False
ALLOWED_HOSTS=127.0.0.1
CORS_ORIGIN_ALLOW_ALL=True

# ---- 2t58 爬虫：人机验证通过后的 PHPSESSID（浏览器获取，过期需更新）----
MUSIC_2T58_PHPSESSID=你的PHPSESSID

# ---- 小影 API 基础地址（友情链接数据源）----
XIAOYING_API_BASE=http://127.0.0.1:8002

# ---- 爬虫缓存（见下文「缓存机制」，全部可选，有默认值）----
CACHE_TTL_HOURS=2
CACHE_TTL_PLAY_MINUTES=30
```

| 变量 | 说明 | 默认值 |
|---|---|---|
| `SECRET_KEY` | Django 密钥 | 开发用兜底值（生产必须改） |
| `DEBUG` | 调试模式（`True` 显示详细报错） | `False` |
| `ALLOWED_HOSTS` | 允许访问的域名，逗号分隔 | `*` |
| `CORS_ORIGIN_ALLOW_ALL` | 是否允许跨域 | `False` |
| `MUSIC_2T58_PHPSESSID` | 源站人机验证凭证，**必填**，过期需更新 | 空 |
| `XIAOYING_API_BASE` | 小影 API 地址（友情链接） | `https://xiaoyingapi.com` |
| `CACHE_TTL_HOURS` | 全局缓存时长（小时） | `2` |
| `CACHE_TTL_PLAY_MINUTES` | 播放直链/MV 详情短缓存（分钟） | `30` |
| `CACHE_TTL_HOURS_<类型>` | 按页面类型覆盖缓存时长（小时） | 不配用全局 |

## 六、页面路由一览

| 路由 | 页面 | 对应爬虫方法 |
|---|---|---|
| `/` | 首页 | `fetch_home` |
| `/singer/<sid>.html` | 歌手详情 | `fetch_singer` |
| `/singerlist/<area>/<gender>/<style>/<letter>.html` | 歌手列表 | `fetch_singer_list` |
| `/song/<sid>.html` | 歌曲详情 | `fetch_song` |
| `/api/song/<sid>.json` | 播放条切歌 JSON | `fetch_song` |
| `/so/<keyword>.html` | 搜索 | `fetch_search` |
| `/list/<chart>.html` | 榜单 | `fetch_chart` |
| `/playtype/<playtype>.html` | 歌单列表 | `fetch_playtype_list` |
| `/playlist/<sid>.html` | 歌单详情 | `fetch_playlist` |
| `/mvlist/<mvtype>.html` | MV 列表 | `fetch_mvlist` |
| `/video/<sid>.html` | MV 详情 | `fetch_video` |
| `/download/<sid>/mp3.html` | 下载 MP3（`lrc`=歌词，`all`=打包ZIP） | `fetch_download` |
| `/sitemap.xml` | 站点地图（缓存 6 小时） | — |

## 七、爬虫说明

### 7.1 人机验证（PHPSESSID）

源站（2t58.com）有"安全人机验证"：首次访问返回含 `csrf_token` 的验证页，
需 POST 表单（勾选"我不是人机"）通过验证后才返回真实内容，验证状态约保留 1 小时。
爬虫已内置 `_pass_verification` 自动过验证，但前提是 `.env` 里的
`MUSIC_2T58_PHPSESSID` 有效。

**如何更新 PHPSESSID**（当出现"验证失效/数据为空"时）：

1. 用浏览器打开 `https://www.2t58.com/`，手动完成人机验证；
2. 按 F12 → Application → Cookies，复制 `PHPSESSID` 的值；
3. 粘贴到 `.env` 的 `MUSIC_2T58_PHPSESSID`，重启服务。

### 7.2 播放链接解密

源站播放链接是 AES-ECB 加密（密钥在页面 JS 中，爬虫已提取并内置），
爬虫解密后把真实的 CDN 直链交给前端播放。

### 7.3 反爬细节（代码内已处理）

- 模拟浏览器 `User-Agent` / `Referer`；
- 自动识别并处理"人机验证页"；
- 页面编码自动检测，避免中文乱码；
- 下载代理转发时**不带 Referer**，绕过 CDN 防盗链 403。

---

## 八、缓存机制（重要，详细说明）

> 为什么需要缓存：网站有**上千万个页面**，而数据全部来自爬虫实时抓取。
> 如果不缓存，每来一个访客就请求一次源站，会把源站压垮，也可能触发反爬封 IP。
> 加缓存后，**同一个页面在缓存有效期内，只会访问源站一次**，其余全部命中本地缓存。

### 8.1 缓存了什么

爬虫 `main.py` 中 **12 个 `fetch_xxx` 方法的返回结果全部被缓存**（除了下载专用方法）：

| 缓存键前缀 | 对应方法 | 默认时长 |
|---|---|---|
| `2t58_home` | 首页三大板块 | 2 小时 |
| `2t58_singer_*` | 歌手详情 | 2 小时 |
| `2t58_song_*` | 歌曲信息 + 歌词 + 每日推荐 | 2 小时 |
| `2t58_song_play_*` | 歌曲播放直链 | **30 分钟** |
| `2t58_search_*` | 搜索结果 | 2 小时 |
| `2t58_chart_*` | 榜单页 | 2 小时 |
| `2t58_singer_list_*` | 歌手列表 | 2 小时 |
| `2t58_playtype_*` | 歌单列表 | 2 小时 |
| `2t58_playlist_*` | 歌单详情 | 2 小时 |
| `2t58_mvlist_*` | MV 列表 | 2 小时 |
| `2t58_video_*` | MV 详情（含 CDN 直链） | **30 分钟** |

**不缓存**：`fetch_download`（下载必须拿到最新直链，否则链接可能已过期）。

### 8.2 缓存时长怎么配置

缓存时长全部由 `.env` 控制，**改配置后重启服务生效**。

- **全局默认**：`CACHE_TTL_HOURS=2`（单位：小时，可设 1-3 或任意值）
- **按页面类型覆盖**：`CACHE_TTL_HOURS_<类型>=1`，只改指定页面，其余页面仍用全局值。

支持的类型（对应 `CACHE_TTL_HOURS_` 后缀）：

```
HOME       首页            SINGER     歌手详情
SONG       歌曲信息/歌词    SEARCH     搜索结果
CHART      榜单页          SINGER_LIST 歌手列表
PLAYTYPE   歌单列表        PLAYLIST   歌单详情
MVLIST     MV 列表
```

**示例**：把榜单页改成 1 小时、歌曲页改成 3 小时，其余保持全局 2 小时：

```ini
CACHE_TTL_HOURS=2
CACHE_TTL_HOURS_CHART=1
CACHE_TTL_HOURS_SONG=3
```

**播放直链单独短缓存**：`CACHE_TTL_PLAY_MINUTES=30`（单位：分钟）。
CDN 播放直链是有时效的，缓存太久会导致"链接过期播放失败"，
所以歌曲播放直链、MV 详情（内含视频直链）单独用短缓存，默认 30 分钟。

### 8.3 缓存存哪里、怎么工作

- **存储**：Django 的 **FileBasedCache（文件缓存）**，写入项目根 `cache/` 目录，
  每个 URL 对应一个 `<key的md5>.djcache` 文件，**零依赖**（不需要 Redis/数据库）。
- **命中流程**：用户访问页面 → 视图调用爬虫 `fetch_xxx` → 先查缓存 →
  命中直接返回，未命中才请求源站并写入缓存。
- **缓存键规范**：`<站点缩写>_<功能>_<参数>`，如 `2t58_home`、`2t58_singer_d2t3eA_1`；
  参数含特殊字符（中文/空格等）时自动取 md5，避免文件名异常。
- **失效机制**：文件带有效期（TTL），到期后自动失效，下次访问自动重新抓取。
- **独立脚本兼容**：爬虫通过 `_CacheAdapter` 访问缓存——在 Django 环境里用文件缓存；
  脱离 Django 独立跑脚本时自动退化为进程内内存缓存，不会报错。

### 8.4 手动清缓存（重要）

修改了爬虫解析逻辑、改了缓存时长，或发现页面数据不对时，旧缓存不会自动失效，
**必须清空 `cache/` 目录**：

```bash
python -c "import shutil; shutil.rmtree('cache')"
```

> 注意：Windows PowerShell 里 `Remove-Item` 可能被安全拦截，请用上面的 Python 方式删除。

### 8.5 实现位置（给开发者）

- 缓存配置：[BeiZiMusic/settings.py](BeiZiMusic/settings.py)（`CACHES` + `CACHE_TTL_HOURS`）
- 缓存逻辑：[SpiderServices/Music_2t58/main.py](SpiderServices/Music_2t58/main.py)：
  - `_CacheAdapter`：统一缓存入口（django cache 优先，内存兜底）
  - `_ttl_for(类型)`：按类型取时长（支持 .env 覆盖）
  - `_play_ttl()`：播放直链短缓存时长
  - `_cache_key(参数...)`：生成缓存键（特殊字符自动 md5）
  - `_cached(key, 抓取函数, 时长)`：所有 `fetch_xxx` 的统一封装
- sitemap 缓存：`Web/views/request.py`（`sitemap_urls`，固定 6 小时）

## 九、播放功能说明

- **全局播放条**：站内任意页面底部常驻，支持切歌、暂停/播放、进度拖拽、倍速。
- **播放记忆**：歌曲进度、倍速、播放状态保存在浏览器 localStorage，刷新/重开自动恢复。
- **刷新自动续播**：**正在播放时**刷新页面，会自动从断点继续播放，无需再点播放按钮。
  若刷新前是暂停状态，则不会自动播放（符合浏览器自动播放策略）。
- **歌曲页播放**：歌曲详情页操作区有「播放」按钮，点击直接加入播放并开播，
  无需先加入待播放列表/喜欢列表。

## 十、常见问题（FAQ）

| 问题 | 解决办法 |
|---|---|
| 页面数据为空/首页空白 | ① 检查 `.env` 的 `MUSIC_2T58_PHPSESSID` 是否过期，更新后重启；② 源站可能暂时不可达，稍后重试 |
| 修改代码后页面没变化 | ① 服务是否用 `--noreload` 启动（是则需手动重启）；② 浏览器强刷（Ctrl+F5）绕过本地缓存 |
| 改了缓存时长/解析逻辑不生效 | 执行 `python -c "import shutil; shutil.rmtree('cache')"` 清缓存 |
| 播放/下载失败 | 播放直链有时效，清掉 `cache/` 后重试；下载走 `/download/<sid>/mp3.html` 由后端代理 |
| 控制台报 `Uncaught (in promise)` | 浏览器自动播放被拦截，属正常现象，点击播放按钮即可恢复 |
| 想改网站 SEO 标题/关键词 | 见 `.trae/documents/底部导航高亮与全站SEO优化.md` 及 `.trae/skills/seo-updater/` |

## 十一、开发约定

- 新增页面：视图 + 爬虫方法 + 模板，**爬虫方法必须用 `_cached` 包一层**（参考第八节）。
- 所有外部数据（爬虫、API）调用处必须 `try/except` 降级空数据，保证页面永远可访问。
- 静态资源引入带版本号（如 `player.js?v=v9`），改 JS/CSS 后记得升级版本号并强刷。
- `.env` 不入库；`cache/` 不入库（已配置 .gitignore）。
