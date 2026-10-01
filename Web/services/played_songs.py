"""播放页本地缓存 +「大家正在听」（PlayedSong 表）

目的：把"每打开一次播放页就回源抓一次"改成"优先读本地表"。播放页只有两种情况还会回源，
其余（歌名/歌手/封面/歌词/歌手页链接）统统读库：

    ① 库里还没有这首歌（首次播放）        → 完整抓一次页面 + play.php + 歌词，落库
    ② 有记录但直链过期（PLAYED_URL_TTL_MINUTES）→ **当场**刷一次直链（这次访问等它）

两个入口 / 一个出口：
    取数  get_for_page(sid)  —— 播放页 song() 与连播 song_info() 共用这一条路径
    上报  report_start(sid)  —— 前端"开始播放"时调，刷新 last_played_at
    展示  now_listening()    —— 播放页「大家正在听」，读最近 PLAYED_SHOW_MINUTES 分钟

为什么"开始播放"才上报（而不是打开页面）：
    只有真的按下播放，这首歌才算"此刻正在听"，列表反映的才是真实热度；
    也让"正在听"与"被爬虫/SEO 抓过的页面"区分开 —— 后者只落缓存，不进列表。

为什么直链刷新从"后台异步"改回**同步**（2026-10-01，见 _refresh_url_now）：
    异步那套是"本页照旧发库里那条、后台悄悄刷"，前提是库里那条还有效。可库里那条一旦
    超过 CDN 寿命（酷我直链实测 60~73 分钟后返回 410 Gone）就是在把死链塞给访客 ——
    当天全表 37958 行里有 33057 行正处于这种状态，于是每次打开播放页几乎必报
    "播放链接失效，请刷新页面重试"；更糟的是 TTL 内不会再触发刷新，所以"刷新"也没用。
    现在改成宁可在这次访问里等一次刷新（实测 4~6 秒，并用 PLAYED_URL_REFRESH_BUDGET
    封顶，不会像以前那样顶到 REQUEST_BUDGET），也要把一条确定能播的直链交给访客。
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError
from django.db.models import F, Q
from django.utils import timezone

from SpiderServices.Music_2t58.main import Music2t58Spider
from Web.models import PlayedSong
from Web.services import db_alert

logger = logging.getLogger(__name__)

# 过期清理的冷却键：清理由上报顺带触发，但不必每次上报都扫一遍表
_CLEANUP_KEY = 'bz_played_cleanup_at'
# 清理冷却时长（秒）。一小时一次足够，删的是几十天没动过的行
_CLEANUP_COOLDOWN = 3600


def get_for_page(sid):
    """播放页取数（表优先）—— 返回与爬虫 fetch_song 同形态的 dict

    返回 {'song': {name, artists, cover, singer_url}, 'play_url': '', 'lyrics': ''}，
    调用方（Web/views/request.py）不必关心数据来自库还是源站。
    """
    sid = (sid or '').strip()[:32]
    if not sid:
        return _empty()

    row = PlayedSong.objects.filter(sid=sid).first()
    if row is not None and row.name:
        # 直链过期就**当场**刷一次再给访客：库里那条已经可能失效了（见文件头说明），
        # 把它发出去只会换来一声"播放链接失效"。
        if _url_stale(row):
            _refresh_url_now(row)
        return _from_row(row)

    # 首次播放，或上次只抓到半截（连歌名都没有）：完整抓一次并落库
    return _fetch_full(sid)


def report_start(sid):
    """某首歌开始播放：刷新 last_played_at（「正在听」的排序依据）

    只更新已存在的行、不新建：播放前页面一定已经过 get_for_page 建过行，
    没有行说明是个异常请求，新建反而会让"正在听"里冒出没缓存过的记录。
    顺带触发一次过期清理（自带冷却）。
    """
    sid = (sid or '').strip()[:32]
    if not sid:
        return

    try:
        updated = PlayedSong.objects.filter(sid=sid).update(
            last_played_at=timezone.now(), plays=F('plays') + 1)
    except OperationalError as exc:
        _on_write_error(exc, '播放上报写入')
        return

    if updated:
        cleanup_expired()


def now_listening():
    """「大家正在听」：最近 PLAYED_SHOW_MINUTES 分钟内有播放记录的歌

    按播放时间倒序，最多 PLAYED_SHOW_COUNT 条（展示截断，不删数据）。
    整块读本地表，不产生任何回源。
    """
    since = timezone.now() - timedelta(minutes=settings.PLAYED_SHOW_MINUTES)
    return list(
        PlayedSong.objects
        .filter(last_played_at__gte=since)
        .order_by('-last_played_at')[:settings.PLAYED_SHOW_COUNT]
    )


def cleanup_expired():
    """删除 PLAYED_KEEP_DAYS 天没被播放过的行 —— 防止表无限增长

    由 report_start 顺带触发，带冷却（一小时内只做一次），不需要额外定时任务。
    两种行都算"该删"：
      · 播放过但很久没再播的（last_played_at 早于截止线）
      · 只被打开过页面、从没播放过的（last_played_at 为空）—— 爬虫抓播放页就会
        产生这种行，没有它会被漏掉、永远清理不掉
    """
    days = settings.PLAYED_KEEP_DAYS
    if days <= 0:
        return
    # cache.add 只在键不存在时写入并返回 True，正好实现"冷却期内只做一次"
    if not cache.add(_CLEANUP_KEY, 1, _CLEANUP_COOLDOWN):
        return

    cutoff = timezone.now() - timedelta(days=days)
    stale = Q(last_played_at__lt=cutoff) | Q(last_played_at__isnull=True, info_at__lt=cutoff)
    try:
        PlayedSong.objects.filter(stale).delete()
    except OperationalError as exc:
        cache.delete(_CLEANUP_KEY)   # 失败就放掉冷却，下次还能重试
        _on_write_error(exc, '播放缓存清理')


def _from_row(row):
    """把库里的行拼成播放页需要的形态

    artists 用 '/' 存（与模板 join 的分隔符一致），取出时拆回列表，好让模板继续用
    {{ song.artists|join:'/' }} 那套写法。
    """
    return {
        'song': {
            'name': row.name,
            'artists': [a for a in (row.artists or '').split('/') if a],
            'cover': row.cover,
            'singer_url': row.singer_url,
        },
        'play_url': row.play_url,
        'lyrics': row.lyrics,
    }


def _url_stale(row):
    """直链是否该刷新：没取过、或取到的时间早于 PLAYED_URL_TTL_MINUTES

    只看 play_url_at，不看直链是否为空 —— 上次没抓到直链时也记了时间（只是记法改成了
    "推迟 PLAYED_URL_REFRESH_RETRY_MINUTES 分钟"，见 _refresh_url_now），
    于是重试窗口内不会每次开页面都去试，避免对源站的重复打扰。
    """
    ttl = settings.PLAYED_URL_TTL_MINUTES
    if ttl <= 0 or row.play_url_at is None:
        return True
    return timezone.now() - row.play_url_at > timedelta(minutes=ttl)


def _refresh_url_now(row):
    """**在访客这次请求里**同步刷一次直链，把新链接写回 row（失败保留旧直链、只推迟重试）

    为什么不等后台：这次访问要用的就是这条直链（见文件头说明）。源站不可达时最坏要等
    PLAYED_URL_REFRESH_BUDGET 秒，之后拿不到就退回旧直链 —— 至少访客不用为一次注定
    失败的刷新等满 REQUEST_BUDGET（25 秒）。超预算时 _fetch_play_info 会自己返回空串，
    这里按"没刷到"处理。
    """
    try:
        url = Music2t58Spider().fetch_play_url(
            row.sid, budget=settings.PLAYED_URL_REFRESH_BUDGET)
    except Exception:
        logger.exception('刷新直链失败：%s', row.sid)
        url = ''

    if url:
        row.play_url = url[:1000]
        row.play_url_at = timezone.now()
    else:
        # 没刷到：把 play_url_at 回拨到"距离过期还差 PLAYED_URL_REFRESH_RETRY_MINUTES"的
        # 位置 —— 效果是过这么久才会再次触发刷新。刻意**不**记成当前时间：那会把这条
        # 已失效的直链锁满整个 TTL，期间每个访客都拿到死链、且不会再触发任何刷新
        # （2026-10-01 全表 3282 行就是这么来的：play_url_at 一律晚于直链签发时间，
        # 反向偏差 0 行 —— 单向偏差正是"续命"留下的指纹）。
        row.play_url_at = timezone.now() - timedelta(
            minutes=settings.PLAYED_URL_TTL_MINUTES
            - settings.PLAYED_URL_REFRESH_RETRY_MINUTES)
    _save(row, '直链刷新')


def _fetch_full(sid):
    """完整抓一次并落库（首次播放走这里）

    抓不到歌名就当作失败、不落库，返回空 —— 让页面走"暂时听不了"的兜底分支，
    下次访问再试。避免把半截记录写进库、之后一直命中它。
    """
    try:
        data = Music2t58Spider().fetch_song(sid)
    except Exception:
        logger.warning('播放页回源失败：%s', sid, exc_info=True)
        return _empty()

    song = data.get('song') or {}
    name = (song.get('name') or '').strip()
    if not name:
        return _empty()

    now = timezone.now()
    row, _ = PlayedSong.objects.get_or_create(sid=sid)
    row.name = name[:255]
    row.artists = '/'.join(song.get('artists') or [])[:255]
    row.singer_url = (song.get('singer_url') or '')[:500]
    row.cover = (song.get('cover') or '')[:500]
    row.lyrics = data.get('lyrics') or ''
    row.play_url = (data.get('play_url') or '')[:1000]
    row.play_url_at = now
    row.info_at = now
    _save(row, '播放页缓存落库')
    return _from_row(row)


def _empty():
    """抓取失败时的空形态：模板据此走"暂时听不了"的兜底分支"""
    return {'song': {}, 'play_url': '', 'lyrics': ''}


def _save(row, label):
    """统一的写库包装：写锁冲突时发换库告警，其余情况只记日志

    刻意不往上抛 —— 访客正在听歌，不该因为缓存写不上就让他看到报错页。
    """
    try:
        row.save()
    except OperationalError as exc:
        _on_write_error(exc, label)


def _on_write_error(exc, label):
    if db_alert.is_lock_error(exc):
        db_alert.alert_switch_db(f'{label}出现 SQLite 写锁冲突', exc)
    else:
        logger.exception('%s失败', label)
