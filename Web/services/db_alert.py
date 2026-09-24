"""换库告警邮件

触发条件：SQLite 写锁冲突（database is locked）。持续出现说明单机 SQLite 已经撑不住并发写入，
该换 MySQL / PostgreSQL 了。

为什么要发邮件：
    这个信号不会主动报错给站长，只在日志里一闪而过。邮件把现场信息（体量、路径、IP、
    换库步骤）直接送到 SITE_CONTACT_EMAIL，不用再去翻日志。

防轰炸：
    锁冲突会在短时间内反复触发，所以用 Django 缓存做冷却（DB_ALERT_COOLDOWN_HOURS），
    默认 24 小时最多一封。

调用方式见 Web/services/music_library.py：写库处捕获 OperationalError 后调 alert_switch_db()。
邮件本身通过小影 API 的 /api/email/v1/send 发送（见 xiaoying_api.post_form）。
"""
import logging
import os
import socket
import sys
from collections import OrderedDict

import django
from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError
from django.utils import timezone

from Web.services import xiaoying_api

logger = logging.getLogger(__name__)

# 小影 API 的发信接口（表单字段：subject / body / recipients）
EMAIL_SEND_PATH = '/api/email/v1/send'
# 冷却键：cache.add 在键已存在时返回 False，正好用来做"期内只发一封"
_COOLDOWN_KEY = 'bz_db_alert_sent'


def is_lock_error(exc):
    """是否为 SQLite 写锁冲突 —— 只有这类错误才是"该换库"的信号"""
    return isinstance(exc, OperationalError) and 'locked' in str(exc).lower()


def _recipient():
    """收件邮箱

    SITE_CONTACT_EMAIL 在 .env 与页面里写成 `3766849790#qq.com`（防爬虫的常见写法），
    发信前必须把 # 换回 @，否则不是合法地址。
    """
    return (settings.SITE_CONTACT_EMAIL or '').replace('#', '@').strip()


def _local_ips():
    """取本机 IP（可能多个网卡），取不到时返回空列表 —— 只用于邮件里定位是哪台机器"""
    try:
        return sorted({ip for ip in socket.gethostbyname_ex(socket.gethostname())[2]})
    except Exception:
        return []


def _human_size(size):
    """字节数转成好读的单位"""
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.1f} {unit}'
        value /= 1024


def _call(func, default='取不到'):
    """安全取值：数据库正被锁住时计数查询本身也会失败，告警不该因此发不出去"""
    try:
        return func()
    except Exception as exc:
        return f'{default}（{exc}）'


def _collect_report(reason, exc):
    """收集现场信息：体量、路径、IP、版本号"""
    db_conf = settings.DATABASES.get('default') or {}
    db_name = db_conf.get('NAME', '')
    report = OrderedDict()
    report['告警原因'] = reason
    report['错误详情'] = f'{type(exc).__name__}: {exc}' if exc else '（无异常对象）'
    report['发生时间'] = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S %Z')
    report['服务器主机名'] = _call(socket.gethostname, '未知')
    report['服务器 IP'] = '、'.join(_local_ips()) or '未取到'
    report['项目根目录'] = str(settings.BASE_DIR)
    report['数据库引擎'] = db_conf.get('ENGINE', '未知')
    report['数据库文件'] = str(db_name)
    report['数据库文件大小'] = _call(lambda: _human_size(os.path.getsize(db_name)), '没找到文件')
    # 体量：这几个数字决定要不要换库、以及换库时搬多少数据
    report['歌曲表 Song 条数'] = _call(lambda: _song_count(), '查询失败')
    report['关键词表 SearchKeyword 条数'] = _call(lambda: _keyword_count(), '查询失败')
    report['搜索结果表 SearchResult 条数'] = _call(lambda: _result_count(), '查询失败')
    report['播放次数表 SongPlay 条数'] = _call(lambda: _play_count(), '查询失败')
    report['DEBUG'] = settings.DEBUG
    report['Python 版本'] = sys.version.split()[0]
    report['Django 版本'] = django.get_version()
    return report


def _song_count():
    from Web.models import Song
    return Song.objects.count()


def _keyword_count():
    from Web.models import SearchKeyword
    return SearchKeyword.objects.count()


def _result_count():
    from Web.models import SearchResult
    return SearchResult.objects.count()


def _play_count():
    from Web.models import SongPlay
    return SongPlay.objects.count()


def _build_html(report):
    """拼 HTML 邮件正文（邮件客户端不认外部样式，全部写内联样式）"""
    rows = ''.join(
        f'<tr>'
        f'<td style="padding:8px 12px;border:1px solid #e5e7eb;background:#f9fafb;'
        f'white-space:nowrap;font-weight:600;vertical-align:top">{key}</td>'
        f'<td style="padding:8px 12px;border:1px solid #e5e7eb;'
        f'word-break:break-all">{value}</td>'
        f'</tr>'
        for key, value in report.items()
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<body style="margin:0;padding:24px;background:#f3f4f6;
             font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;color:#111827">
  <div style="max-width:720px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;
              border:1px solid #e5e7eb">
    <div style="padding:18px 22px;background:#dc2626;color:#fff">
      <h1 style="margin:0;font-size:18px">曲库需要换数据库了</h1>
      <p style="margin:6px 0 0;font-size:13px;opacity:.9">
        {settings.SITE_NAME} 的曲库出现 SQLite 写锁冲突，单机 SQLite 已接近上限
      </p>
    </div>

    <div style="padding:22px">
      <h2 style="margin:0 0 10px;font-size:15px">现场信息</h2>
      <table style="width:100%;border-collapse:collapse;font-size:13px">{rows}</table>

      <h2 style="margin:26px 0 10px;font-size:15px">换库步骤</h2>
      <ol style="margin:0;padding-left:20px;font-size:13px;line-height:1.9">
        <li>先停服务，避免搬迁过程中还有写入：<code>Ctrl+C</code> 停掉 runserver / uwsgi。</li>
        <li>导出曲库数据（三个模型，量不大）：<br>
            <code>python manage.py dumpdata Web --indent 2 -o library.json</code></li>
        <li>装数据库驱动：MySQL 用 <code>pip install mysqlclient</code>；
            PostgreSQL 用 <code>pip install psycopg[binary]</code>。</li>
        <li>建好空库，然后改 <code>XiaoYingMusic/settings.py</code> 的 <code>DATABASES</code>，例如：
          <pre style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;
                      padding:10px;font-size:12px;overflow:auto">DATABASES = {{
    'default': {{
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'library',
        'USER': '...', 'PASSWORD': '...',
        'HOST': '127.0.0.1', 'PORT': '3306',
    }}
}}</pre>
        </li>
        <li>在新库建表并回灌数据：<br>
            <code>python manage.py migrate</code><br>
            <code>python manage.py loaddata library.json</code></li>
        <li>重启服务，再到搜索页确认能搜到结果、且日志里不再出现 database is locked。</li>
        <li>确认无误后再删掉 <code>db.sqlite3</code> 与 <code>library.json</code>（都含数据）。</li>
      </ol>

      <p style="margin:22px 0 0;padding:12px 14px;background:#fffbeb;border:1px solid #fde68a;
                border-radius:6px;font-size:13px;line-height:1.7">
        <strong>提醒：</strong>这份邮件有冷却时间（默认 24 小时最多一封），
        没换库之前会一直重复提醒。冷却时长可在 .env 里用
        <code>DB_ALERT_COOLDOWN_HOURS</code> 调整。
      </p>
    </div>

    <div style="padding:14px 22px;background:#f9fafb;border-top:1px solid #e5e7eb;
                font-size:12px;color:#6b7280">
      本邮件由 {settings.SITE_NAME} 曲库自动发出，无需回复。
    </div>
  </div>
</body>
</html>"""


def _take_cooldown():
    """冷却判断：cache.add 只在键不存在时写入并返回 True，正好实现"期内只发一封" """
    seconds = max(60, int(settings.DB_ALERT_COOLDOWN_HOURS * 3600))
    return cache.add(_COOLDOWN_KEY, 1, seconds)


def alert_switch_db(reason, exc=None):
    """发送换库提醒邮件

    返回是否真的发出去了。任何异常都在内部消化 —— 告警失败不能连累业务。
    """
    recipient = _recipient()
    if not recipient:
        logger.warning('未配置 SITE_CONTACT_EMAIL，换库告警无法发送：%s', reason)
        return False

    if not _take_cooldown():
        logger.info('换库告警处于冷却期，本次跳过：%s', reason)
        return False

    try:
        report = _collect_report(reason, exc)
        payload = xiaoying_api.post_form(
            EMAIL_SEND_PATH,
            {
                'subject': f'【{settings.SITE_NAME}】曲库需要换数据库了 —— {reason}',
                'body': _build_html(report),
                'recipients': recipient,
            },
            timeout=15,
        )
        code = payload.get('code')
        if code == 10000:
            logger.warning('换库告警邮件已发送至 %s：%s', recipient, reason)
            return True
        # 接口返回非成功码时不写冷却，下次还能重试
        cache.delete(_COOLDOWN_KEY)
        logger.error('换库告警邮件发送失败：%s', payload.get('msg') or payload)
        return False
    except Exception:
        cache.delete(_COOLDOWN_KEY)
        logger.exception('换库告警邮件发送出错')
        return False
