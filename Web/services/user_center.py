"""小影用户中心（UAC）客户端

把 UAC 的账号接口包成几个函数给视图层用。注册、登录、验证码、Token 校验全部由
小影负责，本地一个密码都不存（见 Web/models.py 的 UcUser）。

请求走 xiaoying_api 的 auth_params() 做项目签名 —— UAC 除少数开放接口外都要求签名，
缺签名直接返回 20011。

本模块统一把响应翻成 (ok, msg, data) 三元组：视图层不必到处判 code，直接把 msg 喂给
页面提示即可（平台的 msg 本身就是给用户看的中文，如「发送过于频繁，请 60 秒后再试」）。

实测记录（2026-09-27，本地平台跑通全链路）—— 下面几条与文档描述有出入，按实测来：
    · 注册是**两步**：register 只暂存注册意向 + 发验证码，verify/email|phone 才真正建号
    · **verify/* 建号成功但不发 Token**，所以注册完必须再调一次 login 才算登录
    · 验证码 **60 秒冷却，注册与登录共用**同一个冷却
    · 登录连续失败 5 次锁 15 分钟（返回 20040；密码错本身是 20011）
    · 注册验证邮件与登录验证码邮件的**主题完全一样**（都叫「用户，验证您的邮箱」），
      只能靠收件时间区分 —— 页面上提示用户以"最新一封"为准
"""
import logging

import requests

from .xiaoying_api import API_BASE, APP_ID, APP_SECRET, auth_params

logger = logging.getLogger(__name__)

# 请求超时（秒）。UAC 都是轻量查询，正常情况下几十毫秒
TIMEOUT = 15


def _request(path, params, method='POST'):
    """调用 UAC 接口，返回 (ok, msg, data)

    统一的错误口径：网络异常/解析失败算失败（msg 给用户看），平台返回非 10000 也算失败
    （msg 直接透传平台文案），只有 10000 才是成功。
    """
    if not (APP_ID and APP_SECRET):
        # 没配签名凭证时接口必然报 20011，提前说清楚，免得被误当成用户操作问题
        return False, '用户系统尚未配置，请联系管理员', {}

    # 丢掉空值：平台对多余的空字段不友好，而且签名本来就只算非空值（见 auth_params）
    params = {k: v for k, v in (params or {}).items() if v not in (None, '')}

    try:
        if method == 'GET':
            resp = requests.get(f'{API_BASE}{path}', params=auth_params(params),
                                timeout=TIMEOUT)
        else:
            resp = requests.post(f'{API_BASE}{path}', data=auth_params(params),
                                 timeout=TIMEOUT)
        payload = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning('小影用户中心请求失败 %s：%s', path, e)
        return False, '网络异常，请稍后再试', {}

    if payload.get('code') != 10000:
        msg = payload.get('msg') or '操作失败'
        logger.info('小影用户中心返回错误 %s：%s', path, msg)
        return False, msg, payload.get('data') or {}

    return True, payload.get('msg') or '', payload.get('data') or {}


def register(email='', phone='', password='', username=''):
    """注册第一步：暂存注册意向并发验证码（**不建号**）

    成功后必须再调 verify_register() 提交收到的验证码，才算注册完成。
    """
    return _request('/api/user_center/users/register',
                    {'email': email, 'phone': phone,
                     'password': password, 'username': username})


def resend_register_code(email='', phone=''):
    """重发注册验证码

    注意平台两条通道的接口名不对称：邮箱是 verify/email/**resend**，
    手机是 verify/phone/**send**（没有 verify/email/send 这个接口）。
    """
    if phone:
        return _request('/api/user_center/users/verify/phone/send', {'phone': phone})
    return _request('/api/user_center/users/verify/email/resend', {'email': email})


def verify_register(email='', phone='', code=''):
    """注册第二步：提交验证码，通过后建号

    ⚠️ **不发 Token**：返回的 data 里只有 user_id / account 等身份信息，
    注册完还得再调一次 login() 才算登录 —— 别指望它直接给出登录态。
    """
    if phone:
        return _request('/api/user_center/users/verify/phone',
                        {'phone': phone, 'code': code})
    return _request('/api/user_center/users/verify/email',
                    {'email': email, 'code': code})


def send_login_code(email='', phone=''):
    """发登录验证码（验证码登录第一步）。60 秒冷却，且与注册发码共用冷却"""
    return _request('/api/user_center/users/login/send',
                    {'email': email, 'phone': phone})


def login(email='', phone='', account='', password='', code=''):
    """登录：邮箱 / 手机 / 账号 + 密码，或 邮箱 / 手机 + 验证码

    成功时 data 含 token / user_id / account / username / expire_time（7 天后过期）。
    传了 code 就走验证码登录（免密码）。
    """
    return _request('/api/user_center/users/login',
                    {'email': email, 'phone': phone, 'account': account,
                     'password': password, 'code': code})


def verify_token(token):
    """校验 Token 是否仍然有效，返回 (ok, msg, data)

    只有平台返回 10000 且 data.valid 为 True 才算有效 —— 登出或过期的 Token 会返回
    20030，这里是 ok=False。
    """
    ok, msg, data = _request('/api/user_center/users/verify', {'token': token})
    return (ok and bool(data.get('valid'))), msg, data


def logout(token):
    """登出：作废这个 Token

    只影响"当前接入项目下"的这个 Token，用户在其他设备/项目上的登录不受影响。
    """
    return _request('/api/user_center/users/logout', {'token': token})
