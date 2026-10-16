# course-kit 交接文档

> 给接手的会话看。这份文档是自包含的：读完它应该可以直接开工，不需要上一轮的对话。
> 写于 2026-10-16，紧接用户报告「有的书跑不出结果」之后。
> **本日更新（按时间顺序）**：
> 1. 扫描件那份误导性报错已修（第二节）；
> 2. 插件界面名改成「书籍、视频摘要」（第一节）；
> 3. **加了本地 OCR**（第三节「扫描件 OCR」）——用户的书绝大多数是扫描件，这是刚需；
> 4. 冒烟测试 30 → 45 条；沙箱下 node 删工作区文件会崩，测试加了 `COURSEKIT_SMOKE_NO_RM` 开关；
> 5. 产物统一收到 `course-kit\demo\out\` 下，且**一本书一个目录**（见第四节）。

## 一、这个插件是干什么的

界面名（侧栏图标与页面标题）：**书籍、视频摘要**。包名仍是 `dsh-course-kit`
（宿主半）/ `dsh-course-kit-console`（浏览器工作台）——改包名要动 profile 与重挂，
见第七节第 3 条。

把**一本书**和**一个视频**做成教学材料，交付三样产物：

| 产物 | 形式 | 内容 |
|---|---|---|
| 书摘 | Markdown / Word | 书籍的重点段落与总结，原文逐字保留、带页码 |
| 文稿 | Markdown / Word / SRT | 视频的画面与音频识别结果，带时间码 |
| 课件 | pptx | 讲义式：演示原文是主体，说明与批注作旁栏 |

**一条铁律贯穿全部设计：原文不许经手模型。** 搬运交给 Python 脚本（逐字、带页码/时间码），
判断交给模型并写进 `presets/*.json`。这条铁律是下面所有结构决定的原因，改代码前先理解它。

用户是大学老师，要做写作课教学材料。沟通用中文。

## 二、当前状态

### 跑得通的

- **书籍 · 通用重点提取（默认路径）**：`pdf_extract.py → digest.py → md_to_docx.py`。
  已实跑《金枝》并产出 `01_重点提取_*.md/.docx/.json`。四章实跑《大师们的写作课》关键词区分度正常。
- **书籍 · 演示／说明／批注（可选路径）**：`curate.py → book_report.py`。已跑通《大师们的写作课》四章
  34 个演示单元，书摘 28.8k 字。
- **视频**：B 站取回 → SenseVoice 本地识别 → 文稿 + 摘要 + SRT。7 分钟视频识别 20 秒（20 倍速）。
  已跑通两个视频（BV16q4y1S7Ap、BV1d8pU6xEZX）。
- **课件**：29 页 pptx，含渲染自检（溢出报告 0）。
- **书籍 · 扫描件 OCR（新增）**：`pdf_ocr.py → digest.py → md_to_docx.py`。
  逐页渲染 + RapidOCR（本地 ONNX，CPU），一页三四秒，逐页缓存可续跑，
  产出与 `pdf_extract.py` 同构。实跑《文化论》（125 页扫描件）整本，
  见第三节。
- **浏览器控制台**：三个页签（书籍／视频／课件），各自独立成一次作业，拖入文件、
  读目录、勾章节、选输出格式、看进度与产物。界面名（侧栏 + 页面标题）叫
  **「书籍、视频摘要」**，包名/工具名/技能 id 仍是 `dsh-course-kit`、`coursekit_*`、`course-kit`。
  书籍页签有「扫描件：先做 OCR 再提取」开关，读目录发现没有文本层时会自动打开。
- **冒烟测试**：`node console/test/smoke.mjs` → **43/43 通过**。

### 出错的（用户报的 bug）

**扫描件 PDF 没有文本层时，整条链给出误导性报错。** —— **已修（2026-10-16）**

修之前，用《东方学》（577 页、550 条目录、**每页都是图像、0 个字符**）实测：

```
pdf_extract.py  →  {"ok": true, "chapters": [...], "paragraphs": 0, "chars": 0}   退出码 0   ← 谎报成功
digest.py       →  {"ok": false, "message": "paragraphs.json 是空的，先跑 pdf_extract.py"}   退出码 1
```

用户看到的是「先跑 pdf_extract.py」——**去跑一个他刚刚跑成功的步骤**。这是最糟的一类报错：
把工具自己的限制说成用户的顺序错误。

修法（四层，从源头到界面）：

| 层 | 改动 | 现在的行为 |
|---|---|---|
| `pdf_extract.py` | 新增 `text_layer_stats()` / `scan_message()` | 结果 JSON 带 `pages / textPages / noTextPages / imagePages / probeChars / hasTextLayer`；**一个段落都没提出来时退出码 3**，message 说清是"扫描件没有文本层"还是"选中的范围内没有文字"。`--list` 读不出目录时也带 message（《千年终结》0 条目录的情况） |
| `digest.py` | 空段落 / 缺文件 / JSON 坏掉分别给话 | 「这份书的段落是空的……最常见的原因是这份 PDF 是扫描件（没有文本层），需要先做 OCR」——不再提 pdf_extract.py |
| `console/lib/main.js` | 提取步骤挂 `after: guardParagraphs()`；失败时优先用脚本 JSON 里的 `message` 当原因 | 段落为 0 → 后续步骤全部 `skipped`，原因写明扫描件与 OCR；脚本报错时后续跳过原因直接引用那句人话，而不是「上一步失败：提取段落与页码」 |
| `plugin/lib/client.js` | 读目录返回空时显示扫描件警告框 | 换书后提示不会错挂；扫描件那本书的文字也不再劝人"勾全文识别" |

实跑证据（2026-10-16，`.pylibs` 环境）：

```
千年终结.pdf   493 页 / 0 字符 → --list 退出 0 带扫描件 message；提取退出 3
东方学.pdf     577 页 / 0 字符 → 同上（550 条目录照常列出）
大师们的写作课.pdf 205 页 / 116718 字 → hasTextLayer true，提取「美食」章 43 段 4442 字，退出 0
digest 空段落 / 缺文件 → 各自的 message，退出 1
```

**扫描件本身已经能处理了**：见第三节的 OCR。这里保留的是"报错要说人话"这条教训——
拿不准是不是扫描件时，`pdf_extract.py --list` 的 JSON 里就有 `hasTextLayer`。


### 其他已知问题（不紧急）

| 问题 | 说明 |
|---|---|
| `digest.py` 打分未跨体裁验证 | 只跑过《金枝》（人类学）与《大师们的写作课》（写作教材）两种。阈值是拍的，换体裁可能需要调。 |
| `deck_preview.py` 是近似渲染 | 自己写的 PIL 渲染器。本机 LibreOffice 是空壳、PowerPoint COM 被沙箱挡住，所以拿不到真渲染。字号按 1.22 倍安全系数估算。 |
| 视频清晰度上限 480P | B 站未登录的限制。够认画面与字幕，不够看清小字。 |
| `frame_sheets.py` 只在一支视频上验过 | strip（字幕条带）与 cards（文字卡片）两种模式。 |
| 遗留目录 | `E:\desktop\肥鱼专属空间\book`（1:34）、`course-kit\demo\book`、`demo\video`、`demo\render` 都是早期几轮把输出目录填错留下的，与现在的流程无关，可删——**要删之前先问用户**，里面还有当时跑的视频与渲染图。 |

## 三、环境事实（踩过的坑，别再踩）

- **工作区**：`E:\desktop\肥鱼专属空间`；项目在 `course-kit\`。
- **Python**：`C:\Users\win10\AppData\Local\Programs\Python\Python310\python.exe`
- **第三方依赖**：`course-kit\.pylibs`（pymupdf、python-pptx、python-docx、yt-dlp、sherpa-onnx、
  rapidocr-onnxruntime、onnxruntime、numpy、opencv-python、shapely、pyclipper、PyYAML、onnx）。
  用 `pip install --target <该目录> --only-binary=:all:` 装。**不要用 `--only-binary` 以外的方式**：
  曾因某个包要从源码构建，pip 空转 11 分钟无输出。
  **而且要把版本钉死**：光给 `--only-binary` 不够——pip 22.2.1 的解析器会在
  numpy/opencv/onnxruntime 的版本组合上空转十几分钟（CPU 拉满、零网络连接），
  钉死以下组合实测 1 分钟装完：

  ```powershell
  $env:PYTHONPATH=""  # 别让 .pylibs 干扰 pip 自己
  & $py -m pip install --target E:\desktop\肥鱼专属空间\course-kit\.pylibs --only-binary=:all: --no-cache-dir `
    "rapidocr-onnxruntime==1.4.4" "numpy==1.26.4" "opencv-python==4.10.0.84" "onnxruntime==1.19.2" `
    "shapely==2.0.6" "pyclipper==1.3.0.post6" "PyYAML==6.0.2" "six==1.16.0" "onnx==1.16.2"
  ```
  `onnx` 只在"自己量化模型"时才需要，见本节末。

- **ffmpeg**：`C:\Program Files (x86)\FormatFactory\ffmpeg.exe`（**没有 ffprobe**）。
  可用环境变量 `DSH_FFMPEG` 覆盖。
- **语音识别模型**：`C:\Users\win10\.dsh\speech-to-text\sensevoice\models`
  （sensevoice-onnx/model.int8.onnx + silero/silero_vad.onnx）。复用 DSH 自带的，不额外下载。
- **网络**：TCP 可达，但 **PowerShell 的 `curl.exe` 与 `Invoke-WebRequest` 会失败**
  （schannel `SEC_E_NO_CREDENTIALS`）。**Python 的 `requests` 正常**，pip 正常。
  B 站网页端有反爬（HTTP 412），但 `playurl` / `pagelist` 接口带浏览器 UA + Referer 可用——
  `video_fetch.py` 走的就是接口，不是 yt-dlp 的页面解析。
- **PowerShell 两个坑**：
  1. `Get-Content -Raw` 用系统默认编码读 UTF-8 文件，**会把中文读坏**（曾据此写坏一个源文件）。
     读文件用 read 工具，或用 `[System.IO.File]::ReadAllText($p,[Text.Encoding]::UTF8)`。
  2. 给原生程序传含引号的参数时，PowerShell **会把内层双引号吃掉**。
     需要多行 Python 时，用 write 工具写一个临时 `.py` 再跑，别用 `-c "..."`。
- **沙箱限制（两条，都踩过）**：
  1. Node 的 `child_process.spawn` 带 `stdio: 'pipe'` 会 **EPERM**。
     所以冒烟测试不能真的驱动 Python，只能用注入假 runner 的方式验逻辑。
     真 Python 执行要在运行中的应用里验（或直接在 pwsh 里跑脚本）。
  2. Node 在**工作区里删文件会让进程直接崩掉**（退出码 `0xC0000409` ≈ STATUS_STACK_BUFFER_OVERRUN，
     没有任何 stderr）。实测 `rmSync`（文件/空目录/整棵树）、`rmdirSync`、`fs.promises.rm` 全崩，
     临时目录里的删除正常；而 PowerShell 的 `Remove-Item` 与 Python 的 `os.remove`/`shutil.rmtree`
     删**同一个**文件没事——所以这是 node fs 在这套沙箱下的问题，不是文件权限问题。
     插件代码本身从不删文件，受影响的是测试：`console/test/smoke.mjs` 因此把假素材放进
     `os.tmpdir()`，工作区里的残渣（预设、上传件）用 `COURSEKIT_SMOKE_NO_RM=1` 开关跳过清理，
     跑完自己用 Python 收尾。**删工作区里的东西时优先用 Python，不要用 node。**
  3. `multiprocessing` 的 `Pipe` 会 **WinError 5 拒绝访问**（命名管道被沙箱挡住）。
     所以 OCR 的并行用**线程池**，不用进程池——一行代码的差别，但在这里是能不能跑的问题。

### 扫描件 OCR（本机）

- **引擎**：`rapidocr-onnxruntime` 1.4.4，PP-OCRv4 的 det/rec ONNX 模型随包分发
  （`...\.pylibs\rapidocr_onnxruntime\models\`，共约 16MB），**不联网、不上传、不需要 Tesseract**。
- **速度**（本机 i7-11800H / 16 逻辑核 / 8 物理核，200 DPI，中文书页约 20 行）：
  单线程约 5.0 s/页；**4 线程 × 每线程 4 核 ≈ 3.9–4.2 s/页**（整本 125 页约 9 分钟）。
  识别占八成、检测一成、方向分类可忽略。实测过的调参（都别再试）：
  `intra_op_num_threads=16` 反而更慢（6.7 s/页，线程打架）；
  `rec_batch_num=12` 略慢于默认 6；`use_cls=False` 没差别（分类本来就便宜）。
  16 页 wall 63s 是这套参数下的上限，别指望再加线程。
- **识别质量**：印刷体中文页置信度普遍 0.98–1.00。错字集中在形近字、生僻标点、
  外文人名（"Euegians"），以及"——"被认成"一一"或"---"（后者已在 `normalize_ocr_text` 里并回）。
- **版式处理**（都在 `pdf_ocr.py` 里，改之前先读那段注释）：
  1. 逐页判断走文字层还是 OCR（混合型 PDF 不用挑）；
  2. 页眉/书眉/页码在切段**之前**清掉——判定特征是"短 + 没写到右边"，
     只看左边界不行（长标题的左边界和缩进后的正文首行几乎重合）；
  3. 缩进阈值 = 正文左边 + **1.3 个字宽**（不是 pdf_extract 那套最小/次小值：
     OCR 行框会左右抖，抖一下就每行一段）；
  4. 小节标题（"丙语言"）会被上一段吸走，靠"短 + 无收尾标点 + 下一行是段首"拆出来；
  5. 逐页缓存 `ocr_pages/page_XXXX.json`，原子写（`.tmp` + `os.replace`），中断可续跑。
- **int8 量化：试过，先不做**。用 `onnxruntime.quantization.quantize_dynamic`
  只量化 MatMul/Gemm 能把 rec 模型从 5.07 降到 3.90 s/页（1.3×，同页输出逐行一致，
  全量化 Conv 会报 `Expected conv2d_157.w_0 to be an initializer`）。
  收益不大、要多存一份模型 + 一个 `onnx` 依赖，所以先搁着；真要做，
  用 `RapidOCR(rec_model_path=...)` 指过去即可，路径写进 `ocr.json` 便于回溯。
- **第一次真书实跑（2026-10-16）**：《文化论》（Malinowski，费孝通译，商务印书馆）
  125 页全扫描件，取自 `E:\AI DATASETS\文化·媒介与社会书单\`。

  ```
  pdf_ocr.py  125 页 / 98 页要识别 / 27 页命中缓存 → 415.3s（4.24 s/页，4 线程）
              341 段 / 63704 字，逐页置信度 0.94–1.00（多数 0.99）
  目录映射     按 TOC 把段落分回 24 章（+封面/书名/版权/前言/目录，共 29 个章名）
  digest.py   29 章 / 151 段重点段落 → 01_重点提取_文化论.md + .docx
  产物位置     course-kit\demo\out\文化论\（含 book\paragraphs.md 与 ocr_pages\ 逐页缓存，**别删**，可续跑）
  ```

  同日又用《东方学》（577 页扫描件）前 10 页试了一遍：
  `--pages 1-10` → 10 页 / 38.6s（3.86 s/页）、57 段 / 4561 字、按目录分到 封面/书名/版权/前言 四章。
  这 10 页是封面、版权页、丛书序，字号与边距与正文完全不同——**正好暴露了一个真 bug**：
  版式估计原来是把整个目标范围（10 页）合在一起算的，版权页那种小字号密排把
  `body_left` 拉偏，丛书序那页（p6）于是**每行都成了一个段落**。
  现在 `estimate_layout` 改成**按页**调用（一页行数少于 4 才退回整段估计），
  并且 `body_left` 取"最早那个至少占 25% 行的左边界"（原来 15%、且众数会被引文块带跑）。
  改完 p6 从 18 段并回 2 段，整本《文化论》从 341 段降到 295 段（合并更正确）。

  质量：印刷体中文页几乎无错字；错的地方集中在形近字、外文人名、生僻标点。
  跨页续段、书眉/页码清理都按预期工作（《文化论》每页都有居中书眉 + 页脚汉字数字页码，
  是最难的那类版式，正好当了试金石）。

## 四、架构与文件

```
course-kit/
├─ HANDOFF.md              ← 本文档
├─ plugin/                 包名 dsh-course-kit（宿主半 + 引擎 + 客户端半）
│  ├─ package.json         exports: "." → lib/host.js, "./client" → lib/client.js
│  ├─ cordis.patch.yml     insert id=course-kit, name=dsh-course-kit
│  ├─ SKILL.md             技能正文（控制台里注册为 course-kit 技能）
│  ├─ README.md
│  ├─ lib/
│  │  ├─ py.js             ★ 进程与路径层：findPython / runScript（第三个参数是进度回调）/ call
│  │  ├─ host.js           ★ 12 个 agent 工具 + 技能注册
│  │  └─ client.js         ★ 浏览器控制台 UI（三个页签）
│  └─ engine/              14 个 Python 脚本，见下表
├─ console/                包名 dsh-course-kit-console（只有宿主半）
│  ├─ package.json         main: lib/main.js
│  ├─ cordis.patch.yml     insert id=course-kit-console
│  ├─ lib/main.js          ★ 全部 /coursekit/* 路由 + 作业链执行器（BUILD = console-4-out）
│  └─ test/smoke.mjs       ★ 45 条断言的自检
└─ presets/                用户可编辑的判断层
   ├─ _默认.json / _默认.notes.json    内置通用预设（下划线开头，排在最前）
   ├─ 大师们的写作课.json / .notes.json  为那本书手写的角色预设与注解
   ├─ BV16q4y1S7Ap.notes.json          示例视频的文稿注解
   └─ deck_大师们的写作课.json           课件规格

demo/                      跑出来的东西全在这里，脚本源码不含任何产物
├─ out/                    ★ 所有产物的落点，**一个素材一个目录**
│  ├─ 文化论/              book/（paragraphs.json/md + ocr_pages/ 逐页缓存）
│  │                       + 01_重点提取_文化论.md / .docx  ← 整本 OCR 后出的书摘
│  ├─ 东方学/              同上（只跑了前 10 页试版式）
│  ├─ 冒烟测试留下的目录会自己清掉（COURSEKIT_SMOKE_NO_RM=1 时留给 Python 收）
│  └─ book/ video/ deck/   ★ 老约定：所有书挤在 out/book 里，会互相覆盖，已不用
├─ uploads/                控制台拖进来的原文件（原文件不动，这里只是副本）
├─ book/ video/ render/    早期几轮的临时工作目录，与现在的流程无关，可删
```

**产物目录的约定（2026-10-16 起）**：一律 `demo/out/<书名或视频名>/`，由 `console/lib/main.js`
的 `safeDirName()` 从标题生成（挡掉 `\/:*?"<>|` 与结尾的点和空格）。
书籍的中间产物在该目录的 `book/` 子目录里，书摘与 Word 直接放该目录下；
视频同理放在 `video/` 子目录。**别再退回"所有书共用一个 out/book"**：
两本书先后跑会覆盖同一份 `paragraphs.json`（这是改之前真实发生过的事）。

### 仓库（2026-10-16 起）

`course-kit/` 本身就是一个 git 仓库（分支 `main`），为了搬到别的机器上测试。
**入库的只有源码与文档**：`.gitignore` 挡掉 `.pylibs/`（各机按平台重装）与 `demo/`
（上传的素材、产物、逐页缓存：体积大且含版权内容）。提交前用 `git status` 扫一眼，
别把 `.pylibs` 或某本书扫进去。`.gitattributes` 里统一 `eol=lf`——
本机 git 的系统配置是 `core.autocrlf=true`，不覆盖的话到了 macOS 上会整片文件显示为已修改。

**系统级 git 配置里 `init.defaultbranch=hehe`**，所以建新仓库要显式 `git init -b main`。

### 引擎脚本

| 脚本 | 角色 |
|---|---|
| `pdf_probe.py` | 勘察一本书的排版（目录、字体、缩进），写新预设前先跑它 |
| `pdf_extract.py` | 文字版 PDF → 带页码的段落单元（`paragraphs.json` / `.md`） |
| `pdf_ocr.py` | **扫描件** → 同构的段落单元（逐页渲染 + 本地 OCR，逐页缓存） |
| `digest.py` | **通用重点提取**（默认）：关键词 + 要点摘句 + 重点段落原文 |
| `curate.py` | 按角色预设归类演示／说明／批注（可选路径） |
| `book_report.py` | 按注解预设组装书摘（可选路径） |
| `video_fetch.py` | B 站或本地视频 → source.mp4 + audio_16k.wav + 抽帧 |
| `video_asr.py` | SenseVoice 本地识别 → asr.json / asr.srt / asr.txt |
| `frame_sheets.py` | 字幕条带拼图 / 文字卡片抽取（省读图次数） |
| `video_report.py` | 文稿 + 摘要 |
| `deck_build.py` | 课件规格 JSON → pptx（7 种版式） |
| `deck_preview.py` | pptx → PNG + 文本框溢出报告 |
| `md_to_docx.py` | Markdown → Word（正文小四宋体，标题小三四号加粗提亮） |
| `copy_file.py` | 链条里"另存一份"这一步 |

### 两种书籍提取方式（重要）

**默认**是通用重点提取，**不需要任何预设，换哪本书都能跑**：
按段落的通用特征排序——长度是否适中、是不是章首章末、有没有引文与数字、连接词密度、
对本章词汇的代表性；再按 MMR 去冗余。
产出「关键词 + 要点摘句 + 重点段落原文」，**总结是抽取式的**（挑原句），不是模型写的概述。

**可选**是演示／说明／批注，只在书确实是"引一段原文再点评"的结构时才用。
这套角色来自《大师们的写作课》，换成人类学、历史、科普往往不成立。
`_默认.json` 走的是纯启发式（引出语识别），没有人工覆写。

## 五、平台约束：宿主半的改动需要重启

**这是最容易浪费时间的地方，务必先读。**

Loader 把加载过的宿主模块**永久留在 ESM 缓存里**。实测确认：
改文件内容 ✗、改 `package.json` 的 `main`/`exports` ✗、重挂 bundle ✗、改版本号 ✗
——都不会重新导入。**只有换一个文件路径才会。**

- 客户端半（`plugin/lib/client.js`）：**刷新页面即可**。
- 宿主半（`console/lib/main.js`、`plugin/lib/host.js`、`plugin/lib/py.js`）：**必须重启 DSH**。

诊断手法：注册一个探针路由，然后用 Python requests 打 `http://127.0.0.1:19387/<路由>`，
**401 = 已注册**（被 `connection.requestRejection` 信任闸拦下，正常），**404 = 未注册**。
也可以对比已知存在的 `/storyboard/state`（同样 401）。

正因为这条约束，`console/lib/main.js` 是全自包含的，不跨包 import。

### 安装与重挂

用 `plugin_manager` 工具（需要 `dangerouslySkipPermissions: true`）：

- 安装：`install_bundle`，target = `link:E:/desktop/肥鱼专属空间/course-kit/plugin`
  （控制台是 `.../course-kit/console`）
- 重挂：`set_bundle` enabled=false 再 true

两个包都已在 profile `desktop` 里注册好了，正常情况下不用再动。

## 六、怎么验

```powershell
# 逻辑层（45 条断言，含作业链条的跳过/中止/状态判定与 OCR 分支）
# 沙箱下 node 删不了工作区里的文件，加这个开关跳过清理（跑完用 Python 收残渣）：
$env:COURSEKIT_SMOKE_NO_RM="1"
node E:\desktop\肥鱼专属空间\course-kit\console\test\smoke.mjs

# 端到端（在 pwsh 里直接跑脚本，绕过沙箱的 spawn 限制）
$env:PYTHONPATH="E:\desktop\肥鱼专属空间\course-kit\.pylibs"
$env:PYTHONUTF8="1"
$py="C:\Users\win10\AppData\Local\Programs\Python\Python310\python.exe"
& $py -u -W ignore <engine脚本> <参数...>

# 扫描件走一遍（先小范围试，缓存留着；再放大到整本）
$eng="E:\desktop\肥鱼专属空间\course-kit\plugin\engine"
& $py -u -W ignore $eng\pdf_ocr.py --pdf <书.pdf> --out <输出目录>\book --pages 28-29
& $py -u -W ignore $eng\digest.py  --paragraphs <输出目录>\book\paragraphs.json --out <输出目录> --name 01_重点提取_<书名> --title <书名>
& $py -u -W ignore $eng\md_to_docx.py --md <输出目录>\01_重点提取_<书名>.md --out <输出目录>\01_重点提取_<书名>.docx
```

每一轮改动后至少跑一次冒烟测试。它覆盖：路由注册、上传落盘、预设读写与越权拦截、
九种作业链场景（齐全／缺预设／默认提取／空段落扫描件／脚本自报扫描件／勾了 OCR／默认输出目录／失败中止／三链独立）。
**已知 1 条 SKIP**（`/coursekit/outline` 需要真跑 Python，沙箱禁止带管道的 spawn），
那部分证据是直接跑 `pdf_extract.py --list` 拿到的（见第二节的实跑记录）。

## 七、建议的下一步

1. ~~修扫描件那个 bug~~ **已完成**（第二节），连带 4 条断言（场景 G/H）。
2. ~~做 OCR~~ **已完成**（第三节「扫描件 OCR」），控制台、工具、文档一并接上，
   连带 5 条断言（场景 I）。**下一步建议拿用户书单里的一两本真书再跑一遍**：
   目前只验过《文化论》一本（125 页、24 章、印刷体横排）。
   值得留意的差异：竖排古籍、双栏排版、表格与图注、繁体与异体字——
   这些都可能让"按缩进切段"失效；真遇到，只改 `pdf_ocr.py` 的
   `estimate_layout` 与 `is_running_head` 两处即可，别动下游。
3. **要不要把 npm 包名也改掉**（`dsh-course-kit` → 新名字）。现在改的只是界面显示名：
   侧栏、页面标题、包描述、SKILL.md。插件管理页里显示的是 cordis patch 条目的
   `name`（即包名），要改它得同步改 profile 的 `package.json`（dependencies + bundles）、
   两个 `cordis.patch.yml` 与 `client.js` 里的 `__ModuleLoader__.load({id})`，
   再重挂 bundle 并重启——**风险与收益都不大**，等用户明确要求再说。
4. 冒烟测试有一条环境相关的开关（`COURSEKIT_SMOKE_NO_RM`）。
   如果哪天沙箱修好了 node 的删除，这个开关可以留着不管，也可以顺手删掉。
5. 可选优化（都量过，收益不大，先不做）：识别模型 int8 量化（1.3×）、
   把 OCR 限制到只在正文区域认字（省掉页边噪声）。


## 八、用户已知的偏好

- 报错要说人话，不要把工具的限制说成用户的顺序错误。
- 不要让他为每本书做配置；预设定制是"有必要时再说"的选项，不是默认流程。
- 课件标题要写具体的东西（「兔头、香椿，和一只铝饭盒」），
  **避免 AI 腔的总结式标题**（「从 X 到 Y：…」「全流程：…」「赋能」「揭秘」）。
- 界面文字行距要松，别挤。
- 他会直接看浏览器界面，所以描述改动时要讲清"刷新页面"还是"重启应用"。
