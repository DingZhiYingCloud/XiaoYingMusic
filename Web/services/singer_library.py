"""歌手库：把已入库的歌手名册与曲目列表供页面读取

名册由 `manage.py sync_singers` 全量落库（见 Web/models.py 的 Singer，实测 23,435 位），
曲目列表由 `manage.py sync_singer_songs` 逐位落库（见 Web/models.py 的 SingerSong），
所以这几个页面不必再回源站：
    首页 24 个头像、歌手大全每页 96 位、歌手详情页第 1 页的曲目列表、首页「随机点唱机」
歌手详情页第 2 页及以后仍走爬虫（曲目深分页没入库，见 sync_singer_songs 的说明）。

库里**没有分类数据**，源站也不提供结构化的地区/性别/类型（实测：歌手列表页每个条目
只有「图 + 名字」，歌手详情页只有「名字 + 头像 + 一段简介正文」）。所以歌手大全只做
全量分页、不带筛选；带分类参数的旧地址由视图 301 收敛到全量页，详见 README。
"""
from django.conf import settings
from django.core.cache import cache
from django.http import Http404

from Web.models import Singer, SingerSong
from Web.services import pager

# 每页几位：跟源站对齐（源站列表页也是 96 位），免得"每页几条"一改、旧链接的页码就对不上
PAGE_SIZE = 96
# 首页歌手墙显示几位（源站首页给的也是 24 个）
HOME_COUNT = 24
# 首页「随机点唱机」整批缓存的缓存键
RANDOM_CACHE_KEY = 'bz_random_pick'
# 列表页标题。源站原名叫「全部歌手列表」，本站统一叫「歌手大全」
# （其它列表页的改名对照见 Web/views/request.py 的 RENAME_MAP）
ALL_TITLE = '歌手大全'
# 四个筛选维度全为 index 时才是"全部歌手"页
ALL_FILTERS = ('index', 'index', 'index', 'index')


def page_url(page):
    """歌手大全某页的地址，与 Web/views/urls.py 的路由一致：第 1 页不带页码"""
    base = '/singerlist/index/index/index/index'
    page = max(1, int(page))
    return f'{base}.html' if page == 1 else f'{base}/{page}.html'


def home_singers():
    """首页歌手墙：按入库顺序取前 24 位

    入库顺序就是源站「歌手大全」列表的默认顺序（半吨兄弟、汪苏泷、S.H.E、周深…），
    库里没有"热门"这个维度，所以不另外排序 —— 固定不变，对页面缓存和 SEO 都更稳。
    """
    return list(Singer.objects.order_by('id')[:HOME_COUNT])


def random_songs():
    """首页「随机点唱机」：从已入库的歌手曲目里随机取一批

    数据源是本地 SingerSong（3 万多首），一次源站都不请求。
    整批缓存 RANDOM_PICK_TTL_HOURS 小时后再摇下一批：
    一是 `ORDER BY RANDOM()` 要扫全表（实测约 9ms），不该每个首页请求都跑一遍；
    二是只有这样才存在"一批"这个概念 —— 换批由缓存过期后的第一个访客触发，不是整点。
    """
    cached = cache.get(RANDOM_CACHE_KEY)
    if cached is not None:
        return cached
    rows = SingerSong.objects.order_by('?')[:settings.RANDOM_PICK_COUNT]
    songs = [{'title': row.title, 'link': row.link} for row in rows]
    # 空库不写缓存：否则清过表/刚迁移完的站点，首页会一直空到缓存过期
    if songs:
        cache.set(RANDOM_CACHE_KEY, songs, int(settings.RANDOM_PICK_TTL_HOURS * 3600))
    return songs


def _singer_url_for(sid):
    """歌手页某页的地址，与 Web/views/urls.py 的路由一致：第 1 页不带页码"""
    def url_for(page):
        page = max(1, int(page))
        return f'/singer/{sid}.html' if page == 1 else f'/singer/{sid}/{page}.html'
    return url_for


def page_songs(sid, page):
    """歌手页的曲目列表：本地有就返回模板上下文，没有就返回 None（调用方回落到爬虫）

    只有"这位歌手已经用 sync_singer_songs 同步过、且这一页确实在库里"才走本地。
    第 2 页及以后目前没有入库（只同步了第 1 页），这类请求会返回 None 交给爬虫，
    不会拿空列表糊弄用户 —— 源站那一页有歌，我们本地只是没存。
    第 1 页即使是空列表也照常返回：那是"这位歌手在源站就没有歌"，与"没同步过"是两回事。
    """
    singer = Singer.objects.filter(sid=sid, songs_synced_at__isnull=False).first()
    if singer is None:
        return None
    rows = SingerSong.objects.filter(singer_sid=sid, page=page).order_by('position')
    songs = [{'title': row.title, 'link': row.link} for row in rows]
    if not songs and page > 1:
        return None
    return {
        'sid': sid,
        'singer': {'name': singer.name, 'pic': singer.pic, 'intro': singer.intro},
        'songs': songs,
        # song_pages 是同步时从源站分页区读来的真实末页，没有分页时按 1 页算
        'pagination': {'links': pager.build(page, max(1, singer.song_pages),
                                            _singer_url_for(sid))},
    }


def _page_count(total):
    """总页数（向上取整；空库按 1 页算，保证第 1 页永远存在）"""
    return max(1, -(-total // PAGE_SIZE))


def roster_updates():
    """歌手大全每一页的最近修改时间：{页码: datetime}

    每页内容就是名册里连续 96 位（按 id 升序），所以"这一页变没变"取决于这 96 位里
    最近一次被同步的时间。一次查询取全量 pulled_at 再按页切片，比每页各发一条聚合
    查询省 244 次往返 —— sitemap 会一次问全部页。空库返回 {}。
    """
    updates = {}
    for index, pulled_at in enumerate(Singer.objects.order_by('id')
                                      .values_list('pulled_at', flat=True)):
        page = index // PAGE_SIZE + 1
        if page not in updates or pulled_at > updates[page]:
            updates[page] = pulled_at
    return updates


def total_pages():
    """歌手大全总页数"""
    return _page_count(Singer.objects.count())


def page_singers(page):
    """歌手大全第 page 页，返回模板需要的上下文

    页码越界（如只有 245 页却请求第 300 页）直接 404，不返回"200 的空页面"：
    否则任何一个页码都会得到一张可访问的空壳页，搜索引擎能收录出无穷多个重复页。
    """
    page = max(1, int(page))
    total = Singer.objects.count()
    pages = _page_count(total)
    if page > pages:
        raise Http404(f'歌手大全只有 {pages} 页')
    offset = (page - 1) * PAGE_SIZE
    return {
        'title': ALL_TITLE,
        'singers': list(Singer.objects.order_by('id')[offset:offset + PAGE_SIZE]),
        'total': total,
        'pagination': {'links': pager.build(page, pages, page_url)},
    }
