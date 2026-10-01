"""数据模型

六组互不相干的表：
    Singer —— 全站歌手名册（歌手名 / 详情页 ID / 封面 / 简介），由 manage.py sync_singers 同步
    SingerSong —— 歌手曲目列表（哪位歌手有哪些歌、排在第几页第几首），由 sync_singer_songs 同步
    SongPlay —— 每日播放榜（哪首歌在哪一天被整首听完过几次），首页「今日热听榜」的数据源
    Song / SearchKeyword / SearchResult —— 曲库，把搜索结果沉淀到本地
    PlayedSong —— 播放过的歌：歌曲信息的本地缓存 + 播放页「大家正在听」的数据源（见文件末尾）
    UcUser / Favorite —— 用户与「喜欢」（见文件末尾）。认证由小影用户中心负责，
                          本地只保存它的身份镜像与用户收藏的歌曲

PlayedSong 与 SongPlay 都跟"播放"有关，容易混，这里先点明区别：
    SongPlay   —— 历史统计，按 (歌曲, 日期) 分桶，记"整首听完了几次"，给「今日热听榜」排名
    PlayedSong —— 实时 + 缓存，按歌曲唯一，记"最近一次开始播放是什么时候"，并顺手把歌名/封面/
                  歌词/直链存下来当缓存用（见文件末尾的 PlayedSong 说明）

曲库那部分的用途：把从 2t58 搜索页抓到的歌曲沉淀到本地，使同一个关键词不必反复回源站爬取。
搜索先查本地库；库里不够翻页时再去源站"补货"，新数据顺带写入。

SingerSong 与 Song 的区别（容易混，特别说明）：
    Song       —— "被搜索过的歌"，sid 唯一，多个关键词共用同一行，供搜索页用
    SingerSong —— "按歌手组织的曲目表"，同一首歌出现在多位歌手名下会各存一行
                  （合唱歌、精选集就是这么来的），所以 sid 在这里不唯一。
                  供歌手页本地渲染 + 站内模糊搜索用。

为什么以 sid 为唯一键：
    2t58 的歌曲 id（sid）天然一歌一值，且正是我们播放页 /song/<sid>.html 的主键，
    用它做唯一键最直接，不会出现"同名同歌手但确实是两首歌"被误合并的情况。

为什么还要一张"关键词 → 歌曲"的关联表（SearchResult）：
    源站的搜索到底按什么匹配、什么时候会变成兜底结果，都不可控（实测：搜歌手名能搜到，
    但「周杰伦」被单独屏蔽返回空；搜「音乐」反而返回 68 条一条都不含"音乐"的歌）。
    所以"这个关键词的结果里有哪些歌"猜不出来，只能把源站实际返回的列表原样记下来。
"""
from django.db import models
from django.utils import timezone


class Singer(models.Model):
    """一位歌手：详情页 ID + 名称 + 封面 + 简介

    数据来源是源站的「全部歌手列表」（/singerlist/index/index/index/index.html，
    实测共 23631 位 / 247 页），由 manage.py sync_singers 抓取并入库。

    封面存两份地址，各有各的用处：
        source_pic —— 源站列表页里那张图的地址（gimg3.baidu.com 中转的 kuwo 头像）
        cover      —— 下载后上传到小影 API 图床（PicUI 线路）得到的 CDN 外链，页面展示用这个
    留 source_pic 是为了以后想换图床、或图床图丢了时，不用重爬 247 页列表就能重传。
    """

    # 2t58 歌手 id，如 ZGNua2t4dw（来自列表页链接 /singer/ZGNua2t4dw.html）
    sid = models.CharField('2t58 歌手 id', max_length=32, unique=True)
    name = models.CharField('歌手名', max_length=255)
    source_pic = models.URLField('源站封面地址', max_length=500, blank=True)
    cover = models.URLField('图床封面外链', max_length=500, blank=True)
    # 歌手简介：源站歌手页正文里那段。爬曲目列表（sync_singer_songs）时顺手存下来，
    # 这样歌手页整页都能本地渲染，不必为了简介再回源一次。
    intro = models.TextField('歌手简介', blank=True)
    # 该歌手曲目的总页数（源站分页区「尾页」的页码；没有分页时按 1 页算）。
    # 歌手页第 1 页本地渲染时要靠它决定分页器显示到第几页。
    song_pages = models.PositiveIntegerField('曲目总页数', default=0)
    # 曲目列表最近一次同步时间。为空 = 还没同步过 —— sync_singer_songs 就靠它断点续跑。
    songs_synced_at = models.DateTimeField('曲目最近同步时间', null=True, blank=True)
    # 最近一次同步到这张记录的时间：入库时写入，之后每次同步再见到就刷新。
    pulled_at = models.DateTimeField('最近同步时间', auto_now=True)

    class Meta:
        verbose_name = '歌手'
        verbose_name_plural = '歌手'

    def __str__(self):
        return self.name

    @property
    def link(self):
        """站内歌手页链接（模板直接当 href 用），与 Web/views/urls.py 的路由一致"""
        return f'/singer/{self.sid}.html'

    @property
    def pic(self):
        """展示用的头像地址：**源站原图优先**，没有才用图床外链

        2026-10-01：小影图床（free.picui.cn）上的图**全部 404** 了 —— 实测 cover
        列抽 5 位全是 404，而同一批的源站原图（gimg3.baidu.com 中转的 kuwo 头像）
        全部 200 仍然可用。所以把优先级倒过来，页面立刻恢复。

        两列地址一直都在库里，所以以后图床恢复、或换别家图床，把这两行换回去即可，
        不用重爬 247 页列表（这也是当初把 source_pic 留一份的原因）。
        """
        return self.source_pic or self.cover


class SingerSong(models.Model):
    """歌手曲目列表里的一行：某位歌手第 page 页的第 position 首

    为什么要这张表：歌手页原本每次都要回源抓（虽带 2 小时页面缓存，但缓存一过期
    第一个访客就要等 1 秒多）。把每位歌手的曲目落库后，歌手页可以整页本地渲染
    （0 请求、毫秒级），站内搜索也才有本地数据可查。

    为什么用 (page, position) 而不是一个"全局序号"：
        源站一页固定 68 首，存页内序号不需要任何魔法数字，跨页拼接也不用换算；
        取某一页就是 filter(singer_sid=..., page=...).order_by('position')。
    为什么 sid 不唯一：同一首歌会出现在多位歌手名下（合唱/精选集），
        每个歌手页要各展示一份，所以这里允许重复；唯一性由
        (singer_sid, page, position) 保证 —— 同一歌手的同一位置只能有一首。
    """

    singer_sid = models.CharField('2t58 歌手 id', max_length=32)
    page = models.PositiveIntegerField('源站页码', default=1)
    position = models.PositiveIntegerField('该页内第几首')
    sid = models.CharField('2t58 歌曲 id', max_length=32)
    # 源站列表里的整串标题（"歌手 - 歌名"）。刻意不做拆分：歌名里本身可能含 " - "，
    # 硬拆会把「A - B - C」这类标题拆错；搜索也只需要对这一列做模糊匹配。
    title = models.CharField('歌手 - 歌名', max_length=255)

    class Meta:
        verbose_name = '歌手曲目'
        verbose_name_plural = '歌手曲目'
        constraints = [
            # 唯一键顺带就是歌手页的查询索引（singer_sid + page + position 全在里面）
            models.UniqueConstraint(fields=['singer_sid', 'page', 'position'],
                                    name='uniq_singer_song_pos'),
        ]

    def __str__(self):
        return f'{self.singer_sid} 第 {self.page} 页 #{self.position} {self.title}'

    @property
    def link(self):
        """站内播放页链接（模板直接当 href 用），与 Song.link 保持一致"""
        return f'/song/{self.sid}.html'


class SongPlay(models.Model):
    """某首歌在某一天被"整首听完"的次数 —— 首页「今日热听榜」的数据源

    数据来源是播放页：song_player.js 的 ended 事件（整首放完）才发一次计数请求，
    所以这里记的是"真正被听完的次数"，不是"点开播放的次数"。
    计数入口见 Web/services/play_rank.py，取用见 Web/services/play_rank.py 的 today_top()。

    按天分桶（一行 = 一首歌 + 一天），而不是"每首歌一行 + 跨天归零"：
        · "每日 00:00 重新计算"变成一句查询条件（play_date = 今天），不需要任何定时任务
        · 历史自然留存，以后要做周榜/月榜不用改表结构

    歌名/歌手为什么存在这里，而不是去关联 Song 表：
        本地 Song 表只沉淀"被搜索过"的歌，而播放页绝大多数是从歌手页/榜单点进来的，
        库里根本没有对应记录。所以标题跟着播放页一起落库（入库前截断长度），
        这张表才能独立渲染出"歌手 - 歌名"。
    """

    # 2t58 歌曲 id，如 d3dkc2t3（对应播放页 /song/<sid>.html）。与 play_date 一起唯一。
    sid = models.CharField('2t58 歌曲 id', max_length=32)
    # 播放日期（北京时间）。TIME_ZONE=Asia/Shanghai，用 timezone.localdate() 取的就是北京的"今天"
    play_date = models.DateField('播放日期（北京时间）')
    name = models.CharField('歌名', max_length=255)
    artists = models.CharField('歌手', max_length=255, blank=True)
    plays = models.PositiveIntegerField('当日听完次数', default=0)
    last_played_at = models.DateTimeField('最近一次听完时间', null=True, blank=True)
    # ---- 以下两个字段是防刷状态 ----
    # 同一个人对同一首歌在 PLAY_DEDUP_MINUTES 分钟内重复听完只算一次。
    # 状态记在当日那一行上，不写缓存键：缓存键里带 IP 的话，伪造 X-Forwarded-For
    # 就能一个请求撑出一个缓存文件，攒够几万个就把 cache/ 目录塞爆。
    last_hit_ip = models.CharField('最近一次计数来自哪个 IP', max_length=64, blank=True)
    last_hit_at = models.DateTimeField('最近一次计数时间', null=True, blank=True)

    class Meta:
        verbose_name = '歌曲播放次数'
        verbose_name_plural = '歌曲播放次数'
        constraints = [
            # 同一首歌同一天只留一行：反复听完是累加 plays，不是插入新行
            models.UniqueConstraint(fields=['sid', 'play_date'], name='uniq_song_play_day'),
        ]
        indexes = [
            # 今日榜的查询就是「play_date = 今天」按次数倒序取前几，正好对上这个复合索引
            models.Index(fields=['play_date', '-plays'], name='idx_play_rank'),
        ]

    def __str__(self):
        return f'{self.title}（{self.play_date} 听完 {self.plays} 次）'

    @property
    def title(self):
        """展示标题，格式与 Song.title 一致（歌手 - 歌名），首页模板直接渲染"""
        return f'{self.artists} - {self.name}' if self.artists else self.name

    @property
    def link(self):
        """站内播放页链接（模板直接当 href 用），与 Song.link 保持一致"""
        return f'/song/{self.sid}.html'


class Song(models.Model):
    """一首歌"""

    # 2t58 歌曲 id，如 d3dkc2t3（来自搜索结果链接 /song/d3dkc2t3.html）
    sid = models.CharField('2t58 歌曲 id', max_length=32, unique=True)
    name = models.CharField('歌名', max_length=255)
    # 歌手原串（多个以 & 连接，如「周杰伦&袁咏琳」）。用原串而不是顿号分隔，
    # 是为了能原样还原成源站结果列表里的「歌手 - 歌名」标题。
    singers = models.CharField('歌手', max_length=255, blank=True)
    # 最近一次在源站见到这首歌的时间：入库时写入，之后每次补货再见到就刷新。
    pulled_at = models.DateTimeField('最近在源站见到的时间', auto_now=True)

    class Meta:
        verbose_name = '歌曲'
        verbose_name_plural = '歌曲'

    def __str__(self):
        return self.title

    @property
    def title(self):
        """还原源站结果列表的标题格式（歌手 - 歌名），供模板直接渲染"""
        return f'{self.singers} - {self.name}' if self.singers else self.name

    @property
    def link(self):
        """站内播放页链接，与源站结果链接格式保持一致（模板直接当 href 用）"""
        return f'/song/{self.sid}.html'


class SearchKeyword(models.Model):
    """一个搜索关键词：抓取进度 + 被搜次数

    crawled_pages 是"水位线"：已经从源站爬到第几页。展示层与源站页码一一对应
    （1 个展示页 = 1 个源站页），所以"第 P 页能不能看"就看 crawled_pages 有没有到 P。
    """

    keyword = models.CharField('关键词', max_length=255, unique=True)
    crawled_pages = models.PositiveIntegerField('已爬到源站第几页', default=0)
    # 源站真实末页页码（从分页区「尾页」链接的 href 里读，不是从可见的页码文字里猜）
    total_pages = models.PositiveIntegerField('源站末页页码', default=0)
    # 源站自己报的结果总数（分页区的「共有 N 首搜索结果」）。
    # 直接用它，不要拿"页数 × 每页条数"估 —— 末页通常不满，估出来会偏大。
    total_results = models.PositiveIntegerField('源站报的结果总数', default=0)
    # 最近一次抓取时间，保鲜判断用：超过 SEARCH_KEYWORD_TTL_HOURS 就同步重爬刷新
    crawled_at = models.DateTimeField('最近抓取时间', auto_now=True)
    # 源站确认这个关键词拿不到任何结果（翻过了最后一页，或命中屏蔽）。
    # 这是"负缓存"：没有它，每有人搜一次这种词就会把爬取预算烧光，结果永远是空。
    source_empty = models.BooleanField('源站确认查不到结果', default=False)
    # 被源站屏蔽：这类词源站不给结果页，只回一句"没有找到…正在返回首页"。
    # 单独记一笔是为了：① 给用户准确的提示（而不是让他以为打错了字）；
    # ② 屏蔽不会自愈，重试间隔可以拉长，少白跑几趟。
    blocked = models.BooleanField('被源站屏蔽', default=False)
    # ---- 以下两个字段供热门搜索榜使用 ----
    search_count = models.PositiveIntegerField('被搜次数', default=0)
    last_searched_at = models.DateTimeField('最近被搜时间', null=True, blank=True)
    # ---- 以下两个字段是热门榜的防刷状态 ----
    # 同一个人短时间内重复搜同一个词只算一次。状态记在关键词行上，而不是放进缓存键：
    # 缓存键里带 IP 的话，伪造 X-Forwarded-For 就能一个请求撑出一个缓存文件，
    # 攒够几万个就把 cache/ 目录塞爆；记在行上则一个关键词永远只占一行。
    last_hit_ip = models.CharField('最近一次计数来自哪个 IP', max_length=64, blank=True)
    last_hit_at = models.DateTimeField('最近一次计数时间', null=True, blank=True)

    class Meta:
        verbose_name = '搜索关键词'
        verbose_name_plural = '搜索关键词'
        indexes = [
            # 热门榜的查询条件就是「没被屏蔽 + 最近活跃」（source_empty=False 已含屏蔽），
            # 再按次数倒序取前几个，所以这个复合索引正好对上。
            models.Index(fields=['source_empty', '-search_count'], name='idx_hot_keyword'),
        ]

    def __str__(self):
        return f'{self.keyword}（已爬到第 {self.crawled_pages} 页）'


class SearchResult(models.Model):
    """关键词 → 歌曲 的对应关系：源站为这个关键词返回过哪些歌、排在第几页第几位

    关键词与展示页码一一对应（1 个展示页 = 1 个源站页），所以取第 P 页就是
    "筛出 source_page == P 的行"。position 只负责页内顺序。
    """

    keyword = models.ForeignKey(SearchKeyword, on_delete=models.CASCADE,
                                related_name='results', verbose_name='关键词')
    song = models.ForeignKey(Song, on_delete=models.CASCADE, verbose_name='歌曲')
    source_page = models.PositiveIntegerField('来自源站第几页')
    position = models.PositiveIntegerField('在该页里的第几位')

    class Meta:
        verbose_name = '搜索结果'
        verbose_name_plural = '搜索结果'
        # id 兜底：保鲜重爬第 1 页捞到新歌时，新歌的位次会与老歌撞上，
        # 用 id 升序让新歌排在老歌之后，保证切片顺序是确定的而不是看数据库心情。
        ordering = ['source_page', 'position', 'id']
        constraints = [
            # 同一关键词里同一首歌只留一行：重复爬到同一页时不产生新行，
            # 也不会把这首歌原来的位置改写掉（累加池：只追加，不重排）。
            models.UniqueConstraint(fields=['keyword', 'song'], name='uniq_keyword_song'),
        ]
        indexes = [
            # 每次搜索页渲染都走这个条件，必须有索引，否则关键词一多就是全表扫。
            models.Index(fields=['keyword', 'source_page'], name='idx_result_page'),
        ]

    def __str__(self):
        return f'{self.keyword.keyword} 第 {self.source_page} 页 #{self.position}'


class UcUser(models.Model):
    """小影用户中心（UAC）的用户，在本地留的一份身份镜像

    为什么要有这张表，而不是只把 Token 塞进 session：
      ① Favorite 需要一个稳定的外键。直接拿小影的 user_id（UUID 字符串）当外键也行，
         但那样每次查询、每个模板变量都要带着这串 UUID，以后想加"昵称/头像"之类
         本地资料也没地方放；
      ② 登录/注册后把身份在本地留一份，便于排查问题与统计。

    **密码一律不存**：注册、登录、改密全部由小影用户中心负责，本地只保存它返回的
    身份信息。所以这张表里没有任何可用来登录的凭据，丢了也不影响用户登录。

    查询入口是 get_or_create_by_uc()：UAC 的 user_id 是稳定主键，登录时按它 upsert。
    """

    # 小影的 user_id（UUID 字符串，如 6002dd04-93c6-42c1-a40a-239ac2ca13b7）
    uc_user_id = models.CharField('小影 user_id', max_length=64, unique=True)
    # 系统发放的账号（12 位数字，如 880083678837），可用于「账号 + 密码」登录
    account = models.CharField('小影账号', max_length=32, blank=True)
    # 昵称：注册时可选填。小影那边可以为空（实测返回 username 是空串）
    username = models.CharField('昵称', max_length=64, blank=True)
    email = models.CharField('邮箱', max_length=255, blank=True)
    phone = models.CharField('手机号', max_length=32, blank=True)
    created_at = models.DateTimeField('首次登录时间', auto_now_add=True)
    last_login_at = models.DateTimeField('最近登录时间', auto_now=True)

    class Meta:
        verbose_name = '用户'
        verbose_name_plural = '用户'

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        """页面上展示的名字：昵称优先，没有就用账号，再没有兜个"用户"

        小影的 username 允许为空（实测注册时不填就是空串），所以模板里不要直接用
        username，统一读这个属性，免得页面上出现"欢迎，"这种半截话。
        """
        return self.username or self.account or '用户'

    @property
    def link(self):
        """「我的喜欢」页地址（模板直接当 href 用）"""
        return '/my/likes.html'


class Favorite(models.Model):
    """用户"喜欢"的一首歌 —— 喜欢列表页与播放页心形按钮的数据源

    为什么标题要跟着一起存（而不是只留 sid、渲染时再去查）：
        与 SongPlay 同样的理由 —— 播放页与喜欢列表里的歌绝大多数是从歌手页、榜单、
        搜索结果点进来的，本地 Song 表只沉淀"被搜索过"的歌，库里往往根本没有这条记录。
        只存 sid 的话，喜欢列表渲染不出「歌手 - 歌名」。

    sid 用 2t58 的歌曲 id（也就是 /song/<sid>.html 的主键）。兜底源的 id 已在爬虫层
    统一转码成同一形态（见 SpiderServices/Music_2t58/main.py 的 _to_fallback_id），
    所以全站只有一个 id 体系，这里不必区分来源。
    """

    user = models.ForeignKey(UcUser, on_delete=models.CASCADE,
                             related_name='favorites', verbose_name='用户')
    sid = models.CharField('2t58 歌曲 id', max_length=32)
    title = models.CharField('歌手 - 歌名', max_length=255)
    created_at = models.DateTimeField('喜欢的时间', auto_now_add=True)

    class Meta:
        verbose_name = '喜欢的歌'
        verbose_name_plural = '喜欢的歌'
        # 最近喜欢的排在前面（列表和"取消喜欢"的体验都靠它）
        ordering = ['-created_at']
        constraints = [
            # 同一个人对同一首歌只留一行：重复点"喜欢"是幂等的累加操作，不该堆出多行。
            # 有它之后，"切换喜欢"就是 create / delete 两种原子操作，不必先查再判。
            models.UniqueConstraint(fields=['user', 'sid'], name='uniq_user_favorite'),
        ]
        indexes = [
            # 喜欢列表的查询条件就是「user = 当前用户」按时间倒序取，正好对上这个索引
            models.Index(fields=['user', '-created_at'], name='idx_fav_user_time'),
        ]

    def __str__(self):
        return f'{self.user} ♥ {self.title}'

    @property
    def link(self):
        """站内播放页链接（模板直接当 href 用），与 Song.link 保持一致"""
        return f'/song/{self.sid}.html'


class PlayedSong(models.Model):
    """播放过的歌：歌曲信息的本地缓存 + 播放页「大家正在听」的数据源

    解决两个问题（都是为了少回源站，避免 IP 被封）：
      ① 缓存 —— 同一首歌再被播放时，歌名/歌手/封面/歌词/直链不再回源抓一遍，
         直接读这里。只有"库里还没有这首歌"（首次播放）才完整抓一次并落库。
      ② 正在听 —— 页面上那块「大家正在听」就是在本表里取最近 N 分钟有播放记录的行，
         按时间倒序，完全不额外回源。

    与 SongPlay 的区别（都跟"播放"有关，别混）：
        SongPlay   —— 按 (歌曲, 日期) 分桶，记"整首听完了几次"，给「今日热听榜」排名，
                      是**历史统计**，一行只增不减（跨天新开一行）
        PlayedSong —— 按歌曲唯一，记"最近一次开始播放是什么时候"，是**实时状态 + 缓存**，
                      一首歌永远只有一行，重复播放是刷新时间、累加次数

    为什么按歌曲唯一（sid 唯一键），而不是每次播放插一行：
        每次插一行的话，热门歌一会儿就把表撑大，清理还得按行删。按歌曲唯一之后，
        "正在听"看的是 last_played_at，"缓存"看的是同一行上的歌曲信息，一举两得。

    时效（两个不同的语义，别混，见 settings）：
        PLAYED_URL_TTL_MINUTES  —— 直链新鲜度：超过它说 CDN 直链可能过期了，需要刷一次
        PLAYED_SHOW_MINUTES     —— 展示窗口：last_played_at 在它之内才算"正在听"
    """

    # 2t58 歌曲 id（/song/<sid>.html 的主键）。兜底源的 id 已在爬虫层统一转码成同一形态，
    # 所以全站只有一个 id 体系，这里不必区分来源。
    sid = models.CharField('2t58 歌曲 id', max_length=32, unique=True)
    name = models.CharField('歌名', max_length=255)
    artists = models.CharField('歌手', max_length=255, blank=True)
    # 源站歌手页链接（解析歌曲页时顺带拿到的相对路径）。存下来是为了让"读缓存渲染"
    # 与"回源渲染"完全一致 —— 否则命中缓存时歌手名会少一条链接。
    singer_url = models.CharField('源站歌手页链接', max_length=500, blank=True)
    # 封面外链（play.php 返回的 pic）。存下来，「正在听」列表就能直接出图、不回源
    cover = models.URLField('封面外链', max_length=500, blank=True)
    # 歌词正文（LRC 原文）。空歌词也照存空串 —— 区分"抓过但确实没有"与"还没抓过"
    lyrics = models.TextField('歌词', blank=True)
    # 音频直链（CDN，60~73 分钟时效）。过期不用重抓整页，调 play.php 刷新一次即可
    play_url = models.URLField('音频直链', max_length=1000, blank=True)
    # 上面这条直链是何时取到的：用来判断是否新鲜（超过 PLAYED_URL_TTL_MINUTES 就刷）
    play_url_at = models.DateTimeField('直链获取时间', null=True, blank=True)
    # 最近一次「开始播放」的时间：正在听窗口（PLAYED_SHOW_MINUTES）与排序都靠它
    last_played_at = models.DateTimeField('最近一次开始播放时间', null=True, blank=True)
    plays = models.PositiveIntegerField('累计开始播放次数', default=0)
    # 歌曲信息（歌名/歌手/封面/歌词）最近一次从源站刷新的时间。仅用于排查与将来的保鲜，
    # 不参与"正在听"的窗口判断（那个看 last_played_at）。
    info_at = models.DateTimeField('歌曲信息抓取时间', null=True, blank=True)

    class Meta:
        verbose_name = '播放过的歌'
        verbose_name_plural = '播放过的歌'
        indexes = [
            # 「正在听」的查询就是「last_played_at 在窗口内」按时间倒序取前 N，
            # 降序索引正好对上，免得每次渲染都全表排序。
            models.Index(fields=['-last_played_at'], name='idx_played_recent'),
        ]

    def __str__(self):
        return f'{self.title}（{self.plays} 次）'

    @property
    def title(self):
        """展示标题，格式与 Song.title 一致（歌手 - 歌名），正在听列表直接渲染"""
        return f'{self.artists} - {self.name}' if self.artists else self.name

    @property
    def ago(self):
        """「正在听」里那句相对时间（刚刚 / N 分钟前）

        这模块讲的就是"此刻"，所以列表右侧给一个相对时间而不是绝对时间 —— 一眼看出
        "有人刚刚点开"。服务端渲染时算一次；页面开久了这句会停在旧值（列表本身也是
        服务端渲染的，要真正实时得整块轮询刷新，见 README 9.1 的已知限制）。
        """
        if not self.last_played_at:
            return ''
        seconds = (timezone.now() - self.last_played_at).total_seconds()
        minutes = int(max(0, seconds) // 60)   # 负数是时钟偏差，按"刚刚"处理
        return '刚刚' if minutes < 1 else f'{minutes} 分钟前'

    @property
    def link(self):
        """站内播放页链接（模板直接当 href 用），与 Song.link 保持一致"""
        return f'/song/{self.sid}.html'
