# 命名规范

新增采集功能时,各类命名从目标站域名/URL 推导,保持与项目现有风格一致。

## 站点名推导
从目标站域名推导,格式 `<内容类型>_<域名标识>`:
- `2t58.com`(音乐) → `Music_2t58`
- `xxx.com`(电影) → `Movie_xxx`
- `yyy.com`(壁纸) → `Wallpaper_yyy`
- `zzz.com`(博客) → `Blog_zzz`

内容类型前缀:`Music` / `Movie` / `Wallpaper` / `Blog` / `News` 等,根据站点实际内容定。

## 命名映射表

| 对象 | 规则 | 示例 |
|------|------|------|
| 爬虫目录 | `SpiderServices/<站点名>/` | `SpiderServices/Movie_xxx/` |
| 爬虫文件 | `main.py` | `SpiderServices/Movie_xxx/main.py` |
| 爬虫类名 | `<站点名去下划线>PascalCase` + `Spider` | `MovieXxxSpider` |
| 采集方法 | `fetch_<功能>` | `fetch_movie_list` / `fetch_movie` |
| 解析方法 | `_parse_<功能>` | `_parse_movie_list` |
| 视图函数 | `<功能名>`(下划线) | `movie_list` / `movie` |
| URL路径 | `path('<功能>/<sid>.html', ...)` | `path('movie/<sid>.html', ...)` |
| 模板文件 | `<功能>.html` | `movie_list.html` / `movie.html` |
| .env变量 | `<站点名大写>_` + 凭证名 | `MOVIE_XXX_PHPSESSID` |

## 功能名推导
从目标站 URL 路径推导功能名(下划线风格):
- 源站 `/singerlist/...` → 功能 `singer_list`
- 源站 `/singer/xxx.html` → 功能 `singer`
- 源站 `/list/xxx.html` → 功能 `chart`
- 源站 `/movie/xxx.html` → 功能 `movie`

## URL 路由风格(参考现有)
```python
# 列表页(带分类+分页)
path('singerlist/<area>/<gender>/<style>/<letter>/<int:page>.html', ...)
# 列表页(单分类+分页)
path('list/<chart>/<int:page>.html', ...)
# 详情页
path('song/<sid>.html', ...)
path('singer/<sid>/<int:page>.html', ...)  # 详情页内列表分页
```
- 路径参数用 `<sid>`(字符串)、`<int:page>`(数字)
- 第1页与第N页可分两条路由,或在爬虫内用 `if page > 1` 拼URL

## 沟通确认时必须明确的命名
分析后向用户确认以下命名(全部列出):
站点名 / 爬虫类名 / fetch方法名 / 视图函数名 / URL路径 / 模板文件名
