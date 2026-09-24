"""节日主题解析

把 Web/data/ 下的两张纯数据表读出来，算出「今天是什么日子」：
    Web/data/holidays.py            节日 → 主题 / 节日名
    Web/data/holiday_greetings.json 节日 → 祝福语池

解析结果由 Web/services/site_info.py 注入模板，模板与前端脚本只消费结果，
不再自己判断日期。
"""
import json
import random
from dataclasses import dataclass
from pathlib import Path

from Web.data.holidays import BANNER_PICK, HOLIDAYS, SEASON_THEMES

_GREETINGS_PATH = Path(__file__).resolve().parent.parent / 'data' / 'holiday_greetings.json'


@dataclass
class DayTheme:
    """某一天对应的主题设置"""
    theme: str         # 今日主题标识
    is_holiday: bool   # 是否节日（同时决定要不要显示祝福横幅）
    name: str          # 节日名（非节日为空串）
    greetings: list    # 横幅要滚动的祝福语（非节日为空列表）
    preview: bool      # 是否是 ?bz_preview= 预览出来的（预览不写任何本地标记）


def _load_greetings():
    """读取祝福语池。

    每次请求现读，不做缓存：这是个十几 KB 的本地小文件，而祝福语会用 AI 频繁追加，
    现读能保证改完刷新页面就生效，不必重启开发服务器。
    """
    with _GREETINGS_PATH.open(encoding='utf-8') as f:
        return json.load(f)


def _pick_greetings(pool, key, today):
    """从该节日的祝福语池里抽 BANNER_PICK 条

    随机种子取当天日期：同一天内所有页面、所有刷新抽到的都是同一批，
    不会点一下导航文案就变；换一天自动换一批。
    """
    items = pool.get(key) or []
    if not items:
        return []
    return random.Random(today.toordinal()).sample(items, min(BANNER_PICK, len(items)))


def _match_preview(value):
    """解析 ?bz_preview= 的取值，两种写法都支持，返回命中的节日键；没命中返回 None

        /?bz_preview=02-14        写月-日
        /?bz_preview=halloween    写该节日的主题标识
    """
    if value in HOLIDAYS:
        return value
    for key, item in HOLIDAYS.items():
        if item['theme'] == value:
            return key
    return None


def resolve(today, preview=''):
    """算出 today 该用什么主题、要不要显示横幅、横幅滚哪些祝福语

    preview 非空时（URL 带 ?bz_preview=）强制按指定节日处理，方便随时预览任意节日的
    效果。真实用户不带这个参数，永远走正常日期逻辑。
    """
    key = _match_preview(preview) if preview else None
    if key is None:
        key = today.strftime('%m-%d')
        key = key if key in HOLIDAYS else None

    if key is None:
        # 普通日子：季节主题只作默认值，也不显示横幅
        return DayTheme(SEASON_THEMES.get(today.month, 'emerald'), False, '', [], False)

    item = HOLIDAYS[key]
    greetings = _pick_greetings(_load_greetings(), key, today)
    if not greetings:
        # 祝福语池还没配这个节日：用节日名兜底，避免出现一条空横幅
        greetings = [f'{item["name"]}快乐']
    return DayTheme(item['theme'], True, item['name'], greetings, bool(preview))
