"""登录态：Django session ↔ 本地用户（UcUser）

小影用户中心只负责认证并签发 Token，本站的登录态靠 Django session 维持。session 里
只放两样东西：

    uc_token  小影签发的 Token（登出时还给小影作废）
    uc_uid    本地 UcUser 的主键

为什么不直接存小影的 user_id（UUID）：本地主键是整型，模板与查询都省事，而且本地
用户记录一旦被删，这里的查询会自然查不到，能立刻反映出来。

为什么不每个请求都调 verify 校验 Token：
    那样每个页面请求都要多一次外部 HTTP（还得自己加缓存与降级），收益很小。Token 有效期
    7 天，这里只在登录时校验一次、之后信任 session，并让 session 的过期时间与之一致
    （见 sign_in 的 set_expiry）。被小影提前作废（改密码、在别处登出）时，最坏情况是用户
    在本站多待一会儿，下次重新登录立刻就会被拒。

    ⚠️ 所以本站的"已登录"与"Token 仍有效"可能短暂不一致。喜欢功能只依赖本地用户主键，
    不受影响；将来若要调小影的接口，记得处理 20030（Token 失效）。
"""
import logging

from Web.models import UcUser
from Web.services import user_center

logger = logging.getLogger(__name__)

SESSION_TOKEN = 'uc_token'
SESSION_UID = 'uc_uid'

# 登录态时长（秒），与小影 Token 的 7 天有效期对齐
SESSION_TTL = 7 * 24 * 3600


def current_user(request):
    """取当前登录用户；未登录（或本地记录已不存在）返回 None"""
    uid = request.session.get(SESSION_UID)
    if not uid:
        return None
    user = UcUser.objects.filter(pk=uid).first()
    if user is None:
        # 本地用户记录被清掉了（比如后台手工删了）→ 当作未登录，顺手把 session 也清掉
        sign_out(request, call_api=False)
    return user


def sign_in(request, token, data):
    """登录成功后写入 session，并按小影返回的身份 upsert 本地用户

    返回 UcUser；若返回里缺 token / user_id（接口异常），返回 None 由调用方提示失败。
    """
    uc_user_id = (data or {}).get('user_id') or ''
    if not (token and uc_user_id):
        logger.warning('登录返回缺 token 或 user_id，无法建立登录态：%s', data)
        return None

    user, _ = UcUser.objects.update_or_create(
        uc_user_id=uc_user_id,
        defaults={
            'account': data.get('account') or '',
            'username': data.get('username') or '',
            'email': data.get('email') or '',
            'phone': data.get('phone') or '',
        })

    request.session[SESSION_TOKEN] = token
    request.session[SESSION_UID] = user.pk
    request.session.set_expiry(SESSION_TTL)
    return user


def sign_out(request, call_api=True):
    """登出：清掉 session；call_api=True 时同时让小影作废这个 Token

    作废失败（网络问题、Token 已过期）不改判结果 —— 本地登录态已经清掉了，用户的
    目的达到了，没必要为此报错。
    """
    token = request.session.get(SESSION_TOKEN)
    if token and call_api:
        user_center.logout(token)
    for key in (SESSION_TOKEN, SESSION_UID):
        request.session.pop(key, None)


def current_user_context(request):
    """context processor：把当前登录用户注入所有模板

    header 要靠它决定显示"登录"还是昵称菜单，所以得全站可用 —— 让每个视图都自己传一遍
    不现实。名字用大写 CURRENT_USER，与项目里其它 context processor 的风格一致
    （SITE_NAME / HOT_KEYWORDS）。

    代价是每个页面请求多一次按主键的查询；未登录时连这次查询都没有。
    """
    return {'CURRENT_USER': current_user(request)}
