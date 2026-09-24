# 数据结构约定

爬虫返回的数据结构遵循统一约定,便于视图兜底与模板渲染。

## 列表页数据结构
```python
{
    'title': '页面标题',           # 从页面 h1 解析
    'total': '89',                 # 总数(可选,从 .pagedata span 解析)
    'items': [                     # 列表项(歌手/电影/壁纸...)
        {'title': '...', 'link': '/...', 'pic': 'https://...'},
    ],
    'filters': [                   # 分类筛选(可选)
        {'label': '歌手分类:', 'options': [
            {'title': '全部', 'link': '/...', 'current': True},
        ]},
    ],
    'pagination': {'links': [      # 分页(复用 _parse_pagination)
        {'text': '1', 'href': '/...', 'class': 'current', 'current': True},
        {'text': '下一页', 'href': '/...', 'class': '', 'current': False},
    ]},
}
```

## 详情页数据结构
```python
{
    'sid': 'bXdua2Njc3ZobQ',       # 目标ID(路径参数回传)
    'info': {                      # 主体信息(歌手/电影/壁纸...)
        'title': '...',
        'pic': 'https://...',
        'intro': '...',
    },
    'items': [                     # 关联列表(歌曲/曲目/剧集...)
        {'title': '...', 'link': '/...'},
    ],
    'total': '89',                 # 关联列表总数(可选)
    'pagination': {'links': [...]},# 关联列表分页(可选)
}
```

## 分页解析(复用)
所有分页统一用 `_parse_pagination`,解析 `div.page` 下所有 `<a>`:
```python
def _parse_pagination(self, tree):
    page_div = tree.xpath('//div[@class="page"]')
    if not page_div:
        return {'links': []}
    links = []
    for a in page_div[0].xpath('.//a'):
        text = (a.text or '').strip()
        if not text:
            continue
        cls = a.get('class', '')
        links.append({
            'text': text,
            'href': a.get('href', ''),
            'class': cls,
            'current': 'current' in cls,
        })
    return {'links': links}
```
**复用原则**:若目标站分页也是 `div.page > a` 结构,直接复用;否则按实际结构新写解析,但返回结构保持 `{links: [{text,href,class,current}]}`。

## 视图兜底(必须)
视图函数必须 try/except,失败时返回**同结构空数据**,保证页面可访问:
```python
def movie_list(request, mtype='index', page=1):
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

## 模板渲染约定
- 列表项循环:`{% for item in items %}`
- 分页渲染:
```html
{% if pagination.links %}
<div class="page">
    {% for link in pagination.links %}
    <a class="{{ link.class }}" {% if link.href %}href="{{ link.href }}"{% endif %}>{{ link.text }}</a>
    {% endfor %}
</div>
{% endif %}
```
- 筛选渲染:
```html
{% for filter in filters %}
<div class="ilingku_fl">
    <li>{{ filter.label }}</li>
    {% for opt in filter.options %}
    <li><a class="{% if opt.current %}current{% endif %}" href="{{ opt.link }}">{{ opt.title }}</a></li>
    {% endfor %}
</div>
{% endfor %}
```
