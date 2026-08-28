# 通用反爬框架

不同站点的反爬机制各异,本框架提供通用处理流程,新站点先探测再适配。

## 爬虫类基础骨架(每个新站点照此结构)

```python
import os
import requests
from lxml import etree
from dotenv import load_dotenv

load_dotenv()


class MovieXxxSpider:
    """xxx.com 站点爬虫

    反爬说明:[在此填写探测结论,如:需要PHPSESSID / 无反爬 / 需要登录等]
    凭证配置:在 .env 中设置 MOVIE_XXX_PHPSESSID(或对应变量)
    """

    HOME_URL = 'https://www.xxx.com/'

    HEADERS = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/131.0.0.0 Safari/537.36'
        ),
        'Referer': 'https://www.xxx.com/',
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        # 注入反爬凭证(按需,从 .env 读取)
        cookie_val = os.getenv('MOVIE_XXX_PHPSESSID', '')
        if cookie_val:
            self.session.cookies.set('PHPSESSID', cookie_val)

    def _get_html(self, url):
        """获取页面 HTML,自动处理编码与人机验证"""
        resp = self.session.get(url, timeout=10)
        resp.raise_for_status()
        # 编码处理:无声明或 ISO-8859-1 时用 chardet 检测
        if not resp.encoding or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding
        html = resp.text
        # 命中人机验证页时,提交表单通过验证后重新请求
        if 'csrf_token' in html and '安全人机验证' in html:
            html = self._pass_verification(url, html)
        return html

    def _pass_verification(self, url, html):
        """提交人机验证表单(csrf_token + human_check),返回通过后的真实 HTML"""
        tree = etree.HTML(html)
        csrf_nodes = tree.xpath('//input[@name="csrf_token"]')
        if not csrf_nodes:
            return html
        csrf_token = csrf_nodes[0].get('value', '')
        self.session.post(url, data={'csrf_token': csrf_token, 'human_check': 'on'}, timeout=10)
        resp = self.session.get(url, timeout=10)
        if not resp.encoding or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding
        return resp.text
```

## 反爬探测流程(分析阶段执行)

对目标站先做探测,判断反爬类型,再决定爬虫实现策略:

1. **无凭证直接请求**
   ```python
   resp = requests.get(url, headers={'User-Agent': '标准UA'}, timeout=10, allow_redirects=False)
   ```
2. **判断响应**:
   - **200 + 内容正常**(含目标数据/无验证关键词) → 无反爬,`__init__` 无需注入cookie
   - **200 + 含验证页特征**(`csrf_token` / "安全人机验证" / "人机验证" / "verify") → 表单验证型,实现 `_pass_verification`
   - **302/301 重定向到登录/验证页** → 需要登录态或cookie,在 .env 配置凭证
   - **403/412** → UA/Referer 检测或风控,补全请求头,必要时加cookie
   - **5xx** → 可能IP被限,需延迟/代理(本框架不内置,提示用户)
3. **验证探测**:若疑似验证页,检查 HTML 是否含 `csrf_token` + 验证关键词,确认是否表单验证型

## 各反爬类型适配策略

| 反爬类型 | 探测特征 | 适配方式 |
|---------|---------|---------|
| 无反爬 | 200+正常内容 | 仅设 UA/Referer |
| 表单验证 | 200+csrf_token+验证关键词 | 实现 `_pass_verification`(提交表单) |
| Cookie鉴权 | 302到登录 / 403 | .env 配置cookie,`__init__` 注入 |
| UA检测 | 403 | 补全浏览器UA+Referer |
| 频率限制 | 偶发403/超时 | 加 sleep(本框架提示用户,不内置) |

## .env 凭证配置约定
- 变量名:`<站点名大写>_<凭证类型>`,如 `MOVIE_XXX_PHPSESSID`
- 凭证获取:浏览器登录/通过验证后,从开发者工具 Application→Cookies 复制
- 过期需更新:在爬虫类 docstring 注明"过期需更新"

## 编码处理(易踩坑)
目标站可能不声明编码,requests 默认 ISO-8859-1 导致中文乱码:
```python
if not resp.encoding or resp.encoding.lower() == 'iso-8859-1':
    resp.encoding = resp.apparent_encoding  # chardet 检测
```
务必在 `_get_html` 中处理。

## 缓存实现(强制,每个采集功能必须带缓存)
**目的**:避免每次访问都请求源站,降低源站与服务器压力。
**默认时长**:1-2 小时,`.env` 中 `CACHE_TTL_HOURS` 自定义(默认 2)。

### settings.py 配置
```python
# .env 中配置: CACHE_TTL_HOURS=2
CACHE_TTL_HOURS = float(os.getenv('CACHE_TTL_HOURS', '2'))
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': os.path.join(BASE_DIR, 'cache'),
    },
}
```

### 爬虫缓存骨架(照此结构)
```python
import os
import time

from dotenv import load_dotenv

load_dotenv()

# 缓存时长(秒):从 .env 的 CACHE_TTL_HOURS 读取,默认 2 小时
CACHE_TTL = float(os.getenv('CACHE_TTL_HOURS', '2')) * 3600


class _CacheAdapter:
    """统一缓存入口:优先 django cache(视图请求环境),独立脚本/未配置 settings 时退化为内存缓存"""

    def __init__(self):
        self._backend = None  # None=未初始化

    def _get_backend(self):
        if self._backend is None:
            try:
                from django.core.cache import cache
                cache.get('__probe__')  # 触发 settings 检查
                self._backend = cache
            except Exception:
                self._backend = 'mem'
        return self._backend

    def get(self, key):
        backend = self._get_backend()
        if backend == 'mem':
            item = _CacheAdapter._mem_store.get(key)
            if item and item[1] > time.time():
                return item[0]
            return None
        return backend.get(key)

    def set(self, key, value, timeout):
        backend = self._get_backend()
        if backend == 'mem':
            _CacheAdapter._mem_store[key] = (value, time.time() + timeout)
        else:
            backend.set(key, value, timeout)

    _mem_store = {}


cache = _CacheAdapter()


class MovieXxxSpider:
    ...
    def _cached(self, key, fetch_func):
        """带缓存的采集:命中缓存直接返回,未命中抓取解析后写入缓存"""
        data = cache.get(key)
        if data is not None:
            return data
        data = fetch_func()
        cache.set(key, data, CACHE_TTL)
        return data

    def fetch_movie_list(self, mtype, page):
        # 缓存键规范:<站点缩写>_<功能>_<参数>
        return self._cached(f'movie_{mtype}_{page}',
                            lambda: self._do_fetch_movie_list(mtype, page))
```

### 缓存键规范
- 格式:`<站点缩写>_<功能>_<参数>`,如 `i4_home`、`i4_news_list_1_2`、`i4_news_detail_56195`
- 每个 `fetch_xxx` 方法必须用 `_cached` 包一层,不得裸请求源站
- 参数含空格/逗号等特殊字符时,先 `hashlib.md5(param.encode('utf-8')).hexdigest()` 再拼 key,如 `i4_firmware_{md5(model)}`(直接拼原字符会触发 FileBasedCache 的 key 校验告警)

### 缓存目录结构(FileBasedCache)
- 缓存存于 `settings.CACHES['default']['LOCATION']` 指定目录,本项目为项目根 `cache/`
- 每个 key 对应一个 `<key 的 md5>.djcache` 文件,无数据库/Redis 依赖
- 缓存文件在 TTL 到期后自动失效,下次访问重新抓取

### 手动清缓存(强制,排查必读)
修改爬虫解析逻辑或缓存时长后,已缓存的旧数据不会自动失效,**必须清空 `cache/` 目录**才能看到新逻辑生效:
```bash
python -c "import shutil; shutil.rmtree('cache')"
```
注意:Trae CN 环境下 PowerShell 的 `Remove-Item` 会被安全包装器拦截,请用上面的 Python 方式删除。
