// ===== 歌词同步滚动（原生 JS，改造自源站 jQuery 插件）=====
// 原版用 marginTop 负值强制滚动歌词列表，与用户手动滚动冲突（播放过的歌词无法回看）；
// 现改为容器 scrollTop 滚动 + 用户滚动时暂停自动跟随（停止滚动 3 秒后恢复），
// 并为每句歌词注入 data-t 时间戳，支持点击歌词跳转播放到对应时间。
//
// 对外只暴露 window.BZLrc（song_player.js 用它 start/stop），本文件不依赖任何第三方库。
(function () {
    'use strict';

    var geciTimer = null;

    // 监听用户滚动歌词区（滚轮/触摸），滚动期间暂停自动跟随
    function bindUserScroll(box) {
        if (box.__lrcBound) return;   // 同一容器只绑一次
        box.__lrcBound = true;
        var mark = function () {
            LRC.userScrolling = true;
            clearTimeout(geciTimer);
            geciTimer = setTimeout(function () { LRC.userScrolling = false; }, 3000);
        };
        box.addEventListener('wheel', mark, { passive: true });
        box.addEventListener('touchstart', mark, { passive: true });
        box.addEventListener('touchmove', mark, { passive: true });
    }

    var LRC = {
        handle: null, list: [], callback: null, interval: 0.3,
        hoverClass: 'hover', hoverTop: 34,
        pivot: -1, userScrolling: false,
        // start 时取好并缓存的 DOM 引用；els 是刚重建的 li 实时集合，索引与 list 对齐
        box: null, ul: null, els: null,

        // 解析 lrc 文本并渲染歌词；cb 为回调，返回当前播放秒数
        start: function (text, cb) {
            if (typeof text !== 'string' || text.length < 1 || typeof cb !== 'function') return;
            this.stop();
            this.callback = cb;
            this.list = [];
            var re = /^[^\[]*((?:\s*\[\d+:\d+(?:\.\d+)?\])+)([\s\S]*)$/;
            var reT = /\[(\d+):((?:\d+)(?:\.\d+)?)\]/g;
            var lines = text.split('\n');
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].replace(/^\s+|\s+$/g, '');
                if (!line) continue;
                var m = re.exec(line);
                if (!m) continue;
                var tm;
                reT.lastIndex = 0;
                while ((tm = reT.exec(m[1]))) {
                    this.list.push([parseFloat(tm[1]) * 60 + parseFloat(tm[2]), m[2]]);
                }
            }

            var ul = document.getElementById('lrc_list');
            var box = document.getElementById('play_geci');
            var nofound = document.getElementById('lrc_nofound');
            if (!ul || !box) return;            // 结构不匹配时静默退出，绝不拖累播放
            this.ul = ul;
            this.box = box;

            if (this.list.length === 0) {       // 文本里没有有效时间标签 → 按"无歌词"处理
                this.els = null;
                ul.classList.add('hidden');
                box.classList.add('hidden');
                if (nofound) nofound.classList.remove('hidden');
                return;
            }

            this.list.sort(function (x, y) { return x[0] - y[0]; });
            if (this.list[0][0] >= 0.1) this.list.unshift([this.list[0][0] - 0.1, '']);
            this.list.push([this.list[this.list.length - 1][0] + 1, '']);
            // 先拼进 DocumentFragment 再一次性挂上：只触发一次重排。
            // 歌词正文一律走 textContent，**不能**拼 HTML 字符串后赋给 innerHTML ——
            // 歌词来自第三方接口（js.eev3.com/lrc.php），属于不可信内容，
            // 里面若带 <img onerror=...> 之类的标签就会被当 HTML 解析执行（DOM XSS）。
            var frag = document.createDocumentFragment();
            for (var k = 0; k < this.list.length; k++) {
                var li = document.createElement('li');
                li.dataset.t = this.list[k][0];   // data-t 时间戳，供点击跳转
                li.textContent = this.list[k][1];
                frag.appendChild(li);
            }
            ul.textContent = '';                  // 清掉上一轮渲染的歌词
            ul.appendChild(frag);
            this.els = ul.children;
            ul.classList.remove('hidden');
            box.classList.remove('hidden');
            if (nofound) nofound.classList.add('hidden');
            this.pivot = -1;
            bindUserScroll(box);
            // 用函数形式而不是字符串形式：字符串会被当代码 eval，等价于隐式 eval
            this.handle = setInterval(function () { LRC.jump(LRC.callback()); }, this.interval * 1000);
        },

        // 根据当前播放秒数滚动并高亮对应歌词
        jump: function (e) {
            if (typeof this.handle !== 'number' || typeof e !== 'number' || this.list.length < 1) return this.stop();
            if (e < 0) e = 0;
            e += 0.2 + this.interval;
            // 二分查找当前时间对应的歌词索引
            var lo = 0, hi = this.list.length - 1, pivot = 0;
            while (lo <= hi) {
                var mid = (lo + hi) >> 1;
                if (this.list[mid][0] <= e) { pivot = mid; lo = mid + 1; }
                else hi = mid - 1;
            }
            var prev = this.pivot;
            if (pivot === prev) return;         // 没换句就直接返回（每 300ms 一次的调用绝大多数走这里）
            this.pivot = pivot;
            // 只改"上一句 + 当前句"两个节点：不必像原来那样把全部 li 过一遍 removeClass
            var els = this.els;
            if (els) {
                if (prev >= 0 && els[prev]) els[prev].classList.remove(this.hoverClass);
                if (els[pivot]) els[pivot].classList.add(this.hoverClass);
            }
            if (this.userScrolling) return;     // 用户手动滚动查看时暂停自动滚动
            var cur = els && els[pivot];
            if (!cur || !this.box || !this.ul) return;
            // 读 offsetTop 而不是 getBoundingClientRect/$.offset()：后两者要算元素几何，
            // 而 li 与 ul 的 offsetParent 相同，直接相减得到的相对位置一致。
            var target = cur.offsetTop - this.ul.offsetTop - this.hoverTop;
            if (target < 0) target = 0;
            this.box.scrollTop = target;
        },

        stop: function () {
            if (typeof this.handle === 'number') clearInterval(this.handle);
            this.handle = this.callback = null;
            this.pivot = -1;
            this.list = [];
            this.els = null;
        }
    };

    // 点击歌词跳转播放到对应时间（播放由歌曲页播放器 BZSongPlayer 接管）
    // 事件委托挂在 document 上：歌词 li 是 start() 时动态重建的，直接绑在 li 上会随重建失效
    document.addEventListener('click', function (e) {
        var li = e.target && e.target.closest ? e.target.closest('#lrc_list li') : null;
        if (!li) return;
        var t = parseFloat(li.getAttribute('data-t'));
        if (!isNaN(t) && t >= 0 && window.BZSongPlayer) {
            LRC.userScrolling = false;
            window.BZSongPlayer.seek(t);
        }
    });

    window.BZLrc = LRC;
})();
