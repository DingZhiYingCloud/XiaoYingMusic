"""曲库服务：把搜索结果沉淀到本地，同一个关键词不必反复回源站爬

必要性：
    2t58 有请求频率限制，用户每搜一次就实时回爬一次，很容易被封 IP。
    搜到的歌曲沉淀下来之后，同一个关键词第二次被搜到就零请求返回。

三张表（见 Web/models.py）：
    Song           一首歌（sid 唯一），pulled_at = 最近一次在源站见到它的时间
    SearchKeyword  关键词的抓取进度 + 被搜次数（热门榜用）
    SearchResult   关键词 → 歌曲 的对应关系（源站为此关键词返回过哪些歌、在第几页第几位）

为什么原样记录关键词，而不是自己判断"像不像"：
    源站的搜索行为不可控（实测：歌手名能搜到，但「周杰伦」被单独屏蔽返回空；
    搜「音乐」返回 68 条、里面一条都不含"音乐"）。靠"歌名/歌手含关键词"去猜，
    得到的集合既不全也不准，还会让"够不够翻页"永远判错，于是每次请求都把爬取预算
    烧光 —— 正好与"尽量减少回源次数"相反。所以只认一件事：源站对这个关键词返回过什么。

页码与源站一一对应：
    展示页 = 源站页（2t58 是一页 68 条），取第 P 页就是"筛出 source_page == P 的行"。
    这样页码不会错位，也不用维护"本地一页 20 条 vs 源站一页 68 条"的换算。

一次搜索的四个分支：
    ① 这一页爬过、关键词没过期            → 直接切页返回              零请求
    ② 这一页爬过、关键词过期了            → 先重爬刷新，再切页返回
    ③ 这一页还没爬                        → 从水位线往后补货后再切
    ④ 库里完全没有这个关键词              → 同 ③，从源站第 1 页开始

单次请求的爬取预算（SEARCH_REFILL_MAX_PAGES，默认 3 页）由「刷新」与「补货」共用：
    避免有人直接请求一个巨大页码，把搜索页变成"按键就爬"的入口。
    分页器的页码上限也是"已爬 + 这个数"，所以每个能点开的页码都真有内容。
"""
import logging
import threading
import urllib.parse
from datetime import timedelta

from django.conf import settings
from django.db import OperationalError
from django.db.models import F
from django.utils import timezone

from SpiderServices.Music_2t58.main import Music2t58Spider
from Web.models import SearchKeyword, SearchResult, Song
from Web.services import db_alert, hot_search, pager

logger = logging.getLogger(__name__)

# 进程内锁池：按关键词哈希取锁，池子定长，避免长尾关键词把锁字典越撑越大。
# 不同关键词偶尔撞到同一把锁只会短暂串行，不影响正确性。
# 有了它，全新关键词第一次被搜到时，同时到达的多个请求里只有一个真去爬源站。
_LOCK_POOL_SIZE = 64
_lock_pool = [threading.Lock() for _ in range(_LOCK_POOL_SIZE)]


def search_page(keyword, page, client_ip=''):
    """搜索关键词第 page 页，返回模板需要的上下文

    返回结构在原来的 keyword / results / pagination 之外，多一个 blocked，
    供模板在"该关键词被屏蔽"时给出不同的提示。
    """
    keyword = (keyword or '').strip()
    # 按 SEARCH_KEYWORD_MAX_CHARS 截断（默认 15 字，.env 可配）：搜索框的 maxlength 只拦得住
    # 正常打字，手敲超长 URL 依然能进来，所以后端用同一个值再截一次。
    # 截完还要兜一道数据库上限（字段 max_length=255）：SQLite 不校验长度会照单全收，
    # 但换成 PostgreSQL / MySQL 后超长会抛 DataError，把搜索页打成 500。
    keyword = keyword[:min(settings.SEARCH_KEYWORD_MAX_CHARS, 255)]
    page = max(1, int(page))
    if not keyword:
        return {'keyword': keyword, 'results': [], 'blocked': False,
                'pagination': {'links': []}}

    refill_budget = max(0, settings.SEARCH_REFILL_MAX_PAGES)

    # 热门榜计数：只算第 1 页（翻页不算一次新的搜索），并按 IP 防刷。
    if page == 1:
        _count_search(keyword, client_ip)

    state = _get_state(keyword)
    # 负缓存闸门：源站已经确认过这个关键词拿不到东西、且还没过保鲜期，就一次都不爬。
    # 没有这道闸，搜一个查不到的词每次都会把爬取预算烧光 —— 结果永远是空，却每次都爬满几页。
    may_crawl = not (state.source_empty and not _is_stale(state))

    # 闸门 1：关键词过期 → 先刷新一次。
    # 不刷新的话库会永远停在入库那天的内容，新歌再也进不来。
    # 刷新的两页是关键的两页：第 1 页看新上榜的歌，水位线那页看有没有多出新页。
    # 这 2 页**不占用**下面的补货额度：两者共用一份额度的话，过期刷新会先吃掉 2 页，
    # 补货只剩 1 页 —— 而分页器仍按"已爬 + 3"给出页码，用户点过去就是一张空白页。
    # refill_budget > 0 是"允许回源"的总开关：配成 0 表示完全不回源，刷新也一并停掉。
    if may_crawl and refill_budget > 0 and state.crawled_pages and _is_stale(state):
        with _lock_for(keyword):
            state = _get_state(keyword)     # 双检：可能刚才已经有别的请求刷过了
            if _is_stale(state):
                last_crawled = state.crawled_pages
                _crawl_page(keyword, 1)
                if last_crawled > 1:
                    _crawl_page(keyword, last_crawled)

    # 闸门 2：这一页还没爬到 → 从水位线往后补，直到爬到这一页或撞预算上限
    while may_crawl and refill_budget > 0 and _get_state(keyword).crawled_pages < page:
        with _lock_for(keyword):
            # 双检是关键：只加锁不去重，10 个并发请求会排队各爬一遍；
            # 拿到锁后重新看一遍库，别人刚补好的话这里就直接退出。
            if _get_state(keyword).crawled_pages >= page:
                break
            if not _crawl_next_page(keyword):
                break
        refill_budget -= 1

    return _render(keyword, page)


# ---------- 查询 ----------

def _get_state(keyword):
    """取（或建）关键词的抓取进度

    并发首次搜索同一个新词时 get_or_create 可能撞唯一约束，
    Django 内部会捕获并改成读取已有行，不会抛出来。
    """
    state, _ = SearchKeyword.objects.get_or_create(keyword=keyword)
    return state


def _is_stale(state):
    """关键词是否已过保鲜期

    被屏蔽的词用更长的保鲜期：屏蔽不会自愈，隔 12 小时就重试一次纯属白跑，
    所以拉长到 SEARCH_BLOCKED_TTL_HOURS（默认 7 天）再验一次。
    """
    hours = settings.SEARCH_BLOCKED_TTL_HOURS if state.blocked else settings.SEARCH_KEYWORD_TTL_HOURS
    return timezone.now() - state.crawled_at >= timedelta(hours=hours)


def _render(keyword, page):
    """取出第 page 页的结果并生成分页链接（页码与源站页码一一对应）"""
    state = _get_state(keyword)
    rows = SearchResult.objects.filter(keyword=state, source_page=page).select_related('song')

    return {
        'keyword': keyword,
        'page': page,
        'results': [row.song for row in rows],
        'total_results': state.total_results,
        'blocked': state.blocked,
        'pagination': {'links': _pager_links(keyword, page, _pager_max(state))},
    }


def _pager_max(state):
    """分页器的页码上限：只给到「已爬 + 一次请求能补上来的量」

    这样每个能点开的页码都真有内容 —— 点到最后一页时正好把它补上来。
    如果源站的真实末页比这还小，就以真实末页为准；一个结果都没有时不出分页器。
    """
    if state.source_empty:
        return 1
    if state.crawled_pages <= 0:
        # 一页都没爬下来（源站不可达 / 人机验证失效）：连第一页都没内容，
        # 这时候再给出 2、3 页的页码，点进去只会是空壳页，还各触发一次回源尝试。
        # 返回 1 → pager.build 认为只有一页 → 不出分页器。
        return 1
    reachable = state.crawled_pages + max(0, settings.SEARCH_REFILL_MAX_PAGES)
    return min(state.total_pages, reachable) if state.total_pages else reachable


def _pager_links(keyword, page, total_pages):
    """生成分页链接（text / href / current），common_html/pager.html 直接用

    页码窗口那套规则在 Web/services/pager.py，这里只负责把搜索页的地址拼法传进去。
    """
    base = '/so/' + urllib.parse.quote(keyword)
    # 与 Web/views/urls.py 的路由保持一致：第 1 页不带页码
    return pager.build(page, total_pages,
                       lambda n: f'{base}.html' if n == 1 else f'{base}/{n}.html')


# ---------- 补货 ----------

def _crawl_next_page(keyword):
    """从水位线往下爬一页源站；已经翻到末页、或这页没内容时返回 False"""
    state = _get_state(keyword)
    if state.total_pages and state.crawled_pages >= state.total_pages:
        return False
    return _crawl_page(keyword, state.crawled_pages + 1)


def _crawl_page(keyword, source_page):
    """爬源站搜索结果第 source_page 页并入库，同时推进水位线与末页页码

    调用方必须已经持有该关键词的锁（见 search_page）。这里刻意不再加锁：
    用的是普通 Lock，不可重入，嵌套加锁会直接死锁。
    """
    try:
        data = Music2t58Spider()._do_fetch_search(keyword, source_page)
    except Exception:
        logger.exception('曲库补货失败：关键词=%r 源站第 %s 页', keyword, source_page)
        return False

    results = data.get('results') or []
    if results and not _save_results(_get_state(keyword), source_page, results):
        # 落库失败就不推进水位线：否则下次请求会以为这页已经爬过，直接切页返回，
        # 用户看到的是空白页，而这一页的数据其实从没写进库。
        # 返回 False 让调用方停下补货循环，下次请求再重试这一页。
        return False
    _advance_keyword(
        keyword, source_page,
        found=bool(results),
        blocked=bool(data.get('blocked')),
        total_pages=Music2t58Spider.last_page_number(data),
        total_results=data.get('total_results') or 0,
    )
    return bool(results)


def _save_results(state, source_page, results):
    """把源站这一页的结果写入曲库，并记下这首歌在这个关键词里的位置

    歌曲本身按 sid 去重（一歌一行，多个关键词共用同一行）；
    关键词与歌曲的对应关系按 (关键词, 歌曲) 去重，重复爬到同一页不产生新行。

    返回是否写入成功：调用方（_crawl_page）拿它决定要不要推进水位线。
    没有可写的内容（这一页本来就是空的）算成功。
    """
    rows = []
    for item in results:
        sid = (item.get('sid') or '').strip()
        name = (item.get('name') or '').strip()
        if not sid or not name:
            continue
        rows.append(Song(
            sid=sid[:32],
            name=name[:255],
            singers=(item.get('singers') or '').strip()[:255],
        ))
    if not rows:
        return True

    if not _write(lambda: Song.objects.bulk_create(
        rows,
        update_conflicts=True,
        unique_fields=['sid'],
        update_fields=['name', 'singers', 'pulled_at'],
    ), '曲库写入'):
        return False

    # upsert 后返回的主键是否被填回对象，取决于数据库与版本的 RETURNING 支持情况，
    # 靠不住；所以落库后按 sid 回查一次主键，一次查询拿全，不做逐个 get。
    songs = {song.sid: song for song in Song.objects.filter(sid__in=[row.sid for row in rows])}
    links = [
        SearchResult(keyword=state, song=songs[row.sid],
                     source_page=source_page, position=offset + 1)
        for offset, row in enumerate(rows) if row.sid in songs
    ]
    if not links:
        return True
    # ignore_conflicts：(关键词, 歌曲) 已经存在就跳过，不改写它原来的位置 ——
    # 「累加池」的语义就是只追加、不重排。
    return _write(lambda: SearchResult.objects.bulk_create(links, ignore_conflicts=True),
                  '搜索结果写入')


def _advance_keyword(keyword, source_page, *, found, blocked, total_pages=0, total_results=0):
    """推进水位线（只前进不后退）、更新末页页码与结果总数，并维护「查不到 / 被屏蔽」两个标记

    源站报的几个数字用关键字参数传，避免 total_pages / total_results 这类相近的
    整数被位置调错。
    """
    state = _get_state(keyword)
    state.blocked = blocked
    if found:
        state.crawled_pages = max(state.crawled_pages, source_page)
        if total_pages:
            state.total_pages = total_pages
        if total_results:
            state.total_results = total_results
        state.source_empty = False
    elif blocked or source_page == 1:
        # 被屏蔽，或第 1 页就没有结果：都属于"整词拿不到东西"，打负缓存，
        # 保鲜期内一次都不再爬（被屏蔽的词保鲜期更长，见 _is_stale）。
        state.source_empty = True
    else:
        # 只有第 N(>1) 页是空 → 翻到底了：把末页收敛成实际存在的页数，
        # 否则分页器会一直给出越界的页码。
        state.total_pages = source_page - 1
    # auto_now 的字段必须显式写进 update_fields 才会被保存
    _write(lambda: state.save(update_fields=[
        'crawled_pages', 'total_pages', 'total_results',
        'source_empty', 'blocked', 'crawled_at',
    ]), '曲库进度写入')


def _count_search(keyword, client_ip):
    """给热门榜记一次搜索

    只记第 1 页（翻页不算新的搜索），并且同一个人 HOT_SEARCH_DEDUP_MINUTES 分钟内
    重复搜同一个词只算一次。防刷状态记在关键词行的 last_hit_ip / last_hit_at 上，
    不写缓存键 —— 缓存键里带 IP 的话，伪造 X-Forwarded-For 就能一个请求撑出一个缓存
    文件，攒够几万个就把 cache/ 目录塞爆。

    计数丢了不影响搜索本身，所以走 _write：写锁冲突时只记日志、不上抛。
    自增用 F() 表达式交给数据库做，避免"读出来加一再写回去"在并发下丢计数。
    """
    state, created = SearchKeyword.objects.get_or_create(keyword=keyword)
    if created:
        # 这个词第一次被搜到：立刻清掉热门榜缓存，不然下拉要等
        # HOT_SEARCH_CACHE_MINUTES 才看到它。
        hot_search.invalidate()

    # IP 来自 X-Forwarded-For 首跳，完全由访客控制、长度不限，而字段上限是 64：
    # 不截断的话，伪造一个超长头在 SQLite 下会静默入库，换到 MySQL/PostgreSQL 后
    # 会抛 DataError 把搜索页打成 500（_write 只捕获 OperationalError）。
    # 比较和写入都用这个截断后的值，保证防刷判断前后一致。
    client_ip = (client_ip or '')[:64]

    now = timezone.now()
    minutes = settings.HOT_SEARCH_DEDUP_MINUTES
    if (minutes > 0 and client_ip and state.last_hit_ip == client_ip
            and state.last_hit_at and now - state.last_hit_at < timedelta(minutes=minutes)):
        return
    _write(lambda: SearchKeyword.objects.filter(pk=state.pk).update(
        search_count=F('search_count') + 1,
        last_searched_at=now,
        last_hit_ip=client_ip,
        last_hit_at=now,
    ), '搜索计数写入')


def _lock_for(keyword):
    return _lock_pool[hash(keyword) % _LOCK_POOL_SIZE]


def _write(action, label):
    """统一的写库包装：写锁冲突时发换库告警，其余情况只记日志；返回是否写成功

    刻意不往上抛 —— 搜索页不该因为"存不下来"就 500，库里有什么就先给用户看什么。
    写锁冲突是"该换库了"的信号，交给 db_alert 发邮件（自带冷却，不会轰炸）。
    """
    try:
        action()
    except OperationalError as exc:
        if db_alert.is_lock_error(exc):
            db_alert.alert_switch_db(f'{label}出现 SQLite 写锁冲突', exc)
        else:
            logger.exception('%s失败', label)
        return False
    return True
