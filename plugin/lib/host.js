// course-kit —— 书籍 / 视频 → 三类产物（书摘、文稿、课件）的宿主半。
//
// 设计上刻意"薄"：所有真正的活都由 engine/ 下的 Python 脚本干，
// 这一层只负责三件事——
//   1. 把工具参数翻译成命令行参数（于是接口就是 JSON Schema，能被模型直接调用）；
//   2. 串行化 Python 进程（抽帧、识别都是 CPU 密集，并行只会互相拖慢）；
//   3. 把结果路径、统计数字和错误尾巴原样回给调用方，不做二次加工。
//
// 之所以不把逻辑写在 JS 里：PDF 版面解析、ONNX 识别、pptx 生成在 Python 侧
// 都有成熟实现，而"原文不许被改写"这条要求，靠脚本搬运比靠模型转述可靠得多。
//
// 浏览器控制台是另一个包（dsh-course-kit-console）。分开不是洁癖：
// 宿主半的模块进了 ESM 缓存就不会热更，拆开才能只重挂控制台那一半。

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { ENGINE, LIB_DIR, call, findPython } from "./py.js";

/** 插件在 Loader 中的名字。 */
const name = "course-kit";
/** 注入的服务：tools 注册工具，skills 注册技能说明。 */
const inject = ["tools", "skills"];

const HERE = resolve(ENGINE, "..", "lib");

function flag(value) {
  return value === undefined || value === null || value === "" ? null : String(value);
}

// ------------------------------------------------------------------ 工具注册
function textRender(_args, value) {
  return [{ type: "text", text: JSON.stringify(value, null, 1) }];
}

const OUT_SCHEMA = { type: "object", additionalProperties: true };

function define(registry, def) {
  return registry.register({
    ...def,
    output: { schema: OUT_SCHEMA, render: textRender },
  });
}

const BOOK_OUTLINE = {
  name: "coursekit_book_outline",
  description:
    "列出 PDF 的目录（章节标题 + 起止页）与文本层统计。做书籍摘录前先调它，" +
    "把章节目录交给用户或自己挑，再用 coursekit_book_extract 按章提取；" +
    "若返回 hasTextLayer: false（整本都是图片），改用 coursekit_book_ocr。",
  parameters: {
    type: "object",
    properties: { pdf: { type: "string", description: "PDF 绝对路径" } },
    required: ["pdf"],
    additionalProperties: false,
  },
  execute: async (args) => call("pdf_extract.py", ["--pdf", args.pdf, "--list"]),
};

const BOOK_EXTRACT = {
  name: "coursekit_book_extract",
  description:
    "按章节或页范围把 PDF 拆成带页码的段落单元。只对**有文本层**的 PDF 有效；" +
    "扫描件会以退出码 3 报明 hasTextLayer: false，那种书改用 coursekit_book_ocr。" +
    "chapters 支持模糊匹配（如「美食」匹配「【美食】各种普通食物最好吃的时刻」）；" +
    "preset 指向角色规则 JSON 时，会额外用 curate.py 做演示单元归并——换书时改 preset，不必改代码。",
  parameters: {
    type: "object",
    properties: {
      pdf: { type: "string" },
      outDir: { type: "string", description: "输出目录，写 paragraphs.json / paragraphs.md" },
      chapters: { type: "array", items: { type: "string" }, description: "章节名，支持模糊匹配" },
      pages: { type: "string", description: "1-based 页范围，如 27-34；与 chapters 二选一" },
      preset: { type: "string", description: "角色规则与人工覆写的 JSON 路径" },
      minChars: { type: "number", description: "短于此长度的段落丢弃，默认 8" },
    },
    required: ["pdf", "outDir"],
    additionalProperties: false,
  },
  execute: async (args) => {
    const common = ["--pdf", args.pdf, "--out", args.outDir];
    if (args.chapters?.length) common.push("--chapters", args.chapters.join(","));
    if (args.pages) common.push("--pages", String(args.pages));
    if (args.minChars) common.push("--min-chars", String(args.minChars));
    const extract = await call("pdf_extract.py", common);
    if (extract.isError || !args.preset) return extract;
    const curate = await call("curate.py", [
      "--paragraphs", join(args.outDir, "paragraphs.json"),
      "--spec", args.preset,
      "--out", args.outDir,
    ]);
    return { isError: curate.isError, value: { extract: extract.value, curate: curate.value } };
  },
};

const BOOK_OCR = {
  name: "coursekit_book_ocr",
  description:
    "扫描件 OCR：PDF 每页都是图片（没有文本层）时用它取字——逐页渲染成本地位图，" +
    "用本机 RapidOCR（ONNX，CPU，不联网）识别。产出的 paragraphs.json / paragraphs.md " +
    "与 coursekit_book_extract 完全同构，下游 digest / curate / report 一行都不用改。" +
    "逐页有缓存，中断后重跑会跳过已识别的页。一页三四秒，先跑 coursekit_book_outline " +
    "看 hasTextLayer，只有扫描件（false）才走这条路。",
  parameters: {
    type: "object",
    properties: {
      pdf: { type: "string" },
      outDir: { type: "string", description: "输出目录，写 paragraphs.json / paragraphs.md 与逐页缓存 ocr_pages/" },
      chapters: { type: "array", items: { type: "string" }, description: "章节名，支持模糊匹配；不选则整本" },
      pages: { type: "string", description: "1-based 页范围，如 28-29；与 chapters 二选一" },
      dpi: { type: "number", description: "渲染清晰度，默认 200；小字密排可到 300" },
      workers: { type: "number", description: "并行识别的线程数，默认按 CPU 自动（约 核数/4）" },
      limit: { type: "number", description: "只处理前 N 页，试跑用" },
    },
    required: ["pdf", "outDir"],
    additionalProperties: false,
  },
  execute: async (args) => {
    const argv = ["--pdf", args.pdf, "--out", args.outDir];
    if (args.chapters?.length) argv.push("--chapters", args.chapters.join(","));
    if (args.pages) argv.push("--pages", String(args.pages));
    if (args.dpi) argv.push("--dpi", String(args.dpi));
    if (args.workers) argv.push("--workers", String(args.workers));
    if (args.limit) argv.push("--limit", String(args.limit));
    return call("pdf_ocr.py", argv);
  },
};

const BOOK_DIGEST = {
  name: "coursekit_book_digest",
  description:
    "通用重点提取：不需要任何预设，对任何书都能跑。按段落的通用特征（长度、章首章末、引文与数字、" +
    "连接词密度、对本章词汇的代表性）挑出重点段落，配关键词与抽取式要点摘句，原文逐字保留并标页码。" +
    "这是书籍处理的默认路径；「演示／说明／批注」那套角色只适用于写作教材一类的书。",
  parameters: {
    type: "object",
    properties: {
      paragraphs: { type: "string", description: "pdf_extract.py 产出的 paragraphs.json" },
      out: { type: "string", description: "输出目录，写 <name>.md 与 <name>.json" },
      name: { type: "string", description: "产物文件名（不含扩展名），默认 digest" },
      perChapter: { type: "number", description: "每章保留几段重点段落，默认 6" },
      sentences: { type: "number", description: "每章几句要点摘句，默认 4" },
      keywords: { type: "number", description: "每章几个关键词，默认 10" },
      title: { type: "string", description: "书名，用于报告标题" },
    },
    required: ["paragraphs", "out"],
    additionalProperties: false,
  },
  execute: async (args) => {
    const argv = ["--paragraphs", args.paragraphs, "--out", args.out];
    if (args.name) argv.push("--name", String(args.name));
    if (args.perChapter) argv.push("--per-chapter", String(args.perChapter));
    if (args.sentences) argv.push("--sentences", String(args.sentences));
    if (args.keywords) argv.push("--keywords", String(args.keywords));
    if (args.title) argv.push("--title", args.title);
    return call("digest.py", argv);
  },
};

const BOOK_REPORT = {
  name: "coursekit_book_report",
  description:
    "把 sections.json 组装成「原文 + 总结」的书摘报告（Markdown）。" +
    "演示原文与批注由脚本逐字搬运，notes.json 只提供章旨、技能点与单元要点。",
  parameters: {
    type: "object",
    properties: {
      sections: { type: "string", description: "curate.py 产出的 sections.json" },
      out: { type: "string", description: "输出的 Markdown 路径" },
      notes: { type: "string", description: "章旨与要点的 JSON 路径" },
      title: { type: "string", description: "书名，用于报告标题" },
    },
    required: ["sections", "out"],
    additionalProperties: false,
  },
  execute: async (args) => {
    const argv = ["--sections", args.sections, "--out", args.out];
    if (args.notes) argv.push("--notes", args.notes);
    if (args.title) argv.push("--title", args.title);
    return call("book_report.py", argv);
  },
};

const VIDEO_FETCH = {
  name: "coursekit_video_fetch",
  description:
    "取回并拆解一个视频：B 站链接（走 playurl 接口，绕开网页端 412）或本地文件，" +
    "产出 source.mp4、audio_16k.wav 与等间隔抽帧。start/end 用于只分析某个时间段。",
  parameters: {
    type: "object",
    properties: {
      url: { type: "string", description: "B 站视频地址或 BV 号" },
      file: { type: "string", description: "本地视频绝对路径；与 url 二选一" },
      outDir: { type: "string" },
      start: { type: "number", description: "起始秒（含）" },
      end: { type: "number", description: "结束秒（不含）" },
      frames: { type: "number", description: "等间隔抽帧数量，默认 12；0 表示不抽" },
      quality: { type: "number", description: "期望清晰度 qn，默认 80（未登录会被降级到 480P）" },
    },
    required: ["outDir"],
    additionalProperties: false,
  },
  execute: async (args) => {
    const argv = ["--out", args.outDir];
    if (args.url) argv.push("--url", args.url);
    if (args.file) argv.push("--file", args.file);
    for (const [key, key2] of [["start", "--start"], ["end", "--end"], ["frames", "--frames"], ["quality", "--quality"]]) {
      const value = flag(args[key]);
      if (value !== null) argv.push(key2, value);
    }
    return call("video_fetch.py", argv);
  },
};

const VIDEO_TRANSCRIBE = {
  name: "coursekit_video_transcribe",
  description:
    "本地语音识别（SenseVoice ONNX + Silero VAD，纯 CPU，不上传音频），" +
    "把 audio_16k.wav 转成 asr.json / asr.srt / asr.txt。7 分钟音频约 20 秒。",
  parameters: {
    type: "object",
    properties: {
      wav: { type: "string", description: "16kHz 单声道 wav" },
      outDir: { type: "string" },
      modelDir: { type: "string", description: "SenseVoice 模型根目录（含 sensevoice-onnx 与 silero）" },
      threads: { type: "number" },
    },
    required: ["wav", "outDir"],
    additionalProperties: false,
  },
  execute: async (args) => {
    const argv = ["--wav", args.wav, "--out", args.outDir];
    if (args.modelDir) argv.push("--model-dir", args.modelDir);
    if (args.threads) argv.push("--threads", String(args.threads));
    return call("video_asr.py", argv);
  },
};

const VIDEO_FRAMES = {
  name: "coursekit_video_frames",
  description:
    "画面取样：strip 模式把烧进画面的字幕按固定间隔裁成条带、纵向拼成长图，" +
    "一次即可核对几十个时间点；cards 模式按「白字黑边」特征挑出中途出现的文字卡片并去重。" +
    "两者都是为了少读图——逐帧读图比拼图贵一个数量级。",
  parameters: {
    type: "object",
    properties: {
      mode: { type: "string", enum: ["strip", "cards"] },
      video: { type: "string" },
      out: { type: "string" },
      every: { type: "number", description: "采样间隔秒，strip 默认 2.5，cards 默认 0.6" },
      start: { type: "number" },
      end: { type: "number" },
      band: { type: "string", description: "strip：纵向取带 top,bottom（0~1），如 0.56,0.68" },
      box: { type: "string", description: "cards：检测区域 x0,y0,x1,y1（0~1）" },
      perSheet: { type: "number", description: "strip：每张拼图放几行，默认 28" },
      white: { type: "number", description: "cards：近白像素占比阈值，默认 0.03" },
    },
    required: ["mode", "video", "out"],
    additionalProperties: false,
  },
  execute: async (args) => {
    const argv = [args.mode, "--video", args.video, "--out", args.out];
    if (args.every) argv.push("--every", String(args.every));
    if (args.start) argv.push("--start", String(args.start));
    if (args.end) argv.push("--end", String(args.end));
    if (args.band) argv.push("--band", args.band);
    if (args.box) argv.push("--box", args.box);
    if (args.perSheet) argv.push("--per-sheet", String(args.perSheet));
    if (args.white) argv.push("--white", String(args.white));
    return call("frame_sheets.py", argv);
  },
};

const VIDEO_REPORT = {
  name: "coursekit_video_report",
  description:
    "把 asr.json 组装成「文稿 + 摘要」。逐句文稿由脚本按时间码搬运，不做顺句改写；" +
    "notes.json 提供分节、专名勘误（index → 正确文本）、画面文字抄录与摘要要点。",
  parameters: {
    type: "object",
    properties: {
      asr: { type: "string", description: "video_asr.py 产出的 asr.json" },
      out: { type: "string" },
      notes: { type: "string" },
      manifest: { type: "string", description: "video_fetch.py 产出的 manifest.json，用于补全时长与来源" },
    },
    required: ["asr", "out"],
    additionalProperties: false,
  },
  execute: async (args) => {
    const argv = ["--asr", args.asr, "--out", args.out];
    if (args.notes) argv.push("--notes", args.notes);
    if (args.manifest) argv.push("--manifest", args.manifest);
    return call("video_report.py", argv);
  },
};

const DECK_BUILD = {
  name: "coursekit_deck_build",
  description:
    "按课件规格 JSON 生成 pptx。规格里的 quote_ref / para / ann 是对 sections.json、paragraphs.json 的引用，" +
    "长段原文一律由本工具取，规格里不出现引文——手抄会错字，模型转述会改字。" +
    "版式：cover / section / bullets / quote_side（演示原文主体 + 说明与批注旁栏）/ two_quote / table / end。",
  parameters: {
    type: "object",
    properties: {
      spec: { type: "string", description: "课件规格 JSON" },
      out: { type: "string", description: "输出的 pptx 路径" },
      sections: { type: "string", description: "用于解析 quote_ref / ann" },
      paragraphs: { type: "string", description: "用于解析 para（可指向任意段落，含批注段）" },
    },
    required: ["spec", "out"],
    additionalProperties: false,
  },
  execute: async (args) => {
    const argv = ["--spec", args.spec, "--out", args.out];
    if (args.sections) argv.push("--sections", args.sections);
    if (args.paragraphs) argv.push("--paragraphs", args.paragraphs);
    return call("deck_build.py", argv);
  },
};

const DECK_PREVIEW = {
  name: "coursekit_deck_preview",
  description:
    "把 pptx 读回来画成 PNG，并报告每个文本框的「需要高度 / 可用高度」；比值大于 1 就是溢出。" +
    "交付前跑一次，可以避免翻开才发现文字压到页脚之外这类问题。",
  parameters: {
    type: "object",
    properties: {
      pptx: { type: "string" },
      out: { type: "string", description: "PNG 输出目录" },
      only: { type: "string", description: "只渲染这些页，如 1,5,17" },
    },
    required: ["pptx", "out"],
    additionalProperties: false,
  },
  execute: async (args) => {
    const argv = ["--pptx", args.pptx, "--out", args.out];
    if (args.only) argv.push("--only", args.only);
    return call("deck_preview.py", argv);
  },
};

const MD_DOCX = {
  name: "coursekit_md_docx",
  description:
    "把 Markdown 转成 Word。正文小四（12pt）宋体、行距 1.5；标题小三／四号（15／14pt）微软雅黑加粗并提亮；" +
    "引用块（原文）小四宋体加浅底纹与左竖线；行内 **加粗** 视为重点，一并提亮。",
  parameters: {
    type: "object",
    properties: {
      md: { type: "string", description: "Markdown 路径" },
      out: { type: "string", description: "输出的 .docx 路径" },
    },
    required: ["md", "out"],
    additionalProperties: false,
  },
  execute: async (args) => call("md_to_docx.py", ["--md", args.md, "--out", args.out]),
};

// ------------------------------------------------------------------ 技能说明
function skillContent() {
  const path = resolve(HERE, "..", "SKILL.md");
  if (existsSync(path)) return readFileSync(path, "utf8");
  return "course-kit 技能文档缺失。";
}

function apply(ctx) {
  const definitions = [
    BOOK_OUTLINE,
    BOOK_EXTRACT,
    BOOK_OCR,
    BOOK_DIGEST,
    BOOK_REPORT,
    VIDEO_FETCH,
    VIDEO_TRANSCRIBE,
    VIDEO_FRAMES,
    VIDEO_REPORT,
    DECK_BUILD,
    DECK_PREVIEW,
    MD_DOCX,
  ];
  for (const definition of definitions) {
    ctx.effect(() => define(ctx.tools, definition), `course-kit: ${definition.name}`);
  }

  ctx.effect(
    () =>
      ctx.skills.register({
        name: "course-kit",
        description:
          "书籍、视频摘要：把一本书和一个视频做成教学材料。书默认走「通用重点提取」（不要预设，任何书都能跑），" +
          "也可以按「演示／说明／批注」角色预设出书摘；视频转成带时间码的文稿并出摘要；两者可组装成讲义式 PPT。",
        whenToUse:
          "当用户要求「读这本书并总结某几章」「把这段视频转文字并总结」「把内容做成课件/PPT」，" +
          "或明确要保留原文、区分演示段落与作者批注时使用。",
        source: "runtime",
        invocation: { modelInvocable: true, userInvocable: true },
        content: skillContent(),
        metadata: { engine: ENGINE, python: findPython(), libDir: LIB_DIR },
      }),
    "course-kit: skill"
  );
}

export { apply, inject, name };
