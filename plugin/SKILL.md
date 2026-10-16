---
name: course-kit
description: 书籍、视频摘要——把一本书和一个视频做成教学材料，交付三样东西：书摘（原文 + 总结）、视频文稿与摘要、讲义式 PPT。适用于"读这本书的某几章并总结""把这段视频转文字并总结""把内容做成课件"这类请求，尤其是要求保留原文、区分演示段落与作者批注的场合。
---

# 书籍、视频摘要（course-kit）

一本书 + 一个视频 → 三样产物：

1. **书摘**（Markdown）——按章节提取演示段落，保留原文，附作者说明与批注；
2. **文稿与摘要**（Markdown + SRT）——视频的画面与音频识别结果；
3. **课件**（pptx）——以演示原文为主体、说明与批注作旁栏的讲义。

## 一条铁律：原文不许经手模型

三个产物都涉及大段引文。引文一旦由模型转述就会走样，而且走样得很难发现。
所以本插件把"搬运"和"判断"分开：

| 谁来做 | 做什么 |
|---|---|
| Python 脚本 | 从 PDF / ASR / 画面里**逐字搬运**原文，带页码与时间码 |
| 模型（你） | 判断哪几段是演示、哪几段是说明与批注；写章旨、要点、摘要 |

判断的结果写进 `presets/*.json`（书）与 `presets/*.notes.json`（书与视频），
下次换书、换视频只改这些 JSON，脚本一行不用动。**这就是留给用户的接口。**

## 两种提取方式：默认那个不需要配置

**默认走「通用重点提取」**（`digest.py` / `coursekit_book_digest`）：不看体裁，只用段落的通用特征
排序——长度是否适中、是不是章首或章末、有没有引文与数字、连接词密度、以及它对本章词汇的代表性。
产出「关键词 + 要点摘句 + 重点段落原文」，原文逐字保留并标页码。**任何书都能直接跑，不用写预设。**

「演示／说明／批注」是另一条路，**只在书确实是"引一段原文再点评"的结构时才用**。
这套角色是从《大师们的写作课》抽出来的，换成人类学、历史、科普往往就不成立，
所以它退成可选项，而不是必经之路。

| | 通用重点提取（默认） | 演示／说明／批注 |
|---|---|---|
| 需要预设 | 不需要 | 需要角色预设（+ 注解预设） |
| 适用 | 任何书 | 写作教材、文论、评点类 |
| 总结方式 | 抽取式（挑原句 + 关键词） | 注解预设里写的章旨与要点 |
| 脚本 | `digest.py` | `curate.py` → `book_report.py` |

## 工具

| 工具 | 作用 |
|---|---|
| `coursekit_book_outline` | 列目录 + 看有没有文本层（`hasTextLayer`），用来挑章节 |
| `coursekit_book_extract` | 按章节/页范围提段落（**只对文字版 PDF**；扫描件会报 hasTextLayer:false） |
| `coursekit_book_ocr` | **扫描件取字**：逐页本地 OCR，产出与 extract 同构的 paragraphs.json |
| `coursekit_book_digest` | **通用重点提取**：关键词、要点摘句、重点段落原文 |
| `coursekit_book_report` | 按角色预设组装书摘（演示／说明／批注那条路） |
| `coursekit_video_fetch` | 取回视频（B 站或本地），抽音频与帧，支持时间段 |
| `coursekit_video_transcribe` | 本地 SenseVoice 识别，出 asr.json / srt |
| `coursekit_video_frames` | 字幕条带拼图 / 文字卡片抽取，省读图次数 |
| `coursekit_video_report` | 组装文稿与摘要 |
| `coursekit_deck_build` | 按规格 JSON 生成 pptx |
| `coursekit_deck_preview` | 渲染 PNG 并报告文本框溢出 |
| `coursekit_md_docx` | Markdown → Word（正文小四宋体，标题小三四号加粗提亮）

## 扫描件：先看有没有文本层

**第一步永远是 `coursekit_book_outline`**。它顺带量出这本书有多少页能直接取到字：

- `hasTextLayer: true` → 走 `coursekit_book_extract`，毫秒级。
- `hasTextLayer: false` → 这是扫描件（每页一张图），`extract` 只会以退出码 3 报一句
  「需要先做 OCR」。改用 **`coursekit_book_ocr`**：逐页渲染 + 本机 RapidOCR 识别，
  一页三四秒，整本几百页要跑十几到几十分钟。**只别整本硬跑**——用户通常只要某几章，
  先问清要哪几章，再用 `chapters` 限定范围。
- 混合型（前面文字、后面扫描）不用挑：`coursekit_book_ocr` **逐页判断**，哪页有文字层就读哪页，
  没有才识别。

OCR 之后的一切与文字版**完全相同**：同一套 `paragraphs.json`，
`coursekit_book_digest`（默认）或 `curate.py → book_report.py` 照跑。

识别质量与版式处理（都在脚本里，不必手工修）：

- 页眉、书眉、页码会在切段前清掉（靠"短 + 没写到右边"两条特征认出居中标题）；
- 段落按缩进切，缩进阈值用 OCR 估出的字宽算，不拿最小 x 当左边界（行框会抖）；
- 半角标点落在汉字之间会转成全角，两三个连字符认成的破折号会并回 `——`；
- 逐页结果写在 `<out>/ocr_pages/`，中断后重跑跳过已识别的页——**长书一定要靠这个**。

## 浏览器控制台

除工具外还有一个可视化工作台（侧栏图标「书籍、视频摘要」，由 `dsh-course-kit-console` 包提供）：

- **书籍**：拖入 PDF 或填绝对路径 → 读目录 → 勾章节（或勾「全文识别」）→ 选产物格式 → 开始。
- **视频**：拖入本地视频或粘 B 站链接 → 勾「全片识别」或填时间段 → 选产物格式 → 开始。
- **输出格式**：Markdown / Word，视频另可导出 SRT 字幕。
- **输出目录**：留空即 `course-kit\demo\out\<书名或视频名>\`——**一个素材一个目录**，
  界面里能直接复制路径。别把两本书指到同一个目录：`paragraphs.json` 会互相覆盖。

控制台把「提取 → 归类 → 出稿 → 转格式」串成一条后台链条，前端只轮询进度；
任务串行执行，任一步失败就停在原地并保留日志。上传的文件复制到 `demo/uploads/`，原文件不动。

书籍页签里与 OCR 有关的只有一处：**「扫描件：先做 OCR 再提取」**。读目录时若发现这本书
没有文本层，它会自动打开，并在章节栏上方给出说明；旁边可以调清晰度（默认 200）。
识别进度会实时打在「本次作业」那一步的日志里（长书跑几十分钟，别让它看起来像卡住了）。

## 推荐顺序

```
coursekit_book_outline          →  拿目录 + 看 hasTextLayer
   ├─ 有文本层
   │   coursekit_book_extract   →  paragraphs.json（+ preset 给定时出 sections.json）
   └─ 扫描件（hasTextLayer:false）
       coursekit_book_ocr       →  paragraphs.json（同构，逐页缓存可续跑）
   ⋯ 读 paragraphs.md，判断演示／说明／批注，写 notes.json ⋯
coursekit_book_digest           →  01_重点提取_书名.md（默认路径，零配置）
coursekit_book_report           →  01_书籍摘要.md（可选：演示／说明／批注那条路）

coursekit_video_fetch           →  audio_16k.wav + frames/
coursekit_video_transcribe      →  asr.json
coursekit_video_frames strip    →  字幕条带（用于核对识别结果）
coursekit_video_frames cards    →  画面上的文字卡片
   ⋯ 看条带与卡片，写 notes.json 的分节与勘误 ⋯
coursekit_video_report          →  02_视频文稿与摘要.md

   ⋯ 写 deck spec JSON，长段原文一律用 quote_ref 引用 ⋯
coursekit_deck_build            →  03_课件.pptx
coursekit_deck_preview          →  自检：溢出数为 0 再交付
```

## 分类演示／说明／批注的办法

原书**不会**标注"这是演示段落"。可靠的做法是看结构信号，再看内容：

- **演示段落**：上一段以 `——`、`：` 收尾，或上一段整段就是 `《某某》：`；
  其后连续几段都属于同一段引文，直到出现作者的口吻为止。
- **说明段落**：作者的解说主体。看是否出现"我想""我发现""读完""看来"这类议论腔。
- **批注段落**：紧跟在引文之后、篇幅较短的即时点评。

`curate.py` 用这些规则给初判，`presets/*.json` 的 `overrides` 做人工覆写。
**规则只负责结构，覆写负责这本书特有的判断**——不要试图把判断全塞进规则里。

注意一个常见坑：引出语里的书名常常指的是**上一段**演示（"这是白先勇的《游园惊梦》。
……张爱玲笔下至多是这个样子："）。自动取书名会串，用 `overrides.sources` 按单元号覆写。

## 课件风格

面向大学课堂，取向是"讲义"不是"发布会"：

- 原文是主角。`quote_side` 版式把演示段落放在整幅左侧，字号最大、行距最松；
  作者的说明与批注退到右侧窄栏，字号小一档。
- 不做装饰性图形，没有图标、没有色块拼贴，只有一条分隔线和一个页码。
- **标题写具体的东西**（"兔头、香椿，和一只铝饭盒"），不写"从 X 到 Y""全流程："
  "赋能""揭秘"这类总结腔。标题的作用是告诉学生这一页在讲哪一段文字。

## 环境

- Python：默认用 `C:/Users/win10/AppData/Local/Programs/Python/Python310/python.exe`，
  可用环境变量 `COURSEKIT_PYTHON` 覆盖。
- 第三方依赖装在工作区 `.pylibs`（pymupdf / python-pptx / yt-dlp / sherpa-onnx），
  由宿主半自动加进 `PYTHONPATH`。
- ffmpeg：`video_fetch.py` 会在几个已知路径里找，也可用环境变量 `DSH_FFMPEG` 指定。
- 识别模型复用 DSH 自带的 SenseVoice（`~/.dsh/speech-to-text/sensevoice/models`），
  不额外下载权重、不上传音频。

## 交付前必做

跑一次 `coursekit_deck_preview`。它会把 pptx 画成 PNG 并报出每个文本框的
"需要高度 / 可用高度"，**溢出数不为 0 就不要交付**——字号估算是估算，
渲染结果才是事实。
