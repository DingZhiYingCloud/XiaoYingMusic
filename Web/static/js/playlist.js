/* ============ 播放页连播：队列 / 上一首·下一首 / 循环开关 / 原地换歌 ============
 * 数据来自页面的两处（都由 song.html 输出）：
 *   window.BZ_PLAYLIST  —— 连播队列：有「我的喜欢」就用它，一首都没有（含未登录）就用
 *                          「大家正在听」兜底（由 Web/views/request.py 的 _play_queue 决定）
 *   window.BZ_SONG_DATA —— 当前这首（song_player.js 也读它）
 *
 * 队列的**显示位置有两种**，由 mode 决定（都带 data-play-sid，靠这一个属性就能找到）：
 *   mode='likes'  「我的喜欢」的列表在右栏（#bz-playlist），队列就是它
 *   mode='live'   队列是底部「大家正在听」（#bz-live-list），右栏保持空态/登录引导
 *   mode='empty'  两边都没有，没有队列（点上一首/下一首会提示"列表里还没有歌"）
 * 所以本文件**不假设列表在右栏**：找条目一律用 [data-play-sid]，
 * 重建列表（renderList）才只针对右栏那个 ol —— 它专门用来渲染「我的喜欢」。
 *
 * 分工：本文件只决定"下一首是谁、什么时候换"，真正换歌（音频/封面/歌名/歌词）由
 * song_player.js 的 BZSongPlayer.load() 执行 —— 那边是唯一持有 audio 的地方。
 *
 * 另外它还负责定时刷新「大家正在听」那块（refreshLive）：播放页常被开着很久，
 * 不刷新的话那些"多久前"会停在旧值、新歌也进不来。走 /api/live，只读本地表。
 *
 * 为什么换歌要动地址栏（pushState）：原地换歌不刷新页面，但地址栏得跟着变，否则用户
 * 刷新会回到最开始那首、复制出去的链接也是错的。改了地址还要监听 popstate，不然浏览器
 * 后退键只会改地址、页面停在原地。
 */
(function () {
    'use strict';

    var LOOP_KEY = 'bz_loop';   // 循环开关的记忆（与播放器的其它记忆放在同一个命名空间）

    var queue = [];           // [{sid, title}]
    var index = -1;           // 当前这首在队列里的位置；-1 = 不在队列里（比如从搜索页直接点进来的）
    var loop = false;         // 列表循环：开着时播完最后一首回到第一首
    var switching = false;    // 正在切歌，防连点造成并发请求
    var autoTried = 0;        // 自动连播时连续跳过了几首（整个队列都取不到时用来刹车）
    // 本次页面里**实际播放过**的 sid 顺序（最后一个 = 当前这首）。
    // 为什么单独记一份：从搜索页/歌手页进来时，正在听的那首（A）**不在队列里**（队列是
    // 「我的喜欢」或「大家正在听」），此时下标是 -1，「上一首」按队列算就走不回去 ——
    // 用户点了别的歌再点上一首，会得到"已经是第一首了"，明明刚听过 A 却回不去。
    // 所以「上一首」优先回放这份历史（见 goBack）。
    // ⚠️ 名字里必须带 play：**不能叫 history** —— 那是 window.history 的名字，
    // 一旦被 var 遮蔽，pushUrl 里的 history.pushState 会直接报 "is not a function"，
    // 换歌后地址栏就再也不跟着变了（踩过这个坑）。
    var playHistory = [];

    // 列表区块的 DOM。收藏状态一变就要改写右栏那块（见 syncLiked），所以引用要留着
    var elBox, elList, elTitle, elManage, elHint, elEmpty;
    // 「大家正在听」那块（轮询刷新时要改写它）
    var elLiveBox, elLiveList, elLiveCount;
    var mode = 'likes';       // 队列来自哪：likes（我的喜欢）/ live（大家正在听）/ empty（都没有）
    var loggedIn = false;     // 由页面注入：右栏空态那句提示只给登录用户看（未登录看登录引导）

    function read(key, def) {
        try {
            var v = JSON.parse(localStorage.getItem(key));
            return (v === null || v === undefined) ? def : v;
        } catch (e) { return def; }
    }
    function write(key, val) {
        try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) { /* 隐私模式等忽略 */ }
    }

    function indexOf(sid) {
        for (var i = 0; i < queue.length; i++) {
            if (queue[i].sid === sid) return i;
        }
        return -1;
    }

    /* ---------- 取一首歌的播放信息 ---------- */
    function fetchSong(sid) {
        return fetch('/api/song/' + encodeURIComponent(sid))
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; })
            .then(function (d) { return d || { ok: false, msg: '网络异常，稍后再试' }; });
    }

    /* ---------- 切歌 ---------- */
    // step = 1 下一首 / -1 上一首；越界时按循环开关决定绕回去还是停住
    function playRelative(step) {
        if (switching) return;              // 正在切歌：忽略这次点击，免得历史被连点弹空
        if (step < 0 && goBack()) return;   // 上一首优先走"本次实际听过"的历史（见 playHistory 的说明）
        if (!queue.length) {
            bzToast('列表里还没有歌', 'warning');
            return;
        }
        var next = index + step;
        if (next >= queue.length) {
            if (!loop) { bzToast('已经是最后一首了', 'warning'); return; }
            next = 0;
        }
        if (next < 0) {
            if (!loop) { bzToast('已经是第一首了', 'warning'); return; }
            next = queue.length - 1;
        }
        playAt(next);
    }

    // 记一笔"这首真的开始播了"。相邻重复不记，免得同一首反复播把历史撑长。
    function remember(sid) {
        if (!sid) return;
        if (playHistory[playHistory.length - 1] === sid) return;
        playHistory.push(sid);
    }

    // 回放到"上一首"：返回 false 表示没有上一首可回（交给调用方按队列越界处理）。
    // 先把当前这首弹掉、再把目标弹掉 —— 目标交给下面的播放重新记回来，
    // 所以历史始终等于"听过的歌"，不必手工维护下标。目标在队列里就走 playAt（下标也跟着对），
    // 不在队列里（典型：从搜索页进来的那首）就用 playSid 原地播。
    function goBack() {
        if (playHistory.length < 2) return false;
        playHistory.pop();
        var prev = playHistory.pop();
        var i = indexOf(prev);
        if (i >= 0) playAt(i); else playSid(prev);
        return true;
    }

    // auto=true 表示这是"播完自动接着播"，此时遇到取不到的歌会静默跳过继续往下
    function playAt(i, auto) {
        if (i >= queue.length) {
            bzToast('列表播完了', 'warning');
            return;
        }
        var item = queue[i];
        if (!item || switching) return;

        switching = true;
        fetchSong(item.sid).then(function (song) {
            switching = false;
            if (!song.ok) {
                if (auto && autoTried < queue.length) {
                    // 这首拿不到（没音源 / 抓取失败），当它不存在，接着试下一首
                    autoTried += 1;
                    index = i;
                    playAt(i + 1, true);
                    return;
                }
                bzToast(song.msg || '这首歌暂时听不了', 'warning');
                return;
            }
            autoTried = 0;
            index = i;
            remember(song.sid);
            window.BZSongPlayer.load(song);
            window.BZSongPlayer.play();
            markCurrent(song.sid);
            pushUrl(song.sid);
        });
    }

    // 当场播一首"不在队列里"的歌（点底部「大家正在听」、而队列其实是「我的喜欢」时走这里）。
    // 它不作为队列成员（index=-1），播完接队列开头 —— 不硬塞进「我的喜欢」，
    // 否则点一首别人的歌就等于把它收藏了。
    function playSid(sid) {
        if (!sid || switching) return;
        switching = true;
        fetchSong(sid).then(function (song) {
            switching = false;
            if (!song.ok) {
                bzToast(song.msg || '这首歌暂时听不了', 'warning');
                return;
            }
            index = -1;
            remember(song.sid);
            window.BZSongPlayer.load(song);
            window.BZSongPlayer.play();
            markCurrent(song.sid);
            pushUrl(song.sid);
        });
    }

    /* ---------- 地址栏同步 ---------- */
    function pushUrl(sid) {
        // 带上现有查询串：别让换歌把地址栏上的参数悄悄吃掉（比如从别处带过来的 ?x=1），
        // 否则用户一刷新参数就没了。
        var url = '/song/' + sid + '.html' + location.search;
        if (location.pathname + location.search !== url) history.pushState({ sid: sid }, '', url);
    }

    function sidFromPath() {
        var m = location.pathname.match(/^\/song\/([A-Za-z0-9_-]+)\.html$/);
        return m ? m[1] : '';
    }

    function onPopState() {
        var sid = sidFromPath();
        if (!sid || sid === window.BZSongPlayer.sid()) return;
        var i = indexOf(sid);
        if (i >= 0) {
            playAt(i);
            return;
        }
        // 不在队列里，但确实是本次听过的（典型：从搜索页进来的那首）→ 原地回放。
        // 同时把历史截到这一步（后面那些是"前进"方向，后退之后不该再留在"上一首"里）。
        var h = playHistory.lastIndexOf(sid);
        if (h >= 0) {
            playHistory = playHistory.slice(0, h + 1);
            playSid(sid);      // playSid 会把它记回历史；末尾已是它，remember 不会重复记
            return;
        }
        // 连本次都没听过（比如进播放页之前听的那首）：整页加载它最稳，
        // 原地换歌需要的信息（歌手、歌词、喜欢状态）这一页拿不到。
        location.href = '/song/' + sid + '.html';
    }

    /* ---------- 列表高亮 ---------- */
    // 把条目滚进它自己那个滚动容器。刻意不用 el.scrollIntoView()：那个方法会连带滚动
    // **所有**可滚动祖先（包括整个窗口），连播换歌会把页面往下扯，把正在看歌词的人甩开。
    // 这里只改容器的 scrollTop，页面纹丝不动。
    function scrollItemIntoView(el) {
        var box = el.closest('[data-bz-scroll]');
        if (!box) return;
        var r = el.getBoundingClientRect();
        var b = box.getBoundingClientRect();
        if (r.top < b.top) box.scrollTop += r.top - b.top;
        else if (r.bottom > b.bottom) box.scrollTop += r.bottom - b.bottom;
    }

    // scroll=false 只用于页面刚加载时：那会儿只是把"正在播的那首"标出来，不该主动滚动
    function markCurrent(sid, scroll) {
        var items = document.querySelectorAll('[data-play-sid]');
        Array.prototype.forEach.call(items, function (el) {
            var on = el.getAttribute('data-play-sid') === sid;
            el.classList.toggle('bz-playing', on);
            if (on) {
                el.setAttribute('aria-current', 'true');
                // 列表是滚动区，连播换歌后把当前项带进视野，否则用户看着停在第 1 首
                if (scroll !== false) scrollItemIntoView(el);
            } else {
                el.removeAttribute('aria-current');
            }
        });
    }

    /* ---------- 收藏联动 ----------
     * 播放页的心形按钮（static/js/user.js）收藏成功后会调这里的 syncLiked，让列表当场跟着变 ——
     * 否则用户收藏了却要刷新才看得到，很容易以为没生效。
     *
     * queue 与列表 DOM 是两份数据，必须一起改：queue 决定"下一首是谁"，DOM 只是它的显示。
     * 所以这里改完 queue 就整块重建列表，序号与高亮一起归位，不必手工维护两套顺序。
     */
    function renderList() {
        if (!elList) return;
        elList.innerHTML = '';
        for (var i = 0; i < queue.length; i++) {
            var item = queue[i];
            var li = document.createElement('li');
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'flex w-full items-center gap-3 rounded-field px-2 py-2 text-left transition hover:bg-base-200';
            btn.setAttribute('data-play-sid', item.sid);
            btn.title = item.title || '';

            var no = document.createElement('span');
            no.className = 'w-6 shrink-0 text-right text-xs tabular-nums opacity-45';
            no.textContent = String(i + 1);

            var name = document.createElement('span');
            name.className = 'truncate text-sm';
            // 用 textContent 而不是拼 HTML：标题来自源站/接口，不该被当成 HTML 解析
            name.textContent = item.title || '';

            btn.appendChild(no);
            btn.appendChild(name);
            li.appendChild(btn);
            elList.appendChild(li);
        }
        bindList(elList);     // 元素都换了新的，点击事件要重新绑（只绑这个列表，免得给底部那块重复挂）
        markCurrent(window.BZSongPlayer.sid());
    }

    // 空列表下收藏一首，这块就该从"空态/大家正在听"变成真正的「我的喜欢」：标题、管理链接、
    // 底部提示一起换，队列也整批换掉 —— 兜底队列里那些是别人的歌，不属于"我的喜欢"。
    function switchToLikes() {
        if (mode === 'likes') return;
        mode = 'likes';
        if (elTitle) elTitle.textContent = '我的喜欢';
        if (elManage) elManage.classList.remove('hidden');
        if (elHint) {
            // 提示语从空态换成连播说明。这里用 textContent 覆盖：原来那句更简单，
            // 且能走到这里说明已经登录了，不会丢掉未登录时的登录链接。
            elHint.textContent = '点歌名直接切换，一首放完自动接下一首。';
        }
        queue = [];
        index = -1;   // 交给下面的 insertFirst 把刚收藏的这首放进来
    }

    function insertFirst(item) {
        var i = indexOf(item.sid);
        if (i >= 0) {                    // 已经在列表里 → 挪到最前（"我的喜欢"按最近收藏排序）
            queue.splice(i, 1);
            if (i < index) index -= 1;
        }
        queue.unshift({ sid: item.sid, title: item.title || '' });
        if (index >= 0) {
            index += 1;      // 前面插了一项，当前歌的位置往后挪一位
        } else if (item.sid === window.BZSongPlayer.sid()) {
            // 收藏的正是当前这首、它原先不在队列里 → 现在它在队首，下标要跟上。
            // 不补这一步的话，播完自动接下一首会从队首开始，等于把这首又放一遍。
            index = 0;
        }
    }

    function removeItem(sid) {
        var i = indexOf(sid);
        if (i < 0) return;
        queue.splice(i, 1);
        if (i < index) {
            index -= 1;
        } else if (i === index) {
            index = -1;   // 正在播的这首被取消收藏 → 它不再属于队列，播完接列表第一首
        }
    }

    // 右栏（我的喜欢）的显隐：它**只显示"我的喜欢"自己的条目**。
    // 队列兜底成「大家正在听」时（mode='live'），queue 里虽然有条目，但那些条目显示在
    // 底部那块里，右栏要保持空态/登录引导 —— 否则同一批歌会在页面上出现两次。
    // 「还没有喜欢的歌」那句只给登录用户看：未登录时右栏已经有登录引导了，不必再来一句。
    function renderEmpty() {
        var showOwnList = (mode === 'likes') && queue.length > 0;
        if (elList) elList.classList.toggle('hidden', !showOwnList);
        if (elHint) elHint.classList.toggle('hidden', !showOwnList);
        if (elEmpty) elEmpty.classList.toggle('hidden', !(loggedIn && !showOwnList));
    }

    // 供 static/js/user.js 在收藏状态变化后调用（其它页面没有连播列表，不会调到）
    function syncLiked(liked, item) {
        if (!elList || !item || !item.sid) return;
        if (liked) {
            switchToLikes();     // 需要时先把这块切到「我的喜欢」
            insertFirst(item);
        } else {
            removeItem(item.sid);
        }
        renderList();
        renderEmpty();
    }

    /* ---------- 控件 ---------- */
    function renderLoop() {
        var btn = document.getElementById('bz-btn-loop');
        if (!btn) return;
        btn.classList.toggle('on', loop);
        btn.title = loop ? '列表循环：已开启' : '列表循环：已关闭';
    }

    function bindControls() {
        var prev = document.getElementById('bz-btn-prev');
        var next = document.getElementById('bz-btn-next');
        var loopBtn = document.getElementById('bz-btn-loop');

        if (prev) prev.addEventListener('click', function (e) {
            e.preventDefault();
            playRelative(-1);
        });
        if (next) next.addEventListener('click', function (e) {
            e.preventDefault();
            playRelative(1);
        });
        if (loopBtn) loopBtn.addEventListener('click', function (e) {
            e.preventDefault();
            loop = !loop;
            write(LOOP_KEY, loop);
            renderLoop();
            bzToast(loop ? '列表循环已开启' : '列表循环已关闭', 'info');
        });
    }

    // 点列表里的一首：在队列里就跳到那一首（连播的"当前位"跟着走）；不在队列里
    // （典型：有「我的喜欢」时点了底部「大家正在听」）就当场播它，不改队列。
    function onItemClick(sid) {
        var i = indexOf(sid);
        if (i >= 0) playAt(i);
        else playSid(sid);
    }

    // root 省略 = 绑整页（页面初始化时用）；renderList 重建右栏后只绑那个容器，
    // 否则底部那块每次都会被重复挂一次监听，点一下会触发两次。
    function bindList(root) {
        var items = (root || document).querySelectorAll('[data-play-sid]');
        Array.prototype.forEach.call(items, function (el) {
            el.addEventListener('click', function (e) {
                e.preventDefault();
                onItemClick(el.getAttribute('data-play-sid'));
            });
        });
    }

    /* ---------- 「大家正在听」的自动刷新 ----------
     * 播放页常常被开着很久，那块列表的时间会停在旧值、新歌也进不来，所以定时去 /api/live 拉一次。
     * 接口返回的是**服务端渲染好的条目 HTML**（与页面首次渲染用的是同一个模板片段），
     * 这里直接替换 innerHTML，不必在 JS 里再拼一遍同样的标记（也就不会两边慢慢跑偏）。
     * 只读本地表、不碰源站，所以这个轮询很便宜；间隔见 PLAYED_LIVE_REFRESH_INTERVAL（0=关闭）。
     */
    function refreshLive() {
        if (!elLiveBox || !elLiveList) return;
        fetch('/api/live')
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; })
            .then(function (d) {
                if (!d || !d.ok) return;
                // 内容整段换新（跑马灯：份数、位移、速度都要按新宽度重算，见 fitMarquee）
                elLiveList.innerHTML = d.html;
                if (elLiveCount) elLiveCount.textContent = String(d.count);
                // 一开始没人正在听时整块是隐藏的，后来有人开始听了要让它自己冒出来
                elLiveBox.classList.toggle('hidden', d.count === 0);
                fitMarquee();           // 条目换了，份数与速度要按新宽度重算（跑马灯形态才有）
                bindList(elLiveList);   // 条目是全新的，点击事件要重新绑
                markCurrent(window.BZSongPlayer.sid(), false);
            });
    }

    /* ---------- 跑马灯的份数与速度 ----------
     * 跑马灯要"看不出接缝"，靠的是把内容复制成几份、再把整条位移**一份的宽度**。
     * 但条目数量不固定（这数据一直在变），所以两件事都得按真实宽度现算：
     *   ① 份数：内容比容器窄时，一份滚完后面就是空白（露白）—— 补到"总宽 ≥ 容器宽 + 一份"为止。
     *      补出来的副本标 aria-hidden：它只是接缝，读屏念一遍就够了。
     *   ② 位移与时长：位移永远是"一份的宽度"，份数一变百分比就得跟着变（两份 -50%、三份 -33.33%）；
     *      时长按 60px/秒 折算，这样不管几条几份，观感速度都一样（节日横幅用的也是这个速度）。
     */
    function fitMarquee() {
        if (!elLiveList) return;
        var wrap = elLiveList.parentElement;
        var groups = elLiveList.querySelectorAll(':scope > .bz-marquee-group');
        if (!wrap || !groups.length) return;
        var one = groups[0].offsetWidth;    // 整块藏着时是 0（此刻没人正在听），那就没什么要补的
        if (!one) return;
        var need = Math.max(2, Math.ceil(wrap.clientWidth / one) + 1);
        while (elLiveList.querySelectorAll(':scope > .bz-marquee-group').length < need) {
            var copy = groups[0].cloneNode(true);
            copy.setAttribute('aria-hidden', 'true');
            elLiveList.appendChild(copy);
        }
        var n = elLiveList.querySelectorAll(':scope > .bz-marquee-group').length;
        elLiveList.style.setProperty('--bz-shift', (-100 / n) + '%');
        elLiveList.style.setProperty('--bz-dur', (one / 60) + 's');
    }

    /* ---------- 初始化 ---------- */
    document.addEventListener('DOMContentLoaded', function () {
        // 没有音频元素 = 播放页的兜底形态（正在维护），不启动连播
        if (!document.getElementById('bz-audio') || !window.BZSongPlayer) return;

        queue = window.BZ_PLAYLIST || [];
        loop = read(LOOP_KEY, false) === true;
        loggedIn = window.BZ_CURRENT_USER === true;

        // 右栏（我的喜欢）区块的引用：收藏后要改写它，见 syncLiked。
        // 区块在播放页总是渲染（没播放直链时整块不存在，那时上面已经 return）；
        // 未登录时没有 elHint（那句是登录引导，另有 id）。
        elBox = document.getElementById('bz-playlist-box');
        elList = document.getElementById('bz-playlist');
        elTitle = document.getElementById('bz-playlist-title');
        elManage = document.getElementById('bz-playlist-manage');
        elHint = document.getElementById('bz-playlist-hint');
        elEmpty = document.getElementById('bz-playlist-empty');
        if (elBox) mode = elBox.getAttribute('data-kind') || 'likes';

        // 「大家正在听」区块：整块总是渲染（没人正在听时带 hidden），所以这里能取到
        elLiveBox = document.getElementById('bz-live-box');
        elLiveList = document.getElementById('bz-live-list');
        elLiveCount = document.getElementById('bz-live-count');

        var current = (window.BZ_SONG_DATA || {}).sid || '';
        index = indexOf(current);
        // 历史从"当前这首"起算 —— 它是本次页面听到的第一首，这样"上一首"从一开始就有得回
        playHistory = current ? [current] : [];

        renderLoop();
        markCurrent(current, false);   // 初始只标出"正在播的那首"，不主动滚动页面
        bindControls();
        fitMarquee();                  // 跑马灯：按真实宽度补足份数并定速（别的形态它自己会返回）
        bindList();                    // 一次绑全页：右栏「我的喜欢」+ 底部「大家正在听」
        renderEmpty();                 // 对齐右栏的初始显隐（模板已渲染，这里只是同一套规则再算一遍）
        window.addEventListener('popstate', onPopState);

        // 定时刷新「大家正在听」（0 = 关闭，见 PLAYED_LIVE_REFRESH_INTERVAL）
        var liveSec = window.BZ_LIVE_REFRESH || 0;
        if (liveSec > 0 && elLiveBox) setInterval(refreshLive, liveSec * 1000);

        // 整首播完 → 自动下一首（队列空 / 没开循环且已到末尾时，playRelative 会就地停住）
        window.BZSongPlayer.onEnded(function () {
            autoTried = 0;
            playRelative(1);
        });
    });

    /* ---------- 对外接口（static/js/user.js 收藏后调它同步列表） ---------- */
    window.BZPlaylist = { syncLiked: syncLiked };
})();
