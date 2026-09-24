"""节日 / 季节主题映射表（纯数据，改节日只动这里和同目录的 JSON）

两张表配合使用，用同一个「月-日」作键，新增节日时两处都要加：
    holidays.py             节日 → 主题标识 / 节日名（本文件）
    holiday_greetings.json  节日 → 祝福语池（一个节日可以写任意多条）

解析逻辑在 Web/services/holiday.py，模板变量由 Web/services/site_info.py 注入。

节日当天的行为：
    1. 强制把全站主题切成该节日对应的主题（用户当天手动改过则以用户为准，次日自动恢复）
    2. 在页头下方显示一条可关闭的滚动祝福横幅
想临时看某个节日的效果，加 URL 参数即可：/?bz_preview=02-14 或 /?bz_preview=halloween
"""

# 节日 → 主题与节日名
#   theme：必须是 daisyUI 内置主题名，可选值见 Web/static/js/theme.js 的 THEMES：
#          light dark cupcake bumblebee emerald corporate synthwave retro cyberpunk
#          valentine halloween garden forest aqua lofi pastel fantasy wireframe
#          black luxury dracula cmyk autumn business acid lemonade night coffee
#          winter dim nord sunset caramellatte abyss silk
#   name：横幅里显示的节日名
# 农历节日（春节/端午/中秋）需要农历换算，这里只收公历节日。
HOLIDAYS = {
    '01-01': {'theme': 'nord', 'name': '元旦'},
    '02-14': {'theme': 'valentine', 'name': '情人节'},
    '03-08': {'theme': 'silk', 'name': '妇女节'},
    '03-12': {'theme': 'garden', 'name': '植树节'},
    '04-01': {'theme': 'acid', 'name': '愚人节'},
    '05-01': {'theme': 'lemonade', 'name': '劳动节'},
    '05-20': {'theme': 'valentine', 'name': '520'},
    '05-21': {'theme': 'valentine', 'name': '521'},
    '06-01': {'theme': 'bumblebee', 'name': '儿童节'},
    '09-10': {'theme': 'pastel', 'name': '教师节'},
    '10-01': {'theme': 'autumn', 'name': '国庆节'},
    '10-31': {'theme': 'halloween', 'name': '万圣夜'},
    '11-11': {'theme': 'luxury', 'name': '双十一'},
    '12-24': {'theme': 'coffee', 'name': '平安夜'},
    '12-25': {'theme': 'forest', 'name': '圣诞节'},
    '12-31': {'theme': 'synthwave', 'name': '跨年'},
}

# 没有节日的普通日子按月份取主题，作为站点的「默认主题」兜底。
# 与节日的区别：兜底不做强制切换，用户手动选过主题就一直用用户的，也不显示横幅。
SEASON_THEMES = {
    1: 'winter', 2: 'winter', 3: 'garden', 4: 'garden', 5: 'garden', 6: 'aqua',
    7: 'aqua', 8: 'aqua', 9: 'autumn', 10: 'autumn', 11: 'autumn', 12: 'winter',
}

# 横幅一轮滚动几条祝福语：从该节日的祝福语池里随机抽这么条（池子不足就全用）。
# 随机种子取当天日期，所以同一天内所有页面都是同一批，不会翻一页文案就变；
# 换一天自动换一批。填 1 就是不轮换，只显示一条。
BANNER_PICK = 5
