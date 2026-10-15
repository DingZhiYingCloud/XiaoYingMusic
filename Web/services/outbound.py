"""统一出站代理

作用：让**非爬虫**的出站请求（小影 API、用户中心、反馈换票、图床、歌手封面下载等）
也走爬虫用的那一条代理 —— 见 .env 的 MUSIC_2T58_FORCE_PROXY 与
SpiderServices/Music_2t58/main.py 里 FORCE_PROXY 的说明。

两个**必须直连**的例外（都写死在这里，别改）：
  ① 取代理节点本身（`/api/ProxyIp/...`）：节点就是从它拿的，挂上代理会死循环
     —— 那条请求在爬虫里是单独直连的，不经过本模块；
  ② 环回 / 内网地址（如本地开发的小影实例 http://127.0.0.1:10000，或局域网服务）：
     代理当然访问不到，挂上只会全部失败。
所以这里按 URL 判断，命中例外一律直连（与从前完全一致）。

对外就一个入口：
    resp = outbound.request('post', url, data=..., timeout=10)
"""
import ipaddress
import logging
import os
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


def enabled():
    """是否启用"出站走代理"

    以爬虫的 FORCE_PROXY 为准（单一来源）；脱离 Django/依赖缺失时退回直接读 .env。
    """
    try:
        from SpiderServices.Music_2t58.main import Music2t58Spider
        return bool(Music2t58Spider.FORCE_PROXY)
    except Exception:
        return os.getenv('MUSIC_2T58_FORCE_PROXY', 'true').lower() in ('true', '1', 'yes')


def _is_local(url):
    """环回 / 内网地址（这类地址不能走代理）"""
    host = (urlparse(url).hostname or '').strip()
    if not host:
        return True
    if host == 'localhost' or host.endswith('.local'):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False            # 是域名 → 交给代理
    return ip.is_loopback or ip.is_private or ip.is_link_local


def current_proxy():
    """当前该用的代理节点（爬虫侧维护，每进程粘一条）；取不到返回 None"""
    try:
        from SpiderServices.Music_2t58.main import Music2t58Spider
        return Music2t58Spider.current_proxy()
    except Exception:            # 脱离 Django / 缺依赖：静默降级成直连
        return None


def _invalidate():
    """把当前这条节点标记不可用（让它下次换一条）"""
    try:
        from SpiderServices.Music_2t58.main import Music2t58Spider
        Music2t58Spider.invalidate_proxy()
    except Exception:
        pass


def proxies_for(url):
    """该 URL 要不要挂代理：要就返回 requests 的 proxies dict，否则 None"""
    if not enabled() or _is_local(url):
        return None
    return current_proxy()


def request(method, url, **kwargs):
    """统一的出站请求（带代理 + 失败回落直连）

    流程：启用且非内网 → 挂当前节点请求一次；**这条路要是失败（网络层/HTTP 错误）**，
    就把这条节点作废、**去掉代理再请求一次**（回落直连兜底，与爬虫侧同一口径）。
    两次都失败时抛最后一次的异常，语义与直接调 requests 一致。

    用法：outbound.request('post', url, data=..., timeout=10)
    """
    proxies = proxies_for(url)
    if not proxies:
        resp = requests.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp
    try:
        resp = requests.request(method, url, proxies=proxies, **kwargs)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        logger.warning('经代理由 %s 出站失败，回落直连重试：%s', url, exc)
        _invalidate()
        resp = requests.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp

