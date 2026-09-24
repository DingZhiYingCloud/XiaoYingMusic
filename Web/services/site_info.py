"""站点信息 context processor

职责：把 .env 里配置的站点名称、联系方式、统计 ID 注入全站模板，
使二开者只需修改 .env（不改任何模板）即可完成改名与换联系方式。

模板中可直接使用：
    {{ SITE_NAME }}           站点主名称（如 杯子音乐网）
    {{ SITE_NAME_ALT }}       站点副名称（如 爱听音乐网，可为空）
    {{ SITE_BRAND }}          SEO 文案用的品牌组合短语（如 爱听音乐网（杯子音乐网））
    {{ SITE_CONTACT_EMAIL }}  联系邮箱
    {{ SITE_CONTACT_WECHAT }} 微信号
    {{ SITE_CONTACT_TG }}     Telegram
    {{ ANALYTICS_ID }}        51.la 统计 ID（为空时不输出统计脚本）
    {{ DAY_THEME }}           今日主题标识（节日主题优先，否则季节主题）
    {{ DAY_IS_HOLIDAY }}      今天是否节日（决定要不要显示祝福横幅）
    {{ DAY_IS_PREVIEW }}      是否通过 ?bz_preview= 预览（预览不写本地标记）
    {{ HOLIDAY_NAME }}        节日名（非节日为空字符串）
    {{ HOLIDAY_GREETINGS }}   横幅要滚动的祝福语列表（非节日为空列表）
    {{ STATIC_VERSION }}      output.css 的版本号（文件修改时间，用于刷新浏览器缓存）
    {{ SEARCH_KEYWORD_MAX_CHARS }} 搜索关键词最大长度（搜索框 maxlength 与后端截断共用）

节日/季节与主题的对应关系见 Web/data/holidays.py，祝福语池见同目录的
Web/data/holiday_greetings.json，解析逻辑见 Web/services/holiday.py。
"""
import os

from django.conf import settings
from django.utils import timezone

from Web.services import holiday


def _static_version():
    """output.css 的修改时间，拿来当版本号

    为什么要这个：模板里写死 /static/css/output.css 的话，浏览器会一直用缓存里的旧文件。
    而 output.css 是编译产物，模板里新增 Tailwind 类名后必须重编译，旧文件里没有那些类名，
    表现就是"功能写了但看不到"（例如下拉展开依赖的 group-focus-within:）。
    取文件修改时间可以让版本号随重编译自动变化，不用人工记着改。

    路径用 settings.STATICFILES_DIRS[0]（本项目静态资源的源目录，也就是对外服务的那个
    目录，见 settings.py 与 XiaoYingMusic/urls.py）；取不到文件时返回 0，
    绝不因为读不到一个文件就让整站起不来。
    """
    path = os.path.join(settings.STATICFILES_DIRS[0], 'css', 'output.css')
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return 0


def site_info(request):
    """向全站模板注入站点信息变量（取值见 settings.py 中同名配置，最终来源为 .env）"""
    # 用 localdate() 而非 date.today()：时区取 settings.TIME_ZONE（Asia/Shanghai），
    # 避免服务器跑在 UTC 时「节假日提前 8 小时开始」。
    day = holiday.resolve(timezone.localdate(), request.GET.get('bz_preview', ''))
    return {
        'SITE_NAME': settings.SITE_NAME,
        'SITE_NAME_ALT': settings.SITE_NAME_ALT,
        'SITE_BRAND': settings.SITE_BRAND,
        'SITE_CONTACT_EMAIL': settings.SITE_CONTACT_EMAIL,
        'SITE_CONTACT_WECHAT': settings.SITE_CONTACT_WECHAT,
        'SITE_CONTACT_TG': settings.SITE_CONTACT_TG,
        'ANALYTICS_ID': settings.ANALYTICS_ID,
        'DAY_THEME': day.theme,
        'DAY_IS_HOLIDAY': day.is_holiday,
        'DAY_IS_PREVIEW': day.preview,
        'HOLIDAY_NAME': day.name,
        'HOLIDAY_GREETINGS': day.greetings,
        'STATIC_VERSION': _static_version(),
        'SEARCH_KEYWORD_MAX_CHARS': settings.SEARCH_KEYWORD_MAX_CHARS,
    }
