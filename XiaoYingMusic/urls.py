# 项目URL配置
from django.conf.urls.static import static
from django.conf import settings
from django.urls import path, include, re_path
from django.contrib import admin
from django.views.generic import RedirectView, TemplateView
from django.views.static import serve
from Web.views import request as bz_request

urlpatterns = [
    # path('admin/', admin.site.urls), # 管理员站点
    path('', include('Web.views.urls')), # 前端路由
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain')),
    path('sitemap.xml', bz_request.sitemap, name='sitemap'),
    # 浏览器与部分爬虫会默认探测根路径 /favicon.ico，直接返回实际图标，避免 404
    path('favicon.ico', serve, {'document_root': settings.MEDIA_ROOT, 'path': 'favicon.ico'}),
]

# 自定义错误页处理（DEBUG=False 时生效）
handler404 = 'Web.views.request.error_404'
handler500 = 'Web.views.request.error_500'

# 静态文件 & 媒体文件服务
# DEBUG=True 时 Django 的 staticfiles 会通过 finders 服务 /static/，这里再直连兜一份；
# DEBUG=False 时 static() 返回空列表，必须手动挂路由。
# 两种模式都指向同一个静态源目录（settings.STATICFILES_DIRS[0]，即 Web/static）——
# 本项目静态文件就编译/存放在那里，不经过 collectstatic。
STATIC_SOURCE = settings.STATICFILES_DIRS[0]
if not settings.DEBUG:
    urlpatterns += [
        re_path(r'^static/(?P<path>.*)$', serve, {'document_root': STATIC_SOURCE}),
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]
else:
    urlpatterns += static(settings.STATIC_URL, document_root=STATIC_SOURCE)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

