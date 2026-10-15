/* ============ 播放页连播：队列 / 上一首·下一首 / 循环开关 / 原地换歌 ============
 * 数据来自页面注入的几份（都由 song.html 输出）：
 *   window.BZ_PLAYLIST  —— 待播放列表（连播队列）的初始值：有收藏用收藏，否则用「猜你喜欢」
 *   window.BZ_LIKED     —— 我的喜欢（收藏）
 *   window.BZ_SONG_DATA —— 当前这首（song_player.js 也读它）
 *
 * 右栏是**三个 tab**（切换见 setTab），三块列表各司其职：
 *   猜你喜欢   —— 服务端渲染的推荐列表；点其中一首 = 把队列**整块换成这一块**（点中那首排到队首）
 *   我的喜欢   —— 收藏列表；点其中一首同样会把队列换成这一块（收藏变化时本文件就地重渲染它）
 *   待播放列表 —— 当前连播队列（#bz-playlist，本文件维护），决定"下一首是谁"
 * 三块列表的条目都带 data-play-sid；`data-bz-list` 标记"这块是可选来源列表"，
 * 点它里面的歌就按"整块换成队列"处理。所以找条目一律用 [data-play-sid]。
 *
 * 分工：本文件只决定"下一首是谁、什么时候换"，真正换歌（音频/封面/歌名/歌词）由
 * song_player.js 的 BZSongPlayer.load() 执行 —— 那边是唯一持有 audio 的地方。
 *
 * 另外它还保留了定时刷新「大家正在听」那块的老逻辑（refreshLive）。该模块已暂时下架、
 * 页面上取不到 #bz-live-box，所以这段会自动跳过（见 refreshLive 首行的守卫与初始化里的
 * elLiveBox 判断）；将来把那块加回来即可自动生效。走 /api/live，只读本地表。
 *
 * 为什么换歌要动地址栏（pushState）：原地换歌不刷新页面，但地址栏得跟着变，否则用户
 * 刷新会回到最开始那首、复制出去的链接也是错的。改了地址还要监听 popstate，不然浏览器
 * 后退键只会改地址、页面停在原地。
 */
(function () {
    'use strict';

    var LOOP_KEY = 'bz_loop';   // 循环开关的记忆（与播放器的其它记忆放在同一个命名空间）
    // 「下一首播放」在**非播放页**下点时，先把歌记在这里（见 static/js/song_menu.js），
    // 进播放页后由本文件补进队列（键名两边必须一致）
    var PENDING_KEY = 'bz_play_next';

    var queue = [];           // 待播放列表（连播队列）[{sid, title}]
    var likedSongs = [];      // 我的喜欢（收藏）[{sid, title}]
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

    // 右栏两块由本文件维护的列表 DOM（队列/收藏一变就要就地重渲染，所以引用留着）：
    //   #bz-playlist    —— 待播放列表（连播队列）
    //   #bz-likes-list  —— 我的喜欢（收藏）
    var elQueueList, elQueueEmpty, elQueueHint;
    var elLikesList, elLikesEmpty;
    // 「大家正在听」那块：模块已下架、页面上取不到元素，保留引用只为让旧轮询逻辑能自动跳过
    var elLiveBox, elLiveList, elLiveCount;
    var loggedIn = false;     // 由页面注入：空态那句提示只给登录用户看（未登录看登录引导）

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

    /* ---------- 右栏三块列表的渲染 ----------
     * 三块数据各自独立：
     *   queue      —— 待播放列表（连播队列），本文件维护，决定"下一首是谁"
     *   likedSongs —— 我的喜欢（收藏），收藏状态一变就重渲染这一块
     *   猜你喜欢    —— 服务端渲染的静态列表（推荐）；本文件只负责"点它时把队列换成它"
     * 列表 DOM 只是数据的显示，所以改完数据就整块重建列表，序号与高亮一起归位。
     */

    // 把一串 [{sid,title}] 渲染进某个 <ol>（三块列表共用同一套标记）
    function fillList(el, songs) {
        if (!el) return;
        el.innerHTML = '';
        for (var i = 0; i < songs.length; i++) {
            var item = songs[i];
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
            el.appendChild(li);
        }
        bindList(el);     // 元素都换了新的，点击事件要重新绑（只绑这个容器）
    }

    // 重画「待播放列表」：队列条目 + 空态/提示的显隐
    function renderList() {
        fillList(elQueueList, queue);
        if (elQueueList) elQueueList.classList.toggle('hidden', queue.length === 0);
        if (elQueueEmpty) elQueueEmpty.classList.toggle('hidden', queue.length > 0);
        if (elQueueHint) elQueueHint.classList.toggle('hidden', queue.length === 0);
        markCurrent(window.BZSongPlayer.sid());
    }

    // 重画「我的喜欢」（**刻意不动队列**：收藏一首不该把正在连播的列表整批换掉，
    // 那是「待播放列表」自己的事 —— 想按收藏连播，去那个 tab 点一首即可）
    function renderLikes() {
        fillList(elLikesList, likedSongs);
        if (elLikesList) elLikesList.classList.toggle('hidden', likedSongs.length === 0);
        if (elLikesEmpty) {
            elLikesEmpty.classList.toggle('hidden', !(loggedIn && likedSongs.length === 0));
        }
        markCurrent(window.BZSongPlayer.sid());
    }

    // 供 static/js/user.js 在收藏状态变化后调用（其它页面没有这些列表，不会调到）
    function syncLiked(liked, item) {
        if (!item || !item.sid) return;
        var i = -1;
        for (var k = 0; k < likedSongs.length; k++) {
            if (likedSongs[k].sid === item.sid) { i = k; break; }
        }
        if (liked) {
            if (i >= 0) likedSongs.splice(i, 1);
            // 最近收藏的排在前面（与后端 Favorite 的 ordering 一致）
            likedSongs.unshift({ sid: item.sid, title: item.title || '' });
        } else if (i >= 0) {
            likedSongs.splice(i, 1);
        }
        renderLikes();
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

    // 把一块"可选来源列表"（猜你喜欢 / 我的喜欢）整块换成连播队列。
    // **点中的那首排到队首**：队首恒等于"正在播放"（与进页面时的初始队列同一条规则），
    // 列表读起来就是"第 1 条我正在听、第 2 条接着播谁"。其余条目保持原顺序。
    function adoptList(listEl, headSid) {
        var items = listEl.querySelectorAll('[data-play-sid]');
        var list = Array.prototype.map.call(items, function (b) {
            return { sid: b.getAttribute('data-play-sid'), title: b.getAttribute('title') || '' };
        });
        var at = -1;
        for (var i = 0; i < list.length; i++) {
            if (list[i].sid === headSid) { at = i; break; }
        }
        if (at > 0) list.unshift(list.splice(at, 1)[0]);
        queue = list;
        index = -1;
        renderList();     // 「待播放列表」那一 tab 立刻跟着变
    }

    // 把一首歌插到"当前这首"的后面（全站右键菜单的「下一首播放」调它，见 song_menu.js）。
    // 队列里若已有同一首，先摘掉再插 —— "下一首播放"不该带出重复条目。
    // 返回 false = 没插（点的就是正在播的这首，插进去没意义）。
    function playNext(item) {
        if (!item || !item.sid) return false;
        if (item.sid === window.BZSongPlayer.sid()) return false;
        for (var i = queue.length - 1; i >= 0; i--) {
            if (queue[i].sid === item.sid) {
                queue.splice(i, 1);
                if (i < index) index -= 1;
            }
        }
        var at = index >= 0 ? index + 1 : 0;   // 当前这首不在队列里（-1）就放队首
        queue.splice(at, 0, { sid: item.sid, title: item.title || '' });
        renderList();
        return true;
    }

    // 点列表里的一首：
    //   已经在队列里 → 跳到那一首（连播的"当前位"跟着走）
    //   不在队列里   → **把点中的那一块整块换成队列**（点哪块就按哪块连播），再从那首开始
    function onItemClick(sid, el) {
        var i = indexOf(sid);
        if (i >= 0) {
            playAt(i);
            return;
        }
        var listEl = (el && el.closest) ? el.closest('[data-bz-list]') : null;
        if (!listEl) {           // 不在任何来源列表里（理论上走不到）：当场播这首，不改队列
            playSid(sid);
            return;
        }
        adoptList(listEl, sid);      // 点中的这首排到队首，见 adoptList
        i = indexOf(sid);
        if (i >= 0) playAt(i); else playSid(sid);
    }

    // root 省略 = 绑整页（页面初始化时用）；重渲染某一块后只绑那个容器，
    // 否则同一块每次都会被重复挂一次监听，点一下会触发两次。
    function bindList(root) {
        var items = (root || document).querySelectorAll('[data-play-sid]');
        Array.prototype.forEach.call(items, function (el) {
            el.addEventListener('click', function (e) {
                e.preventDefault();
                onItemClick(el.getAttribute('data-play-sid'), el);
            });
        });
    }

    /* ---------- 右栏三个 tab ----------
     * 高亮用 text-primary + 一条**内联**下边框：output.css 是编译产物、里面没有 daisyUI 的
     * tabs/tab 类，写那些类名不会生效（见 song.html 顶部同一处说明）。
     * 非选中 tab 也给一条透明下边框，避免切换时高度跳动。
     */
    function setTab(name) {
        var tabs = document.querySelectorAll('[data-bz-tab]');
        Array.prototype.forEach.call(tabs, function (b) {
            var on = b.getAttribute('data-bz-tab') === name;
            b.classList.toggle('text-primary', on);
            b.classList.toggle('opacity-60', !on);
            b.style.borderBottom = on ? '2px solid' : '2px solid transparent';
            b.style.marginBottom = '-1px';
        });
        var panels = document.querySelectorAll('[data-bz-panel]');
        Array.prototype.forEach.call(panels, function (p) {
            p.classList.toggle('hidden', p.getAttribute('data-bz-panel') !== name);
        });
    }

    function bindTabs() {
        var tabs = document.querySelectorAll('[data-bz-tab]');
        Array.prototype.forEach.call(tabs, function (b) {
            b.addEventListener('click', function (e) {
                e.preventDefault();
                setTab(b.getAttribute('data-bz-tab'));
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
        likedSongs = window.BZ_LIKED || [];
        loop = read(LOOP_KEY, false) === true;
        loggedIn = window.BZ_CURRENT_USER === true;

        // 右栏两块列表 + 「待播放列表」的空态/提示（收藏或队列一变就要就地重渲染）
        elQueueList = document.getElementById('bz-playlist');
        elQueueEmpty = document.getElementById('bz-playlist-empty');
        elQueueHint = document.getElementById('bz-playlist-hint');
        elLikesList = document.getElementById('bz-likes-list');
        elLikesEmpty = document.getElementById('bz-likes-empty');

        // 「大家正在听」区块：模块已下架，这里取不到元素（旧轮询逻辑因此自动跳过）
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
        bindList();                    // 一次绑全页：右栏三块列表的条目
        bindTabs();                    // 右栏三个 tab 的切换
        setTab('rec');                 // 初始停在「猜你喜欢」（与模板渲染的初始态一致）
        renderList();                  // 对齐「待播放列表」的初始显隐（模板已渲染，这里同一套规则再算一遍）
        renderLikes();                 // 同上，「我的喜欢」
        // 在别的页面右键"下一首播放"的歌：进播放页时补进队列（紧跟当前这首之后）。
        // 倒着插 —— 每次都插在"当前这首"后面，倒序才保持用户点选的先后顺序。
        var pending = read(PENDING_KEY, null);
        if (pending && pending.length) {
            write(PENDING_KEY, []);
            for (var p = pending.length - 1; p >= 0; p--) playNext(pending[p]);
        }
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

    /* ---------- 对外接口 ----------
     * 供 user.js（收藏后同步「我的喜欢」）与 song_menu.js（右键菜单的「下一首播放」）调用。
     * 其它页面没有这个对象，调用方都做了存在性判断。
     */
    window.BZPlaylist = { syncLiked: syncLiked, playNext: playNext };
})();
