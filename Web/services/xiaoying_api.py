"""小影 API 通用客户端

职责：统一的签名与请求封装，供友情链接、告警邮件等需要调用小影 API 的模块复用。

签名算法与服务端 API/apis/user_center/sign.py 的 build_sign 保持一致：
    1. 取除 sign 外的所有非空参数（含 app_id / timestamp / nonce 与业务参数）；
    2. 按键名 ASCII 升序排序，拼接为 key=value&key=value...；
    3. 以 app_secret 为密钥做 HMAC-SHA256，输出小写 hex 即为 sign。

注意：数组参数必须自己拼成单值（如收件人用逗号分隔）再传进来。
      requests 对列表值会拆成重复字段，而签名是按单个值算的，两边对不上。
"""
import hashlib
import hmac
import os
import secrets
import time

import requests

# 小影 API 基础地址（.env 覆盖；私有部署时指向自己的实例）
API_BASE = os.getenv('XIAOYING_API_BASE', 'https://xiaoyingapi.com')
# 签名凭证（未配置时不带签名，接口会返回 20011 签名参数缺失）
APP_ID = os.getenv('XIAOYING_APP_ID', '')
APP_SECRET = os.getenv('XIAOYING_APP_SECRET', '')

# 统一 UA：部分接口对 requests 的默认 UA 不友好
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)


def sign_params(params):
    """生成签名（HMAC-SHA256，小写 hex）"""
    items = sorted((k, str(v)) for k, v in params.items()
                   if k != 'sign' and v not in (None, ''))
    raw = '&'.join(f'{k}={v}' for k, v in items)
    return hmac.new(APP_SECRET.encode('utf-8'), raw.encode('utf-8'), hashlib.sha256).hexdigest()


def auth_params(business_params):
    """为业务参数补上签名公共参数（app_id / timestamp / nonce / sign）

    未配置凭证时原样返回业务参数（接口将返回 20011，由调用方降级处理）。
    """
    params = dict(business_params)
    if not (APP_ID and APP_SECRET):
        return params
    params['app_id'] = APP_ID
    params['timestamp'] = str(int(time.time()))   # 10 位秒级时间戳，服务端校验窗口 ±5 分钟
    params['nonce'] = secrets.token_hex(8)        # 每次请求唯一，服务端窗口内防重放
    params['sign'] = sign_params(params)
    return params


def post_form(path, data, timeout=10):
    """带签名的表单 POST，返回解析后的 JSON（是否成功由调用方判 code）"""
    resp = requests.post(
        f'{API_BASE}{path}',
        data=auth_params(data),
        timeout=timeout,
        headers={'User-Agent': USER_AGENT},
    )
    resp.raise_for_status()
    return resp.json() or {}


def post_multipart(path, files, data=None, timeout=30):
    """带签名的 multipart 上传（如小影图床），返回解析后的 JSON

    签名公共参数走 **query string**，文件走请求体，两者不能混：
    服务端算签名时取的是「非文件表单字段 + query 参数」（见小影 API 的认证中间件），
    而 requests 放进 files 的文件只进 multipart body、不会成为表单字段。
    所以只有"签名参数只放 query、不额外塞表单字段"时，两边算出的参数集才一致；
    多塞一个表单字段（比如 permission），服务端就会把它也算进签名 → 直接 20011。
    """
    resp = requests.post(
        f'{API_BASE}{path}',
        params=auth_params(data or {}),
        files=files,
        timeout=timeout,
        headers={'User-Agent': USER_AGENT},
    )
    resp.raise_for_status()
    return resp.json() or {}
