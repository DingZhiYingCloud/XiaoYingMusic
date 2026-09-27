/* 用户相关的前端脚本：发送验证码 + 喜欢（心形）按钮
 *
 * 两个功能都做成"页面上有对应元素才生效"，所以同一个文件被登录页、注册页、播放页和
 * 各列表页共用即可，不必按页面判断该加载哪个（见 templates/template.html 的引用）。
 *
 * 不用 jQuery（全站脚本已原生化）。异步 POST 必须带 CSRF token —— 项目已启用
 * CsrfViewMiddleware，不带会被 403；token 优先读 <meta name="csrf-token">，
 * 回退到 csrftoken cookie。
 */
(function () {
    'use strict';

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

    /* ---------------- 发送验证码 ----------------
     * 按钮上用 data-* 描述怎么取值，登录页与注册页共用同一段逻辑：
     *   data-send-code  取值的选择器（如 input[name=ident]）；不写就用 data-value 的固定值
     *   data-value      固定值（注册第二步用：账号已经在隐藏字段里，页面上没有对应输入框）
     *   data-field      发过去用哪个字段名：email / phone / auto（按内容自动判断）
     *   data-kind       register（重发注册码）或 login（登录验证码）
     *   data-label      为空时的提示里怎么称呼这个输入框
     */
    function initSendCode() {
        var btn = document.querySelector('[data-send-code]');
        if (!btn) return;

        btn.addEventListener('click', function () {
            if (btn.disabled) return;
            var selector = btn.getAttribute('data-send-code');
            var input = selector ? document.querySelector(selector) : null;
            var value = input
                ? input.value.trim()
                : (btn.getAttribute('data-value') || '').trim();
            if (!value) {
                bzToast('请先填写' + (btn.getAttribute('data-label') || '邮箱或手机号'), 'warning');
                return;
            }

            var field = btn.getAttribute('data-field') || 'email';
            if (field === 'auto') {
                // 登录页只有一个"账号"输入框，得先判断它填的是邮箱还是手机号 ——
                // 发码接口只认这两种，系统账号没有可送达的通道。
                if (value.indexOf('@') > -1) {
                    field = 'email';
                } else if (/^1\d{10}$/.test(value)) {
                    field = 'phone';
                } else {
                    bzToast('验证码登录请填邮箱或手机号', 'warning');
                    return;
                }
            }

            var payload = { kind: btn.getAttribute('data-kind') || 'login' };
            payload[field] = value;

            btn.disabled = true;
            post('/api/uc/code', payload).then(function (res) {
                bzToast(res.msg || (res.ok ? '验证码已发送' : '发送失败'), res.ok ? 'success' : 'error');
                if (!res.ok) {
                    btn.disabled = false;
                    return;
                }
                // 平台侧是 60 秒冷却，这边跟着倒计时，免得用户白点几次都报"太频繁"
                var left = 60;
                btn.textContent = left + ' 秒后可重发';
                var timer = setInterval(function () {
                    left -= 1;
                    if (left <= 0) {
                        clearInterval(timer);
                        btn.textContent = '重新发送';
                        btn.disabled = false;
                    } else {
                        btn.textContent = left + ' 秒后可重发';
                    }
                }, 1000);
            }).catch(function () {
                btn.disabled = false;
                bzToast('网络异常，请稍后再试', 'error');
            });
        });
    }

    /* ---------------- 喜欢（心形） ----------------
     * 每个按钮带 data-fav-sid（必填）与 data-fav-title（可空）。
     * 未登录时后端返回 need_login，这里把用户带去登录页并记住回来。
     */
    function initFavorite() {
        var nodes = document.querySelectorAll('[data-fav-sid]');
        if (!nodes.length) return;

        Array.prototype.forEach.call(nodes, function (node) {
            node.addEventListener('click', function (e) {
                e.preventDefault();
                if (node.dataset.busy === '1') return;
                node.dataset.busy = '1';

                post('/api/favorite', {
                    sid: node.getAttribute('data-fav-sid'),
                    title: node.getAttribute('data-fav-title') || ''
                }).then(function (res) {
                    node.dataset.busy = '0';
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
                    paint(node, res.liked);
                    bzToast(res.liked ? '已加入我的喜欢' : '已取消喜欢', 'success');
                    // 播放页的连播列表当场跟着变（收藏完立刻能在列表里看到，不用刷新）。
                    // 其它页面没有这个接口（只有播放页加载 playlist.js），会自然跳过。
                    if (window.BZPlaylist) {
                        window.BZPlaylist.syncLiked(res.liked, {
                            sid: node.getAttribute('data-fav-sid'),
                            title: node.getAttribute('data-fav-title') || ''
                        });
                    }
                    // 「我的喜欢」页里取消后把整行拿走：列表本来就是"有哪些喜欢的歌"，
                    // 留着一个空心图标反而让人以为还在收藏里
                    if (!res.liked && node.hasAttribute('data-fav-remove')) {
                        var row = node.closest('li') || node.parentNode;
                        if (row) row.remove();
                        // 顺带把右上角的数量改掉。全取消光了就重新加载一次，让服务端渲染
                        // 空态（空态与提示文案都在模板的 else 分支里，前端造不出来）
                        var left = document.querySelectorAll('[data-fav-sid]').length;
                        var counter = document.querySelector('[data-fav-count]');
                        if (counter) counter.textContent = '共 ' + left + ' 首';
                        if (left === 0) location.reload();
                    }
                }).catch(function () {
                    node.dataset.busy = '0';
                    bzToast('网络异常，请稍后再试', 'error');
                });
            });
        });
    }

    /* 心形的两种状态。类名必须与模板里写的一致 —— Tailwind 是静态扫描模板文本生成
       output.css 的，模板里两个分支都出现过 text-error / opacity-60，运行时切换才有效。 */
    function paint(node, liked) {
        var icon = node.querySelector('i');
        if (icon) {
            icon.className = liked ? 'fa fa-heart text-error' : 'fa fa-heart-o opacity-60';
        }
        node.setAttribute('aria-pressed', liked ? 'true' : 'false');
    }

    document.addEventListener('DOMContentLoaded', function () {
        initSendCode();
        initFavorite();
    });
})();
