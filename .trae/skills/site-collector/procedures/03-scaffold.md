# 模式A:框架搭建

**触发**:用户新建 Django 项目后,需要搭建采集框架。
**前置**:已完成 [01-analyze.md](01-analyze.md) 分析 + [02-confirm.md](02-confirm.md) 沟通确认。

## 搭建步骤

### 1. 创建爬虫服务目录与骨架
按 [anti-scraping.md](../reference/anti-scraping.md) 的基础骨架创建:
```
SpiderServices/<站点名>/main.py
```
包含:
- 爬虫类(`<站点名>PascalCaseSpider`)
- `HOME_URL` / `HEADERS` 常量
- `__init__`(session + UA/Referer + cookie注入)
- `_get_html`(请求 + 编码处理 + 验证页检测)
- `_pass_verification`(表单验证,按探测结论决定是否需要)
- `_parse_pagination`(分页解析,通用)
- 类 docstring 注明反爬说明 + 凭证配置

### 2. 确认 Django 项目配置
检查 `项目配置包/settings.py`:
- `INSTALLED_APPS` 含 App 配置(如 `'Web.apps.WebConfig'`)
- `STATIC_ROOT` 指向 `<App>/static`
- `MEDIA_ROOT` 指向 `media`
- `TEMPLATES` 的 `APP_DIRS=True`
- `ROOT_URLCONF` 指向根 urls

检查根 `urls.py`:
- `path('', include('Web.views.urls'))`
- 静态/媒体文件服务(DEBUG=False 时手动 serve)
- `handler404` / `handler500`(可选)

若缺失,按现有项目模板补齐。

### 3. 创建基础模板 template.html
若 `Web/templates/template.html` 不存在,创建之。包含:
- `<head>`:meta(charset/viewport/keywords/description) + CSS引入(base.css/layui/Font Awesome) + JS引入(jQuery/common.js)
- `{% block title/keywords/description/head %}`
- `<body>`:`{% include 'common_html/headers.html' %}` + `{% block content %}` + `{% block js %}`

参考现有项目的 template.html 结构。

### 4. 创建公共片段
- `common_html/headers.html`:顶部导航 + logo + 搜索框
- `common_html/footer.html`:底部移动端导航(用 Font Awesome 图标)
- `common_html/friend_links.html`:友情链接(可选)

### 5. 处理目标站 CSS/JS 资源
根据分析结论(下载 or 模仿),与用户确认后执行:

**方案A:下载目标站资源**
- 下载目标站的 CSS 文件到 `Web/static/css/`
- 下载 JS 文件到 `Web/static/js/`
- 下载图片(logo等)到 `Web/static/images/`
- 在 template.html 中引入
- 注意:清理资源里的目标站品牌词/外链,替换为自己的

**方案B:模仿目标站样式自写**
- 分析目标站关键样式类(header/.logo/.nav/.play_list/.page 等)
- 在 `Web/static/css/base.css` 中实现等价样式
- 保持类名一致,便于后续模板复用

### 6. 配置 .env
在 `.env` 添加反爬凭证占位:
```
# <站点名> 爬虫:[反爬说明]
<站点名大写>_PHPSESSID=
```
告知用户去浏览器获取凭证填入。

### 7. 创建目录占位
确保目录存在:
- `Web/static/css/`、`Web/static/js/`、`Web/static/images/`
- `Web/templates/common_html/`
- `media/`

## 交付说明
框架搭建完成后,向用户输出:
- 创建的目录与文件清单
- .env 需填写的凭证
- 如何启动验证(`python manage.py runserver` 访问首页)
- 提示:框架就绪后,可用模式B逐个新增页面采集功能
