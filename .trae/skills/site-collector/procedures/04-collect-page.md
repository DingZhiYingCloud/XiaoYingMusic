# 模式B:页面采集(核心四件套)

**触发**:项目已有框架,用户提供一个网址,要求新增该页面的采集与展示。
**前置**:已完成 [01-analyze.md](01-analyze.md) 分析 + [02-confirm.md](02-confirm.md) 沟通确认。
**范围**:仅核心四件套(爬虫+视图+路由+模板),不含SEO优化/导航高亮/服务器验证。

## 四件套实施步骤

### 1. 爬虫(SpiderServices/<站点>/main.py)
在对应爬虫类中新增 `fetch_xxx` + `_parse_xxx` 方法。

**列表页示例**:
```python
def fetch_movie_list(self, mtype='index', page=1):
    """抓取电影列表页:分类筛选 / 列表 / 分页

    URL 规则:/movie/{mtype}/{page}.html
    """
    if page > 1:
        url = f'{self.HOME_URL}movie/{mtype}/{page}.html'
    else:
        url = f'{self.HOME_URL}movie/{mtype}.html'
    tree = etree.HTML(self._get_html(url))

    # 标题与总数(按实际XPath调整)
    title_nodes = tree.xpath('//div[@class="play_list"]//h1//text()')
    title = ''.join(title_nodes).strip()

    return {
        'title': title,
        'items': self._parse_movie_list(tree),
        'filters': self._parse_movie_filters(tree, mtype),
        'pagination': self._parse_pagination(tree),
    }

def _parse_movie_list(self, tree):
    """解析电影列表:title / link / pic"""
    result = []
    for li in tree.xpath('//div[@class="video_list"]//ul/li'):
        name_a = li.xpath('.//div[@class="name"]/a')
        if not name_a:
            continue
        img = li.xpath('.//div[@class="pic"]//img/@src')
        result.append({
            'title': name_a[0].get('title', '') or (name_a[0].text or '').strip(),
            'link': name_a[0].get('href', ''),
            'pic': img[0] if img else '',
        })
    return result
```

**详情页示例**:
```python
def fetch_movie(self, sid, page=1):
    """抓取电影详情页:信息 / 播放列表 / 分页"""
    if page > 1:
        url = f'{self.HOME_URL}movie/{sid}/{page}.html'
    else:
        url = f'{self.HOME_URL}movie/{sid}.html'
    tree = etree.HTML(self._get_html(url))
    return {
        'sid': sid,
        'info': self._parse_movie_info(tree),
        'items': self._parse_movie_episodes(tree),
        'pagination': self._parse_pagination(tree),
    }
```

**复用原则**:
- 分页解析复用 `_parse_pagination`(若目标站分页是 `div.page > a` 结构)
- 列表项解析若结构与已有 `_parse_search_results` 一致,直接复用
- 数据结构遵循 [data-conventions.md](../reference/data-conventions.md)

### 2. 视图(Web/views/request.py)
新增视图函数,try/except 降级兜底:
```python
def movie_list(request, mtype='index', page=1):
    """电影列表页:mtype 为分类,page 为页码"""
    try:
        data = MovieXxxSpider().fetch_movie_list(mtype, page)
    except Exception:
        data = {
            'title': '电影列表',
            'items': [],
            'filters': [],
            'pagination': {'links': []},
        }
    return render(request, 'movie_list.html', data)
```
- 视图函数名与 [naming-conventions.md](../reference/naming-conventions.md) 一致
- 兜底数据结构与爬虫返回结构完全一致(空值版本)
- 视图函数放在文件末尾(业务视图区),error 视图(若有)保持在最后

### 3. 路由(Web/views/urls.py)
新增 path 路由:
```python
from Web.views import request  # 已有的import

urlpatterns = [
    # ...已有路由...
    path('movie/<mtype>.html', request.movie_list, name='movie_list'),
    path('movie/<mtype>/<int:page>.html', request.movie_list, name='movie_list_page'),
    path('movie/<sid>.html', request.movie, name='movie'),
]
```
- 路径参数:`<sid>`(字符串)、`<mtype>`(字符串)、`<int:page>`(数字)
- 第1页与第N页可分两条路由
- name 属性用 `<功能>_<页>` 格式

### 4. 模板(Web/templates/<功能>.html)
新建模板,extends template.html:
```html
{% extends 'template.html' %}

{% block title %}{{ title }} - 杯子音乐网{% endblock %}

{% block content %}
<div class="main">
    <div class="layui-container">
        <div class="play_list">
            <div class="title"><h1>{{ title }}</h1></div>
            <ul>
                {% for item in items %}
                <li>
                    <div class="pic"><a href="{{ item.link }}"><img src="{{ item.pic }}" alt="{{ item.title }}"></a></div>
                    <div class="name"><a href="{{ item.link }}">{{ item.title }}</a></div>
                </li>
                {% endfor %}
            </ul>
        </div>
        {% if pagination.links %}
        <div class="page">
            {% for link in pagination.links %}
            <a class="{{ link.class }}" {% if link.href %}href="{{ link.href }}"{% endif %}>{{ link.text }}</a>
            {% endfor %}
        </div>
        {% endif %}
    </div>
</div>
{% include 'common_html/footer.html' %}
{% endblock %}
```
- 复用现有 CSS 类名(`.main`/`.layui-container`/`.play_list`/`.page` 等),不自创新类
- 末尾 `{% include 'common_html/footer.html' %}`
- 详情页模板参考 `singer.html` / `playlist.html` 结构

## 交付说明
实施完成后,向用户输出:
- 改动的 4 个文件及改动点
- 新增的 URL 路径(如 `/movie/index.html`)
- 需配置的 .env 凭证(若新站点首次使用)
- 提示用户重启服务器(若用 --noreload)后访问验证

## 注意事项
- 不做 SEO block 优化(用户未要求)
- 不做导航高亮(用户未要求)
- 不启动服务器验证(用户未要求,但可提示用户如何验证)
- 临时分析文件(`_tmp_*.py`、`.tmp_*.html`)必须清理
