"""问题反馈中心接入（小影统一反馈系统）

反馈页托管在小影 API 侧（`/feedback/<APPID>/`），本站**零代码接入**：只做一件事 ——
把用户带到反馈页，已登录用户由**服务端**用其 UAC Token 换一张一次性票据再跳转
（Token 不出服务端），游客直接进页面匿名提交。
票据 5 分钟过期、用完即废，因此即使出现在地址栏/历史里也无法重放。

托管页自带「提交反馈 / 公开区 / 我的反馈」三个标签页，反馈表单、附件上传、AI 审核、
开发者联系方式都由它负责，本站不保存任何反馈数据。

换票失败（网络抖动、Token 已失效等）一律**降级为游客进入**，不让用户卡在入口上；
反馈页本身也支持游客提交，只是提交后不带用户身份、无法在「我的反馈」里追踪。
"""
import logging

import requests

from Web.services import user_session, xiaoying_api

logger = logging.getLogger(__name__)

# 本项目在小影「接入项目」里的 APPID（决定反馈数据归属哪个项目）
# 直接复用 API 客户端的 APP_ID（即 .env 的 XIAOYING_APP_ID），**不要另配一个、更不要写死**：
# 线上与本地开发指的不是同一个实例、APPID 也不同（本地是自建小影实例），
# 写死任何一个都会在另一边静默失效 —— 实测写错时反馈页 404、contacts 接口返回
# 20030「项目不存在或已停用」，两者都不报错，很难发现。
FEEDBACK_APP_ID = xiaoying_api.APP_ID

# 反馈页地址前缀（与 xiaoying_api.API_BASE 同源：切线上 / 本地只改一处）
FEEDBACK_BASE = xiaoying_api.API_BASE

# 换票请求超时（秒）：入口跳转不该让用户等太久，失败就按游客走
TICKET_TIMEOUT = 4


def feedback_url(ticket=''):
    """反馈页地址；带票据时拼上 ?ticket=xxx"""
    url = f'{FEEDBACK_BASE}/feedback/{FEEDBACK_APP_ID}/'
    return f'{url}?ticket={ticket}' if ticket else url


def issue_ticket(token):
    """用用户 UAC Token 换一张一次性票据；失败返回空串

    该端点免签名（凭证就是用户自己的 Token），所以这里不做签名、也不需要 AppSecret。
    """
    if not token:
        return ''
    try:
        resp = requests.post(
            f'{FEEDBACK_BASE}/api/feedback/ticket',
            data={'token': token},
            timeout=TICKET_TIMEOUT,
            headers={'User-Agent': xiaoying_api.USER_AGENT},
        )
        payload = resp.json() or {}
    except Exception as exc:                      # noqa: BLE001 - 入口尽量不报错，降级即可
        logger.warning('换取反馈页票据失败（按游客进入）：%s', exc)
        return ''

    if payload.get('code') != 10000:
        logger.info('换取反馈页票据被拒（按游客进入）：%s', payload.get('msg'))
        return ''
    return (payload.get('data') or {}).get('ticket') or ''


def entry_url(request):
    """「意见反馈」入口地址：登录用户带上票据，游客直接进"""
    token = request.session.get(user_session.SESSION_TOKEN)
    return feedback_url(issue_ticket(token))
