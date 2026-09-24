# 小影 API 友情链接服务
#
# 职责：后端统一拉取小影 API 的友情链接，模块级缓存 1 小时，供全站模板渲染。
# 关键约束：链接必须在服务端抓取并渲染进 HTML（搜索引擎直接可见），
#          绝不通过前端 JS 请求 API（搜索引擎爬虫执行不到 JS，会漏掉链接）。
import logging
import threading
import time

import requests

from Web.services.xiaoying_api import API_BASE, USER_AGENT, auth_params

logger = logging.getLogger(__name__)

# 友情链接接口路径（status=true 只返回启用状态的链接）
FRIEND_LINKS_PATH = '/api/seo/friend_links'
# 缓存有效期：1 小时（过期后在请求时后台线程静默刷新）
CACHE_TTL = 60 * 60
# 请求超时（秒）
REQUEST_TIMEOUT = 10

# 模块级缓存：links 为 [{name, url}]，fetched_at 为拉取时间戳，fetching 标记后台刷新进行中
_cache = {'links': [], 'fetched_at': 0.0, 'fetching': False}
_lock = threading.Lock()


def _fetch_links():
    """从小影 API 拉取并过滤友情链接

    返回链接列表；**拉取失败返回 None**，用来区别于"接口正常、但一条链接都没有"的 []。
    调用方靠这个区分决定要不要覆盖缓存 —— 否则一次网络抖动就会把全站友情链接清空一小时。
    """
    try:
        resp = requests.get(
            f'{API_BASE}{FRIEND_LINKS_PATH}',
            params=auth_params({'status': 'true'}),
            timeout=REQUEST_TIMEOUT,
            headers={'User-Agent': USER_AGENT},
        )
        resp.raise_for_status()
        # 兼容接口返回错误结构（如 {"code":20011,"msg":"签名参数缺失...","data":null}）：
        # data 可能为 null，必须安全提取，否则 .get() 会抛 NoneType 异常
        payload = resp.json() or {}
        items = (payload.get('data') or {}).get('items') or []
        if not items:
            logger.warning('小影 API 未返回友情链接数据: %s', payload.get('msg') or payload)
        links = []
        for item in items:
            name = (item.get('name') or '').strip()
            url = (item.get('url') or '').strip()
            # 过滤规则：名称非空、状态启用、http(s) 链接、url 不含属性注入字符
            if not name or not url:
                continue
            if item.get('status') is False:
                continue
            if not (url.startswith('http://') or url.startswith('https://')):
                continue
            if any(ch in url for ch in '\'"<>'):
                continue
            links.append({'name': name, 'url': url})
        return links
    except Exception:
        logger.exception('拉取小影 API 友情链接失败')
        return None


def _refresh():
    """同步拉取并写入缓存（内部调用，线程安全）

    失败时保留上一次的链接，只把时间戳往后推：不推的话每个请求都会再试一次，
    接口挂掉时请求量会随访问量放大；而把空结果写进缓存又会让全站链接凭空消失一小时。
    """
    with _lock:
        _cache['fetching'] = True
    try:
        links = _fetch_links()
        with _lock:
            if links is not None:
                _cache['links'] = links
            _cache['fetched_at'] = time.time()
    finally:
        with _lock:
            _cache['fetching'] = False


def _refresh_in_background():
    """后台线程静默刷新，请求不阻塞"""
    threading.Thread(target=_refresh, daemon=True).start()


def get_friend_links():
    """获取友情链接列表（惰性 + 后台线程刷新）

    首次访问：同步拉取一次，保证首次渲染即有链接；
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
        return list(_cache['links'])


def friend_links(request):
    """Django context processor：向全站模板注入 friend_links 变量"""
    return {'friend_links': get_friend_links()}
