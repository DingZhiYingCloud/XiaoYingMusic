"""分页器链接生成（全站列表页共用）

各列表页的地址规则不同（搜索结果 /so/<关键词>/<页>.html、歌手大全 /singerlist/.../<页>.html），
但"页码窗口、省略号、上一页/下一页"这套规则完全一样，所以只把公共部分抽在这里，
每一页的地址怎么拼由调用方传进来。

产出格式与 common_html/pager.html 对齐：每条含 text / href / current，
href 为 None 表示这一格不可点（已到首/尾页，或只是省略号占位）。
"""


def build(page, total_pages, url_for):
    """生成分页链接

    :param page: 当前页（从 1 开始）
    :param total_pages: 总页数
    :param url_for: 函数，接收页码返回该页地址
    """
    total = max(1, total_pages)
    if total <= 1:
        return []

    # 页码窗口：首尾两页常驻，当前页左右各两页，中间断开处用省略号占位
    numbers = {1, total}
    numbers.update(range(max(1, page - 2), min(total, page + 2) + 1))

    links = [{'text': '上一页', 'href': url_for(page - 1) if page > 1 else None,
              'current': False}]
    previous = 0
    for number in sorted(numbers):
        if previous and number - previous > 1:
            links.append({'text': '…', 'href': None, 'current': False})
        links.append({'text': str(number), 'href': url_for(number),
                      'current': number == page})
        previous = number
    links.append({
        'text': '下一页',
        'href': url_for(page + 1) if page < total else None,
        'current': False,
    })
    return links
