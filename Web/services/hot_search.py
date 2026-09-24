"""热门搜索榜

数据来源就是 SearchKeyword 自己 —— 每被搜一次 search_count +1
（计数在 Web/services/music_library.py 的 _count_search 里做）。

这里只负责"挑出前几个"并缓存。之所以要缓存：这段代码会被注入到**每个页面**
的 header 里（见 settings.TEMPLATES 的 hot_keywords 上下文处理器），
不能让每次浏览都查一次库。代价是新热门词最多延迟 HOT_SEARCH_CACHE_MINUTES 才上墙。
"""
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from Web.models import SearchKeyword

_CACHE_KEY = 'bz_hot_keywords'


def invalidate():
    """清掉榜单缓存，让下一次访问重新算

    只在一个词「第一次被人搜到」时调用（见 music_library._count_search）：
    否则新词要等 HOT_SEARCH_CACHE_MINUTES 才出现在下拉里，看起来像没生效。
    注意别在每次搜索时都调 —— 那等于把缓存废掉，失去它保护 header 的意义。
    """
    cache.delete(_CACHE_KEY)


def top_keywords():
    """被搜得最多、且最近 HOT_SEARCH_DAYS 天内还活跃的关键词，取前 HOT_SEARCH_COUNT 个

    两类词会被排除：
      - source_empty=True（含被屏蔽的词）：点进去没有任何内容，摆在榜上等于给用户挖坑；
      - crawled_pages=0：搜过但因为网络/解析失败没存下内容，点进去同样是空页。
    """
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached

    since = timezone.now() - timedelta(days=settings.HOT_SEARCH_DAYS)
    words = list(
        SearchKeyword.objects
        .filter(source_empty=False, crawled_pages__gt=0, last_searched_at__gte=since)
        .order_by('-search_count', '-last_searched_at', 'id')
        .values_list('keyword', flat=True)[:settings.HOT_SEARCH_COUNT]
    )
    cache.set(_CACHE_KEY, words, int(settings.HOT_SEARCH_CACHE_MINUTES * 60))
    return words


def hot_keywords(request):
    """上下文处理器：把热门搜索词注入所有模板（header 搜索框的下拉用）"""
    return {'HOT_KEYWORDS': top_keywords()}
