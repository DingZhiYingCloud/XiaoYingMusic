# 底部导航高亮与全站SEO优化

## Context（背景）

用户反馈两个问题：
1. **底部移动端导航栏**点击后对应图标没有高亮标识，用户无法直观知道当前所在页面（首页图标当前是写死高亮，切到其他页面也不消失）。
2. **全站 SEO 不足**：所有页面共用 `template.html` 写死的 keywords/description，缺少针对各页面内容（歌手、歌曲、榜单等）的差异化 SEO；且模板中残留 "2t58" 关键词（友链、logo 等），与"杯子音乐网"定位冲突。

本次优化目标：
- 底部导航根据当前 URL 自动高亮对应图标
- 全站 6 个页面具备针对性的 title/keywords/description
- 清除所有 "2t58" 残留，统一为"杯子音乐网"（允许"爱听音乐网"作为关键词）

## 改动清单

### 一、底部导航高亮

**1. `Web/templates/common_html/footer.html`**
- 第17行首页 `<a>` 去掉写死的 `fed-text-green` 类（改为纯 JS 控制，避免其他页面首页永远高亮）

**2. `Web/static/js/common.js`**
- 在现有 `$(document).ready` 内、顶部导航高亮逻辑之后，新增底部导航高亮逻辑：
  - 选择器 `.fed-tabr-info a`，先 `removeClass('fed-text-green')`
  - 按 path 匹配（复用顶部导航思路）：
    - 首页：path == `/` 或以 `/index` 开头
    - 排行：path 以 `/list/` 开头
    - 歌手：path 以 `/singerlist/` 或 `/singer/` 开头
  - 命中则 `addClass('fed-text-green')`

### 二、SEO 优化

**3. `Web/templates/template.html`（第13-15行）**
- keywords 改为 `{% block keywords %}默认值{% endblock %}`
- description 改为 `{% block description %}默认值{% endblock %}`
- 默认值保留首页通用 SEO（已确认无 2t58），`bezimusic.Com` 统一为小写 `bezimusic.com`

**4. 各页面模板新增 `{% block keywords %}` 和 `{% block description %}`**（基于已有模板变量，不改后端）：

| 页面 | 关键变量 | keywords 要点 | description 要点 |
|------|---------|--------------|-----------------|
| index.html | - | 杯子音乐网,Mp3免费下载,DJ舞曲,免费音乐网,爱听音乐网 | 站点总览介绍 |
| singer_list.html | `{{ title }}` | 歌手列表,歌手大全,{{ title }},杯子音乐网 | 歌手分类筛选说明 |
| singer.html | `{{ singer.name }}` | {{ singer.name }}歌曲,{{ singer.name }}MP3下载,杯子音乐网 | 歌手全部歌曲与简介 |
| song.html | `{{ song.name }}`, `{{ song.artists }}` | {{ song.name }}MP3下载,{{ song.name }}歌词,杯子音乐网 | 歌曲试听/歌词/推荐 |
| new_songs.html | `{{ title }}` | {{ title }},新歌榜,TOP榜单,歌曲排行榜,杯子音乐网 | 榜单与排行说明 |
| search.html | `{{ keyword }}` | {{ keyword }}搜索,{{ keyword }}歌曲下载,{{ keyword }}MP3,杯子音乐网 | 搜索结果说明 |

### 三、清理 2t58 残留

**5. `Web/templates/index.html`（第150-157行友情连接区块）**
- 保留"友情连接"区块结构（`<div class="link">` + `<h2>友情连接</h2>` + `<div>`）
- 删除 `<a href="http://www.2t58.com/">爱听音乐网</a>`，`<div>` 留空待用户后续添加

**6. `Web/templates/common_html/headers.html`（第4行）**
- 下载 `https://www.2t58.com/images/logo.png` 到 `Web/static/images/logo.png`
- `src` 改为 `/static/images/logo.png`
- `alt` 由"爱听音乐网"改为"杯子音乐网"
- 若下载失败（人机验证拦截图片资源），降级为文字 logo：`<img>` 替换为 `<span>杯子音乐网</span>`，并告知用户

## 验证方式

1. 启动 Django 开发服务器
2. **底部导航高亮**：浏览器开发者工具切移动端视图（宽度≤750px），分别访问首页/排行/歌手列表及详情页，确认底部对应图标变绿（`fed-text-green`）；访问搜索页确认无高亮
3. **SEO**：查看各页面源码，确认 `<title>`、`<meta name="keywords">`、`<meta name="description">` 内容与页面主题匹配
4. **2t58 清理**：全站源码搜索 "2t58" 应无残留（友链已删、logo 已本地化）
5. **logo**：首页头部 logo 图片正常显示
