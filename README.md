# 书籍、视频摘要（course-kit）

把**一本书**和**一个视频**做成教学材料：书摘（Markdown / Word）、视频文稿与摘要（含 SRT）、讲义式 PPT。
界面名「书籍、视频摘要」；包名 `dsh-course-kit`（宿主半 + 引擎 + 客户端半）与 `dsh-course-kit-console`（浏览器工作台）。

## 一条铁律

**原文不许经手模型。** 搬运交给 Python 脚本（逐字、带页码/时间码），判断交给模型并写进 `presets/*.json`。
这条铁律是全部结构决定的原因，改代码前先理解它。

## 目录

```
course-kit/
├─ HANDOFF.md          交接文档（自包含，接着干活先读它）
├─ 工作总结.md         本轮工作总结：做了什么、验过什么、换机器要改什么
├─ plugin/             宿主半 + 引擎 + 客户端半
│  ├─ lib/py.js        进程与路径层（findPython / runScript / call）
│  ├─ lib/host.js      12 个 agent 工具 + 技能注册
│  ├─ lib/client.js    浏览器控制台（三个页签）
│  ├─ engine/          14 个 Python 脚本
│  ├─ SKILL.md         技能正文：工具清单、推荐顺序、分类办法
│  └─ README.md        插件说明
├─ console/            dsh-course-kit-console：/coursekit/* 路由 + 作业链执行器
│  └─ test/smoke.mjs   45 条断言的自检（不启动 Python）
└─ presets/            判断层：角色预设、注解预设、课件规格
```

**不入库的两样**（见 `.gitignore`）：`.pylibs/`（第三方依赖，各机按 `工作总结.md` 第四节装）、
`demo/`（上传的素材与产物，体积大且含版权内容）。它们会在运行时自动创建。

## 跑起来

1. 装依赖与挂载：见 [`工作总结.md`](./工作总结.md) 第四节；
2. 自检：`node console/test/smoke.mjs`（沙箱下加 `COURSEKIT_SMOKE_NO_RM=1`）；
3. 端到端：见 [`HANDOFF.md`](./HANDOFF.md) 第六节。

**扫描件**：PDF 每页都是图时，勾控制台的「扫描件：先做 OCR 再提取」（读目录会自动勾上），
或让会话里的助手调 `coursekit_book_ocr`。一页三四秒，逐页缓存可续跑。

**宿主半的改动要重启 DSH**：`plugin/lib/py.js`、`plugin/lib/host.js`、`console/lib/main.js`
被 Loader 永久缓存，改内容／重挂 bundle 都不会重新导入。客户端半 `plugin/lib/client.js` 刷新页面即可。
