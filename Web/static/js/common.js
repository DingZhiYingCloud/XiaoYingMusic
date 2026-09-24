/* ============ 全站公共脚本 ============
 * 说明：本文件会作为 Tailwind 的扫描源之一（见 input.css 的 @source），
 *       因为 menu-active / dock-active / toast / alert 这些类名是 JS 运行时才挂上的，
 *       只靠模板扫描 Tailwind 不会生成对应样式。
 */

// 轻量提示：替代原 layui layer.msg（避免为一个提示引入整套 layui 样式与脚本）
function bzToast(msg) {
    var box = document.getElementById('bz-toast');
    if (!box) {
        box = document.createElement('div');
        box.id = 'bz-toast';
        box.className = 'toast toast-top toast-end z-[9999] mt-24 md:mt-16';
        document.body.appendChild(box);
    }
    var item = document.createElement('div');
    item.className = 'alert alert-info shadow-lg text-sm';
    item.textContent = msg;
    box.appendChild(item);
    setTimeout(function () { item.remove(); }, 2400);
}

// 站内搜索：统一跳转 /so/<关键词>.html（关键词需 URL 编码）
function bzSearch(form) {
    var kw = (form.wd.value || '').replace(/^\s+|\s+$/g, '');
    if (!kw) { bzToast('请输入要搜索的内容'); return false; }
    window.location.href = '/so/' + encodeURIComponent(kw) + '.html';
    return false;
}

// 点击弹层外部时收起 <details> 弹层（页头「榜单」「主题」都用的它）
// 浏览器原生只支持「再点一次 summary」收起，点空白处关不掉，只能自己补
document.addEventListener('click', function (e) {
    var openList = document.querySelectorAll('details[open]');
    for (var i = 0; i < openList.length; i++) {
        // 点在弹层内部（含 summary 本身）就不管，交给原生行为切换
        if (!openList[i].contains(e.target)) openList[i].removeAttribute('open');
    }
});

// 按当前 URL 给导航打高亮：
//   顶部菜单 / 侧边抽屉 → menu-active，底部 dock → dock-active
//   data-nav 值为 URL 前缀（空格分隔可写多个，如 "/singerlist/ /singer/"）
// 本文件在 <head> 里加载，此刻 body 还没解析，所以逻辑要等 DOMContentLoaded 再跑。
document.addEventListener('DOMContentLoaded', function () {
    var path = window.location.pathname;
    var nodes = document.querySelectorAll('[data-nav]');
    for (var i = 0; i < nodes.length; i++) {
        var el = nodes[i];
        var prefixes = (el.getAttribute('data-nav') || '').split(/\s+/);
        var hit = false;
        for (var j = 0; j < prefixes.length; j++) {
            var p = prefixes[j];
            if (!p) continue;
            // 首页需精确匹配，否则 "/" 会命中所有路径
            if (p === '/') {
                if (path === '/' || path.indexOf('/index/') === 0) { hit = true; break; }
            } else if (path.indexOf(p) === 0) {
                hit = true; break;
            }
        }
        if (hit) el.classList.add(el.closest('.dock') ? 'dock-active' : 'menu-active');
    }
});
