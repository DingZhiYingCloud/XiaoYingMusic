import logging
import os
import re
import time
import hashlib
import threading
import urllib.parse

import requests
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from lxml import etree
from dotenv import load_dotenv
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

load_dotenv(override=True)   # 以 .env 为准，避免已存在的环境变量（旧快照）覆盖项目配置

logger = logging.getLogger(__name__)

# ============ 缓存配置 ============
# 全局默认缓存时长(小时):由 .env 的 CACHE_TTL_HOURS 控制,默认 2 小时(1-3 小时均可)。
# 可按页面类型单独覆盖,如 CACHE_TTL_HOURS_CHART=1 仅将榜单页改为 1 小时,详见 _ttl_for。
CACHE_TTL_HOURS = float(os.getenv('CACHE_TTL_HOURS', '2'))
# 播放直链短缓存时长(分钟):CDN 直链有时效,单独短缓存避免过期播放失败,
# 由 .env 的 CACHE_TTL_PLAY_MINUTES 控制,默认 30 分钟。
CACHE_TTL_PLAY_MINUTES = float(os.getenv('CACHE_TTL_PLAY_MINUTES', '30'))


class _CacheAdapter:
    """统一缓存入口:优先 django cache(视图请求环境),独立脚本/未配置 settings 时退化为内存缓存"""

    def __init__(self):
        self._backend = None  # None=未初始化

    def _get_backend(self):
        if self._backend is None:
            try:
                from django.core.cache import cache
                cache.get('__probe__')  # 触发 settings 检查
                self._backend = cache
            except Exception:
                self._backend = 'mem'
        return self._backend

    def get(self, key):
        backend = self._get_backend()
        if backend == 'mem':
            item = _CacheAdapter._mem_store.get(key)
            if item and item[1] > time.time():
                return item[0]
            return None
        return backend.get(key)

    def set(self, key, value, timeout):
        backend = self._get_backend()
        if backend == 'mem':
            _CacheAdapter._mem_store[key] = (value, time.time() + timeout)
        else:
            backend.set(key, value, timeout)

    _mem_store = {}


cache = _CacheAdapter()


# ============ 源站清单：域名与出站源 IP（都支持多个，用法见 Music2t58Spider）============
def _env_list(name, default=''):
    """读逗号分隔的环境变量，去掉空白项；没配或配成空就用 default"""
    return [item.strip() for item in (os.getenv(name) or default).split(',') if item.strip()]


def _normalize_base(raw):
    """把一条源站域名规整成 `https://主机/` 的形式

    这份清单是手工维护的，写法必须容错 —— 下面三种都认：
        music.2t58.com / https://music.2t58.com / https://music.2t58.com/
    下游全是 f'{base}路径' 这样拼的，少写个 https:// 或少了结尾斜杠就会拼出奇怪的
    URL 而静默失效，所以在这里一次规整好。
    """
    base = raw.strip()
    if not base:
        return ''
    if '://' not in base:
        base = 'https://' + base
    return base.rstrip('/') + '/'


class _SourceIPAdapter(HTTPAdapter):
    """把出站连接绑定到指定源 IP 的 requests 适配器

    为什么需要：源站按**来源 IP** 拦截，而不只是拦海外 IP。实测同一台服务器上，
    主 IP 访问 2t58.com 的 80/443 会在 TCP 层被丢包（ping 得通、22 端口也通，
    只有 Web 端口不通，是定向投的策略而非路由故障），换成同机第二个公网 IP
    就立刻恢复正常。requests 默认让内核挑源地址，挑不到指定的那个，
    只能在连接层用 urllib3 的 source_address 强制指定。

    为什么由 .env 控制而不是写死：国内开发机不需要这层绑定，不配
    MUSIC_2T58_SOURCE_IP 时行为与从前完全一致；换服务器也只需改 .env。
    """

    def __init__(self, source_address, **kwargs):
        self._source_address = source_address
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        kwargs['source_address'] = self._source_address
        self.poolmanager = PoolManager(num_pools=connections, maxsize=maxsize,
                                       block=block, **kwargs)


class Music2t58Spider:
    """2t58.com 首页数据爬虫

    目标站点有人机验证，需在 .env 配置有效的 MUSIC_2T58_PHPSESSID
    （浏览器通过验证后从 cookie 获取，过期需更新）。

    若部署机的主 IP 被源站拦截（症状：ping 通、其它端口通，只有 80/443 不通），
    在 .env 配 MUSIC_2T58_SOURCE_IP 指定另一个可用 IP 作为出站源地址即可。国内
    开发机不需要配置。
    """

    # ============ 源站域名与故障切换 ============
    # 源站有多个域名，解析到**不同 IP**（实测 www→103.85.227.61、music→154.222.31.131），
    # 并且会分别、临时地限制某个域名（实测同一台机器上 www 返回 403 的同一时刻，
    # music 仍是 200）。所以某个域名抓不到数据就自动切下一条。
    #
    # 列表写在 .env 的 MUSIC_2T58_DOMAINS（逗号分隔），**发现新域名直接往后加**，
    # 条数没有上限，会按顺序逐条试、失败的那条进冷却。写法很随意，
    # 下面三种都认（由 _normalize_base 统一规整）：
    #     music.2t58.com / https://music.2t58.com / https://music.2t58.com/
    #
    # 不用标注每条域名的播放接口是加密的还是明文的 —— _play_url 会自动识别：
    # 拿回来是 http(s) 开头就按明文直链用，否则才当密文走 AES 解密。
    #
    # 下面是没配 .env 时的兜底清单，也是从源站整理出来的已知域名。
    DOMAINS = [_normalize_base(d) for d in _env_list(
        'MUSIC_2T58_DOMAINS', 'https://www.2t58.com/,https://music.2t58.com/')]

    # 某条域名硬失败后冷却多久才再试（秒）。冷却期内直接跳过它，
    # 否则每个请求都要先在被封的域名上白等一次超时。
    DOMAIN_COOLDOWN = int(os.getenv('MUSIC_2T58_DOMAIN_COOLDOWN', '300'))

    # 各域名的冷却到期时间 {域名: 时间戳}，多进程共享（见 _alive_bases）
    DOMAIN_BAD_CACHE_KEY = '2t58_bad_domains'

    # ============ 兜底源（最后一层）============
    # 第一层（上面那组域名）**全部**拿不到时，才走这里。默认是 aat.cx（「爱听音乐网」）
    # —— 实测它跟 2t58 是**同一套程序的两个版本**：同一个 /js/play.php、同一套榜单 id
    # （new / top / djwuqu …）、同一个第三方歌词接口，连人机验证的字段名
    # （csrf_token + human_check）都一样；区别只有三处：
    #   ① 歌曲页路径是 /t/<id>.html（2t58 是 /song/<id>.html）
    #   ② 页面里的 id 多了一层十六进制编码（见 _to_fallback_id）
    #   ③ 首页/歌单/MV 等板块更多，页面结构略有出入（解析规则已做容错）
    #
    # 列表写在 .env 的 MUSIC_2T58_FALLBACK_BASES（逗号分隔，写法容错同 DOMAINS）。
    # ⚠️ 只放**同族站点**（页面结构与 id 编码规则一致），放别的站会安静地指错歌。
    FALLBACK_BASES = [_normalize_base(d) for d in _env_list(
        'MUSIC_2T58_FALLBACK_BASES', 'https://www.aat.cx/')]

    # 兜底源的冷却到期时间。**与第一层分开记**：第一层恢复了不该让兜底层陪着一起等，
    # 反之亦然（两边是不同机房的独立站点，可达性互不相关）。
    FALLBACK_BAD_CACHE_KEY = '2t58_bad_fallbacks'

    # 模拟浏览器请求头，避免基础反爬拦截
    # Referer 固定写 www：实测 play.php 的 Referer **不必**与目标域名一致，
    # 响应格式只由目标域名决定（www 回加密串、music 回明文直链）。
    HEADERS = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/131.0.0.0 Safari/537.36'
        ),
        'Referer': 'https://www.2t58.com/',
    }

    # 搜索被屏蔽时的特征文字：源站对这类词不返回结果页，只回一个约 1KB 的提示页。
    # 见过的原文：「信息提示:没有找到该关键词的相关歌曲。正在返回首页!」
    BLOCKED_MARKERS = ('信息提示', '正在返回首页')

    # 歌曲详情页相关配置
    # 歌词接口：与主站不同域（js.eev3.com），实测一直可用，所以不参与域名故障切换
    LRC_API = 'https://js.eev3.com/lrc.php'
    # AES 解密密钥（提取自 playen.js，经 SHA256 后用于 AES-ECB 解密播放链接）
    DECRYPT_KEY = 'SklaBTy1aTSEEtMjAyNg'

    # 全进程共用的 requests.Session（见 _shared_session / _fallback_session）
    _SHARED_SESSION = None
    _FALLBACK_SESSION = None
    _SESSION_LOCK = threading.Lock()

    @classmethod
    def _build_session(cls, with_phpsessid):
        """建一个抓取用 Session：统一请求头 + 出站源 IP 绑定（+ 人机验证 PHPSESSID）

        PHPSESSID 只注入第一层：它是**按域存放**的，而 `cookies.set` 不带 domain 时
        requests 会把它发给**所有**站点 —— 兜底源收到一个不属于自己的 PHPSESSID 后
        会重新弹人机验证，验证状态来回失效。所以两层各用各的会话。
        """
        session = requests.Session()
        session.headers.update(cls.HEADERS)
        # 注入人机验证通过后的 PHPSESSID
        if with_phpsessid:
            phpsessid = os.getenv('MUSIC_2T58_PHPSESSID', '')
            if phpsessid:
                session.cookies.set('PHPSESSID', phpsessid)
        # 指定出站源 IP（见 _SourceIPAdapter 说明）：不配就交给内核自己挑，
        # 与从前完全一致 —— 本地开发不需要这一层。
        source_ip = os.getenv('MUSIC_2T58_SOURCE_IP', '').strip()
        if source_ip:
            adapter = _SourceIPAdapter((source_ip, 0))
            session.mount('http://', adapter)
            session.mount('https://', adapter)
        return session

    @classmethod
    def _shared_session(cls):
        """取全进程共用的 Session，首次调用时创建

        为什么共用：Session 的价值就是连接复用与 Cookie 保持。所有视图都是
        `Music2t58Spider().fetch_xxx()` 这样每次新建实例，如果 Session 也跟着新建，
        那每个请求都要重做一遍 TCP+TLS 握手、连接池形同虚设，人机验证状态也保不住。
        """
        with cls._SESSION_LOCK:
            if cls._SHARED_SESSION is None:
                cls._SHARED_SESSION = cls._build_session(with_phpsessid=True)
            return cls._SHARED_SESSION

    @classmethod
    def _fallback_session(cls):
        """兜底源共用的 Session：与第一层分开，且不带 2t58 的 PHPSESSID

        兜底源的人机验证由 _pass_verification 自动过（GET 取 csrf_token → POST 表单），
        实测新会话就能过，不需要提前准备 PHPSESSID；验证状态存在它自己的 PHPSESSID 里，
        与第一层互不干扰。
        """
        with cls._SESSION_LOCK:
            if cls._FALLBACK_SESSION is None:
                cls._FALLBACK_SESSION = cls._build_session(with_phpsessid=False)
            return cls._FALLBACK_SESSION

    def __init__(self):
        self.session = self._shared_session()
        self.fallback_session = self._fallback_session()

    # ============ 跨源 id 转换 ============
    # 两站的歌曲/歌手 id 是**同一套原始 id 的两层编码**：
    #     2t58（第一层）  →  base64(原始id)              如 dmhoY2NobQ
    #     兜底源          →  hex(ascii(base64(原始id)))  如 646d686f59324e6f6251
    # 内部（缓存键、曲库、URL、模板）统一只认 2t58 那一套：出站时按目标源转码、
    # 入站时再转回来。实测歌曲 id 与歌手 id 都成立（用 5 位歌手逐一对过页面标题）。
    #
    # 为什么必须严格转码：两站 id 形态不同，把兜底源的 id 拿去 2t58 查**不会报错**，
    # 只会安静地返回另一首歌 —— 属于最难发现的那类 bug。
    @staticmethod
    def _to_fallback_id(sid):
        """内部 id → 兜底源要的 id 写法"""
        return sid.encode('utf-8').hex()

    @staticmethod
    def _from_fallback_id(raw_id):
        """兜底源的 id → 内部 id；不是合法 hex 就原样返回（宁可不动，也别改错）"""
        try:
            return bytes.fromhex(raw_id).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return raw_id

    # 兜底源页面里的 id 链接：/t/<id>.html、/singer/<id>.html、/singer/<id>/<页>.html
    _FALLBACK_LINK_RE = re.compile(
        r'href="/(t|singer)/([0-9a-fA-F]{2,})((?:/\d+)?\.html)"')

    @classmethod
    def _rewrite_fallback_html(cls, html):
        """把兜底源页面里的 id 链接改写回第一层的形态，让解析规则原样复用

        只改 href 里的这两种路径（t → song、singer 保持），其它一律原样保留。
        不这么做的话，解析出来的 sid 与链接都是兜底源的 id 写法，一旦拿去第一层查
        就会安静地指向另一首歌。
        """
        def repl(match):
            kind, raw_id, tail = match.groups()
            return 'href="/%s/%s%s"' % (
                'song' if kind == 't' else 'singer',
                cls._from_fallback_id(raw_id), tail)

        return cls._FALLBACK_LINK_RE.sub(repl, html)

    # ============ 缓存辅助方法 ============
    # 每个页面类型对应一个 .env 覆盖项,不配置时用全局 CACHE_TTL_HOURS
    # 不含 search：搜索结果不走页面缓存,由本地曲库统一缓存(见 _do_fetch_search 说明)
    # 不含 singer_list：歌手列表不再有带缓存的抓取,由 sync_singers 直连 _do_fetch_singer_list
    CACHE_TYPE_TTL_ENV = {
        'home': 'CACHE_TTL_HOURS_HOME',
        'singer': 'CACHE_TTL_HOURS_SINGER',
        'song': 'CACHE_TTL_HOURS_SONG',
        'chart': 'CACHE_TTL_HOURS_CHART',
    }

    def _ttl_for(self, cache_type):
        """按页面类型取缓存时长(秒):优先 .env 中 CACHE_TTL_HOURS_<类型> 覆盖,缺省用全局 CACHE_TTL_HOURS"""
        env_name = self.CACHE_TYPE_TTL_ENV.get(cache_type, '')
        hours = float(os.getenv(env_name)) if env_name and os.getenv(env_name) else CACHE_TTL_HOURS
        return int(hours * 3600)

    def _play_ttl(self):
        """播放直链短缓存时长(秒):CDN 直链有时效,单独短缓存防止过期播放失败"""
        return int(CACHE_TTL_PLAY_MINUTES * 60)

    def _cache_key(self, *parts):
        """构造缓存键:<站点缩写>_<功能>_<参数>;参数含特殊字符(空格/斜杠等)时取 md5,避免 FileBasedCache 键名告警"""
        joined = '_'.join(str(p) for p in parts)
        if any(c in joined for c in ' /?:#&=@'):
            joined = f'{parts[0]}_{hashlib.md5(joined.encode("utf-8")).hexdigest()}'
        return f'2t58_{joined}'

    def _cached(self, key, fetch_func, timeout):
        """带缓存的抓取:命中缓存直接返回,未命中执行抓取后写入缓存

        空结果(验证未通过/解析不到内容)不写缓存,避免失败结果被缓存住,
        导致源站恢复后页面仍长时间无数据。
        """
        data = cache.get(key)
        if data is not None:
            return data
        data = fetch_func()
        if not self._is_empty(data):
            cache.set(key, data, timeout)
        return data

    # 判断"是否有内容"时忽略的回显参数(直接来自 URL 参数,不是抓取到的内容)
    CACHE_IGNORE_KEYS = {'sid'}

    def _has_content(self, data):
        """递归判断抓取结果是否含有效内容

        规则:字典看值(跳过回显参数)、列表看长度、字符串需非空白;
        数字/布尔等标量不视为内容,避免把空壳结构误判为有数据。
        """
        if isinstance(data, dict):
            return any(
                self._has_content(v) for k, v in data.items()
                if k not in self.CACHE_IGNORE_KEYS
            )
        if isinstance(data, list):
            return len(data) > 0
        if isinstance(data, str):
            return bool(data.strip())
        return False

    def _is_empty(self, data):
        """抓取结果为空(验证未通过/解析不到内容)时返回 True"""
        return not self._has_content(data)

    # ============ 抓取源故障切换（第一层域名 → 第二层兜底源）============
    def _alive_bases(self, bases, cache_key):
        """从一组基址里挑出**不在冷却期内**的，保持原本顺序；全在冷却里就返回空列表

        状态放 Django cache 而不是类属性：uWSGI 起了多个进程，各记一份的话会出现
        "这个进程在用 www、那个进程还在撞 music"的错乱。cache 是文件后端，多进程共享。

        为什么全部冷却时返回空列表、而不是"清零后回到第一条重来"：后者听起来更贴合
        "全试完就重来"，但源站整体不可达时会让**每个请求**都把每条域名重新撞一遍 ——
        10 秒超时 × N 条，比不做切换还慢一倍，冷却也就白做了。返回空列表则是快速失败，
        页面立刻渲染（数据为空），等冷却到期后自动从第一条重新开始试。
        """
        bad = cache.get(cache_key) or {}
        now = time.time()
        return [b for b in bases if bad.get(b, 0) <= now]

    def _mark_base_bad(self, base, cache_key, label, reason):
        """把一条基址标记为冷却中，同时记一条日志，方便直接从线上日志看出哪条不行了"""
        bad = cache.get(cache_key) or {}
        bad[base] = time.time() + self.DOMAIN_COOLDOWN
        cache.set(cache_key, bad, self.DOMAIN_COOLDOWN * 4)
        logger.warning('%s %s 抓取失败，冷却 %s 秒后重试：%s',
                       label, base, self.DOMAIN_COOLDOWN, reason)

    def _get_html(self, path, fallback_path=None):
        """抓页面：先按第一层域名顺序试，**全都拿不到才走兜底源**

        path          第一层用的相对路径（如 'song/xxx.html'，空串表示首页）
        fallback_path 兜底源用的相对路径；不传就沿用 path（两站多数路径形态相同）。
                      歌曲页必须传 —— 兜底源是 /t/<转码后的id>.html，见 _to_fallback_id。

        路径由本方法决定而不让调用方拼好整条 URL：否则切了域名还在用旧域名。

        什么算硬失败：请求抛异常（连接超时、被拒、HTTP 4xx/5xx）或人机验证过不去。
        刻意**不**把"页面抓回来了但解析不出内容"也算失败 —— 同一层里的各条域名跑的是
        同一套程序（兜底源也是同族站点），解析规则一旦失效会同时影响它们，切了也救不了；
        而空结果在搜索页是合法的。
        """
        first_error = None
        for base in self._alive_bases(self.DOMAINS, self.DOMAIN_BAD_CACHE_KEY):
            try:
                return self._fetch_html(f'{base}{path}')
            except Exception as e:
                self._mark_base_bad(base, self.DOMAIN_BAD_CACHE_KEY, '源站域名', e)
                first_error = e
        return self._fetch_from_fallback(fallback_path or path, first_error)

    def _fetch_from_fallback(self, path, first_error):
        """走兜底源抓页面；兜底源也失败时抛错

        为什么放在最后才走：兜底源是别人家的站、页面又是同族程序的另一个版本，
        能不用就不用 —— 第一层只要还有一条可用，就不该惊动它。

        第一层"全部在冷却中"（一条都没试）同样会走到这里：冷却本身就表示这些域名
        当前不可用，这时不去兜底，页面就只能一直显示维护提示了。

        抛错时优先抛第一层那个：那才是根因，兜底失败通常只是连带结果。
        """
        last_error = None
        for base in self._alive_bases(self.FALLBACK_BASES, self.FALLBACK_BAD_CACHE_KEY):
            try:
                html = self._fetch_html(f'{base}{path}', session=self.fallback_session)
                return self._rewrite_fallback_html(html)
            except Exception as e:
                self._mark_base_bad(base, self.FALLBACK_BAD_CACHE_KEY, '兜底源', e)
                last_error = e
        if last_error is None:
            raise RuntimeError(
                f'兜底源全部处于冷却中，{self.DOMAIN_COOLDOWN} 秒后自动重试：{path}')
        raise first_error or last_error

    def _fetch_html(self, url, session=None):
        """抓单个 URL 的页面 HTML，自动处理人机验证（不做切换）

        目标站点验证机制：首次访问返回含 csrf_token 的验证页，
        需 POST 表单（勾选"我不是人机"）通过验证后才返回真实内容，
        验证状态在 session 中保留约 1 小时。**每条域名各有一套验证状态**（Cookie 按
        域名分域存放），所以切域名之后会自动在新域名上重新过一次验证。
        兜底源是另一个域、另一套 Cookie，所以走它自己的 session（见 _fallback_session）。
        """
        session = session or self.session
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        # 优先用 Content-Type 声明的编码；无声明（requests 默认 ISO-8859-1）时回退到 chardet 检测
        html = self._fix_encoding(resp).text
        # 命中人机验证页时，提交表单通过验证后重新请求原页面
        if 'csrf_token' in html and '安全人机验证' in html:
            html = self._pass_verification(url, html, session=session)
        # 过完验证仍是验证页 → PHPSESSID 失效或源站改了验证流程，这次抓取是失败的。
        # 必须报错，不能把验证页当正常页面返回：验证页里没有结果列表，曲库会把
        # 「空结果」当成「这个关键词查不到」打上负缓存（默认 12 小时内不再回源），
        # 于是一次验证失效就变成"所有搜索都搜不到"。
        # 对故障切换来说这也正是想要的信号：验证过不去 = 这条域名当前不可用。
        if '安全人机验证' in html:
            raise RuntimeError(f'人机验证未通过（PHPSESSID 可能已过期）：{url}')
        return html

    def _pass_verification(self, url, html, session=None):
        """提交人机验证表单（csrf_token + human_check），返回通过验证后的真实页面 HTML

        关键：POST 必须带 Referer + Origin 头，否则服务端返回 200 验证页（不跳转），
        验证永不生效；带上后返回 302 跳转，requests 自动跟随即拿到真实页面。

        字段名与流程两站通用（2t58 与兜底源是同一套验证程序），所以这条逻辑不用分源；
        只有 session 要分 —— 验证状态是记在各自 PHPSESSID 上的。
        """
        session = session or self.session
        tree = etree.HTML(html)
        csrf_nodes = tree.xpath('//input[@name="csrf_token"]')
        if not csrf_nodes:
            return html
        csrf_token = csrf_nodes[0].get('value', '')
        origin = urllib.parse.urlparse(url).scheme + '://' + urllib.parse.urlparse(url).netloc
        # 勾选"我不是人机"并提交，session 自动保存验证状态
        resp = session.post(url, data={'csrf_token': csrf_token, 'human_check': 'on'},
                            headers={'Referer': url, 'Origin': origin}, timeout=10)
        # 先修编码再判断中文标记：POST 回来的验证页一般是 GBK，requests 默认按
        # ISO-8859-1 解码时「安全人机验证」肯定匹配不上，就会漏掉下面的兜底 GET。
        resp = self._fix_encoding(resp)
        # 若 POST 跟随后仍是验证页，再 GET 一次兜底
        if '安全人机验证' in resp.text:
            resp = self._fix_encoding(session.get(url, timeout=10))
        return resp.text

    @staticmethod
    def _fix_encoding(resp):
        """编码兜底：Content-Type 没声明编码时（requests 默认 ISO-8859-1）用探测结果覆盖

        不修的话中文全是乱码，所有中文文案标记（如「安全人机验证」「信息提示」）都匹配不上。
        """
        if not resp.encoding or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding
        return resp

    def fetch_home(self):
        """抓取首页三大板块：热门歌手 / 歌曲飙升榜 / 流行趋势榜（带缓存）

        失败时抛出异常，由调用方捕获降级处理。
        """
        return self._cached(
            self._cache_key('home'),
            self._do_fetch_home,
            self._ttl_for('home'),
        )

    def _do_fetch_home(self):
        tree = etree.HTML(self._get_html(''))

        # 板块名按两站各传一遍：兜底源没有「歌曲飙升榜」，它那个位置的歌曲列表叫「新歌排行」；
        # 「流行趋势榜」在兜底源没有对应板块（找不到就是空列表，sitemap 少几条链接而已）。
        return {
            'hot_singers': self._parse_singers(tree),
            'rising_songs': self._parse_songs(tree, '歌曲飙升榜', '新歌排行'),
            'trending_songs': self._parse_songs(tree, '流行趋势榜'),
        }

    def _section(self, tree, *h1_keywords):
        """通过 h1 文本定位所在的 .layui-row.lkbj 区块；返回第一个命中的区块

        可以传多个候选名：两站是同一套程序的两个版本，同一个板块叫法不一样
        （2t58 叫「歌曲飙升榜」、兜底源叫「新歌排行」），按顺序试到哪个算哪个。
        """
        for keyword in h1_keywords:
            expr = (
                '//div[contains(@class,"layui-row") and contains(@class,"lkbj") '
                'and .//h1[contains(text(),"%s")]]'
            ) % keyword
            rows = tree.xpath(expr)
            if rows:
                return rows[0]
        return None

    def _parse_singers(self, tree):
        """解析热门歌手：name / link / pic"""
        row = self._section(tree, '热门歌手', '推荐歌手')
        if row is None:
            return []
        result = []
        for li in row.xpath('.//li'):
            a = li.xpath('.//div[@class="name"]/a')
            img = li.xpath('.//div[@class="pic"]//img/@src')
            if not a:
                continue
            result.append({
                'name': (a[0].text or '').strip(),
                'link': a[0].get('href', ''),
                'pic': img[0] if img else '',
            })
        return result

    def _parse_songs(self, tree, *h1_keywords):
        """解析歌曲榜单：title / link（h1_keywords 是板块名的候选，见 _section）"""
        row = self._section(tree, *h1_keywords)
        if row is None:
            return []
        result = []
        for li in row.xpath('.//li'):
            title, link = self._song_row(li)
            if not title:
                continue
            result.append({'title': title, 'link': link})
        return result

    def fetch_singer(self, sid, page=1):
        """抓取歌手详情页：歌手信息 / 歌曲列表 / 分页（带缓存）

        sid 为歌手id（如 d2t3eA），page 为歌曲列表页码。
        """
        return self._cached(
            self._cache_key('singer', sid, page),
            lambda: self._do_fetch_singer(sid, page),
            self._ttl_for('singer'),
        )

    def _do_fetch_singer(self, sid, page):
        tree = etree.HTML(self._get_html(
            f'singer/{sid}/{page}.html',
            # 兜底源上歌手页路径形态一样，只有 id 要转码
            fallback_path=f'singer/{self._to_fallback_id(sid)}/{page}.html'))

        return {
            'sid': sid,
            'singer': self._parse_singer_info(tree),
            'songs': self._parse_singer_songs(tree),
            'pagination': self._parse_pagination(tree),
        }

    def _parse_singer_info(self, tree):
        """解析歌手信息：name / pic / intro"""
        box = tree.xpath('//div[@class="singer_info"]')
        if not box:
            return {}
        box = box[0]
        name = box.xpath('.//h1/text()')
        pic = box.xpath('.//div[@class="pic"]//img/@src')
        intro = box.xpath('.//div[@class="info"]//p//text()')
        return {
            'name': name[0].strip() if name else '',
            'pic': pic[0] if pic else '',
            'intro': ''.join(intro).strip(),
        }

    def _parse_singer_songs(self, tree):
        """解析歌手歌曲列表：title / link"""
        result = []
        for li in tree.xpath(self._SONG_LIST_XPATH):
            title, link = self._song_row(li)
            if not title:
                continue
            result.append({'title': title, 'link': link})
        return result

    def _parse_pagination(self, tree):
        """解析分页：提取分页区所有链接（text / href / class / current）

        直接复用源站分页链接，避免其尾页页码在不同页不一致的问题。
        """
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

    @staticmethod
    def last_page_number(data):
        """从分页区「尾页」链接的 href 里读真实末页页码，读不到返回 0（未知）

        不能取可见页码文字的最大值 —— 那只是分页窗口。实测歌手列表第 1 页只显示
        「1 2 下一页 尾页」，取最大值会得到 2，而真实末页是 247（共 23631 位歌手）。
        曲库判断"这个关键词还能不能再翻"用的是同一套判断，所以放在这里共用。
        """
        for link in (data.get('pagination') or {}).get('links') or []:
            if (link.get('text') or '').strip() != '尾页':
                continue
            match = re.search(r'/(\d+)\.html', link.get('href') or '')
            if match:
                return int(match.group(1))
        return 0

    def fetch_song(self, sid):
        """抓取歌曲详情页：歌曲信息 / 播放链接 / 歌词 / 每日推荐（带缓存）

        sid 为歌曲id（如 d2ttY2R2bg）。
        歌曲信息与歌词走常规长缓存；播放直链单独短缓存（默认 30 分钟），
        避免 CDN 直链过期导致播放失败。
        """
        # 长缓存：歌曲信息 / 歌词 / 每日推荐
        # refetched 用来区分"这次真的回源了"还是"命中了长缓存"：
        # 回源时 _do_fetch_song 已经为歌词/封面请求过 play.php，直链也在它的返回值里，
        # 不必再走下面那次短缓存 —— 否则直链抓不到时（_cached 不缓存空值）
        # 同一个请求里会把 play.php 请求两遍，等于对源站的打扰翻倍。
        state = {'refetched': False}

        def _load():
            state['refetched'] = True
            return self._do_fetch_song(sid)

        data = self._cached(
            self._cache_key('song', sid),
            _load,
            self._ttl_for('song'),
        )
        if state['refetched']:
            return data

        # 命中长缓存：直链按它自己的短 TTL（CACHE_TTL_PLAY_MINUTES）单独刷新，
        # 因为 CDN 直链的时效比歌曲信息短得多。
        data['play_url'] = self._cached(
            self._cache_key('song_play', sid),
            lambda: self._fetch_play_info(sid)['play_url'],
            self._play_ttl(),
        )
        return data

    def _do_fetch_song(self, sid):
        tree = etree.HTML(self._get_html(
            f'song/{sid}.html',
            # 兜底源的歌曲页目录是 t/（不是 song/），且 id 要转码
            fallback_path=f't/{self._to_fallback_id(sid)}.html'))

        song_info = self._parse_song_info(tree)
        daily = self._parse_daily_recommend(tree)
        play_info = self._fetch_play_info(sid)
        lyrics = self._fetch_lyrics(play_info['cid'])

        # 封面图优先用页面解析的，为空时用 play.php 返回的
        if not song_info['cover']:
            song_info['cover'] = play_info['cover']

        # 封面统一升级成 https：源站给的是 http://img1.kuwo.cn/...，而本站走 https，
        # 浏览器会把 http 图片当混合内容静默拦掉（图不显示，控制台报 Mixed Content）。
        # 实测同一张图 https 同样返回 200 image/jpeg，直接换协议即可。
        song_info['cover'] = self._force_https(song_info['cover'])

        # 顺手把本次抓取的播放直链写入短缓存，避免被 fetch_song 重复请求 play.php
        if play_info['play_url']:
            cache.set(self._cache_key('song_play', sid), play_info['play_url'], self._play_ttl())

        return {
            'song': song_info,
            'lyrics': lyrics,
            'daily_recommend': daily,
            # 顺带把本次取到的直链带回给 fetch_song：它已经为歌词/封面请求过 play.php，
            # 直接复用即可，不要让调用方再走一次短缓存（否则直链为空时会重复请求源站）。
            'play_url': play_info['play_url'],
        }

    def fetch_download(self, sid):
        """获取歌曲下载所需数据：歌曲信息 / 最新播放链接 / 歌词

        下载时重新请求 play.php 获取直链（页面加载时解密的直链可能已过期），
        由视图层后端代理该直链，避免源站防盗链与链接时效问题。
        注意：下载必须返回最新直链，本方法不缓存。
        """
        tree = etree.HTML(self._get_html(
            f'song/{sid}.html',
            # 兜底源的歌曲页目录是 t/（不是 song/），且 id 要转码
            fallback_path=f't/{self._to_fallback_id(sid)}.html'))
        song_info = self._parse_song_info(tree)
        play_info = self._fetch_play_info(sid)
        lyrics = self._fetch_lyrics(play_info['cid'])
        return {
            'song': song_info,
            'play_url': play_info['play_url'],
            'lyrics': lyrics,
        }

    def _do_fetch_search(self, keyword, page):
        """抓取搜索结果页：结果列表 / 分页 / 是否被屏蔽（不带缓存）

        keyword 为搜索关键词，page 为页码。
        URL 规则：第1页 /so/{kw}.html，第N页 /so/{kw}/{N}.html

        刻意不提供带缓存的 fetch_search 版本：搜索结果统一由本地曲库
        （Web/services/music_library.py）负责缓存，再叠一层页面文件缓存只会
        让数据比预期更旧，还多一层要推理的东西。
        """
        encoded = urllib.parse.quote(keyword)
        if page > 1:
            path = f'so/{encoded}/{page}.html'
        else:
            path = f'so/{encoded}.html'
        html = self._get_html(path)
        tree = etree.HTML(html)
        results = self._parse_search_results(tree)

        return {
            'keyword': keyword,
            'results': results,
            'total_results': self._parse_total_results(tree),
            'pagination': self._parse_pagination(tree),
            'blocked': self._is_blocked(html, results),
        }

    @staticmethod
    def _parse_total_results(tree):
        """源站自己报的结果总数（div.pagedata 里的数字），读不到返回 0

        页面结构：<div class="pagedata">共有<span>3600</span>首搜索结果</div>

        有这个数字就不用拿"页数 × 每页条数"去估：实测末页通常不满，估算会偏大
        （陈奕迅 53 页，估算 3604，源站实际写的是 3600）。
        """
        for text in tree.xpath('//div[@class="pagedata"]/span/text()'):
            text = text.strip()
            if text.isdigit():
                return int(text)
        return 0

    def _is_blocked(self, html, results):
        """判断这一页是不是"关键词被屏蔽"的提示页

        和"这个词真的没有结果"要区分开：真没结果时源站仍会返回完整的结果页骨架
        （有 play_list 容器，写着"共有 0 首搜索结果"），而被屏蔽只回一句提示。
        """
        if results:
            return False
        return any(marker in html for marker in self.BLOCKED_MARKERS)

    def fetch_chart(self, chart, page=1):
        """抓取榜单页：热门榜单 / 结果列表 / 分页（带缓存）

        chart 为榜单标识（如 new、djwuqu），page 为页码。
        URL 规则：第1页 /list/{chart}.html，第N页 /list/{chart}/{N}.html
        """
        return self._cached(
            self._cache_key('chart', chart, page),
            lambda: self._do_fetch_chart(chart, page),
            self._ttl_for('chart'),
        )

    def _do_fetch_chart(self, chart, page):
        if page > 1:
            path = f'list/{chart}/{page}.html'
        else:
            path = f'list/{chart}.html'
        tree = etree.HTML(self._get_html(path))

        # 解析页面标题（容器两版页面通用，见 _LIST_TITLE_XPATH）
        title = ''.join(tree.xpath(self._LIST_TITLE_XPATH)).strip()

        return {
            'title': title,
            'songs': self._parse_search_results(tree),
            'pagination': self._parse_pagination(tree),
            'hot_rankings': self._parse_hot_rankings(tree, chart),
        }

    def _parse_hot_rankings(self, tree, chart=None):
        """解析热门榜单列表：title / link / current（div.ilingku_fl > li > a）

        源站不一定标记 current，传入 chart 时自动匹配当前榜单。
        """
        result = []
        for li in tree.xpath('//div[@class="ilingku_fl"]/li'):
            a = li.xpath('./a')
            if not a:
                continue
            title = (a[0].text or '').strip()
            if not title:
                continue
            link = a[0].get('href', '')
            is_current = 'current' in (a[0].get('class') or '')
            # 源站不一定标记 current，根据 chart 参数补充判断
            if not is_current and chart and link.replace('.html', '').split('/')[-1] == chart:
                is_current = True
            result.append({
                'title': title,
                'link': link,
                'current': is_current,
            })
        return result

    def _do_fetch_singer_list(self, area, gender, style, letter, page):
        """抓取歌手列表页并解析（不带缓存）

        URL 规则：/singerlist/{area}/{gender}/{style}/{letter}/{page}.html
        area/gender/style/letter 为分类标识，index 表示全部。

        只给 manage.py sync_singers 全量爬名册用（一次爬 247 页，逐页读一遍就走，
        没有"下次再读同一页"的场景），所以刻意不经过页面缓存。
        歌手大全页面本身也不再走这里 —— 它读本地歌手库，见 Web/services/singer_library.py。
        """
        base_path = f'singerlist/{area}/{gender}/{style}/{letter}'
        path = f'{base_path}/{page}.html' if page > 1 else f'{base_path}.html'
        tree = etree.HTML(self._get_html(path))

        # 解析页面标题（.singer_list h1）
        title_nodes = tree.xpath('//div[@class="singer_list"]//h1//text()')
        title = ''.join(title_nodes).strip()

        return {
            'title': title,
            'singers': self._parse_singer_list(tree),
            'filters': self._parse_singer_filters(tree),
            'pagination': self._parse_pagination(tree),
        }

    def _parse_singer_list(self, tree):
        """解析歌手列表：name / link / pic（div.singer_list ul li）"""
        result = []
        for li in tree.xpath('//div[@class="singer_list"]//ul/li'):
            name_a = li.xpath('.//div[@class="name"]/a')
            if not name_a:
                continue
            img = li.xpath('.//div[@class="pic"]//img/@src')
            result.append({
                'name': name_a[0].get('title', '') or (name_a[0].text or '').strip(),
                'link': name_a[0].get('href', ''),
                'pic': img[0] if img else '',
            })
        return result

    def _parse_singer_filters(self, tree):
        """解析歌手列表页4个分类筛选区域（div.ilingku_fl）

        每个 ilingku_fl 的第一个 li 是标题文字，后续 li > a 是分类选项。
        返回 [{label, options: [{title, link, current}]}]
        """
        result = []
        for fl in tree.xpath('//div[@class="ilingku_fl"]'):
            lis = fl.xpath('./li')
            if not lis:
                continue
            # 第一个 li 是标题文字（如"歌手分类:"）
            label = ''.join(lis[0].itertext()).strip()
            options = []
            for li in lis[1:]:
                a = li.xpath('./a')
                if not a:
                    continue
                title = (a[0].text or '').strip()
                if not title:
                    continue
                options.append({
                    'title': title,
                    'link': a[0].get('href', ''),
                    'current': 'current' in (a[0].get('class') or ''),
                })
            result.append({'label': label, 'options': options})
        return result

    # ============ 歌曲列表行（两版页面通用）============
    # 第一层与兜底源是**同一套程序的两个版本**，行结构只差一点：
    #     2t58   <div class="name"><a href="/song/<id>.html">歌手 - 歌名</a></div>
    #     兜底源 <div class="name"><a href="/t/<id>.html" title="歌手 - 歌名">
    #                <span class="sname">歌名</span><a href="/v/<id>.html">mv</a></a></div>
    # 兜底源那个 <a> 的文本是空的（首个子节点是元素），所以标题要按
    # @title → 文本 → span.sname 依次回退 —— 只认文本的话整页一条都解析不出来。
    #
    # 容器写法同理：2t58 是 div.play_list，兜底源是 div.lkmusic_list
    # （搜索页 "lkmusic_list lkbj sovd"、歌手页 "video_list lkmusic_list"、榜单页同）。
    # 用 contains 而不是 = ：源站会在同一个 class 属性里挂好几个类名。
    _SONG_LIST_XPATH = (
        '//div[contains(@class,"play_list") or contains(@class,"lkmusic_list")]'
        '//li[.//div[@class="name"]/a]')
    # 榜单页/搜索页的标题块（_do_fetch_chart 用）
    _LIST_TITLE_XPATH = (
        '//div[contains(@class,"play_list") or contains(@class,"lkmusic_list")]'
        '//div[@class="title"]//h1//text()')

    def _song_row(self, li):
        """从一行里取 (标题, 链接)；取不到标题时返回 (None, None)，由调用方跳过该行"""
        a = li.xpath('.//div[@class="name"]/a')
        if not a:
            return None, None
        a = a[0]
        title = (a.get('title') or '').strip()
        if not title:
            title = (a.text or '').strip()
        if not title:
            sname = a.xpath('./span[@class="sname"]/text()')
            title = sname[0].strip() if sname else ''
        return (title or None), a.get('href', '')

    def _parse_search_results(self, tree):
        """解析搜索结果列表：title / link / sid / name / singers

        搜索结果与榜单、每日推荐用的是同一套行结构（见 _SONG_LIST_XPATH）。
        title 保留源站原文（格式「歌手 - 歌名」）供列表直接渲染；
        另外拆出 sid / name / singers，让曲库入库时不必再解析一遍标题。
        """
        result = []
        for li in tree.xpath(self._SONG_LIST_XPATH):
            title, link = self._song_row(li)
            if not title:
                continue
            artists, song_name = self._parse_song_title(title)
            result.append({
                'title': title,
                'link': link,
                'sid': self._sid_from_link(link),
                'name': song_name,
                # 歌手用 & 连接，与源站标题里的写法保持一致，便于原样还原出 title
                'singers': '&'.join(artists),
            })
        return result

    @staticmethod
    def _sid_from_link(link):
        """从结果链接末段取 2t58 歌曲 id：/song/d3dkc2t3.html → d3dkc2t3"""
        tail = link.rsplit('/', 1)[-1]
        return tail[:-5] if tail.endswith('.html') else ''

    @staticmethod
    def _parse_song_title(title):
        """解析歌曲标题，拆分歌手列表与歌曲名。

        标题格式: "歌手1&歌手2 - 歌曲名"
        """
        if ' - ' in title:
            artist_str, song_name = title.split(' - ', 1)
            artists = [a.strip() for a in artist_str.split('&') if a.strip()]
        else:
            artists = []
            song_name = title
        return artists, song_name

    def _parse_song_info(self, tree):
        """解析歌曲基本信息：name / artists / cover / singer_url

        结构:
            - 歌名: div.djname > h1 文本，格式 "歌手 - 歌名"
            - 封面图: div.play_singer > div.pic > img @src
            - 歌手: div.play_singer > div.center > div.name > a @title / @href
        """
        h1_texts = tree.xpath('//div[@class="djname"]/h1/text()')
        title = ''.join(h1_texts).strip()
        artists, song_name = self._parse_song_title(title)

        img_nodes = tree.xpath('//div[@class="play_singer"]//div[@class="pic"]//img')
        cover = img_nodes[0].get('src', '') if img_nodes else ''

        name_a = tree.xpath('//div[@class="play_singer"]//div[@class="name"]/a')
        singer_url = ''
        singer_name = ''
        if name_a:
            # 歌手页链接（相对路径，如 /singer/d2t3eA.html），复用源站路径
            singer_url = name_a[0].get('href', '')
            singer_name = (name_a[0].get('title', '') or name_a[0].text or '').strip()
            if not artists and singer_name:
                artists = [singer_name]

        return {'name': song_name, 'artists': artists, 'cover': cover,
                'singer_url': singer_url}

    def _parse_daily_recommend(self, tree):
        """解析"每日推荐"歌曲列表：title / link

        兜底源没有这个板块，解析结果为空 —— 歌曲页少一块推荐而已，不影响播放与主信息。
        """
        result = []
        for li in tree.xpath(self._SONG_LIST_XPATH):
            title, link = self._song_row(li)
            if not title:
                continue
            result.append({'title': title, 'link': link})
        return result

    def _fetch_play_info(self, song_id):
        """请求 play.php 获取播放信息：播放直链 / 封面图 / 歌词cid（带故障切换）

        跟抓页面一样：先按第一层域名顺序试，**全都不行才走兜底源**。实测的坑：
        第一层里 www 返回的是**加密串**、music 返回的是**明文直链**（见 _play_url），
        兜底源回的也是明文 —— 拿到什么格式就按什么格式处理，不能只认加解密那条路。
        """
        data = self._request_play_info(self.DOMAINS, self.DOMAIN_BAD_CACHE_KEY, '源站域名',
                                       song_id, f'song/{song_id}.html', self.session)
        if data is None:
            # 兜底源：接口路径、参数、请求头完全一样，只有两处不同 ——
            # id 要转码（见 _to_fallback_id）、歌曲页目录叫 t/ 而不是 song/。
            fallback_id = self._to_fallback_id(song_id)
            data = self._request_play_info(
                self.FALLBACK_BASES, self.FALLBACK_BAD_CACHE_KEY, '兜底源',
                fallback_id, f't/{fallback_id}.html', self.fallback_session)
        if not data:
            return {'play_url': '', 'cover': '', 'cid': ''}

        return {
            'play_url': self._play_url(data.get('url', '')),
            'cover': data.get('pic', ''),
            'cid': str(data.get('lkid', '')),
        }

    def _request_play_info(self, bases, cache_key, label, song_id, song_path, session):
        """按 base 顺序请求 play.php，返回第一个可用的响应；全都拿不到返回 None

        song_id 与 song_path 都必须用**该源自己的写法**，由调用方负责转码 ——
        两站的 id 形态不同，传错了不会报错，只会安静地返回另一首歌的数据。
        """
        for base in self._alive_bases(bases, cache_key):
            try:
                resp = session.post(
                    f'{base}js/play.php',
                    data={'id': song_id, 'type': 'music'},
                    headers={
                        'X-Requested-With': 'XMLHttpRequest',
                        # Referer 用当前域名的歌曲页（实测不匹配也能用，但保持真实更稳）
                        'Referer': f'{base}{song_path}',
                        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                    },
                    timeout=10,
                )
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                self._mark_base_bad(base, cache_key, label, e)
                continue
            # 接口通了但没给数据：不判定域名坏（可能只是这首歌没有可播音频），
            # 换下一条域名当场再试
            if data.get('msg') == 1:
                return data
        return None

    def _fetch_lyrics(self, cid):
        """请求 lrc.php 获取 LRC 格式歌词文本（带时间标签，供前端 window.BZLrc 同步滚动）"""
        if not cid:
            return ''
        try:
            resp = self.session.get(self.LRC_API, params={'cid': cid}, timeout=10)
            data = resp.json()
            return data.get('lrc', '')
        except (requests.RequestException, ValueError):
            return ''

    @staticmethod
    def _force_https(url):
        """把 http:// 开头的地址升级成 https://，其它原样返回

        只用于外链图片（封面）。本站是 https，页面上出现 http 资源会被浏览器
        按混合内容拦掉，且是静默的 —— 图直接不显示，只有控制台有提示。
        """
        return 'https://' + url[7:] if url.startswith('http://') else url

    def _play_url(self, raw):
        """把 play.php 返回的 url 字段转成可直接播放的地址

        源站不同域名给的格式**不一样**（实测）：
            www.2t58.com    →  39f8974aabb4150e...                十六进制密文，要 AES 解密
            music.2t58.com  →  https://car-er.kuwo.cn/xxx.m4a     明文直链，直接用
        只认解密那一条路的话，切到 music 之后页面有内容但播放不了 —— 直链被当密文，
        解出来是空串。这是本次排查中实际踩到的坑。
        """
        if not raw:
            return ''
        if raw.startswith('http'):
            return raw
        return self._decrypt_play_url(raw)

    # 注意：上面这套"看开头是不是 http 来判断格式"只对**同一把 AES 密钥**成立。
    # 以后新增的域名如果换了密钥，加密串照样解不出来（症状：页面有内容但不播放），
    # 那时再把新密钥加进来按域名分流，不用提前设计。

    def _decrypt_play_url(self, encrypted):
        """解密播放链接：AES-ECB，密钥由固定明文经 SHA256 生成"""
        if not encrypted:
            return ''
        try:
            key = hashlib.sha256(self.DECRYPT_KEY.encode('utf-8')).digest()
            ciphertext = bytes.fromhex(encrypted)
            cipher = AES.new(key, AES.MODE_ECB)
            decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
            return decrypted.decode('utf-8')
        except (ValueError, KeyError):
            return ''
