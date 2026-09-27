# 项目URL配置
from django.urls import path, re_path

from Web.views import request, users

urlpatterns = [
    path('', request.index, name='home'),
    path('index/', request.index, name='index'),
    path('singer/<sid>.html', request.singer, name='singer'),
    path('singer/<sid>/<int:page>.html', request.singer, name='singer_page'),
    path('song/<sid>.html', request.song, name='song'),
    path('so/<str:keyword>.html', request.search, name='search'),
    path('so/<str:keyword>/<int:page>.html', request.search, name='search_page'),
    # 兜底：关键词里带斜杠（如 "AC/DC"）时，WSGI 会把 %2F 解码成 /，
    # <str:keyword> 默认不匹配斜杠，上面两条路由都会 404。这里用非贪婪的 .+? 兜住，
    # 末尾的页码组是可选的 —— 否则 "/so/AC/DC/2.html" 会被整串当成关键词（AC/DC/2），
    # 带斜杠的关键词就永远翻不了页（分页器生成的链接会全部指错）。
    # 必须放在上面两条之后：带页码的那条是 \d+ 结尾，会先命中，不会和这条抢。
    re_path(r'^so/(?P<keyword>.+?)(?:/(?P<page>\d+))?\.html$', request.search, name='search_slash'),
    path('list/<str:chart>.html', request.chart, name='chart'),
    path('list/<str:chart>/<int:page>.html', request.chart, name='chart_page'),
    path('singerlist/<str:area>/<str:gender>/<str:style>/<str:letter>.html', request.singer_list, name='singer_list'),
    path('singerlist/<str:area>/<str:gender>/<str:style>/<str:letter>/<int:page>.html', request.singer_list, name='singer_list_page'),
    path('download/<sid>/<str:kind>.html', request.download, name='download'),
    # 播放页整首播完时的计数回调（首页「今日热听榜」的数据源），只接受 POST。
    # 刻意不带 .html 后缀：它不是页面，不该被当成页面爬。
    path('api/play/ended', request.play_ended, name='play_ended'),
    # 播放页开始播放时的上报（「大家正在听」的数据源），只接受 POST。同样不带 .html。
    path('api/play/start', request.play_start, name='play_start'),
    # 「大家正在听」的当前内容（JSON + 渲染好的 HTML 片段）：播放页开久了由前端轮询它刷新，
    # 见 static/js/playlist.js。只读本地表，不碰源站。同样不带 .html。
    path('api/live', request.live_now, name='live_now'),
    # 播放页连播时"原地换歌"用：按 sid 取这首歌的播放信息（JSON）。同样不带 .html。
    # 不要求登录 —— 谁都能点开播放页切歌。
    path('api/song/<sid>', request.song_info, name='song_info'),

    # ---- 用户账号与收藏（视图见 Web/views/users.py）----
    # 页面照旧带 .html，接口不带后缀；接口类只有"发验证码"和"切换喜欢"两个，
    # 都是需要不刷新页面完成的动作，其余（登录、注册）走普通表单 POST。
    path('login.html', users.login_view, name='login'),
    path('register.html', users.register_view, name='register'),
    # 登出走 GET：幂等操作，页面上就是个普通链接
    path('logout', users.logout_view, name='logout'),
    path('my/likes.html', users.my_likes, name='my_likes'),
    path('api/uc/code', users.send_code, name='uc_send_code'),
    path('api/favorite', users.toggle_favorite, name='toggle_favorite'),
]