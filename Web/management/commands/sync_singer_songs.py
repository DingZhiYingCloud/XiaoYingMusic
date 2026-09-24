"""同步每位歌手的曲目列表（第 1 页）到本地库

为什么做这件事：
    歌手页原本每次都要回源抓（虽有 2 小时页面缓存，但缓存一过期，第一个访客就要等
    1 秒多）。把每位歌手最热的那一页曲目落库后，歌手页第 1 页可以整页本地渲染
    （0 请求、毫秒级），站内搜索也才有本地数据可查。

只抓第 1 页，不深挖：
    实测抽样 30 位歌手（2026-09）——40% 只有 1 页曲目，有分页的平均 5.8 页，
    平均每位 3.9 页。全量深挖约 9 万次请求、570 万首歌，性价比很低（详见 README）。
    所以第 2 页及以后仍走原来的「爬虫 + 页面缓存」，用户翻到才抓。

请求节奏：
    单次抓取实测 0.43~0.48 秒；每位之间默认再停 2.5 秒（相当于 0.4 次/秒），
    两万三千多位约需 19 小时。慢是一路不被源站限流的前提 —— 一旦被限流，
    PHPSESSID 失效会让全站数据变空，这个代价远大于多跑半天。

中断了直接再跑一次就续上：已经同步过的会跳过（--limit 是先按列表顺序圈定范围、
再筛掉已同步的，所以重跑既不会重复爬，也不会越过范围）。

    python manage.py sync_singer_songs                # 全量（约 19 小时）
    python manage.py sync_singer_songs --limit 480    # 只要列表前 5 页歌手（源站一页 96 位）
    python manage.py sync_singer_songs --limit 20     # 只处理 20 位（试跑）
    python manage.py sync_singer_songs --interval 0.5 # 缩短间隔（谨慎，容易被限流）
    python manage.py sync_singer_songs --refresh      # 已同步过的也重新同步
"""
import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from SpiderServices.Music_2t58.main import Music2t58Spider
from Web.models import Singer, SingerSong

# 只同步源站第几页。做成常量而不是写成字面量 1，是为了以后若要深挖能一眼找到这里。
PAGE = 1
# 每位歌手之间默认停多久（秒）。调小等于加大源站压力，默认值踩在"稳"这一边。
DEFAULT_INTERVAL = 2.5
# 单次抓取的实测耗时（秒），只用来估算总时长，不影响抓取行为。
FETCH_SECONDS = 0.5
# 标题按数据库字段上限截断：实测源站最长的一条标题有 264 个字符，
# SQLite 不校验会照收，换到 MySQL / PostgreSQL 会直接抛 DataError。
MAX_TITLE_CHARS = 255
# bulk_create 的批大小（每位歌手约 68 行，远小于这个数，这里只是给个上限）
BATCH_SIZE = 500
# 每处理多少位打印一次进度
PROGRESS_EVERY = 50


class Command(BaseCommand):
    help = '同步每位歌手的第 1 页曲目列表（写 SingerSong，并回填歌手简介与曲目总页数）'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0,
                            help='只处理列表前 N 位歌手（0=全部；源站一页 96 位，480 ≈ 前 5 页）')
        parser.add_argument('--interval', type=float, default=DEFAULT_INTERVAL,
                            help=f'每位歌手之间的间隔秒数（默认 {DEFAULT_INTERVAL}，别把源站打急）')
        parser.add_argument('--refresh', action='store_true',
                            help='已同步过的也重新同步')

    def handle(self, *args, **options):
        self.spider = Music2t58Spider()

        targets = self._targets(options['refresh'], options['limit'])
        if not targets:
            self.stdout.write('没有需要同步的歌手（都同步过了；想重来请加 --refresh）')
            return

        total = len(targets)
        interval = max(0.0, options['interval'])
        self.stdout.write(
            f'开始同步{total}位歌手的第 {PAGE} 页曲目，间隔 {interval} 秒，'
            f'预计{self._eta(total, interval)}（可随时 Ctrl+C，重跑即续）')

        done = failed = songs_total = 0
        for index, singer in enumerate(targets, 1):
            try:
                songs_total += self._one(singer)
            except Exception as exc:
                # 失败就不写 songs_synced_at，下次重跑会再试这位
                failed += 1
                self.stderr.write(f'  [{singer.sid}] {singer.name} 失败：'
                                  f'{type(exc).__name__}: {exc}')
            else:
                done += 1
            if index % PROGRESS_EVERY == 0 or index == total:
                self.stdout.write(f'  进度 {index}/{total}：成功 {done}，失败 {failed}，'
                                  f'本次已入库 {songs_total} 首')
            if index < total:
                time.sleep(interval)

        self.stdout.write(self.style.SUCCESS(
            f'结束：成功 {done} 位 / 失败 {failed} 位，本次入库 {songs_total} 首，'
            f'库里共 {SingerSong.objects.count()} 首'))
        if failed:
            self.stdout.write('失败的重新执行本命令即可，它只补还没同步过的那些。')

    def _targets(self, refresh, limit):
        """待同步的歌手

        顺序按 id 升序（≈ 源站「歌手大全」列表的默认顺序），所以 --limit 480 就是
        "只要列表前 5 页的歌手"（源站一页 96 位）。**先圈范围、再筛掉已同步的**：
        重跑时既不会重复爬，也不会越过指定范围。
        """
        qs = Singer.objects.order_by('id')
        if limit:
            rows = list(qs[:limit])
            return rows if refresh else [r for r in rows if r.songs_synced_at is None]
        # 不限范围时交给 SQL 过滤：以后绝大多数都同步过了，没必要把两万多行读进内存
        if not refresh:
            qs = qs.filter(songs_synced_at__isnull=True)
        return list(qs)

    def _one(self, singer):
        """同步一位歌手，返回本次入库的曲目条数"""
        data = self.spider._do_fetch_singer(singer.sid, PAGE)
        info = data.get('singer') or {}
        if not info.get('name'):
            # 连歌手信息都解析不出来 = 这次抓取是失败的（人机验证失效、源站改版、
            # 或者被限流返回了拦截页）。必须抛出去让上层记一笔失败、下次重试，
            # 绝不能当成"这位歌手没有歌"而标记成已同步 —— 那会永久留下一个空页面。
            raise RuntimeError('页面里解析不到歌手信息（可能验证失效或源站改版）')

        rows = []
        seen = set()
        for item in data.get('songs') or []:
            sid = Music2t58Spider._sid_from_link(item.get('link') or '')
            # sid 取不到的行没法指向播放页；同一页里的重复条目只留第一条
            if not sid or sid in seen:
                continue
            seen.add(sid)
            rows.append(SingerSong(
                singer_sid=singer.sid, page=PAGE, position=len(rows) + 1, sid=sid,
                title=(item.get('title') or '').strip()[:MAX_TITLE_CHARS],
            ))

        # 先删后插，而不是逐行 upsert：源站删过歌时重跑会让结果集变小，
        # 只 upsert 的话旧行会留下来，页面上就会出现源站已经没有的歌。
        # 包在一个事务里：删除成功但插入失败是不会发生的，否则这位歌手会变成空页面。
        with transaction.atomic():
            SingerSong.objects.filter(singer_sid=singer.sid, page=PAGE).delete()
            if rows:
                SingerSong.objects.bulk_create(rows, batch_size=BATCH_SIZE)
            Singer.objects.filter(pk=singer.pk).update(
                intro=(info.get('intro') or '').strip(),
                # 没有分页区时 last_page_number 返回 0，按"只有 1 页"算
                song_pages=max(1, Music2t58Spider.last_page_number(data)),
                songs_synced_at=timezone.now(),
            )
        return len(rows)

    @staticmethod
    def _eta(total, interval):
        """按实测的单次抓取耗时估总时长，只用于提示"""
        seconds = total * (interval + FETCH_SECONDS)
        return f'{seconds / 3600:.1f} 小时' if seconds >= 3600 else f'{seconds / 60:.0f} 分钟'
