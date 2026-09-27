/* ============ 歌曲详情页播放器（原生 audio + 原生 DOM，不依赖第三方库） ============
 * 背景：原全站底部悬浮播放条已移除，音乐只在歌曲详情页播放。
 * 功能：播放/暂停、进度拖拽、时间显示、音量与静音、倍速、歌词同步（window.BZLrc）、
 *       断点续播（刷新前正在播放则自动从断点继续，暂停状态则点击播放时从断点继续）、
 *       开始播放上报一次（「大家正在听」的数据源，见 reportStart）、
 *       整首播完上报一次（首页「今日热听榜」的数据源，见 reportPlayed）。
 * 连播：本文件只管"一首歌怎么放"；队列、上一首/下一首、循环开关、地址栏同步都在
 *       playlist.js —— 它用 load() 换歌、用 onEnded() 注册"这首放完了"的回调。
 * 数据：由页面提供 window.BZ_SONG_DATA（sid / name / cover / play_url / lyrics）。
 */
(function () {
    'use strict';

    var KEY = {
        progress: 'bz_song_progress',  // {sid, time} 上次播放进度
        playing: 'bz_song_playing',    // 上次离开时是否正在播放
        rate: 'bz_song_rate',          // 倍速
        volume: 'bz_song_volume',      // 音量 0-100
        muted: 'bz_song_muted'         // 静音
    };
    var RATES = [1, 1.25, 1.5, 2];

    function read(key, def) {
        try {
            var v = JSON.parse(localStorage.getItem(key));
            return (v === null || v === undefined) ? def : v;
        } catch (e) { return def; }
    }
    function write(key, val) {
        try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) { /* 隐私模式等忽略 */ }
    }
    function fmtTime(sec) {
        if (!isFinite(sec) || sec < 0) sec = 0;
        var m = Math.floor(sec / 60), s = Math.floor(sec % 60);
        return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
    }

    var data = {};   // 歌曲数据，init 时从 window.BZ_SONG_DATA 读取（避免受脚本加载顺序影响）
    var state = {
        rate: read(KEY.rate, 1),
        volume: read(KEY.volume, 80),
        muted: read(KEY.muted, false),
        playing: false,
        curTime: 0,
        pendingSeek: null,   // 待恢复的播放进度（秒）
        autoResume: false,   // 自动续播中（被浏览器拦截时用于兜底提示）
        lrcStarted: false
    };
    var audio = null;
    // DOM 引用：init 时一次性取好，播放过程中不再查 DOM
    var elPlayBtn, elPlayIcon, elRateBtn, elMuteBtn, elMuteIcon;
    var elProgBar, elVolValue, elCurTime, elDuration, elCover, elStateSpan;
    var lastSave = 0;
    var endedCb = null;   // 整首播完后的回调（由 playlist.js 注册，见文件头"连播"说明）
    var startedSid = '';  // 已上报过"开始播放"的歌：同一首只报一次，暂停续播不重复发

    /* ---------- 渲染 ---------- */
    // 进度条/音量条的填充一律用 transform: scaleX 表示，而不是改 width：
    // width 会触发样式重算与布局，scaleX 只在合成层做变换 —— 进度条每 250ms 更新一次，
    // 用 scaleX 可以完全避开重排（父元素 .jp-progress 的宽度不变，拖拽取的也是它的 rect）。
    function setBarScale(el, ratio) {
        if (!el) return;
        if (!(ratio >= 0 && ratio <= 1)) ratio = 0;   // NaN / 越界一律归 0
        el.style.transform = 'scaleX(' + ratio + ')';
    }
    function renderPlayBtn() {
        if (elPlayIcon) elPlayIcon.className = state.playing ? 'fa fa-pause' : 'fa fa-play';
        if (elPlayBtn) elPlayBtn.title = state.playing ? '暂停' : '播放';
    }
    function renderRateBtn() {
        if (!elRateBtn) return;
        elRateBtn.textContent = state.rate === 1 ? '1x' : state.rate + 'x';
        elRateBtn.classList.toggle('on', state.rate !== 1);   // 非 1x 高亮，提示已开启倍速
    }
    function renderVolBtn() {
        var silent = state.muted || state.volume === 0;
        if (elMuteIcon) elMuteIcon.className = silent ? 'fa fa-volume-off' : 'fa fa-volume-up';
        if (elMuteBtn) elMuteBtn.title = silent ? '取消静音' : '静音';
        setBarScale(elVolValue, state.muted ? 0 : state.volume / 100);
    }
    function renderProgress() {
        var d = audio ? audio.duration : 0;
        setBarScale(elProgBar, (isFinite(d) && d > 0) ? (state.curTime / d) : 0);
        if (elCurTime) elCurTime.textContent = fmtTime(state.curTime);
        if (elDuration) elDuration.textContent = fmtTime(isFinite(d) ? d : 0);
    }
    // 封面旋转 + 播放状态文字
    function renderPageState() {
        if (elStateSpan) {
            elStateSpan.textContent = state.playing ? '播放中' : '已暂停';
            elStateSpan.classList.toggle('play', state.playing);
        }
        if (elCover) elCover.style.animationPlayState = state.playing ? 'running' : 'paused';
    }
    function renderAll() {
        renderPlayBtn();
        renderRateBtn();
        renderVolBtn();
        renderProgress();
        renderPageState();
    }

    /* ---------- 进度持久化（断点续播用） ---------- */
    function saveProgress() {
        if (!data.sid || !(state.curTime > 0)) return;   // 未真正播放时不覆盖已有记录
        write(KEY.progress, { sid: data.sid, time: state.curTime });
    }

    /* ---------- 歌词同步 ---------- */
    function startLyrics() {
        var lrc = window.BZLrc;
        if (state.lrcStarted || !lrc) return;
        state.lrcStarted = true;
        if (data.lyrics) {
            lrc.start(data.lyrics, function () { return state.curTime; });
        } else {
            // 无歌词：隐藏歌词列表，展示"暂无歌词"提示
            var geci = document.getElementById('play_geci');
            var nofound = document.getElementById('lrc_nofound');
            if (geci) geci.classList.add('hidden');
            if (nofound) nofound.classList.remove('hidden');
        }
    }

    /* ---------- 播放计数（首页「今日热听榜」的数据源） ---------- */
    // 只在**整首播完**时上报一次，所以后端记的是"听完次数"而不是"点开次数"。
    // 失败一律吞掉：记不上只是一次计数丢了，绝不能让正在听歌的页面出任何异常
    // （所以这里连 toast 都不弹）。
    function reportPlayed() {
        if (!data.sid) return;
        try {
            var body = new URLSearchParams();
            body.append('sid', data.sid);
            body.append('name', data.name || '');
            body.append('artists', data.artists || '');
            fetch('/api/play/ended', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: body.toString(),
                keepalive: true
            }).catch(function () { /* 忽略 */ });
        } catch (e) { /* 老浏览器没有 fetch / URLSearchParams，同样忽略 */ }
    }

    /* ---------- 开始播放上报（「大家正在听」的数据源） ---------- */
    // 按下播放时记一次"这首歌此刻正在被听"。同一首只报一次（暂停再播不重复发），
    // 换到下一首会自动再报。失败一律吞掉，理由同 reportPlayed。
    function reportStart() {
        if (!data.sid || startedSid === data.sid) return;
        startedSid = data.sid;
        try {
            var body = new URLSearchParams();
            body.append('sid', data.sid);
            fetch('/api/play/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: body.toString(),
                keepalive: true
            }).catch(function () { /* 忽略 */ });
        } catch (e) { /* 同上 */ }
    }

    /* ---------- 换歌（连播用，由 playlist.js 调用）---------- */
    // 页面上那些跟"当前是哪首歌"有关的东西（歌名、歌手、封面、浏览器标题）都要跟着换，
    // 否则连播几首之后，页面显示的还停在最开始那一首上。
    function renderSongInfo(song) {
        var elName = document.querySelector('[data-bz-name]');
        if (elName) {
            elName.textContent = song.name || '';
            elName.title = song.name || '';
        }
        var elSinger = document.querySelector('[data-bz-singer]');
        if (elSinger) {
            elSinger.textContent = song.artists || '';
            elSinger.title = song.artists || '';
            elSinger.href = song.singer_url || '#';
        }
        if (elCover && song.cover) {
            elCover.src = song.cover;
            elCover.alt = song.name || '';
        }
        // 浏览器标题：格式与模板里的 block title 保持一致（页面里由 {{ SITE_NAME_ALT }} 拼）
        if (window.BZ_SITE_ALT) {
            document.title = (song.artists ? song.artists + ' - ' : '') + (song.name || '') +
                '免费在线听完整版 - ' + window.BZ_SITE_ALT;
        }
    }

    function load(song) {
        if (!audio || !song || !song.play_url) return;

        // 换掉当前歌曲数据：saveProgress / reportPlayed / 歌词都读它
        data = {
            sid: song.sid || '',
            name: song.name || '',
            artists: song.artists || '',
            cover: song.cover || '',
            play_url: song.play_url || '',
            lyrics: song.lyrics || ''
        };
        // 顺手把页面那个全局变量也对齐：它本来是页面初始化时的输入，但换歌后若不同步，
        // 就成了"同一份数据两个地方存、其中一个永远是旧的" —— 以后谁读它谁踩坑。
        window.BZ_SONG_DATA = data;

        // 复位上一首残留的状态（进度、待恢复进度、歌词开关）
        state.curTime = 0;
        state.pendingSeek = null;
        state.lrcStarted = false;
        if (window.BZLrc) window.BZLrc.stop();

        // 换源后必须 load()：单纯改 src 不会让一个已 ended 的 audio 重新加载
        audio.src = data.play_url;
        audio.load();
        audio.playbackRate = state.rate;

        renderSongInfo(song);
        renderProgress();
    }

    /* ---------- 播放控制 ---------- */
    // 应用待恢复进度：媒体元数据就绪后再 seek，避免设置无效
    function applyPendingSeek() {
        if (state.pendingSeek === null || !audio) return;
        var t = state.pendingSeek;
        state.pendingSeek = null;
        var doSeek = function () {
            var d = audio.duration;
            if (isFinite(d) && d > 0 && t >= d - 3) return;   // 已接近结尾，从头播放
            try { audio.currentTime = t; } catch (e) { /* 忽略 */ }
        };
        if (audio.readyState >= 1) doSeek();
        else audio.addEventListener('loadedmetadata', doSeek, { once: true });
    }

    function play() {
        if (!audio) return;
        // 没有直链时（爬虫抓取失败，模板照常渲染播放按钮）直接给提示：
        // 否则 audio.play() 抛的是 NotSupportedError，下面的 catch 只认 NotAllowedError，
        // 用户点了按钮毫无反应、也没有任何解释。
        if (!audio.src) { bzToast('播放链接暂不可用，请刷新页面重试', 'error'); return; }
        applyPendingSeek();
        var p = audio.play();
        if (p && typeof p.catch === 'function') {
            p.catch(function (e) {
                // 浏览器自动播放策略拦截：切回暂停态并提示（点击播放按钮即可继续）
                if (e && e.name === 'NotAllowedError' && state.autoResume) {
                    state.autoResume = false;
                    state.playing = false;
                    write(KEY.playing, false);
                    renderAll();
                    bzToast('浏览器拦截了自动播放，点击播放按钮继续', 'warning');
                }
            });
        }
    }
    function pause() { if (audio) audio.pause(); }
    function toggle() { if (!audio) return; if (audio.paused) play(); else pause(); }

    /* ---------- 拖动条（进度 / 音量通用，支持鼠标与触摸） ---------- */
    function bindDrag(bar, onRatio) {
        if (!bar) return;
        var dragging = false;
        function ratioOf(clientX) {
            var r = bar.getBoundingClientRect();
            if (!r.width) return 0;
            return Math.max(0, Math.min(1, (clientX - r.left) / r.width));
        }
        bar.addEventListener('pointerdown', function (e) {
            dragging = true;
            // 指针捕获：手指/鼠标拖出条子范围后仍能收到 pointermove 与 pointerup
            if (bar.setPointerCapture) {
                try { bar.setPointerCapture(e.pointerId); } catch (err) { /* 忽略 */ }
            }
            onRatio(ratioOf(e.clientX));
            e.preventDefault();
        });
        bar.addEventListener('pointermove', function (e) {
            if (dragging) onRatio(ratioOf(e.clientX));
        });
        bar.addEventListener('pointerup', function () { dragging = false; });
        bar.addEventListener('pointercancel', function () { dragging = false; });
    }

    /* ---------- 初始化 ---------- */
    document.addEventListener('DOMContentLoaded', function () {
        // DOM 就绪时页面内联脚本已执行完，此处读取歌曲数据最可靠
        data = window.BZ_SONG_DATA || {};
        if (!data.sid) return;
        audio = document.getElementById('bz-audio');
        if (!audio) return;

        elPlayBtn = document.getElementById('bz-btn-play');
        elRateBtn = document.getElementById('bz-btn-rate');
        elMuteBtn = document.getElementById('bz-btn-mute');
        elPlayIcon = elPlayBtn ? elPlayBtn.querySelector('i') : null;
        elMuteIcon = elMuteBtn ? elMuteBtn.querySelector('i') : null;
        elProgBar = document.querySelector('.bz-pp-prog .jp-play-bar');
        elVolValue = document.querySelector('.bz-pp-vol .jp-volume-bar-value');
        elCurTime = document.querySelector('.bz-pp-time .jp-current-time');
        elDuration = document.querySelector('.bz-pp-time .jp-duration');
        elCover = document.getElementById('mcover');
        var coverBox = elCover ? elCover.closest('.djpic') : null;
        elStateSpan = coverBox ? coverBox.querySelector('.state span') : null;

        // 音频源与初始状态（音量/静音/倍速均沿用上次设置）
        if (data.play_url) audio.src = data.play_url;
        audio.volume = Math.max(0, Math.min(1, state.volume / 100));
        audio.muted = !!state.muted;
        audio.playbackRate = state.rate;

        audio.addEventListener('timeupdate', function () {
            state.curTime = audio.currentTime;
            renderProgress();
            var now = Date.now();
            if (now - lastSave > 5000) { saveProgress(); lastSave = now; }
        });
        audio.addEventListener('loadedmetadata', renderProgress);
        audio.addEventListener('play', function () {
            state.playing = true;
            state.autoResume = false;
            write(KEY.playing, true);   // 标记"正在播放"，供刷新后自动续播判定
            renderAll();
            startLyrics();
            reportStart();   // 「大家正在听」：这首歌此刻开始被听了
        });
        audio.addEventListener('pause', function () {
            state.playing = false;
            write(KEY.playing, false);
            saveProgress();
            lastSave = Date.now();
            renderAll();
        });
        audio.addEventListener('ended', function () {
            state.playing = false;
            state.curTime = 0;
            write(KEY.playing, false);
            write(KEY.progress, { sid: data.sid, time: 0 });   // 播完清除断点，下次从头播
            renderAll();
            // 整首播完 → 给首页「今日热听榜」记一次。放在歌词复位之前：
            // 后面那些复位万一抛错，也不该把这次计数一起带走。
            reportPlayed();
            // 停掉歌词同步，并把"已启动"标记复位：
            // BZLrc.stop() 会清掉定时器与歌词数组，而 startLyrics() 用 lrcStarted 做一次性开关，
            // 不复位的话播完再点播放会直接 return —— 歌词高亮与滚动从此永久静止。
            if (window.BZLrc) {
                window.BZLrc.stop();
                state.lrcStarted = false;
            }
            // 连播：交给 playlist.js 决定下一首（队列空了且没开循环时，它会就地停住）
            if (typeof endedCb === 'function') endedCb();
        });
        audio.addEventListener('error', function () {
            bzToast('播放链接失效，请刷新页面重试', 'error');
        });

        // 离开页面（刷新/关闭/切走）立即保存进度，保证续播位置精准
        window.addEventListener('pagehide', saveProgress);

        /* --- 控制条交互 --- */
        if (elPlayBtn) elPlayBtn.addEventListener('click', function (e) { e.preventDefault(); toggle(); });
        if (elRateBtn) elRateBtn.addEventListener('click', function (e) {
            e.preventDefault();
            var i = RATES.indexOf(state.rate);
            state.rate = RATES[i < 0 ? 0 : (i + 1) % RATES.length];
            audio.playbackRate = state.rate;
            write(KEY.rate, state.rate);
            renderRateBtn();
            bzToast('播放速度 ' + (state.rate === 1 ? '1x' : state.rate + 'x'), 'info');
        });
        if (elMuteBtn) elMuteBtn.addEventListener('click', function (e) {
            e.preventDefault();
            state.muted = !state.muted;
            audio.muted = state.muted;
            write(KEY.muted, state.muted);
            renderVolBtn();
        });
        // 进度拖动：按比例 seek（媒体未就绪时忽略）
        bindDrag(document.querySelector('.bz-pp-prog .jp-progress'), function (r) {
            var d = audio.duration;
            if (!isFinite(d) || d <= 0) return;
            state.pendingSeek = null;   // 手动拖走后不再恢复旧断点
            audio.currentTime = r * d;
            state.curTime = audio.currentTime;
            renderProgress();
        });
        // 音量拖动：按比例调整（音量大于 0 时自动解除静音）
        bindDrag(document.querySelector('.bz-pp-vol .jp-volume-bar'), function (r) {
            state.volume = Math.round(r * 100);
            if (state.muted && state.volume > 0) state.muted = false;
            audio.muted = state.muted;
            audio.volume = state.volume / 100;
            write(KEY.volume, state.volume);
            write(KEY.muted, state.muted);
            renderVolBtn();
        });

        // 恢复上次进度：同曲时点击播放从断点继续；若刷新前正在播放则自动续播
        var prog = read(KEY.progress, null);
        if (prog && prog.sid === data.sid && prog.time > 3) {
            state.pendingSeek = prog.time;
            state.curTime = prog.time;
            renderProgress();
            if (read(KEY.playing, false) === true) {
                state.autoResume = true;
                play();
            }
        }

        renderAll();
    });

    /* ---------- 对外接口（play.js 歌词跳转 + playlist.js 连播） ---------- */
    window.BZSongPlayer = {
        seek: function (t) {
            if (!audio || !isFinite(t) || t < 0) return;
            try {
                audio.currentTime = t;
                state.curTime = t;
                renderProgress();
            } catch (e) { /* 忽略 */ }
        },
        // 换一首歌（音频、封面、歌名、歌词一起换），换完由调用方决定要不要立即播
        load: function (song) { load(song); },
        play: play,
        pause: pause,
        // 注册"整首播完"的回调，连播靠它触发下一首
        onEnded: function (cb) { endedCb = cb; },
        // 当前正在播的歌曲 id（playlist.js 用来对齐队列下标）
        sid: function () { return data.sid || ''; }
    };
})();
