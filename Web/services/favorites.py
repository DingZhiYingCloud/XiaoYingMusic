"""喜欢（收藏）的读写

Favorite 表就那么点操作，单独成模块是为了给"跨视图复用"留一个统一入口：列表页每行
一个心形，需要**批量**取已喜欢状态（见 liked_sids），不能一行查一次库。
"""
from django.db import IntegrityError

from Web.models import Favorite


def liked_sids(user, sids):
    """这批 sid 里，哪些已经被该用户喜欢 —— 返回 set

    user 为 None（未登录）或 sids 为空时直接返回空集合。
    调用方（列表页视图）拿它去模板里判断 each row 是否点亮心形。
    """
    if not user or not sids:
        return set()
    return set(
        Favorite.objects.filter(user=user, sid__in=list(sids))
        .values_list('sid', flat=True)
    )


def toggle(user, sid, title):
    """切换喜欢状态，返回切换**之后**是否已喜欢

    靠 (user, sid) 的唯一约束保证幂等：有就删（取消喜欢），没有就建。
    并发下同一个用户重复点可能撞唯一约束（另一个请求刚好抢先插入），
    那时结果同样是"已喜欢"，捕获 IntegrityError 当作已喜欢即可。
    """
    if not sid:
        return False

    existing = Favorite.objects.filter(user=user, sid=sid).first()
    if existing:
        existing.delete()
        return False

    try:
        Favorite.objects.create(user=user, sid=sid, title=(title or '')[:255])
    except IntegrityError:
        pass
    return True
