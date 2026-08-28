# 阶段2:沟通确认

⚠️ **这是强制阶段,严禁跳过直接写代码。**

分析完成后,向用户输出分析结论 + 实施方案,等用户明确确认后再进入实施。

## 必须输出的内容

### 1. 分析结论
- **目标站**:域名 + 站点类型(音乐/电影/壁纸/博客)
- **页面类型**:列表页 / 详情页 / 搜索页 / 榜单页
- **页面URL**:用户提供的网址
- **数据字段**:将提取的字段清单(如 title/link/pic/intro)
- **URL规则**:分页规则 / 详情链接格式 / 分类参数结构
- **反爬机制**:探测结论(无反爬 / 表单验证 / cookie鉴权)+ 需要的凭证

### 2. 实施方案(命名清单)
按 [naming-conventions.md](../reference/naming-conventions.md) 列出全部命名:
```
站点名:        Movie_xxx
爬虫类名:      MovieXxxSpider
爬虫文件:      SpiderServices/Movie_xxx/main.py
采集方法:      fetch_movie_list  /  fetch_movie
视图函数:      movie_list  /  movie
URL路径:       /movie/<mtype>/<int:page>.html  /  /movie/<sid>.html
模板文件:      movie_list.html  /  movie.html
.env变量:      MOVIE_XXX_PHPSESSID
```

### 3. 改动文件清单
列出将改动的文件及每个文件的改动点:
```
1. SpiderServices/Movie_xxx/main.py  → 新增 fetch_movie_list + _parse_movie_list
2. Web/views/request.py              → 新增 movie_list 视图函数
3. Web/views/urls.py                 → 新增 path 路由
4. Web/templates/movie_list.html     → 新建模板
```

### 4. 需用户配合的事项
- 反爬凭证(如需):告知用户去浏览器获取 PHPSESSID,配置到 .env
- CSS/JS 决策(模式A):是下载目标站资源还是模仿样式自写
- 不确定的命名/字段:列出选项让用户选

## 确认方式
- 用 `AskUserQuestion` 工具就关键分歧点(如命名、反爬策略、CSS来源)提问
- 或用文字输出完整方案,明确说"请确认以上方案,确认后我开始实施"
- **必须等用户明确回复确认(如"确认""可以""开始")后,才进入实施阶段**

## 用户可能提出的调整
- 修改命名(如改 `movie` 为 `film`)
- 调整字段(如不要某字段、增加某字段)
- 改变反爬策略
- 要求分步实施(先爬虫再前端)

收到调整后,更新方案,必要时再次确认,然后实施。

## 禁止行为
- ❌ 未经确认直接写代码
- ❌ 在确认阶段就创建/修改业务文件(临时分析脚本除外)
- ❌ 用户说"你看着办"时擅自决策命名/字段——仍要给出推荐方案请用户确认
