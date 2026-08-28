---
name: "site-collector"
description: "Analyze a target website and extend the Django project with content-collection features following the core flow (spider→view→url→template). Invoke when user provides a URL to add a scraped page, or needs to scaffold a collection framework for a new site. Works for music/movie/wallpaper/blog etc."
---

# 站点采集器 (site-collector)

## 用途
给定目标网站,自动分析并按当前 Django 项目的核心流程扩展采集功能:
```
爬虫(SpiderServices) → 视图(Web/views) → 路由(urls) → 模板(templates)
```
内置通用反爬框架,适配不同站点。未来可复用到音乐/电影/壁纸/博客等任意内容站点。

## 两种执行模式

### 模式A:框架搭建
**触发条件**:用户刚创建 Django 项目,需要搭建采集框架(创建 SpiderServices/templates/static/media 目录,下载或模仿目标站 CSS/JS,搭基础模板)。
**执行**:→ [procedures/03-scaffold.md](procedures/03-scaffold.md)

### 模式B:页面采集(核心)
**触发条件**:项目已有框架,用户提供一个网址,要求新增该页面的采集与展示。
**执行**:→ [procedures/04-collect-page.md](procedures/04-collect-page.md)

## 强制流程(两种模式都必须遵循)

⚠️ **严禁跳过"沟通确认"阶段直接写代码。**

1. **分析** → [procedures/01-analyze.md](procedures/01-analyze.md)
   分析目标网址:页面类型 / 数据结构 / URL规则 / 反爬机制 / CSS-JS资源
2. **沟通确认** → [procedures/02-confirm.md](procedures/02-confirm.md)
   输出分析结论 + 实施方案(爬虫类名/方法名/字段/URL前缀/模板名/反爬策略),**等用户明确确认后再动手**
3. **实施**:按确认方案执行(模式A或B对应的 procedure)
4. **交付说明**:列出改动文件清单 + 需配置项(如 .env 反爬凭证)

## 模式判断规则
- 用户提供网址 + 项目里**已有** SpiderServices 目录和 template.html → **模式B(页面采集)**
- 用户提到"新建项目""搭建框架""初始化""下载对方CSS/JS" → **模式A(框架搭建)**
- 不确定时,在沟通确认阶段问用户

## 项目约定参考(实施时必读)
- 目录结构与文件职责 → [reference/project-structure.md](reference/project-structure.md)
- 命名规范(站点名/类名/方法名/URL/模板) → [reference/naming-conventions.md](reference/naming-conventions.md)
- 数据结构约定(列表/详情/分页/筛选) → [reference/data-conventions.md](reference/data-conventions.md)
- 通用反爬框架 → [reference/anti-scraping.md](reference/anti-scraping.md)

## 缓存规范(强制,每个采集功能都必须实现)
**目的**:避免每次访问都请求源站,降低源站与服务器压力。

- **缓存对象**:所有 `fetch_xxx` 采集方法的返回结果
- **默认时长**:1-2 小时(`.env` 中 `CACHE_TTL_HOURS` 自定义,默认 2)
- **缓存键**:`<站点缩写>_<功能>_<参数>`,如 `i4_home`、`i4_news_list_1_2`、`i4_news_detail_56195`;参数含空格/逗号等特殊字符时取 md5 再拼,如 `i4_firmware_{md5(model)}`(直接拼原字符会触发缓存后端告警)
- **缓存目录**:FileBasedCache 把每个 key 存为项目根 `cache/` 目录下的 `<md5>.djcache` 文件(无数据库/Redis,零依赖)
- **手动清缓存**:修改爬虫解析或缓存逻辑后必须清空 `cache/` 目录才能生效,用 `python -c "import shutil; shutil.rmtree('cache')"`(Trae CN 环境下 PowerShell `Remove-Item` 会被安全包装器拦截,勿用)
- **实现方式**:爬虫内 `_cached(key, fetch_func)` 统一封装(先查缓存,未命中抓取解析后写入)
- **settings.py 必须配置**:
  ```python
  CACHE_TTL_HOURS = float(os.getenv('CACHE_TTL_HOURS', '2'))
  CACHES = {
      'default': {
          'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
          'LOCATION': os.path.join(BASE_DIR, 'cache'),
      },
  }
  ```
- **独立脚本兼容**:爬虫可在 Django 环境外运行,缓存后端需有内存兜底(参考 anti-scraping.md 的 `_CacheAdapter`)
- 完整模板见 [reference/anti-scraping.md](reference/anti-scraping.md) 的"缓存实现"章节

## 关键原则
- **最小改动**:仅修改与需求直接相关的代码,不重构无关代码
- **复用优先**:分页解析 `_parse_pagination`、列表解析 `_parse_search_results` 等公共方法能复用就复用
- **降级兜底**:视图必须 try/except,爬虫失败时返回同结构空数据,保证页面可访问
- **尊重现有风格**:严格遵循项目既有代码风格与命名规范
