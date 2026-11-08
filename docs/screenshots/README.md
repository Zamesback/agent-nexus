# 截图

README 里引用的运行时截图。全部来自当前版本（1600×1000，深色主题，真实项目数据）。

| 文件名 | 内容 |
|---|---|
| `01-project-manager.png` | 项目管理器首页（多项目列表） |
| `02-board-main.png` | 看板主界面（任务 + 聊天） |
| `03-launch-page.png` | 启动页 |
| `04-architecture.png` | 架构示意图 |
| `05-org-chart.png` | 项目组织架构图（三国指挥官，含侧栏「公司员工」入口） |
| `06-org-member-card.png` | 托管成员卡片：左侧当前工作 / 最近交付 / 交付物，右侧实时对话 + 回复下面的附件 |
| `07-org-chart-collab-board-dev.png` | 另一个项目的组织架构（collab-board-dev） |
| `08-member-card-planner.png` | 另一个托管成员的卡片（玩法策划负责） |

## 在 README 中引用

```markdown
![Org Chart](docs/screenshots/05-org-chart.png)
```

## 截图技巧

- 无头 Edge：`--headless=new --window-size=1600,1000 --virtual-time-budget=14000 --screenshot=out.png <url>`
- 需要先打开成员卡片时，用一个同源页面 iframe 应用，加载完后调用 `contentWindow.openPerson('<id>')` 再截图
- 深色主题下效果最好（默认主题）
