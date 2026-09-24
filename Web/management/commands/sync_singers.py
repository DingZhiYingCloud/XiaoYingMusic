"""同步全部歌手：源站列表 → 下载封面 → 上传小影图床 → 入库

数据源是源站的「全部歌手列表」（/singerlist/index/index/index/index.html），
实测共 23631 位、247 页（每页 96 条，末页 15 条）。末页页码从分页区的「尾页」
链接里读，不写死 —— 源站加人减人都不用改代码。

分两个阶段，对应"先把数据准备好、再入库"：
    阶段一  爬完列表页，先把 (sid / 名称 / 源图地址) 落库
    阶段二  逐个下载封面 → 上传小影图床（PicUI 线路）→ 回填图床外链
分开的好处：阶段二中途挂了，名册本身是完整的，重跑时只补还没封面的那些。

全程按 sid 幂等，已入库且已有图床外链的会跳过，所以中断后直接再跑一次就续上了。

    python manage.py sync_singers                 # 全量
    python manage.py sync_singers --pages 3       # 只爬前 3 页列表（试跑）
    python manage.py sync_singers --limit 10      # 阶段二只处理 10 位（试跑）
    python manage.py sync_singers --skip-upload   # 只入库名册，不动图床
    python manage.py sync_singers --refresh       # 已有外链的也重新下载上传

封面**逐张串行上传**（实测并发会被图床压垮：大量超时、整批卡住），所以全量要跑很久。
图床只返回外链、不替我们保存任何映射，外链的唯一记录就是我们这张表的 cover 列。
"""
import time

import requests
from django.core.management.base import BaseCommand

from SpiderServices.Music_2t58.main import Music2t58Spider
from Web.models import Singer
from Web.services import xiaoying_api

# 列表页的筛选维度：全部地区 / 全部性别 / 全部类型 / 全部字母
ALL_FILTERS = ('index', 'index', 'index', 'index')

# 小影图床 PicUI 线路的上传接口
PICUI_UPLOAD_PATH = '/api/ImageHosting/picui/upload'
# 接口返回码：成功 / Token 池容量用尽（继续跑也没意义）
CODE_OK = 10000
CODE_NO_CAPACITY = 50002
# Content-Type → 上传文件名的后缀（PicUI 靠文件名判类型，后缀给错可能被拒）
MIME_EXT = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/gif': 'gif',
    'image/bmp': 'bmp',
    'image/tiff': 'tiff',
}
# 单张封面的下载上限：源站头像是几十 KB 的小图，超过这个数说明地址不对
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# 下载失败的重试次数（网络抖动居多，重试有意义）
DOWNLOAD_RETRY = 2
# 图床上传超时（秒）：留足余量，避免单张稍慢就被判超时、白下载一次
UPLOAD_TIMEOUT = 60

# 下载封面用的请求头：只带浏览器 UA，不带 Referer。
# 源图在 gimg3.baidu.com 上，实测不带 Referer 就能拿到。
DOWNLOAD_HEADERS = {'User-Agent': Music2t58Spider.HEADERS['User-Agent']}


class QuotaExhausted(Exception):
    """图床 Token 池容量用尽 —— 这是"整体不可继续"，不是单个歌手的问题"""


class Command(BaseCommand):
    help = '同步源站全部歌手（名称 / 详情页 ID / 封面），封面下载后上传小影图床'

    def add_arguments(self, parser):
        parser.add_argument('--pages', type=int, default=0,
                            help='只同步列表前 N 页（0=全部）')
        parser.add_argument('--limit', type=int, default=0,
                            help='阶段二最多处理 N 位歌手（0=全部）')
        parser.add_argument('--skip-upload', action='store_true',
                            help='不上传图床，只入库名称 / 详情页 ID / 源图地址')
        parser.add_argument('--refresh', action='store_true',
                            help='已有图床外链的也重新下载并上传')
        parser.add_argument('--page-delay', type=float, default=0.3,
                            help='翻列表页之间的间隔秒数（默认 0.3，别把源站打急）')

    def handle(self, *args, **options):
        self.spider = Music2t58Spider()

        singers = self._collect(options['pages'], options['page_delay'])
        self._save(singers)

        if options['skip_upload']:
            self.stdout.write(self.style.WARNING(
                '--skip-upload：本次只入库名册，没有上传图床'))
            return
        self._upload_covers(options['limit'], options['refresh'])

    # ---------- 阶段一：爬列表 ----------

    def _collect(self, pages, page_delay):
        """爬列表页，返回 {sid: {name, source_pic}}（按 sid 去重，顺序即源站顺序）"""
        first = self.spider._do_fetch_singer_list(*ALL_FILTERS, 1)
        last_page = Music2t58Spider.last_page_number(first) or 1
        if pages:
            last_page = min(last_page, pages)
        self.stdout.write(f'歌手列表共 {last_page} 页，开始抓取…')

        found = {}
        for page in range(1, last_page + 1):
            try:
                data = first if page == 1 else self.spider._do_fetch_singer_list(*ALL_FILTERS, page)
                items = data.get('singers') or []
            except Exception as exc:
                # 单页失败不中断整轮：少一页总比整批白跑强，日志里能看出漏了哪页
                self.stderr.write(f'  第 {page} 页抓取失败，跳过：{type(exc).__name__}: {exc}')
                items = []
            for item in items:
                sid = Music2t58Spider._sid_from_link(item.get('link') or '')
                name = (item.get('name') or '').strip()
                if not sid or not name:
                    continue
                found[sid] = {
                    'name': name[:255],
                    'source_pic': (item.get('pic') or '').strip(),
                }
            if page % 20 == 0 or page == last_page:
                self.stdout.write(f'  已抓 {page}/{last_page} 页，累计 {len(found)} 位歌手')
            time.sleep(page_delay)
        return found

    # ---------- 阶段一：入库 ----------

    def _save(self, singers):
        """把名册写入库：按 sid 幂等，只更新有变化的那两列"""
        if not singers:
            self.stderr.write('没有抓到任何歌手，跳过入库')
            return
        # 一次性把现有名册读进内存再比对：歌手总量就两万出头，字典很小。
        # 不写成 sid__in=<两万多个 sid>：那会逼近 SQLite 的 SQL 变量上限（默认 3.2 万），
        # 源站再涨些人就整批报错。
        existing = {row.sid: row for row in Singer.objects.all()}
        to_create, to_update = [], []
        for sid, item in singers.items():
            row = existing.get(sid)
            if row is None:
                to_create.append(Singer(sid=sid, name=item['name'],
                                        source_pic=item['source_pic']))
            elif row.name != item['name'] or row.source_pic != item['source_pic']:
                row.name = item['name']
                row.source_pic = item['source_pic']
                to_update.append(row)
        if to_create:
            Singer.objects.bulk_create(to_create, batch_size=500)
        if to_update:
            # auto_now 的字段必须显式写进 update_fields，否则不会被保存
            Singer.objects.bulk_update(to_update, ['name', 'source_pic', 'pulled_at'],
                                       batch_size=500)
        self.stdout.write(self.style.SUCCESS(
            f'名册入库完成：新增 {len(to_create)} 位，更新 {len(to_update)} 位，'
            f'库里共 {Singer.objects.count()} 位'))

    # ---------- 阶段二：封面下载 + 图床上传 ----------

    def _upload_covers(self, limit, refresh):
        targets = Singer.objects.exclude(source_pic='')
        if not refresh:
            targets = targets.filter(cover='')
        targets = list(targets.order_by('id')[:limit] if limit else targets.order_by('id'))
        if not targets:
            self.stdout.write('没有需要上传封面的歌手（都处理过了；想重传请加 --refresh）')
            return
        total = len(targets)
        self.stdout.write(f'开始下载并上传封面，共 {total} 位，一张一张传…')

        done = failed = 0
        # 刻意**串行**：图床一次只能安稳处理一张。实测开并发（6~8）后本地 API 会大量
        # 读超时，吞吐反而掉到十分之一都不到，还白耗图床容量。宁可慢，也不要限流。
        for index, singer in enumerate(targets, 1):
            try:
                cover = self._one_cover(singer)
            except QuotaExhausted as exc:
                self.stderr.write(self.style.ERROR(
                    f'图床不能再传了，阶段二提前结束：{exc}'))
                self.stderr.write('补足 Token 池容量后重新执行本命令即可续上。')
                break
            except Exception as exc:
                failed += 1
                self.stderr.write(f'  [{singer.sid}] {singer.name} 失败：'
                                  f'{type(exc).__name__}: {exc}')
            else:
                try:
                    Singer.objects.filter(pk=singer.pk).update(cover=cover)
                except Exception:
                    # 图床只回外链、不替我们存任何映射，外链只有库里这一份。
                    # 写库失败就等于这次上传白花钱，所以把地址打到日志里，至少还能人工捞回来。
                    self.stderr.write(f'  [{singer.sid}] {singer.name} 传成功但写库失败，'
                                      f'外链：{cover}')
                    failed += 1
                else:
                    done += 1
            if index % 50 == 0 or index == total:
                self.stdout.write(f'  进度 {index}/{total}：成功 {done}，失败 {failed}')

        self.stdout.write(self.style.SUCCESS(
            f'封面上传结束：成功 {done}，失败 {failed}'))
        if failed:
            self.stdout.write('失败的重新执行本命令即可，它只会补还没封面的那些。')

    def _one_cover(self, singer):
        """下载一位歌手的封面并上传图床，返回图床外链"""
        raw, content_type = self._download(singer.source_pic)
        return self._upload(raw, content_type, singer.sid)

    def _download(self, url):
        """下载封面图，返回 (二进制内容, Content-Type)"""
        for attempt in range(DOWNLOAD_RETRY + 1):
            try:
                resp = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=20)
                resp.raise_for_status()
            except requests.RequestException:
                if attempt >= DOWNLOAD_RETRY:
                    raise
                time.sleep(1 + attempt)
                continue
            # 下面这些是"地址或内容本身不对"，重试也没用，直接抛
            content_type = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if not content_type.startswith('image/'):
                raise ValueError(f'返回的不是图片（Content-Type={content_type or "空"}）')
            if not resp.content:
                raise ValueError('图片内容为空')
            if len(resp.content) > MAX_IMAGE_BYTES:
                raise ValueError(f'图片过大（{len(resp.content)} 字节）')
            return resp.content, content_type

    def _upload(self, raw, content_type, sid):
        """上传到小影图床（PicUI 线路），返回 CDN 外链"""
        ext = MIME_EXT.get(content_type, 'jpg')
        payload = xiaoying_api.post_multipart(
            PICUI_UPLOAD_PATH, {'image': (f'{sid}.{ext}', raw, content_type)},
            timeout=UPLOAD_TIMEOUT)
        code = payload.get('code')
        if code == CODE_NO_CAPACITY:
            raise QuotaExhausted(payload.get('msg') or 'Token 池容量已用尽')
        if code != CODE_OK:
            raise RuntimeError(payload.get('msg') or f'图床返回 code={code}')
        url = (payload.get('data') or {}).get('url') or ''
        if not url:
            raise RuntimeError('图床返回里没有外链')
        return url
