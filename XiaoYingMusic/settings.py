import os
from pathlib import Path
from dotenv import load_dotenv

# override=True：以 .env 为唯一权威配置源。
# 原因：IDE 集成终端/系统可能已存在同名环境变量（如旧的 SITE_NAME 快照），
# python-dotenv 默认不覆盖它们，会导致「改了 .env 却不生效」的困惑。
load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-fallback-key-change-in-production')

DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')

# 跨域设置
# COOP 响应头：Django 6 只认 same-origin / same-origin-allow-popups / unsafe-none 三个值，
# 写别的（以前这里写成 "None"）会让 manage.py check --deploy 报 security.E024 错误，
# 且实际响应头会变成非法的 Cross-Origin-Opener-Policy: None。
# unsafe-none = 不隔离 window.opener，与"允许跨域"的意图一致。
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'unsafe-none'

# HTTPS 反向代理支持（按需打开，见 .env 的 USE_X_FORWARDED_PROTO）。
# 不打开时，若站点跑在 Nginx/CDN 后面，request.scheme 会恒为 http，
# 导致 canonical / og:url / sitemap.xml / robots.txt 里全是 http:// 地址（伤 SEO）。
# 注意：只在**确实**有反向代理时打开 —— 直接暴露给公网时，任何人加一个
# X-Forwarded-Proto: https 头就能让 Django 以为请求是加密的。
if os.getenv('USE_X_FORWARDED_PROTO', '').lower() in ('true', '1', 'yes'):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# 跨域请求配置，允许所有源的跨域请求（由 .env 中 CORS_ORIGIN_ALLOW_ALL 控制）
CORS_ORIGIN_ALLOW_ALL = os.getenv('CORS_ORIGIN_ALLOW_ALL', 'False').lower() in ('true', '1', 'yes')

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders', # 跨域请求中间件
    'Web.apps.WebConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware', # 跨域请求中间件
    'django.middleware.common.CommonMiddleware',
    # 'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'XiaoYingMusic.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'Web.services.friend_links.friend_links', # 小影 API 友情链接（后端拉取+1小时缓存，渲染进HTML供搜索引擎可见）
                'Web.services.site_info.site_info', # 站点名称/联系方式/统计ID（取自 .env，二开改名只改 .env）
                'Web.services.hot_search.hot_keywords', # 热门搜索榜（header 搜索框下拉用，带 10 分钟缓存）
            ],
        },
    },
]

WSGI_APPLICATION = 'XiaoYingMusic.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases
#
# 曲库（Song / SearchKeyword）需要持久化，所以启用 SQLite。
# 单机、低并发写入下 SQLite 完全够用；一旦出现持续的写锁竞争，
# Web/services/db_alert.py 会发告警邮件提示换库（邮件内含换库步骤）。
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            # WAL（预写日志）：让读事务不再被写事务挡住，反之亦然。
            # 默认的 rollback journal 模式下，一次写会把整个库的读锁住，
            # 多个访客同时搜索时就容易冒 "database is locked"，
            # 进而误触发 db_alert 的换库告警邮件。
            'init_command': 'PRAGMA journal_mode=WAL;',
            # 拿不到写锁时的等待秒数：给足重试窗口，超时才报错。
            # 注意 Python sqlite3 的 timeout 单位是秒（不是毫秒）。
            'timeout': 20,
        },
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'zh-hans' # 中文简体

TIME_ZONE = 'Asia/Shanghai' # 上海时间

USE_I18N = True # 开启国际化

USE_TZ = True # 开启时区支持


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = '/static/'
# 静态源目录：全站静态资源都放这里（output.css / js / images）。
# 开发与生产**都直接从这个目录对外服务**，不走 collectstatic
# （见 XiaoYingMusic/urls.py 的两条分支、Web/services/site_info.py 取 output.css 版本号）。
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'Web', 'static')]
# collectstatic 的收集目标：刻意与源目录分开，否则"收集"就是把文件复制到自己身上。
# 本项目不依赖 collectstatic，保留它只是为了需要时能把静态文件集中交给 Nginx / CDN。
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')


# 默认主键字段类型配置
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# 媒体文件配置
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')


# ============ 站点品牌与联系方式（二开改名只需改 .env） ============
# 站点主名称：用于 logo、SEO 标题、结构化数据等全站展示
SITE_NAME = os.getenv('SITE_NAME', '杯子音乐网')
# 站点副名称：与主名称并列出现在 SEO 文案中（留空则模板只用主名称）
SITE_NAME_ALT = os.getenv('SITE_NAME_ALT', '爱听音乐网')
# SEO 描述里的品牌组合短语：优先取 .env 的 SITE_BRAND，未配置时自动按「副名（主名）」拼接
SITE_BRAND = os.getenv('SITE_BRAND') or (f'{SITE_NAME_ALT}（{SITE_NAME}）' if SITE_NAME_ALT else SITE_NAME)
# 页脚免责声明的联系邮箱
SITE_CONTACT_EMAIL = os.getenv('SITE_CONTACT_EMAIL', '3766849790#qq.com')
# 联系方式（README/文档引用）
SITE_CONTACT_WECHAT = os.getenv('SITE_CONTACT_WECHAT', 'duyanbz')
SITE_CONTACT_TG = os.getenv('SITE_CONTACT_TG', 'xiaoying1216')
# 51.la 第三方统计 ID：留空则不输出统计脚本（二开者请填自己的，避免数据混入原作者账号）
ANALYTICS_ID = os.getenv('ANALYTICS_ID', '3QisJxfuIZ0dJgUf')


# ============ 日期主题 / 节日祝福 ============
# 数据表已拆到独立文件，二开时改这里、不用动 settings：
#   Web/data/holidays.py            节日 → 主题 / 节日名，以及季节兜底主题
#   Web/data/holiday_greetings.json 节日 → 祝福语池（一个节日可写任意多条）
# 解析逻辑见 Web/services/holiday.py，注入模板见 Web/services/site_info.py。
# 预览某个节日的效果：/?bz_preview=02-14 或 /?bz_preview=halloween


# ============ 曲库：搜索结果本地化（减少回源站次数） ============
# 说明：搜索命中的歌曲会沉淀到本地库，同一个关键词不必反复爬 2t58；
#       库里不够翻页时再去源站补货，新数据顺带入库。
# 完整流程与设计取舍见 Web/services/music_library.py 的模块说明。
# 注意：这里没有"每页几条"的配置项 —— 展示页与源站页是一一对应的，
#       一页几条由源站决定（2t58 是 68 条/页），自己改这个数只会让页码错位。
# 搜索关键词的最大长度（字符）：注入模板给输入框当 maxlength，后端入库前也按它截断。
# 前后端共用这一个值，避免"输入框拦住了、手敲超长 URL 却能搜"的不一致。
SEARCH_KEYWORD_MAX_CHARS = int(os.getenv('SEARCH_KEYWORD_MAX_CHARS', '15'))
# 关键词保鲜时长（小时）：距上次抓取超过这个时间再被搜到，就同步重爬一次刷新数据，
# 否则库里会永远停在入库那天的内容，新歌再也进不来。
SEARCH_KEYWORD_TTL_HOURS = float(os.getenv('SEARCH_KEYWORD_TTL_HOURS', '12'))
# 被屏蔽关键词的保鲜时长（小时）：屏蔽不会自愈，反复重试只是白跑，所以拉长到 7 天再验一次。
SEARCH_BLOCKED_TTL_HOURS = float(os.getenv('SEARCH_BLOCKED_TTL_HOURS', '168'))
# 单次请求最多为翻页补爬几页源站：防止有人直接请求一个巨大页码，
# 把搜索页变成"按键就爬"的入口。分页器的页码上限也用它 —— 只给到"已爬 + 这个数"，
# 保证每个能点开的页码都有内容。
SEARCH_REFILL_MAX_PAGES = int(os.getenv('SEARCH_REFILL_MAX_PAGES', '3'))

# ============ 热门搜索榜（header 搜索框的下拉） ============
# 数据来源就是 SearchKeyword 自己的 search_count（见 Web/services/hot_search.py）。
# 榜单显示几个词
HOT_SEARCH_COUNT = int(os.getenv('HOT_SEARCH_COUNT', '7'))
# 只统计最近多少天内被搜过的词：不然老词会永久霸榜，新热词挤不上来。
HOT_SEARCH_DAYS = float(os.getenv('HOT_SEARCH_DAYS', '30'))
# 同一个人对同一个词在多少分钟内重复搜索只算一次（防刷）。0 表示不防刷。
HOT_SEARCH_DEDUP_MINUTES = float(os.getenv('HOT_SEARCH_DEDUP_MINUTES', '5'))
# 热门榜结果的缓存时长（分钟）：它在每个页面的 header 里都要用，
# 不能让每次浏览都查一次库。所以新词最多延迟这么久才出现在榜上。
HOT_SEARCH_CACHE_MINUTES = float(os.getenv('HOT_SEARCH_CACHE_MINUTES', '10'))
# 换库告警邮件的冷却时长（小时）：写锁冲突会被反复触发，没有冷却会变成邮件轰炸。
DB_ALERT_COOLDOWN_HOURS = float(os.getenv('DB_ALERT_COOLDOWN_HOURS', '24'))


# ============ 每日播放榜（首页「今日热听榜」） ============
# 数据来源就是 SongPlay 自己的 plays —— 播放页整首播完时才 +1
# （计数在 Web/services/play_rank.py 的 record 里做，取用在同文件的 today_top）。
# 榜单显示几首
PLAY_RANK_COUNT = int(os.getenv('PLAY_RANK_COUNT', '10'))
# 同一个人对同一首歌在多少分钟内重复听完只算一次（防刷）。0 表示不防刷。
# 注意：拦不住"换 IP 慢慢刷"，完整防护要校验实际播放时长，成本很高，
# 这里只做最小防护。详见 README。
PLAY_DEDUP_MINUTES = float(os.getenv('PLAY_DEDUP_MINUTES', '10'))


# ============ 首页「随机点唱机」 ============
# 从本地歌手曲目库（SingerSong）随机挑一批歌，数据全在本地，不经过爬虫。
# 显示几首
RANDOM_PICK_COUNT = int(os.getenv('RANDOM_PICK_COUNT', '18'))
# 同一批保持多久（小时）。复用 .env 里首页数据的缓存时长（CACHE_TTL_HOURS_HOME，
# 缺省回落到全局 CACHE_TTL_HOURS），跟站内其它"多久换一次"共用一个旋钮，
# 不新增配置项。换批由缓存过期后的第一个访客触发，不是整点。
RANDOM_PICK_TTL_HOURS = float(
    os.getenv('CACHE_TTL_HOURS_HOME') or os.getenv('CACHE_TTL_HOURS') or '2'
)


# ============ 缓存配置（爬虫数据缓存，减轻源站压力） ============
# 说明：爬虫抓取的页面数据缓存到本地文件（零依赖）。
# 缓存**时长**不在这里配：它由 SpiderServices/Music_2t58/main.py 直接读 .env 的
# CACHE_TTL_HOURS / CACHE_TTL_HOURS_<类型> / CACHE_TTL_PLAY_MINUTES（见 README 第八节）。
# 清缓存：python -c "import shutil; shutil.rmtree('cache')"
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': os.path.join(BASE_DIR, 'cache'),
        'OPTIONS': {
            # 缓存文件数上限。Django 默认只有 300，而 FileBasedCache 每写一个键都会检查
            # 这个上限，超了就随机删掉 1/3 —— 本站有成百上千个页面，用默认值会让缓存
            # 互相挤掉、命中率塌陷、回源次数暴涨（恰恰是最该避免的事），
            # 连换库告警的冷却键 bz_db_alert_sent 也会被随机淘汰掉。
            # 提到 2 万：足够放下几万个页面，又不至于让目录大到拖慢每次写入的目录扫描。
            'MAX_ENTRIES': 20000,
            # 满了以后每次淘汰 1/4（默认 3 = 淘汰 1/3），淘汰得温和一些。
            'CULL_FREQUENCY': 4,
        },
    },
}
