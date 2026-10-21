/* ============ 播放页「歌词 / 观看MV」切换（原生 DOM，不依赖第三方库） ============
 * 需求：只有源站**确实有 MV** 的歌才显示这个切换，没有的歌完全看不到这个按钮。
 *
 * 判定与取地址都在后端（见 Web/views/request.py 的 song_mv / mv_stream、
 * SpiderServices/Music_2t58/main.py 的 fetch_mv）：
 *   GET /api/mv/<sid>      → {has_mv, poster, stream}
 *   GET /mv/<sid>.mp4      → 302 到源站直链（https，浏览器可直连）
 * 本文件只负责"问一次、记住答案、按答案显示按钮、切换播放"。
 *
 * 几个刻意的取舍：
 *   ① 异步问，不阻塞首屏 —— 查 MV 要额外回源一次，塞进页面渲染会让每个播放页都慢一拍。
 *   ② 同一页里切歌不重复问（cache），连播切十首也只有第一次问需要回源。
 *   ③ 看 MV 时**暂停歌曲**：两个音轨同时出声没法听；切回歌词时恢复原进度，
 *      原来是播放状态就接着播（暂停状态就保持暂停，不擅自开始）。
 *   ④ 切歌时如果是 MV 模式，先退回歌词（新歌不一定有 MV），并重新问一次。
 */
(function () {
    'use strict';

    var switchBox, lrcBtn, mvBtn, geci, nofound, box, video, tip;

    var state = {
        sid: '',          // 当前问过的歌
        has: false,       // 当前这首歌有没有 MV
        poster: '',
        mode: 'lrc',      // lrc | mv
        lyricsEl: null,   // 进 MV 前显示的是歌词列表还是"暂无歌词"
        resumeAt: 0,      // 进 MV 时的歌曲进度
        wasPlaying: false
    };
    var cache = {};       // sid → 有没有 MV（内存缓存，切歌不必重复问后端）
    var asked = '';       // 最近一次发出去的查询（丢弃过期响应）

    function songAudio() { return document.getElementById('bz-audio'); }

    function currentSid() {
        if (window.BZSongPlayer && typeof window.BZSongPlayer.sid === 'function') {
            return window.BZSongPlayer.sid() || '';
        }
        return (window.BZ_SONG_DATA && window.BZ_SONG_DATA.sid) || '';
    }

    function showSwitch(on) {
        if (switchBox) switchBox.style.display = on ? '' : 'none';
    }

    // 两个按钮的高亮：只用模板里已经出现过的类名（output.css 是编译产物，不能临时新增类名）
    function setTab(which) {
        [[lrcBtn, 'lrc'], [mvBtn, 'mv']].forEach(function (pair) {
            var btn = pair[0];
            if (!btn) return;
            var on = pair[1] === which;
            btn.classList.toggle('text-primary', on);
            btn.classList.toggle('font-semibold', on);
            btn.classList.toggle('opacity-60', !on);
            btn.style.background = on
                ? 'color-mix(in oklab,var(--color-primary) 12%,transparent)' : '';
        });
    }

    function playVideo() {
        if (!video || !state.sid) return;
        if (tip) tip.classList.add('hidden');
        if (state.poster) video.poster = state.poster;
        // 每次都重设地址：直链会在后端重新解析（302），浏览器只跟一次，
        // 同一次播放里的拖动进度不会再走后端
        video.src = '/mv/' + encodeURIComponent(state.sid) + '.mp4';
        var p = video.play();
        if (p && typeof p.catch === 'function') p.catch(function () { /* 被自动播放策略拦下时用户手动点 */ });
    }

    function stopVideo() {
        if (!video) return;
        video.pause();
        video.removeAttribute('src');
        video.load();     // 释放上一首的缓冲，避免它继续后台下载
    }

    function enterMv() {
        if (!video || state.mode === 'mv') return;
        var audio = songAudio();
        state.resumeAt = audio ? audio.currentTime : 0;
        state.wasPlaying = !!(audio && !audio.paused);
        if (audio && !audio.paused) audio.pause();

        // 歌词区可能是 #play_geci（有歌词）或 #lrc_nofound（无歌词），记住当前那个，
        // 切回来时只把**它**放出来，别把两个同时显示
        state.lyricsEl = (nofound && !nofound.classList.contains('hidden')) ? nofound : geci;
        if (state.lyricsEl) state.lyricsEl.classList.add('hidden');

        if (box) box.classList.remove('hidden');
        state.mode = 'mv';
        setTab('mv');
        playVideo();
    }

    function exitMv(resume) {
        if (state.mode !== 'mv') return;
        stopVideo();
        if (box) box.classList.add('hidden');
        if (tip) tip.classList.add('hidden');
        if (state.lyricsEl) state.lyricsEl.classList.remove('hidden');
        state.lyricsEl = null;
        state.mode = 'lrc';
        setTab('lrc');

        var audio = songAudio();
        if (!audio) return;
        if (state.resumeAt > 0) {
            try { audio.currentTime = state.resumeAt; } catch (e) { /* 元数据没就绪时忽略 */ }
        }
        if (resume && state.wasPlaying) {
            var p = audio.play();
            if (p && typeof p.catch === 'function') p.catch(function () { /* 同上 */ });
        }
    }

    function apply(has, info) {
        state.has = !!has;
        state.poster = (info && info.poster) || '';
        showSwitch(state.has);
        if (has && state.mode === 'mv') playVideo();
    }

    function check(sid) {
        if (!sid || asked === sid) return;
        asked = sid;
        showSwitch(false);        // 查询期间先不露按钮，避免"闪一下又消失"
        if (cache.hasOwnProperty(sid)) {
            apply(cache[sid], {});
            return;
        }
        try {
            fetch('/api/mv/' + encodeURIComponent(sid), { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    var has = !!(d && d.has_mv);
                    cache[sid] = has;
                    // 只有"问的还是当前这首"才落地：慢响应回来时用户可能已经切歌了
                    if (asked === sid) apply(has, d || {});
                })
                .catch(function () { /* 查询失败就当没有 MV，不打扰用户 */ });
        } catch (e) { /* 老浏览器没有 fetch：静默降级，不显示切换 */ }
    }

    // 换歌（连播）：sid 变了就退回歌词并重新问
    function onSongChanged() {
        var sid = currentSid();
        if (!sid || sid === state.sid) return;
        state.sid = sid;
        if (state.mode === 'mv') exitMv(false);
        check(sid);
    }

    document.addEventListener('DOMContentLoaded', function () {
        switchBox = document.getElementById('bz-mv-switch');
        if (!switchBox) return;      // 不是播放页（或模板改版）：直接退出
        lrcBtn = switchBox.querySelector('[data-bz-mv-tab="lrc"]');
        mvBtn = switchBox.querySelector('[data-bz-mv-tab="mv"]');
        geci = document.getElementById('play_geci');
        nofound = document.getElementById('lrc_nofound');
        box = document.getElementById('bz-mv-box');
        video = document.getElementById('bz-mv-video');
        tip = document.getElementById('bz-mv-tip');

        if (lrcBtn) lrcBtn.addEventListener('click', function () { exitMv(true); });
        if (mvBtn) mvBtn.addEventListener('click', function () { enterMv(); });

        if (video) {
            video.addEventListener('error', function () {
                // 直链解析失败（源站那边没给地址）或网络断了：给一句人话，别让用户对着黑屏
                if (tip) tip.classList.remove('hidden');
            });
        }

        // 连播换歌时 loadedmetadata / timeupdate 都会来，用 sid 比较做去重（很便宜）
        var audio = songAudio();
        if (audio) {
            audio.addEventListener('loadedmetadata', onSongChanged);
            audio.addEventListener('timeupdate', onSongChanged);
        }

        // 看 MV 时空格应该控制视频，而不是把底下的歌曲又播起来
        document.addEventListener('keydown', function (e) {
            if (state.mode !== 'mv' || !video) return;
            if (e.key === ' ' || e.key === 'Spacebar') {
                e.preventDefault();
                e.stopPropagation();      // 拦掉 song_player.js 的空格快捷键
                if (video.paused) video.play(); else video.pause();
            }
        }, true);

        onSongChanged();
    });
})();
