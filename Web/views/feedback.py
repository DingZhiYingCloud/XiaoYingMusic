"""问题反馈入口

只负责一次跳转：把小影托管的反馈页地址交给浏览器。登录态由 Web/services/feedback.py
在服务端换成一次性票据后拼进地址（Token 不落浏览器），游客则直接进页面匿名提交。
"""
from django.shortcuts import redirect

from Web.services import feedback


def entry(request):
    """「意见反馈」入口：302 跳到小影反馈页（登录用户自动带票）"""
    return redirect(feedback.entry_url(request))
