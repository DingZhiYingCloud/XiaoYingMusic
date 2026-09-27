"""用户账号与收藏的视图

账号走小影用户中心（Web/services/user_center.py），本站只负责页面与会话
（Web/services/user_session.py）；收藏走本地库（Web/services/favorites.py）。

接口风格：项目此前没有任何 JSON 接口（唯一的 api/play/ended 是 204 无 body），这里是
第一批。取舍是 —— 需要"不刷新页面"完成的动作（发验证码、点心形）返回 JsonResponse；
登录、注册用普通表单 POST + 页面跳转，可靠、无 JS 也能走通。
"""
import logging
import re
import time

from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from Web.services import favorites, user_center, user_session
from Web.views.request import SID_PATTERN

logger = logging.getLogger(__name__)

# 注册第二步要用密码去换登录态（小影的 verify 只建号、**不发 Token**，见 user_center 的
# 说明），而第二步的表单里只有验证码。密码在**服务端 session 暂存**，不放页面隐藏字段 ——
# 放隐藏字段等于明文密码进 HTML。只留 REG_PWD_TTL 秒，超时就当作没有，免得它长期留在 session。
REG_PWD_KEY = 'uc_reg_pwd'
REG_PWD_TTL = 600

# 表单里那个"账号"输入框的形态判断：11 位数字当手机号，含 @ 当邮箱，其余当系统账号。
# 小影的系统账号是 12 位数字（如 880083678837），不会与 11 位手机号混淆。
PHONE_PATTERN = re.compile(r'^1\d{10}$')


def _safe_next(request):
    """取登录后要跳回的地址；只接受站内相对路径，防开放重定向

    不做这个校验的话，/login.html?next=https://坏站 会把用户带去第三方。
    """
    target = (request.POST.get('next') or request.GET.get('next') or '').strip()
    if target.startswith('/') and not target.startswith('//'):
        return target
    return ''


# ============ 登录 ============
def login_view(request):
    """登录页：GET 渲染表单；POST 处理登录（密码或验证码）"""
    if request.method != 'POST':
        return render(request, 'login.html',
                      {'mode': 'password', 'next': _safe_next(request)})

    mode = 'code' if request.POST.get('mode') == 'code' else 'password'
    ident = (request.POST.get('ident') or '').strip()
    password = request.POST.get('password') or ''
    code = (request.POST.get('code') or '').strip()

    params = _identity_params(ident)
    if not params:
        return _login_failed(request, mode, ident, '请填写邮箱、手机号或账号')
    if mode == 'code':
        params['code'] = code
    else:
        params['password'] = password

    ok, msg, data = user_center.login(**params)
    if ok and user_session.sign_in(request, data.get('token'), data):
        return redirect(_safe_next(request) or 'home')
    return _login_failed(request, mode, ident, msg or '登录失败，请稍后再试')


def _login_failed(request, mode, ident, error):
    """登录失败：带着原输入重新渲染，不跳页（免得用户重填一遍）"""
    return render(request, 'login.html',
                  {'mode': mode, 'ident': ident, 'error': error,
                   'next': _safe_next(request)})


def _identity_params(ident):
    """把"邮箱 / 手机号 / 账号"这一个输入框，拆成小影那边的参数名

    小影的 login 把三个标识做成三个独立字段（email / phone / account），而用户不该被迫
    先选类型，所以这里按形态判断。判断不出来就按"系统账号"发过去，由平台给出准确报错。
    """
    if not ident:
        return {}
    if PHONE_PATTERN.match(ident):
        return {'phone': ident}
    if '@' in ident:
        return {'email': ident}
    return {'account': ident}


def logout_view(request):
    """登出：清掉本站 session，同时让小影作废这个 Token

    用 GET：登出是幂等的，页面上就是个普通链接，不必为它单独做表单。
    """
    user_session.sign_out(request)
    return redirect('home')


# ============ 注册（两步）============
def register_view(request):
    """注册页：GET 渲染表单；POST 按 stage 分派到两步

    分两步是平台的规定：register 只暂存注册意向并发验证码，verify/email|phone 才真正建号。
    """
    if request.method != 'POST':
        return render(request, 'register.html', {'stage': 'send', 'channel': 'email'})

    if request.POST.get('stage') == 'verify':
        return _register_verify(request)
    return _register_send(request)


def _register_send(request):
    """注册第一步：提交邮箱/手机号 + 密码 + 昵称，由平台发验证码"""
    channel = 'phone' if request.POST.get('channel') == 'phone' else 'email'
    email = (request.POST.get('email') or '').strip()
    phone = (request.POST.get('phone') or '').strip()
    password = request.POST.get('password') or ''
    username = (request.POST.get('username') or '').strip()
    ident = phone if channel == 'phone' else email

    ctx = {'stage': 'send', 'channel': channel,
           'email': email, 'phone': phone, 'username': username}
    if not ident:
        ctx['error'] = '请填写手机号' if channel == 'phone' else '请填写邮箱'
        return render(request, 'register.html', ctx)

    ok, msg, _ = user_center.register(email=email, phone=phone,
                                      password=password, username=username)
    if not ok:
        ctx['error'] = msg
        return render(request, 'register.html', ctx)

    # 密码暂存到 session，等第二步验证码通过后用它换登录态（见文件头的说明）
    request.session[REG_PWD_KEY] = {'password': password, 'at': time.time()}
    return render(request, 'register.html',
                  {'stage': 'verify', 'channel': channel, 'ident': ident,
                   'tip': '验证码已发送，请查收后填在下面（邮件里也有同样的码和一条激活链接，'
                          '两者选一个即可；若之前收到过本站发来的验证信息，以**最新一封**为准）'})


def _register_verify(request):
    """注册第二步：提交验证码 —— 先建号，再用第一步暂存的密码换登录态"""
    channel = 'phone' if request.POST.get('channel') == 'phone' else 'email'
    email = (request.POST.get('email') or '').strip()
    phone = (request.POST.get('phone') or '').strip()
    code = (request.POST.get('code') or '').strip()
    ident = phone if channel == 'phone' else email

    ctx = {'stage': 'verify', 'channel': channel, 'ident': ident}
    if not code:
        ctx['error'] = '请填写收到的验证码'
        return render(request, 'register.html', ctx)

    ok, msg, _ = user_center.verify_register(email=email, phone=phone, code=code)
    if not ok:
        ctx['error'] = msg
        return render(request, 'register.html', ctx)

    # 账号建好了，但 verify 不发 Token —— 必须再用刚才的密码登录一次
    password = _take_reg_password(request)
    if not password:
        return render(request, 'login.html',
                      {'mode': 'password', 'ident': ident,
                       'error': '注册成功！请用刚设置的密码登录'})

    params = {'phone': phone} if channel == 'phone' else {'email': email}
    ok, msg, data = user_center.login(password=password, **params)
    if ok and user_session.sign_in(request, data.get('token'), data):
        return redirect('home')
    return render(request, 'login.html',
                  {'mode': 'password', 'ident': ident,
                   'error': '注册成功，但自动登录没成功，请手动登录一次'})


def _take_reg_password(request):
    """取出第一步暂存的密码；超过 REG_PWD_TTL 秒（或压根没存过）返回空串"""
    saved = request.session.pop(REG_PWD_KEY, None) or {}
    if not isinstance(saved, dict):
        return ''
    if time.time() - float(saved.get('at') or 0) > REG_PWD_TTL:
        return ''
    return saved.get('password') or ''


@require_POST
def send_code(request):
    """发验证码（AJAX）：注册重发 / 登录验证码

    参数 kind=register|login，账号用 email 或 phone 传。
    60 秒冷却由平台控制（注册与登录**共用**同一个冷却），被拒时把平台文案原样交给页面。
    """
    kind = request.POST.get('kind') or ''
    email = (request.POST.get('email') or '').strip()
    phone = (request.POST.get('phone') or '').strip()
    if not (email or phone):
        return JsonResponse({'ok': False, 'msg': '请先填写邮箱或手机号'})

    if kind == 'register':
        ok, msg, _ = user_center.resend_register_code(email=email, phone=phone)
    else:
        ok, msg, _ = user_center.send_login_code(email=email, phone=phone)
    return JsonResponse({'ok': ok, 'msg': msg or ('验证码已发送' if ok else '发送失败')})


# ============ 喜欢 ============
def my_likes(request):
    """「我的喜欢」页：列出喜欢的歌（最近喜欢的在前）

    未登录直接送去登录页，带上 next 以便登录后自动跳回来。
    """
    user = user_session.current_user(request)
    if user is None:
        return redirect('/login.html?next=/my/likes.html')
    return render(request, 'my_likes.html', {'likes': user.favorites.all()})


@require_POST
def toggle_favorite(request):
    """切换喜欢状态（AJAX）

    未登录不报错，而是回 need_login=True 让前端引导去登录 —— 这是"未登录点心形"
    的既定交互（登录后带回原页面）。
    """
    user = user_session.current_user(request)
    if user is None:
        return JsonResponse({'ok': False, 'need_login': True, 'msg': '登录后才能收藏'})

    sid = (request.POST.get('sid') or '').strip()
    if not SID_PATTERN.match(sid):
        return JsonResponse({'ok': False, 'msg': '歌曲参数不合法'})

    liked = favorites.toggle(user, sid, request.POST.get('title', ''))
    return JsonResponse({'ok': True, 'liked': liked})
