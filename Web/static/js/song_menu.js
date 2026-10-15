/* 歌曲链接的右键菜单：喜欢 / 下一首播放
 *
 * 全站加载（见 templates/template.html）。在两类元素上点右键都会弹出：
 *   ① 指向 /song/<sid>.html 的 <a>（首页、歌手页、搜索页、榜单页…的歌曲条目）
 *   ② 播放页里带 data-play-sid 的列表条目（右栏三块列表）
 * 为什么用右键：左键的"打开歌曲页 / 原地播放"是原有行为，不能被抢走。
 *
 * 两个菜单项：
 *   喜欢       —— 调 /api/favorite（与心形按钮同一个接口）；未登录会带你去登录页
 *   下一首播放 —— 把这首歌插到"当前这首"的后面：
 *                   在播放页（有 window.BZPlaylist）→ 直接插进队列并立刻刷新右栏
 *                   不在播放页                      → 记进 localStorage（bz_play_next），
 *                                                     等进播放页时自动补进队列
 *
 * 不用 jQuery；异步 POST 必须带 CSRF token（读法与 user.js 一致）。
 */
(function () {
    'use strict';

    // 跨页面携带"待插播"的歌（与 playlist.js 共用同名键）
    var PENDING_KEY = 'bz_play_next';
    // 菜单需要的那点样式：菜单是 JS 现造的，模板里没有对应类名，而 output.css 是
    // 编译产物（不能临时新增 Tailwind 类），所以这里自带一小段样式。
    var MENU_CSS =
        '#bz-songmenu{position:fixed;z-index:60;min-width:150px;padding:4px;border-radius:10px;' +
        'border:1px solid rgba(0,0,0,.08);background:#fff;box-shadow:0 6px 20px rgba(0,0,0,.16);' +
        'font-size:14px;line-height:1.4}' +
        '#bz-songmenu button{display:block;width:100%;padding:8px 10px;text-align:left;border:0;' +
        'border-radius:6px;background:transparent;cursor:pointer;color:#333;font:inherit}' +
        '#bz-songmenu button:hover{background:rgba(0,0,0,.06)}' +
        '#bz-songmenu i{display:inline-block;width:18px;margin-right:6px;text-align:center;color:#888}';

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function post(url, data) {
        return fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-CSRFToken': csrfToken()
            },
            body: new URLSearchParams(data)
        }).then(function (resp) {
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return resp.json();
        });
    }

    function read(key, def) {
        try {
            var v = JSON.parse(localStorage.getItem(key));
            return (v === null || v === undefined) ? def : v;
        } catch (e) { return def; }
    }
    function write(key, val) {
        try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) { /* 隐私模式等忽略 */ }
    }

    /* ---------- 菜单 DOM（懒建，只建一次） ---------- */
    var elMenu = null;
    var currentItem = null;

    function ensureMenu() {
        if (elMenu) return elMenu;
        var style = document.createElement('style');
        style.textContent = MENU_CSS;
        document.head.appendChild(style);

        elMenu = document.createElement('div');
        elMenu.id = 'bz-songmenu';
        elMenu.setAttribute('role', 'menu');
        elMenu.hidden = true;
        elMenu.innerHTML =
            '<button type="button" data-act="fav"><i class="fa fa-heart-o"></i>喜欢</button>' +
            '<button type="button" data-act="next"><i class="fa fa-step-forward"></i>下一首播放</button>';
        document.body.appendChild(elMenu);

        elMenu.addEventListener('click', function (e) {
            var btn = e.target.closest ? e.target.closest('button[data-act]') : null;
            if (!btn) return;
            e.preventDefault();
            e.stopPropagation();
            var act = btn.getAttribute('data-act');
            var item = currentItem;
            hide();
            if (!item) return;
            if (act === 'fav') doFavorite(item); else doPlayNext(item);
        });
        return elMenu;
    }

    function show(x, y, item) {
        var m = ensureMenu();
        currentItem = item;
        m.hidden = false;
        // 先显示再量尺寸，才能做"贴边内收"（否则菜单会跑出视口）
        var maxLeft = window.innerWidth - m.offsetWidth - 8;
        var maxTop = window.innerHeight - m.offsetHeight - 8;
        m.style.left = Math.max(8, Math.min(x, maxLeft)) + 'px';
        m.style.top = Math.max(8, Math.min(y, maxTop)) + 'px';
    }

    function hide() {
        if (elMenu) elMenu.hidden = true;
        currentItem = null;
    }

    /* ---------- 从右键位置反查"这是哪首歌" ---------- */
    function titleOf(node, sid) {
        var t = node.getAttribute('data-bz-title') || node.getAttribute('title') || '';
        if (!t) t = (node.textContent || '').replace(/\s+/g, ' ').trim();
        return t;
    }

    function itemFrom(el) {
        var node = el;
        while (node && node.nodeType === 1 && node !== document.body) {
            var sid = node.getAttribute('data-play-sid');
            if (sid) return { sid: sid, title: titleOf(node, sid) };
            if (node.tagName === 'A') {
                var m = (node.getAttribute('href') || '').match(/^\/song\/([A-Za-z0-9_-]+)\.html/);
                // 是链接但不是歌曲链接（如 /singer/xxx.html）→ 不接管右键
                return m ? { sid: m[1], title: titleOf(node, m[1]) } : null;
            }
            node = node.parentNode;
        }
        return null;
    }

    /* ---------- 两个动作 ---------- */
    function doFavorite(item) {
        post('/api/favorite', { sid: item.sid, title: item.title || '' }).then(function (res) {
            if (res.need_login) {
                // 登录后回到当前页（带上查询串，翻页位置也保住）
                location.href = '/login.html?next=' +
                    encodeURIComponent(location.pathname + location.search);
                return;
            }
            if (!res.ok) {
                bzToast(res.msg || '操作失败', 'error');
                return;
            }
            bzToast(res.liked ? '已加入我的喜欢' : '已取消喜欢', 'success');
            // 页面上同一首的心形按钮跟着变（播放页、我的喜欢页都有）
            var heart = document.querySelector('[data-fav-sid="' + item.sid + '"]');
            if (heart) {
                var icon = heart.querySelector('i');
                if (icon) {
                    icon.className = res.liked ? 'fa fa-heart text-error' : 'fa fa-heart-o opacity-60';
                }
                heart.setAttribute('aria-pressed', res.liked ? 'true' : 'false');
            }
            // 播放页右栏的「我的喜欢」当场更新（与心形按钮走同一个接口）
            if (window.BZPlaylist) {
                window.BZPlaylist.syncLiked(res.liked, { sid: item.sid, title: item.title || '' });
            }
        }).catch(function () {
            bzToast('网络异常，请稍后再试', 'error');
        });
    }

    function doPlayNext(item) {
        var song = { sid: item.sid, title: item.title || '' };
        if (window.BZPlaylist && window.BZPlaylist.playNext) {
            if (window.BZPlaylist.playNext(song)) bzToast('已设为下一首播放', 'success');
            else bzToast('这首正在播放', 'info');
            return;
        }
        // 不在播放页（首页/歌手页/搜索页…）：先记下来，进播放页时自动补进队列
        var list = read(PENDING_KEY, []) || [];
        list.push(song);
        write(PENDING_KEY, list);
        bzToast('已记下：进播放页后会紧接着播它', 'success');
    }

    /* ---------- 事件绑定 ---------- */
    document.addEventListener('contextmenu', function (e) {
        var t = e.target;
        // 输入框里不接管：右键要留给系统的复制/粘贴
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
        var item = itemFrom(t);
        if (!item) { hide(); return; }
        e.preventDefault();
        show(e.clientX, e.clientY, item);
    });

    // 关菜单的几种情形：点了别处 / 页面整体滚动 / 改窗口尺寸 / Esc
    document.addEventListener('click', function (e) {
        if (elMenu && !elMenu.hidden && !elMenu.contains(e.target)) hide();
    });
    // ⚠️ 这里只用**捕获**监听（才能收到任意元素的滚动），但只对"页面整体滚动"下手：
    // 播放页的歌词区会随播放进度自动往下滚（play.js），那是**容器内部滚动**，
    // 它不该把菜单关掉 —— 否则右键菜单点着点着就自己消失了。
    // 判据是滚动事件的目标：窗口/文档级滚动时 target 是 document（或 documentElement），
    // 元素内部滚动时 target 就是那个元素。
    window.addEventListener('scroll', function (e) {
        var t = e.target;
        if (t === document || t === document.documentElement || t === document.body) hide();
    }, true);
    window.addEventListener('resize', hide);
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') hide();
    });
})();
