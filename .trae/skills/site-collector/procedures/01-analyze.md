# 阶段1:分析目标网址

这是强制流程的第一步。分析目标网址,为"沟通确认"阶段准备结论。

## 分析步骤

### 1. 抓取目标页面 HTML
优先用 WebFetch 抓取目标 URL。若被反爬拦截(返回验证页/403/重定向),改用临时脚本探测:

```python
# _tmp_probe.py(分析完即删)
import requests
url = 'https://目标网址'
resp = requests.get(url, headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...'
}, timeout=10, allow_redirects=False)
print('status:', resp.status_code)
print('len:', len(resp.text))
with open(r'项目根\.tmp_page.html', 'w', encoding='utf-8') as f:
    f.write(resp.text)
```

若需带cookie/验证,参考 [anti-scraping.md](../reference/anti-scraping.md) 的探测流程。

### 2. 判断页面类型
根据页面结构判断:
| 类型 | 特征 | 示例 |
|------|------|------|
| 列表页 | 多个相似条目(li/div循环) + 分类筛选 + 分页 | 歌手列表/歌单列表/MV列表 |
| 详情页 | 单个主体信息 + 关联列表 + 简介 | 歌手详情/歌单详情/MV详情 |
| 搜索页 | 搜索框 + 结果列表 + 分页 | 搜索结果 |
| 榜单页 | 排名列表 + 热门榜单侧栏 | 新歌榜/TOP榜 |
| 首页 | 多板块聚合 | 热门歌手+飙升榜+趋势榜 |

### 3. 分析数据结构(用 XPath)
对抓取的 HTML,确定要提取的字段及其 XPath:
- **列表页**:列表容器(`div.xxx ul li`)、每项的标题/链接/图片
- **详情页**:主体信息容器(`div.xxx_info`)、标题(h1)、图片、简介
- **分页**:分页容器(`div.page`)、链接结构
- **筛选**:筛选区(`div.ilingku_fl`)、标题+选项

记录关键 XPath,后续写 `_parse_xxx` 时用。

### 4. 分析 URL 规则
- **分页规则**:第1页 vs 第N页的URL差异(如 `/list/new.html` vs `/list/new/2.html`)
- **详情链接**:列表项指向详情页的href格式(如 `/song/xxx.html`)
- **分类筛选**:筛选链接的URL参数结构(如 `/singerlist/{area}/{gender}/...`)
- **ID格式**:详情页ID是明文还是加密串(如 `bXdua2Njc3ZobQ`)

### 5. 分析反爬机制
按 [anti-scraping.md](../reference/anti-scraping.md) 的探测流程:
- 无凭证请求 → 看状态码/重定向/验证页特征
- 判断反爬类型(无反爬/表单验证/cookie鉴权/UA检测)
- 记录需要的凭证(如 PHPSESSID)

### 6. 分析 CSS/JS 资源(仅模式A框架搭建需要)
若用户要搭建框架(模式A),额外分析:
- 目标站用的前端框架(layui/bootstrap/jQuery/原生)
- 关键 CSS 文件(base.css 等)及样式类名
- 关键 JS 文件及功能
- 决定:直接下载目标站CSS/JS,还是模仿其样式自己写

## 输出
分析完成后,整理结论,进入 [02-confirm.md](02-confirm.md) 沟通确认阶段。

⚠️ 分析阶段产生的临时文件(`_tmp_probe.py`、`.tmp_page.html`)在分析完成后必须删除。
