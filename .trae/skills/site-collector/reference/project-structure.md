# 项目目录结构与文件职责

当前 Django 项目的标准结构。新增采集功能时,改动集中在以下 4 处。

```
项目根/
├── manage.py
├── .env                              # 环境变量(反爬凭证等,如 MUSIC_2T58_PHPSESSID)
├── BeiZiMusic/                       # Django 项目配置包
│   ├── settings.py                   # INSTALLED_APPS 含 'Web.apps.WebConfig'
│   │                                 # STATICFILES_DIRS = Web/static(直接对外服务), MEDIA_ROOT = media
│   │                                 # TEMPLATES: APP_DIRS=True(模板从 app 目录加载)
│   ├── urls.py                       # 根路由: path('', include('Web.views.urls'))
│   │                                 #   + handler404/500 + 静态/媒体服务
│   └── wsgi.py
├── SpiderServices/                   # 爬虫服务(每站独立目录)
│   └── <站点名>/                      # 如 Music_2t58 / Movie_xxx
│       └── main.py                   # 爬虫类(如 Music2t58Spider),所有 fetch_xxx 方法
├── Web/                              # Django App
│   ├── apps.py                       # WebConfig (name='Web')
│   ├── static/                       # 静态资源
│   │   ├── css/base.css              # 全局样式(含 header/.logo/.play_list/.page 等)
│   │   ├── js/common.js              # 导航高亮等公共逻辑
│   │   ├── js/play.js                # 播放器逻辑(音乐站)
│   │   ├── images/                   # logo 等图片
│   │   └── layui/                    # layui 前端框架
│   ├── templates/
│   │   ├── template.html             # 基础模板(block: title/keywords/description/content/js)
│   │   ├── common_html/              # 公共片段
│   │   │   ├── headers.html          # 顶部导航+logo+搜索
│   │   │   ├── footer.html           # 底部移动端导航
│   │   │   └── friend_links.html     # 友情链接
│   │   ├── index.html                # 首页
│   │   ├── <功能>_list.html          # 列表页(如 singer_list.html / new_songs.html)
│   │   ├── <功能>.html               # 详情页(如 singer.html / song.html)
│   │   ├── 404.html / 500.html       # 错误页
│   │   └── robots.txt
│   └── views/
│       ├── request.py                # 视图函数(try/except 降级 + render)
│       └── urls.py                   # 路由(path 参数风格)
└── media/                            # 媒体文件(favicon 等)
```

## 新增一个采集功能的 4 处改动点

| 序号 | 文件 | 改动 |
|------|------|------|
| 1 | `SpiderServices/<站点>/main.py` | 新增 `fetch_xxx` + `_parse_xxx` 方法 |
| 2 | `Web/views/request.py` | 新增视图函数(try/except + render) |
| 3 | `Web/views/urls.py` | 新增 `path(...)` 路由 |
| 4 | `Web/templates/<功能>.html` | 新建模板(extends template.html) |

## 基础模板 template.html 的 block 结构
```html
{% extends 'template.html' %}
{% block title %}...{% endblock %}
{% block keywords %}...{% endblock %}
{% block description %}...{% endblock %}
{% block content %}
    ...页面内容...
    {% include 'common_html/footer.html' %}
{% endblock %}
{% block js %}...{% endblock %}
```
- `template.html` 已引入 base.css、layui、jQuery、Font Awesome、common.js
- 自定义 JS/CSS 通过 `{% block head %}` 或 `{% block js %}` 追加
