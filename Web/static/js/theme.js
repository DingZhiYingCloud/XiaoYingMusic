/* ============ 全站主题切换（daisyUI data-theme）============
 * 实现方式参考小影 API（P:\XiaoYingAPI\API\static\js\site\theme.js）：
 * - THEMES 与 daisyUI 内置主题一一对应（input.css 已 themes: all）
 * - 面板结构由本文件生成：标题 → 搜索框 → 可滚动主题列表
 * - 点击任意 [data-theme-set] 元素即切换并写入 localStorage
 * - 防闪烁：<head> 内联脚本在样式加载前恢复主题（见 template.html）
 * - 默认主题由日期决定（节日主题 / 季节兜底）：服务端算好后经 template.html 的脚本
 *   挂到 window.BZ_DAY 上，本文件只读取，不重复日期逻辑
 *
 * 注意：本文件是 Tailwind 的扫描源之一（见 input.css 的 @source），
 *       下面用到的类名必须保持字面量，不能拼接，否则不会生成样式。
 */
(function () {
  'use strict';

  var KEY = 'bz_theme';

  // 今日主题（节日主题 / 季节兜底），由 template.html 的防闪烁脚本注入；
  // 取不到就退回 emerald（绿色，与站点 logo 一致）
  var DAY = window.BZ_DAY || {};
  var DEFAULT_THEME = DAY.theme || 'emerald';

  // [主题标识, 展示名]（daisyUI 全部内置主题）
  var THEMES = [
    ['light', '明亮'], ['dark', '暗黑'], ['cupcake', '纸杯蛋糕'],
    ['bumblebee', '大黄蜂'], ['emerald', '翡翠'], ['corporate', '商务'],
    ['synthwave', '合成波'], ['retro', '复古'], ['cyberpunk', '赛博朋克'],
    ['valentine', '情人节'], ['halloween', '万圣夜'], ['garden', '花园'],
    ['forest', '森林'], ['aqua', '水蓝'], ['lofi', '低保真'],
    ['pastel', '粉彩'], ['fantasy', '幻想'], ['wireframe', '线框'],
    ['black', '纯黑'], ['luxury', '奢华'], ['dracula', '德古拉'],
    ['cmyk', 'CMYK'], ['autumn', '秋日'], ['business', '商务夜'],
    ['acid', '酸性'], ['lemonade', '柠檬水'], ['night', '深夜'],
    ['coffee', '咖啡'], ['winter', '冬日'], ['dim', '微光'],
    ['nord', '北极'], ['sunset', '日落'], ['caramellatte', '焦糖拿铁'],
    ['abyss', '深渊'], ['silk', '丝绸'],
  ];

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function currentTheme() {
    var cur = document.documentElement.getAttribute('data-theme');
    if (cur) return cur;
    try { return localStorage.getItem(KEY) || DEFAULT_THEME; } catch (e) { return DEFAULT_THEME; }
  }

  function themeLabel(key) {
    var hit = DEFAULT_THEME;
    THEMES.forEach(function (pair) { if (pair[0] === key) hit = pair[1]; });
    return hit;
  }

  /* ---- 标签页图标跟随主题 ----
   * 标签页是独立文档，读不到页面的 CSS 变量，只能取当前主题色后生成 SVG data-URI 替换 <link>。
   * HTML 源里的 href 始终是固定色 /media/favicon.ico，搜索引擎抓到的仍是稳定图标。
   * 图形坐标与 common_html/logo_mark.html、favicon.ico 一致。 */
  var FAVICON_SHAPE =
    '<rect width="64" height="64" rx="17" fill="%P%"/>' +
    '<g fill="%F%">' +
    '<polygon points="24.5,20 52.9,14 52.9,22.5 24.5,28.5"/>' +
    '<rect x="24.5" y="20" width="4.4" height="25.5" rx="1"/>' +
    '<rect x="48.5" y="14" width="4.4" height="25.5" rx="1"/>' +
    '<ellipse cx="19" cy="45" rx="8" ry="6.2" transform="rotate(18 19 45)"/>' +
    '<ellipse cx="43" cy="39" rx="8" ry="6.2" transform="rotate(18 43 39)"/>' +
    '</g>';

  /* ---- oklch → hex 降级 ----
   * daisyUI 主题色全部是 oklch()，老浏览器（Chrome<111 / Safari<15.4 / Firefox<113）
   * 解析不了，SVG 里会回退成黑色方块，所以这里手动换算成 hex 兜底。
   * 判定方式用特性检测而不是 UA 版本号：更可靠，也自动覆盖未来可能回退支持的情况。 */
  function supportsOklch() {
    return !!(window.CSS && CSS.supports && CSS.supports('color', 'oklch(50% 0.1 150)'));
  }

  /* oklch(...) → #rrggbb；解析失败返回 null，调用方保留原值 */
  function oklchToHex(str) {
    var m = /oklch\(\s*([\d.]+)(%?)\s+([\d.]+)(%?)\s+([\d.]+)(deg|grad|rad|turn)?/i.exec(str);
    if (!m) return null;

    var L = parseFloat(m[1]);
    if (m[2]) L /= 100;                                   // 76.662% → 0.76662
    var C = parseFloat(m[3]);
    if (m[4]) C = C / 100 * 0.4;                          // CSS 规范：色度 100% = 0.4
    var H = parseFloat(m[5]);                             // 色相
    if (m[6] === 'rad') H = H * 180 / Math.PI;
    else if (m[6] === 'grad') H = H * 0.9;
    else if (m[6] === 'turn') H = H * 360;

    var hr = H * Math.PI / 180;
    var a = C * Math.cos(hr);
    var b = C * Math.sin(hr);

    // OKLab → LMS'（立方前）
    var l_ = L + 0.3963377774 * a + 0.2158037573 * b;
    var m_ = L - 0.1055613458 * a - 0.0638541728 * b;
    var s_ = L - 0.0894841775 * a - 1.2914855480 * b;

    var ll = l_ * l_ * l_;
    var mm = m_ * m_ * m_;
    var ss = s_ * s_ * s_;

    // LMS → 线性 sRGB
    var lin = [
      4.0767416621 * ll - 3.3077115913 * mm + 0.2309699292 * ss,
      -1.2684380046 * ll + 2.6097574011 * mm - 0.3413193965 * ss,
      -0.0041960863 * ll - 0.7034186147 * mm + 1.7076147010 * ss,
    ];

    // 线性 → sRGB gamma → 0-255
    return '#' + lin.map(function (v) {
      if (v < 0) v = 0;               // 超饱和主题色会算出负值，先夹住，否则 Math.pow → NaN
      else if (v > 1) v = 1;          // 超出色域的高亮同样夹住
      v = v <= 0.0031308 ? 12.92 * v : 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
      var n = Math.round(v * 255);
      return (n < 16 ? '0' : '') + n.toString(16);
    }).join('');
  }

  /* 读取主题变量；浏览器不支持 oklch 时换算成 hex */
  function themeColor(varName) {
    var v = (getComputedStyle(document.documentElement).getPropertyValue(varName) || '').trim();
    if (!v) return '';
    if (supportsOklch() || !/^oklch\(/i.test(v)) return v;   // 浏览器认识 oklch 就原样用
    return oklchToHex(v) || v;                               // 换不出来就保留原值，不更糟
  }

  function updateFavicon() {
    var link = document.getElementById('bz-favicon');
    if (!link) return;
    var primary = themeColor('--color-primary');
    var fg = themeColor('--color-primary-content');
    if (!primary || !fg) return;   // 取不到主题变量就保留固定色文件，不折腾
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">' +
      FAVICON_SHAPE.replace('%P%', primary).replace('%F%', fg) + '</svg>';
    link.href = 'data:image/svg+xml,' + encodeURIComponent(svg);
  }

  /* 移动端浏览器的地址栏 / 状态栏颜色也跟着主题走。
   * 模板里的静态值是 emerald 主色，只在 JS 没跑起来时兜底。 */
  function updateThemeColor() {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) return;
    var primary = themeColor('--color-primary');
    if (primary) meta.setAttribute('content', primary);
  }

  /* 跟随主题变化的浏览器级资源，统一在这里更新 */
  function syncThemeAssets() {
    updateFavicon();
    updateThemeColor();
  }

  function applyTheme(name, persist) {
    if (!name) return;
    document.documentElement.setAttribute('data-theme', name);
    if (persist !== false) {
      try { localStorage.setItem(KEY, name); } catch (e) { /* 隐私模式等忽略 */ }
    }
    markActive();
    updateCurrentText();
    // 延迟一帧再读变量，避免读到切换前的旧值
    setTimeout(syncThemeAssets, 0);
  }

  /* 高亮当前项 + 显示勾选 */
  function markActive() {
    var cur = currentTheme();
    document.querySelectorAll('[data-theme-set]').forEach(function (btn) {
      var on = btn.dataset.themeSet === cur;
      btn.classList.toggle('text-primary', on);
      btn.classList.toggle('font-semibold', on);
      var check = btn.querySelector('[data-theme-check]');
      if (check) check.classList.toggle('hidden', !on);
    });
  }

  function updateCurrentText() {
    var label = themeLabel(currentTheme());
    document.querySelectorAll('[data-theme-current]').forEach(function (e) {
      e.textContent = label;
    });
  }

  /* 行内实时色卡：在该主题作用域内取它的 CSS 变量 */
  function previewDots(theme) {
    var box = el('span', 'inline-flex shrink-0 items-center gap-0.5 pr-1');
    box.setAttribute('data-theme', theme);
    [['--color-primary', 'h-3 w-3 rounded-full'],
     ['--color-secondary', 'h-3 w-3 rounded-full'],
     ['--color-accent', 'h-3 w-3 rounded-full']].forEach(function (c) {
      var dot = el('span', c[1]);
      dot.style.background = 'var(' + c[0] + ')';
      dot.style.boxShadow = '0 0 0 1px rgb(0 0 0 / 0.08)';
      box.appendChild(dot);
    });
    return box;
  }

  /* 为每个 [data-theme-menu] 面板生成：标题 → 搜索框 → 主题列表 */
  function renderMenus() {
    document.querySelectorAll('[data-theme-menu]').forEach(function (panel) {
      panel.innerHTML = '';

      var head = el('div', 'flex items-center justify-between px-2 pb-1.5 pt-0.5');
      head.appendChild(el('span', 'text-xs font-semibold', '选择主题'));
      var cur = el('span', 'badge badge-ghost badge-xs max-w-24 truncate');
      cur.dataset.themeCurrent = '';
      cur.textContent = themeLabel(currentTheme());
      head.appendChild(cur);
      panel.appendChild(head);

      var input = el('input', 'input input-sm w-full');
      input.type = 'text';
      input.name = 'bz-theme-filter';
      input.placeholder = '搜索主题…';
      input.setAttribute('aria-label', '搜索主题');
      panel.appendChild(input);

      var ul = el('ul',
        'menu mt-1.5 w-full min-h-0 flex-1 flex-nowrap gap-0.5 overflow-y-auto p-0 text-sm ' +
        '[scrollbar-width:none] [&::-webkit-scrollbar]:hidden');
      THEMES.forEach(function (pair) {
        var li = document.createElement('li');
        li.dataset.themeItem = pair[0];

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'flex h-8 w-full items-center gap-1.5 rounded-lg px-2 text-left hover:bg-base-200/60';
        btn.dataset.themeSet = pair[0];

        btn.appendChild(previewDots(pair[0]));
        var name = el('span', 'min-w-0 flex-1 truncate', pair[1]);
        name.title = pair[1];
        btn.appendChild(name);

        var check = el('i', 'fa fa-check hidden shrink-0 text-xs text-primary');
        check.dataset.themeCheck = '';
        btn.appendChild(check);

        li.appendChild(btn);
        ul.appendChild(li);
      });
      panel.appendChild(ul);

      // 关键词过滤
      input.addEventListener('input', function () {
        var q = input.value.trim().toLowerCase();
        ul.querySelectorAll('[data-theme-item]').forEach(function (li) {
          li.classList.toggle('hidden', q !== '' && li.textContent.toLowerCase().indexOf(q) === -1);
        });
      });
    });
  }

  // 点击切换（事件委托，面板重绘后依然有效）
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-theme-set]');
    if (!btn) return;
    applyTheme(btn.dataset.themeSet);
    // 节日当天：记下「用户已手动改过」，当天不再被强制拉回节日主题。
    // 非节日时该函数内部直接返回，不产生任何标记。
    if (window.BZ_markUserTheme) window.BZ_markUserTheme();
  });

  document.addEventListener('DOMContentLoaded', function () {
    renderMenus();
    markActive();
    updateCurrentText();
    syncThemeAssets();
  });
})();
