"""每日播放榜：首页「今日热听榜」的数据源

计数只有一处入口 —— 播放页在**整首播完**时（song_player.js 的 ended 事件）回调
POST /api/play/ended，落到 Web/services/play_rank.py 的 record()；
取用只有一处出口 —— 首页 index() 调 today_top()。

为什么整首播完才算：
    用户要的是"今日被听得最多的歌"，点开听两秒就跳走不该算数。前端只在 ended
    时上报，中途暂停、关页面、拖走进度条都不发请求。

为什么要按天分桶（见 Web/models.py 的 SongPlay）：
    "每日 00:00 重新计算"不需要定时任务，取榜时加一句 play_date = 今天 就是了。
    往前的日期还在库里，以后想做周榜/月榜不用改表。

榜单不缓存：
    首页三块数据现在全部读本地库（见 README 8.11），取榜就是在每次请求里查一次 SongPlay，
    所以跨过 00:00 立刻就是新一天的数据，不会出现"昨天的榜还挂到今天"。
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import OperationalError
from django.db.models import F, Max
from django.utils import timezone

from Web.models import SongPlay
from Web.services import db_alert

logger = logging.getLogger(__name__)


def record(sid, name, artists, client_ip):
    """给某首歌的"今日听完次数"加一次

    sid / name / artists 都来自播放页的 POST 请求，属于外部输入，一律按数据库字段长度
    截断：SQLite 不校验长度会照单全收，换到 MySQL / PostgreSQL 后超长直接抛 DataError。

    防刷沿用热门搜索榜的做法（见 Web/services/music_library.py 的 _count_search）：
    同一个人 PLAY_DEDUP_MINUTES 分钟内重复听完同一首歌只算一次，状态记在当日那一行上。
    拦不住"换 IP 慢慢刷"，完整防护要校验实际播放时长，成本很高，这里只做最小防护。
    """
    today = timezone.localdate()     # TIME_ZONE=Asia/Shanghai，取到的就是北京的"今天"
    name = (name or '').strip()[:255]
    row, _ = SongPlay.objects.get_or_create(
        sid=(sid or '').strip()[:32],
        play_date=today,
        # 歌名兜底用 sid：爬虫只抓到一半时播放页的 name 可能是空的，
        # 首页总得有个能认出来的标题，不能显示空白。
        defaults={'name': name or sid, 'artists': (artists or '').strip()[:255]},
    )

    # IP 来自 X-Forwarded-For 首跳，完全由访客控制、长度不限，而字段上限是 64：
    # 不截断的话，伪造一个超长头在 SQLite 下会静默入库，换到 MySQL/PostgreSQL 后
    # 会抛 DataError。比较和写入都用这个截断后的值，保证防刷判断前后一致。
    client_ip = (client_ip or '')[:64]

    now = timezone.now()
    minutes = settings.PLAY_DEDUP_MINUTES
    if (minutes > 0 and client_ip and row.last_hit_ip == client_ip
            and row.last_hit_at and now - row.last_hit_at < timedelta(minutes=minutes)):
        return
    # 自增用 F() 表达式交给数据库做，避免"读出来加一再写回去"在并发下丢计数
    _write(lambda: SongPlay.objects.filter(pk=row.pk).update(
        plays=F('plays') + 1,
        last_played_at=now,
        last_hit_ip=client_ip,
        last_hit_at=now,
    ), '播放计数写入')


def today_top():
    """今日被听完次数最多的前 PLAY_RANK_COUNT 首

    排序固定为 次数倒序 → 最近听完时间倒序 → id 升序：后两级是为了让"次数相同"时
    有确定的顺序，否则同一个页面刷新两次，并列的几首会互相换位。
    """
    return list(
        SongPlay.objects
        .filter(play_date=timezone.localdate())
        .order_by('-plays', '-last_played_at', 'id')[:settings.PLAY_RANK_COUNT]
    )


def latest_play_at():
    """今天最近一次"整首听完"的时间；今天还没有任何计数时返回 None

    sitemap 拿它当首页的 lastmod：首页的可见内容（今日热听榜）就是在这一刻变的。
    今天没人听完任何一首时返回 None —— 首页跟昨天一模一样，这时候不该写 lastmod。
    """
    return (
        SongPlay.objects
        .filter(play_date=timezone.localdate())
        .aggregate(latest=Max('last_played_at'))['latest']
    )


def _write(action, label):
    """统一的写库包装：写锁冲突时发换库告警，其余情况只记日志

    刻意不往上抛 —— 访客正在听歌，不该因为"次数记不上"就让他看到报错页。
    写锁冲突是"该换库了"的信号，交给 db_alert 发邮件（自带冷却，不会轰炸）。
    """
    try:
        action()
    except OperationalError as exc:
        if db_alert.is_lock_error(exc):
            db_alert.alert_switch_db(f'{label}出现 SQLite 写锁冲突', exc)
        else:
            logger.exception('%s失败', label)
