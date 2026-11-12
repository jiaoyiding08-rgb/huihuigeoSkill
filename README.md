# huihuigeoSkill 1.0

作者：汇汇

huihuigeoSkill 是一套面向 Codex 和 Gemini CLI 的生成式搜索优化（GEO）技能。它帮助你建立问题库，记录 AI 回答中的引用、品牌提及和推荐，按证据安排选题、写作和发布，并用同一批问题复测。

## 1.0 能做什么

| 工作 | 怎么做 | 得到什么 |
|---|---|---|
| 定选题 | 对照旧稿，写清新文章要给读者增加哪个可执行的办法 | 一份能交给写作者的任务，减少同一内容换标题重写 |
| 做检测 | 保存原问题、回答、来源和会话地址。按题标记缺少的材料 | 一张补采清单。原会话还能打开时，回去补缺项即可 |
| 查引用 | 核对回答中的引用位置与来源网址，匹配发布台账中的作品 ID | 看清 AI 用了哪一篇、哪一段，以及有没有提到或推荐品牌 |
| 审稿和核对公开稿 | 把硬事实及出处放在稿外，发布后将保存的公开正文与审定稿对照 | 找出公开版本里缺失或变动的关键段落 |
| 做复测 | 用同一问题和设置开独立会话，分别记录引用、品牌推荐和事实准确性 | 看结果是否能在多个样本中重复出现 |

技能包含工作说明、可重复使用的表格和本地脚本。Python 脚本需要 Python 3.10 或更新版本，仅使用标准库。打开平台、采集网页和发布稿件由操作者或所用工具按实际权限处理。脚本处理已保存的材料，可见性效果需要在目标 AI 的实际入口复测。

## 安装

### Gemini CLI

在需要使用的项目目录运行：

```bash
gemini skills install https://github.com/jiaoyiding08-rgb/huihuigeoSkill.git --path geo --scope workspace
```

如同一项目已经安装过旧的 `geo`，先用 `gemini skills list` 确认旧版所在范围，再卸载对应范围的旧技能并运行安装命令。安装后在 Gemini 会话中运行 `/skills reload`，再用 `/skills list` 确认出现 `geo`。 [Gemini CLI 技能文档](https://geminicli.com/docs/cli/skills/)

### 下载 ZIP 安装

从 [v1.0.0 发布页](https://github.com/jiaoyiding08-rgb/huihuigeoSkill/releases/tag/v1.0.0) 下载 `geo-loop-pro-v1.0.0.zip`，解压后保留完整的 `geo/` 目录。

- Gemini CLI 可在品牌项目目录运行 `gemini skills install /你的解压路径/geo --scope workspace`。
- Codex 可将 `geo/` 放到用户技能目录 `~/.codex/skills/`。已有同名技能时，先把旧目录移到技能目录之外备份，再放入新版。

仓库名称为 huihuigeoSkill，技能调用名为 `geo`，现有项目仍可沿用原有调用方式。

## 开始使用

对 AI 说：“使用 geo。先读这个品牌项目的 AGENTS.md 和 GEO状态.md，再按这次任务执行。”

已有项目继续使用原来的题库、台账和批次。第一次可挑两道题试用。旧版只保存了页面链接的记录，新版会列为候选来源，补上对应的正文引用位置后再计入确认结果。脚本命令和字段说明见 [证据与复测](geo/references/7_证据与复测.md)。

## 许可证

[MIT](LICENSE)
