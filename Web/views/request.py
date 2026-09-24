# 项目URL配置
import io
import re
import zipfile
from urllib.parse import quote

from django.shortcuts import redirect, render
from django.http import HttpResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.core.cache import cache
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import requests

from SpiderServices.Music_2t58.main import Music2t58Spider

from Web.services import music_library, play_rank, singer_library


# ============ 榜单名称映射（原名称 → 本站文艺风新名称） ============
# 说明：为规避与源站（2t58.com）的名称雷同，将源站榜单名称统一替换为本站
# 名称。右侧注释保留源站原名，方便后续开发者对照定位。
# 注意：只服务榜单（/list/<chart>.html）。歌手大全的标题见 singer_library.ALL_TITLE，
#       首页「今日热听榜」的名字写在 index.html 里。
RENAME_MAP = {
    # —— 榜单页热榜合集（原：热门榜单侧栏 33 项）——
    'DJ舞曲大全': '律动电音集',
    '音乐热评榜': '乐评精选榜',
    '音乐先锋榜': '先锋新势力',
    '爱听电音榜': '幻彩电音榜',
    '车载歌曲榜': '旅途音乐榜',
    '英国排行榜': '英伦之声榜',
    '韩国排行榜': '韩流风尚榜',
    '日本排行榜': '和风旋律榜',
    '快手热歌榜': '短视频热歌榜',
    '抖音热歌榜': '抖音爆款榜',
    '酷我原创榜': '原创力量榜',
    'ACG新歌榜': '动漫新曲榜',
    '酷我飙升榜': '酷炫上升榜',
    '电音热歌榜': '电音浪潮榜',
    '综艺新歌榜': '综艺新声榜',
    '说唱先锋榜': '说唱前沿榜',
    '影视金曲榜': '影视原声榜',
    '粤语金曲榜': '粤语经典榜',
    '欧美金曲榜': '欧美流行榜',
    '80后热歌榜': '怀旧八零榜',
    '网红新歌榜': '网红新势力',
    '古风音乐榜': '古韵雅音榜',
    '夏日畅爽榜': '夏日清凉榜',
    '会员喜爱榜': '人气甄选榜',
    '跑步健身榜': '燃动健身榜',
    '宝宝哄睡榜': '安睡摇篮榜',
    '睡前放松榜': '夜色舒缓榜',
    '熬夜修仙榜': '夜猫陪伴榜',
    'Vlog必备榜': 'Vlog标配榜',
    'KTV点唱榜': 'KTV欢唱榜',
    '通勤路上榜': '通勤随身榜',
    '网络红歌榜': '网络热歌榜',
    '网络最新榜': '网际新声榜',
}

# 榜单页大标题兜底（原榜单名：new=新歌榜 / top=TOP榜单 / djwuqu=DJ舞曲大全）
CHART_ID_TITLES = {
    'new': '新声速递',
    'top': '巅峰热榜',
    'djwuqu': '律动电音集',
}


def rename_title(title):
    """将源站榜单名称映射为本站名称，未命中时原样返回"""
    return RENAME_MAP.get(title, title)


def index(request):
    # 首页三块数据全部取自本地库，一次源站都不请求（见 README 8.11）：
    #   歌手推荐   → 本地歌手名册（Web/services/singer_library.py）
    #   今日热听榜 → 每日播放榜（Web/services/play_rank.py）
    #   随机点唱机 → 本地歌手曲目库随机取样（Web/services/singer_library.py）
    # 所以这里不再调爬虫的 fetch_home()，也就没有"源站挂了首页就空"这回事。
    return render(request, 'index.html', {
        'hot_singers': singer_library.home_singers(),
        'rising_songs': play_rank.today_top(),
        'random_songs': singer_library.random_songs(),
    })


def singer(request, sid, page=1):
    # 歌手详情页：sid 为歌手id，page 为歌曲列表页码（路径参数）
    # 曲目列表优先读本地库（manage.py sync_singer_songs 同步下来的 SingerSong）：
    # 0 请求、毫秒级返回，也不再受 2 小时页面缓存到期的影响。
    # 库里还没有这位歌手、或他要看的是第 2 页及以后（深分页未入库）时回落到爬虫，
    # 所以未同步完也能正常访问，不会出现空白页。
    local = singer_library.page_songs(sid, page)
    if local is not None:
        return render(request, 'singer.html', local)
    try:
        data = Music2t58Spider().fetch_singer(sid, page)
    except Exception:
        data = {
            'sid': sid,
            'singer': {},
            'songs': [],
            'pagination': {'links': []},
        }
    return render(request, 'singer.html', data)


def song(request, sid):
    # 歌曲详情页：sid 为歌曲id（路径参数）
    try:
        data = Music2t58Spider().fetch_song(sid)
    except Exception:
        data = {
            'song': {},
            'play_url': '',
            'lyrics': '',
            'daily_recommend': [],
        }
    data['sid'] = sid  # 供模板下载弹窗拼接下载地址
    return render(request, 'song.html', data)


def search(request, keyword, page=1):
    # 搜索页：keyword 为搜索关键词，page 为页码（路径参数）
    # 走本地曲库（见 Web/services/music_library.py）：库里有的直接返回，没爬过的才回源站补货，
    # 目的是把访问 2t58 的次数压到最低，避免请求过密被封 IP。
    # 上下文结构：keyword / results / pagination / blocked（被屏蔽时模板给不同提示）。
    # 空关键词（有人手敲 /so/%20%20.html 这类地址）直接退回首页：
    # 留着只会渲染出一个「搜索「」」的空壳页，对用户和搜索引擎都是垃圾页。
    if not keyword.strip():
        return redirect('home')
    return render(request, 'search.html',
                  music_library.search_page(keyword, page, _client_ip(request)))


def _client_ip(request):
    """访客 IP：给热门榜的防刷去重用

    优先取反向代理带的 X-Forwarded-For 第一跳（真实访客地址），取不到再退回直连地址。
    """
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


# 歌曲 id 的合法形态：源站是 6~12 位 base62（如 d3dkc2t3 / ZGNua2t4dw）。
# 播放计数接口收的是外部输入，先卡一道格式，免得有人往里塞任意字符串把表撑大。
SID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,32}$')


# csrf_exempt 是**预留**的：项目当前没启用 CsrfViewMiddleware（见 README 上线清单），
# 一旦启用，这个接口会直接 403 —— 而前端是 catch 忽略失败的，榜单会静默停掉、
# 不报任何错，很难查。这个接口只接收 sid/歌名/歌手并做计数，没有会话语义，
# 保持豁免即可；启用 CSRF 时不要顺手把这一行删掉。
@csrf_exempt
@require_POST
def play_ended(request):
    """整首播完回调：给这首歌的今日听完次数 +1（首页「今日热听榜」的数据源）

    只有播放页在 audio 的 ended 事件里会调它，所以这个计数天然是"完整听完一遍"。

    只收 sid / name / artists 三个字段。歌名和歌手后端没有（本地 Song 表只沉淀被搜到
    的歌，播放页大多从歌手页/榜单进来），只能跟着播放页一起上来；既然是外部输入，
    格式与长度都在这里和服务层卡住。

    写库异常在 play_rank.record 里已消化（只记日志/告警），所以访客正在听的歌
    不会因为计数失败而出错。
    """
    sid = (request.POST.get('sid') or '').strip()
    if not SID_PATTERN.match(sid):
        return HttpResponseBadRequest('sid 不合法')
    play_rank.record(sid, request.POST.get('name', ''), request.POST.get('artists', ''),
                     _client_ip(request))
    return HttpResponse(status=204)


def chart(request, chart='new', page=1):
    # 榜单页：chart 为榜单标识（如 new、djwuqu），page 为页码（路径参数）
    try:
        data = Music2t58Spider().fetch_chart(chart, page)
    except Exception:
        data = {
            'title': '新声速递',        # 原：新歌榜
            'songs': [],
            'pagination': {'links': []},
            'hot_rankings': [],
        }
    # 热榜合集名称映射（规避源站榜单名）
    for item in data.get('hot_rankings', []):
        item['title'] = rename_title(item['title'])
    # 页面大标题：优先取当前榜单映射名，否则按 chart 标识兜底（<title> 仍保留源站 SEO 长句）
    page_title = next(
        (item['title'] for item in data.get('hot_rankings', []) if item.get('current')),
        None
    )
    data['page_title'] = page_title or CHART_ID_TITLES.get(chart, '热歌榜')
    return render(request, 'new_songs.html', data)


def singer_list(request, area='index', gender='index', style='index', letter='index', page=1):
    # 歌手大全：数据来自本地歌手名册（见 Web/services/singer_library.py）。
    # 库里没有分类数据（源站也不给结构化的地区/性别/类型），所以只做全量分页：
    # 带分类参数的地址（搜索已收录的旧链接、外链）一律 301 收敛到全量页 ——
    # 不收敛的话，同一个列表会在几千个不同 URL 上重复出现，搜索引擎会当成重复内容。
    if (area, gender, style, letter) != singer_library.ALL_FILTERS:
        return redirect(singer_library.page_url(page), permanent=True)
    return render(request, 'singer_list.html', singer_library.page_singers(page))


# ============ 歌曲下载（后端代理，防防盗链与链接过期） ============
# 说明：源站 CDN（如 kuwo）防盗链规则为拒绝源站域名 Referer（2t58.com→403），
#       因此代理 CDN 直链时必须不带 Referer（无 Referer 或 CDN 自身域名→200）。
def _attachment_name(filename):
    """生成支持中文文件名的 Content-Disposition（RFC 5987 filename*）"""
    return f"attachment; filename*=UTF-8''{quote(filename)}"


# CDN 直链请求头：仅带浏览器 UA，不带 Referer（避免触发 CDN 防盗链 403）
_CDN_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/131.0.0.0 Safari/537.36'
    ),
}

# 打包下载的大小上限（字节）：一首 320kbps 的歌约 10~15MB，50MB 足够宽松，
# 只用来兜住"直链给错、指向一个巨大文件"这种异常情况。
ZIP_MAX_BYTES = 50 * 1024 * 1024


def _fetch_cdn_stream(play_url):
    """请求源站 CDN 直链（不带 Referer），返回流式响应；失败返回 None"""
    if not play_url:
        return None
    try:
        resp = requests.get(play_url, headers=_CDN_HEADERS, stream=True, timeout=15)
        resp.raise_for_status()
        return resp
    except requests.RequestException:
        return None


def _proxy_mp3(play_url, base):
    """后端代理下载 MP3：无 Referer 流式转发 CDN 直链，避免防盗链 403"""
    resp = _fetch_cdn_stream(play_url)
    if resp is None:
        return HttpResponse('播放链接获取失败，请稍后重试', status=502)

    def stream():
        try:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            resp.close()

    response = StreamingHttpResponse(stream(), content_type='audio/mpeg')
    response['Content-Disposition'] = _attachment_name(f'{base}.mp3')
    return response


def _text_attachment(text, filename):
    """返回文本附件（歌词 .lrc）"""
    response = HttpResponse(text, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = _attachment_name(filename)
    return response


def _zip_attachment(play_url, lyrics, base):
    """MP3 + 歌词打包为 zip（标准库 zipfile，无额外依赖）

    直链文件多大由源站决定，不由我们控制，所以按块读、超限即中止：
    原来的写法用 resp.content 一次把整个文件读进内存，直链一旦给错
    （指到一个大文件上），一个下载请求就能把进程内存撑爆。
    """
    resp = _fetch_cdn_stream(play_url)
    if resp is None:
        return HttpResponse('播放链接获取失败，请稍后重试', status=502)
    mp3 = io.BytesIO()
    total = 0
    try:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > ZIP_MAX_BYTES:
                return HttpResponse('歌曲文件过大，请改用 MP3 单独下载', status=502)
            mp3.write(chunk)
    finally:
        resp.close()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f'{base}.mp3', mp3.getvalue())
        if lyrics:
            zf.writestr(f'{base}.lrc', lyrics)
    response = HttpResponse(buf.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = _attachment_name(f'{base}.zip')
    return response


def download(request, sid, kind='mp3'):
    """歌曲下载：kind 为 mp3（仅歌曲）/ lrc（仅歌词）/ all（两个一起打包zip）

    **当前暂停开放**（后续会恢复）：歌详页的下载入口已撤（见 Web/templates/song.html），
    这里也直接 404，免得旧地址/收藏继续能下载。重新开放时删掉下面那行 return 即可 ——
    下面的实现与 _proxy_mp3 / _text_attachment / _zip_attachment 都保持可用、未删改。

    后端统一经爬虫获取数据，直链经无 Referer 代理转发，
    避免直链过期或防盗链导致下载失败；文件名用歌曲名-歌手。
    """
    # ↓↓↓ 下载功能暂停开放（后续会恢复）：删掉这一行即可重新启用 ↓↓↓
    return HttpResponse('下载功能暂未开放', status=404)

    spider = Music2t58Spider()
    try:
        data = spider.fetch_download(sid)
    except Exception:
        return HttpResponse('下载数据获取失败，请稍后重试', status=502)

    song = data.get('song') or {}
    if not song:
        # sid 不存在时，源站歌曲页里解析不出任何歌曲信息 —— 这是地址不对，不是服务端故障，
        # 所以返回 404 而不是 502（502 会让搜索引擎和监控以为是我们的后端挂了）。
        return HttpResponse('找不到这首歌', status=404)
    name = song.get('name') or f'song_{sid}'
    artists = '/'.join(song.get('artists') or []) or '未知歌手'
    # 清理文件名非法字符（Windows 不允许 \ / : * ? " < > | 和连续空格）
    base = re.sub(r'[\\/:*?"<>|\s]+', '_', f'{name} - {artists}').strip('_') or f'song_{sid}'

    if kind == 'mp3':
        return _proxy_mp3(data.get('play_url', ''), base)
    if kind == 'lrc':
        return _text_attachment(data.get('lyrics', ''), f'{base}.lrc')
    if kind == 'all':
        return _zip_attachment(data.get('play_url', ''), data.get('lyrics', ''), base)
    return HttpResponse('不支持的下载类型', status=400)


def error_404(request, exception=None):
    """404 错误页：访问不存在的路径或文件时返回（DEBUG=False 时生效）"""
    return render(request, '404.html', status=404)


def error_500(request, exception=None):
    """500 错误页：服务器内部错误时返回（DEBUG=False 时生效）"""
    return render(request, '500.html', status=500)


# ============ Sitemap（站点地图） ============
# 只放**当前确实可被收录**的页面。sitemap 里出现 noindex 页面是搜索引擎明确不建议的，
# 所以每一项的收录条件都跟页面模板里的 robots 判定保持同一份数据源：
#   首页          本地名册非空才列      （模板判的是 hot_singers / random_songs，同源本地库）
#   榜单页        抓得到歌才列          （模板判的是 songs，见 new_songs.html）
#   歌手大全分页  本地名册非空才列      （模板判的是 singers，见 singer_list.html）
#   歌手详情页    只列本地名册里的那 24 位（模板判的是 singer.name）
#   歌曲详情页    只能从源站榜单现取，抓不到就整体跳过（那时页面是空壳，本身也是 noindex）
#
# lastmod 只写**有可信来源**的页面，宁缺勿假 —— Google 对不准的 lastmod 会直接打折扣：
#   歌手详情页    该歌手曲目的同步时间（songs_synced_at，没同步过就用名册的 pulled_at）
#   歌手大全分页  该页那 96 位里最近一次同步的时间（见 singer_library.roster_updates）
#   首页          今天最近一次"整首听完"的时间（今日热听榜就是这一刻变的）
#   榜单页/歌曲页 内容来自源站实时抓取，本地没有可信时间戳，干脆不写
#
# 生成的**路径**列表缓存 6 小时，绝对地址（<loc>）在每次响应时按当前请求的域名拼，
# 所以换域名不会留下旧域名的死链。原理见 README 第十五章。
SITEMAP_CHARTS = [
    ('/list/new.html', 'new'),
    ('/list/top.html', 'top'),
    ('/list/djwuqu.html', 'djwuqu'),
]
SITEMAP_CACHE_KEY = 'sitemap_urls_v2'
SITEMAP_CACHE_TTL = 60 * 60 * 6  # 6 小时
# 键名带版本号是刻意的：缓存值是这个列表本身，一旦列表元素的含义/长度变了（比如
# 这里从 3 元组变成带 lastmod 的 4 元组），服务器上还没过期的旧缓存会在解包时抛异常，
# 让 /sitemap.xml 500 到缓存过期为止。改结构时顺手把版本号加一最省事。
# （缓存目录 cache/ 不入库，所以本地清缓存影响不到服务器。）


def _chart_has_songs(chart):
    """榜单页当前有没有歌 —— 跟 new_songs.html 的 robots 用同一份数据

    顺带把这个榜单的页面缓存捂热（两边用的是同一个缓存键）。
    只在生成 sitemap 时跑（6 小时一次），不影响正常访问。
    """
    try:
        return bool((Music2t58Spider().fetch_chart(chart, 1) or {}).get('songs'))
    except Exception:
        return False


def _build_sitemap_urls():
    """收集 sitemap 的 URL：(路径, changefreq, priority, lastmod 或 None)"""
    urls = []
    seen = set()

    def add(path, freq, prio, lastmod=None):
        if path and path not in seen:
            seen.add(path)
            urls.append((path, freq, prio, lastmod))

    # 首页歌手墙那 24 位，同时也是名册的前 24 位
    singers = singer_library.home_singers()
    if singers:
        # 首页可见内容跟着「今日热听榜」变；今天还没人听完任何一首就不写 lastmod
        add('/', 'daily', '1.0', play_rank.latest_play_at())

    for path, chart in SITEMAP_CHARTS:
        if _chart_has_songs(chart):
            add(path, 'daily', '0.8')

    if singers:
        updates = singer_library.roster_updates()
        for page in range(1, singer_library.total_pages() + 1):
            add(singer_library.page_url(page), 'daily', '0.8', updates.get(page))
        for singer in singers:
            add(singer.link, 'weekly', '0.6',
                singer.songs_synced_at or singer.pulled_at)

    try:
        data = Music2t58Spider().fetch_home()
    except Exception:
        data = {}
    for song in (data.get('rising_songs') or []) + (data.get('trending_songs') or []):
        add(song.get('link'), 'weekly', '0.6')

    return urls


def sitemap(request):
    """sitemap.xml：本地歌手库 + 源站榜单歌曲，路径列表缓存 6 小时

    单文件 sitemap 的上限是 5 万条 URL / 50 MB（未压缩），本站只有几百条，够用很久；
    真要超了再拆成 sitemap index（见 README 第十五章）。
    """
    urls = cache.get(SITEMAP_CACHE_KEY)
    if urls is None:
        urls = _build_sitemap_urls()
        cache.set(SITEMAP_CACHE_KEY, urls, SITEMAP_CACHE_TTL)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, freq, prio, lastmod in urls:
        loc = request.build_absolute_uri(path)
        # lastmod 用 W3C Datetime（秒级 + 时区偏移，形如 2026-09-24T19:16:07+08:00）。
        # 刻意不带微秒：那不属于 W3C Datetime 的标准写法，个别解析器会判为无效；
        # 也不省略时区，否则搜索引擎会按 UTC 理解。
        stamp = (f'<lastmod>{timezone.localtime(lastmod).isoformat(timespec="seconds")}'
                 f'</lastmod>' if lastmod else '')
        lines.append(f'  <url><loc>{loc}</loc>{stamp}'
                     f'<changefreq>{freq}</changefreq><priority>{prio}</priority></url>')
    lines.append('</urlset>')
    return HttpResponse('\n'.join(lines), content_type='application/xml')
