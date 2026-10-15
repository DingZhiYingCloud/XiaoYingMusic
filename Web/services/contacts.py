# 小影 API 开发者联系方式服务
#
# 职责：后端统一拉取小影 API 的「接入项目开发者联系方式」，模块级缓存 1 小时，供全站模板渲染。
# 与 friend_links 同一套做法（服务端抓取 + 缓存 + 后台线程刷新 + 拉取失败保留旧值），
# 所以联系方式只需在小影后台维护一份，全站（页脚「联系我们」弹窗）自动跟随。
#
# 接口：GET {API_BASE}/api/feedback/contacts?app_id=<APPID>（**免签名**，app_id 即项目标识）
#   app_id 复用 XIAOYING_APP_ID（与「意见反馈」入口同一个项目，不能另配、更不能写死，
#   否则线上/本地会静默失效 —— 见 Web/services/feedback.py 的说明）。
# 返回每条联系方式的字段（实测来自平台接口文档）：
#   platform 平台代码 / name 平台名称 / icon 平台图标 / label 值标签 / value 联系方式值 /
#   url 可点击链接（空串表示纯文本展示）
import logging
import threading
import time

from Web.services import outbound
from Web.services.xiaoying_api import API_BASE, APP_ID, USER_AGENT

logger = logging.getLogger(__name__)

# 联系方式接口路径（免签名）
CONTACTS_PATH = '/api/feedback/contacts'
# 缓存有效期：1 小时（过期后在请求时由后台线程静默刷新，页面不阻塞）
CACHE_TTL = 60 * 60
# 请求超时（秒）：页脚每个页面都要用，不能拖慢渲染
REQUEST_TIMEOUT = 8

# 模块级缓存：contacts 为 [{platform,name,icon_url,fa_icon,label,value,url}]，fetched_at 为拉取时间戳
_cache = {'contacts': [], 'fetched_at': 0.0, 'fetching': False}
_lock = threading.Lock()


def _icon_url(icon):
    """平台侧 icon 是**图片地址**时才返回它（http(s):// 或 / 开头），是图标名则返回空串

    实测平台允许两种写法：图片地址，或图标名（如 `message-square`）。
    图标名我们这边没有对应的图标字体，交给 _fa_icon 按平台映射成 Font Awesome 类名。
    """
    if icon.startswith(('http://', 'https://', '/')):
        return icon
    return ''


# 平台代码/名称/标签 → Font Awesome 4 图标（本项目前端用的是 FA 4.7）。
# 平台是后台自定义的、没有固定枚举，所以按关键字匹配，匹配不到给个通用名片图标。
_FA_BY_HINT = (
    (('wechat', 'weixin', '微信'), 'fa fa-weixin'),
    (('telegram', '纸飞机'), 'fa fa-paper-plane-o'),
    (('qq',), 'fa fa-qq'),
    (('mail', 'email', '邮箱', '邮件'), 'fa fa-envelope-o'),
    (('phone', 'tel', '电话', '手机'), 'fa fa-phone'),
    (('weibo', '微博'), 'fa fa-weibo'),
    (('github',), 'fa fa-github'),
    (('site', 'link', 'url', '网站', '官网'), 'fa fa-link'),
)


def _fa_icon(contact):
    """按平台代码 / 名称 / 值标签挑一个 Font Awesome 图标"""
    blob = '%s %s %s' % (contact.get('platform', ''), contact.get('name', ''), contact.get('label', ''))
    low = blob.lower()
    for hints, cls in _FA_BY_HINT:
        if any(hint in low for hint in hints):
            return cls
    return 'fa fa-address-card-o'


def _fetch_contacts():
    """从小影 API 拉取开发者联系方式

    返回联系方式列表；**拉取失败返回 None**，用来区别于「接口正常、但一条都没配」的 []。
    调用方靠这个区分决定要不要覆盖缓存 —— 否则一次网络抖动就会把全站联系方式清空一小时。
    """
    if not APP_ID:
        logger.warning('未配置 XIAOYING_APP_ID，跳过联系方式拉取')
        return None
    try:
        resp = outbound.request(
            'get', f'{API_BASE}{CONTACTS_PATH}',
            params={'app_id': APP_ID},
            timeout=REQUEST_TIMEOUT,
            headers={'User-Agent': USER_AGENT},
        )
        payload = resp.json() or {}
        # 兼容错误结构（如 {"code":20030,"msg":"项目不存在或已停用"}）：data 可能为 null
        if payload.get('code') != 10000:
            logger.warning('小影 API 联系方式接口被拒: %s', payload.get('msg') or payload)
            return None
        items = (payload.get('data') or {}).get('contacts') or []
        contacts = []
        for item in items:
            value = (item.get('value') or '').strip()
            if not value:
                continue          # 没有值的那条没法展示，跳过
            url = (item.get('url') or '').strip()
            # 防属性注入：链接里出现引号/尖括号一律降级成纯文本（与 friend_links 同一套过滤）
            if any(ch in url for ch in '\'"<>'):
                url = ''
            row = {
                'platform': (item.get('platform') or '').strip(),
                # 展示名优先用 name，没有就退回 label（两者在后台是"平台名 / 值标签"）
                'name': (item.get('name') or '').strip() or (item.get('label') or '').strip(),
                # 平台侧 icon 有两种写法：图片地址或图标名（如 message-square），见 _icon_url / _fa_icon
                'icon_url': _icon_url((item.get('icon') or '').strip()),
                'label': (item.get('label') or '').strip(),
                'value': value,
                'url': url,
            }
            row['fa_icon'] = _fa_icon(row)
            contacts.append(row)
        return contacts
    except Exception:
        logger.exception('拉取小影 API 开发者联系方式失败')
        return None


def _refresh():
    """同步拉取并写入缓存（内部调用，线程安全）

    失败时保留上一次的联系方式，只把时间戳往后推：不推的话每个请求都会再试一次，
    接口挂掉时请求量会随访问量放大；把空结果写进缓存又会让全站联系方式凭空消失一小时。
    """
    with _lock:
        _cache['fetching'] = True
    try:
        contacts = _fetch_contacts()
        with _lock:
            if contacts is not None:
                _cache['contacts'] = contacts
            _cache['fetched_at'] = time.time()
    finally:
        with _lock:
            _cache['fetching'] = False


def _refresh_in_background():
    """后台线程静默刷新，请求不阻塞"""
    threading.Thread(target=_refresh, daemon=True).start()


def get_contacts():
    """获取开发者联系方式列表（惰性 + 后台线程刷新）

    首次访问：同步拉取一次，保证首次渲染即有数据；
    缓存过期：后台线程静默刷新，当前请求先用旧缓存，页面永不阻塞。
    """
    with _lock:
        fetched_at = _cache['fetched_at']
        fetching = _cache['fetching']
    if fetched_at == 0:
        _refresh()
    elif time.time() - fetched_at >= CACHE_TTL and not fetching:
        _refresh_in_background()
    with _lock:
        return list(_cache['contacts'])


def site_contacts(request):
    """Django context processor：向全站模板注入 site_contacts 与 site_contact_email"""
    contacts = get_contacts()
    return {
        'site_contacts': contacts,
        'site_contact_email': pick_email(contacts),
    }


# 判定"这条是邮箱"的特征词：平台代码 / 平台名称 / 值标签里出现这些就认。
# 平台（ContactPlatform）是后台自定义的、没有固定枚举，所以只能按特征挑。
_EMAIL_HINTS = ('mail', 'email', 'e-mail', '邮箱', '邮件')


def pick_email(contacts):
    """从联系方式里挑出"邮箱"那条的值（页脚「侵权处理」那句要用）

    先看平台代码 / 名称 / 值标签里有没有邮箱字样；都没有，再看哪条的值本身像个邮箱地址。
    挑不到就返回空串 —— 模板会退化成"请通过页脚「联系我们」告知我们"，
    而不是在句子里留一个空白。
    """
    for c in contacts:
        blob = '%s %s %s' % (c.get('platform', ''), c.get('name', ''), c.get('label', ''))
        if any(hint in blob.lower() for hint in _EMAIL_HINTS):
            return c.get('value', '')
    for c in contacts:
        if '@' in (c.get('value') or ''):
            return c['value']
    return ''
