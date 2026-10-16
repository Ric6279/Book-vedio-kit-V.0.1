// 控制台宿主半的冒烟测试。
//
// 为什么不靠界面点：宿主模块进了 ESM 缓存，改完在跑着的应用里不会生效，
// 只能重启后才验证——而"改完等重启再看"会把问题留到用户手里。
//
// 分两段：
//   第一段用真 apply(ctx)，验证路由、上传、预设读写与越权拦截；
//   第二段注入一个假的 runScript，把作业链条的"跳过 / 中止 / 状态判定"
//   逐条走一遍——不需要启动任何 Python 进程，所以结果只取决于逻辑本身。
//
// 真正的 Python 执行不在这里验：当前沙箱禁止带管道的 spawn（EPERM），
// 那部分的证据是在运行中的应用里调 coursekit_book_outline 拿到的真实目录。
//
// 用法：node console/test/smoke.mjs

import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { apply, createConsole } from "../lib/main.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE = resolve(HERE, "..", ".."); // …/course-kit
// 假素材与假产物放在系统临时目录：那里删除正常，也不会把测试残渣留在插件目录里
const SANDBOX = join(tmpdir(), "coursekit-smoke");
const PRESETS = join(WORKSPACE, "presets");

/**
 * 沙箱提醒：当前的 Windows 沙箱下，node 删工作区里的文件会让进程直接崩掉
 * （退出码 0xC0000409；rmSync / rmdirSync / fs.promises.rm 都一样，而 PowerShell 与
 * Python 删同一个文件没事）。所以工作区里的测试残渣走这个开关：
 * COURSEKIT_SMOKE_NO_RM=1 时跳过删除，运行完把残留路径打出来，由调用方清理。
 * 临时目录里的 SANDBOX 不受影响，照常删。
 */
const NO_RM = process.env.COURSEKIT_SMOKE_NO_RM === "1";
const leftovers = [];
function purge(path) {
  if (NO_RM) { leftovers.push(path); return; }
  rmSync(path, { recursive: true, force: true });
}

const results = [];
const uploads = []; // 自己上传的文件自己收尾，别把测试残留留在 uploads 目录里
function check(name, ok, detail) {
  results.push({ name, ok, detail });
}
function skip(name, why) {
  results.push({ name, ok: true, skipped: true, detail: why });
}

function makeCtx(store) {
  return {
    webServer: {
      register(route) {
        store.set(route.path, route.handler);
        return () => store.delete(route.path);
      },
    },
    connection: { requestRejection: () => undefined },
    effect(fn) {
      const d = fn();
      return typeof d === "function" ? d : () => undefined;
    },
  };
}

function makeRes() {
  return {
    statusCode: 200,
    headers: {},
    body: "",
    setHeader(k, v) { this.headers[k] = v; },
    end(chunk) { if (chunk !== undefined) this.body += String(chunk); },
  };
}

function makeCaller(routes) {
  return async function callRoute(path, { method = "GET", query = {}, body, rawBody, headers = {} } = {}) {
    const handler = routes.get(path);
    if (!handler) throw new Error(`路由未注册：${path}`);
    const url = path + (Object.keys(query).length ? "?" + new URLSearchParams(query).toString() : "");
    const req = {
      method,
      url,
      headers,
      async *[Symbol.asyncIterator]() {
        if (rawBody) yield rawBody;
        else if (body !== undefined) yield Buffer.from(JSON.stringify(body), "utf8");
      },
    };
    const res = makeRes();
    await handler(req, res);
    let json = null;
    try { json = JSON.parse(res.body); } catch { /* 非 JSON */ }
    return { status: res.statusCode, json, text: res.body };
  };
}

rmSync(SANDBOX, { recursive: true, force: true });
mkdirSync(SANDBOX, { recursive: true });

// ================================================================== 第一段：真实现
{
  const routes = new Map();
  apply(makeCtx(routes));
  const call = makeCaller(routes);

  const version = await call("/coursekit/version");
  check("GET /coursekit/version 返回构建号", version.json.build === "console-4-out", JSON.stringify(version.json));

  const state = await call("/coursekit/state");
  const names = (state.json.presets || []).map((p) => p.name);
  check("GET /coursekit/state 列出预设", names.length > 0, names.join(", "));
  check("内置默认预设排在最前", names[0] === "_默认.json", names.slice(0, 3).join(", "));
  check("内置注解预设与角色预设配对",
    names.includes("_默认.json") && names.includes("_默认.notes.json"), "_默认.json / _默认.notes.json");
  check("三类作业各自独立", Object.keys(state.json.jobs || {}).join(",") === "book,video,deck",
    Object.keys(state.json.jobs || {}).join(","));

  const payload = Buffer.from("%PDF-1.4 假的 PDF，只测落盘", "utf8");
  const upload = await call("/coursekit/upload", {
    method: "POST", rawBody: payload, headers: { "x-filename": encodeURIComponent("测试书.pdf") },
  });
  check("POST /coursekit/upload 落盘并返回路径", upload.json.ok && upload.json.size === payload.length, upload.json.path);
  if (upload.json.path) uploads.push(upload.json.path);

  const saved = await call("/coursekit/preset/save", {
    method: "POST",
    body: { name: "冒烟测试", kind: "role", content: JSON.stringify({ _说明: "测试用", overrides: {} }, null, 2) },
  });
  check("POST /coursekit/preset/save 写出预设", saved.json.ok && saved.json.file === "冒烟测试.json", saved.json.file);
  const reloaded = JSON.parse(readFileSync(join(PRESETS, "冒烟测试.json"), "utf8"));
  check("写出的预设内容可解析", reloaded._说明 === "测试用", JSON.stringify(reloaded).slice(0, 50));

  const bad = await call("/coursekit/preset/save", {
    method: "POST", body: { name: "坏的", kind: "role", content: "{ 不是 JSON" },
  });
  check("非法 JSON 被拒绝", bad.status === 400, bad.json.message);
  check("越权名字被清洗后落回 presets",
    (await call("/coursekit/preset/save", {
      method: "POST", body: { name: "../逃逸", kind: "role", content: "{}" },
    })).json.file === ".._逃逸.json", "..\\_逃逸.json");

  const escape = await call("/coursekit/preset/read", { query: { path: join(WORKSPACE, "plugin", "package.json") } });
  check("预设读取挡下目录外的路径", escape.status === 403, String(escape.status));

  const inside = await call("/coursekit/preset/read", { query: { path: join(PRESETS, "冒烟测试.json") } });
  check("预设读取能读 presets 内的文件", inside.json.ok, String(inside.status));

  // 沙箱不允许带管道的 spawn，这条只能记为环境限制并说明它在哪里验过
  const outline = await call("/coursekit/outline", { query: { path: join(resolve(WORKSPACE, ".."), "大师们的写作课.pdf") } });
  if (String((outline.json || {}).message || "").includes("EPERM")) {
    skip("GET /coursekit/outline 读出章节", "本沙箱禁止带管道的 spawn（EPERM）；已在运行中的应用里用 coursekit_book_outline 验证过（返回 36 条目录）");
  } else {
    check("GET /coursekit/outline 读出章节", outline.json.ok && (outline.json.chapters || []).length > 30,
      `${(outline.json.chapters || []).length} 条目录`);
  }
}

// ================================================================== 第二段：假 runner 验链条
{
  const routes = new Map();
  const produced = [];
  /** 置为 true 时，假的 pdf_extract 写出一份"空段落"的文件——模拟扫描件。 */
  let emptyParagraphs = false;
  /** 假 runner：按脚本名"产出"文件，用来控制每一步的 requires 是否满足。 */
  const stub = async (script, argv) => {
    produced.push(script);
    if (script === "pdf_extract.py") {
      const out = argv[argv.indexOf("--out") + 1];
      mkdirSync(out, { recursive: true });
      const rows = emptyParagraphs
        ? []
        : [{ chapter: "章", index: 1, page_start: 1, page_end: 1, text: "这是一段真的原文。", chars: 9 }];
      writeFileSync(join(out, "paragraphs.json"), JSON.stringify(rows), "utf8");
      writeFileSync(join(out, "paragraphs.md"), "# 段落\n", "utf8");
      return { ok: true, log: JSON.stringify({ ok: true, paragraphs: rows.length }) };
    }
    if (script === "pdf_ocr.py") {
      const out = argv[argv.indexOf("--out") + 1];
      mkdirSync(out, { recursive: true });
      const rows = [{ chapter: "章", index: 1, page_start: 1, page_end: 1, text: "OCR 认出来的一段原文。", chars: 11 }];
      writeFileSync(join(out, "paragraphs.json"), JSON.stringify(rows), "utf8");
      writeFileSync(join(out, "paragraphs.md"), "# 段落（OCR）\n", "utf8");
      return { ok: true, log: "[ocr] 1/1  p1   18 行  识别 3.9s  置信 0.99\n" + JSON.stringify({ ok: true, paragraphs: 1, chars: 11 }) };
    }
    if (script === "curate.py") {
      const out = argv[argv.indexOf("--out") + 1];
      writeFileSync(join(out, "sections.json"), "[]", "utf8");
      return { ok: true, log: "{}" };
    }
    if (script === "book_report.py") {
      writeFileSync(argv[argv.indexOf("--out") + 1], "# 书摘\n", "utf8");
      return { ok: true, log: "{}" };
    }
    if (script === "digest.py") {
      const out = argv[argv.indexOf("--out") + 1];
      const stem = argv[argv.indexOf("--name") + 1];
      writeFileSync(join(out, `${stem}.md`), "# 重点提取\n", "utf8");
      writeFileSync(join(out, `${stem}.json`), "[]", "utf8");
      return { ok: true, log: "{}" };
    }
    if (script === "md_to_docx.py") {
      writeFileSync(argv[argv.indexOf("--out") + 1], "docx", "utf8");
      return { ok: true, log: "{}" };
    }
    return { ok: true, log: "{}" };
  };

  const api = createConsole({ runScript: stub });
  api.register(makeCtx(routes), () => false);
  const call = makeCaller(routes);
  const outDir = join(SANDBOX, "chain");
  const pdf = join(SANDBOX, "书.pdf");
  writeFileSync(pdf, "%PDF-1.4", "utf8");

  // 角色路径的四个场景都要显式声明 mode，否则会走默认的通用提取——这正是想要的行为
  async function runBook(extra, label) {
    produced.length = 0;
    emptyParagraphs = Boolean(extra.emptyParagraphs);
    const body = { ...extra };
    delete body.emptyParagraphs;
    rmSync(outDir, { recursive: true, force: true });
    const res = await call("/coursekit/run", {
      method: "POST",
      body: {
        kind: "book", pdf, title: "链条", outDir, chapters: [], full: false,
        mode: "roles", formats: { md: true, docx: true }, ...body,
      },
    });
    // 轮询必须认 id：作业一提交就会占位，但返回的 id 才代表这一轮
    const wantId = res.json.jobId;
    for (let i = 0; i < 400; i += 1) {
      const j = await call("/coursekit/job", { query: { kind: "book" } });
      if (j.json.job.id === wantId && j.json.job.status !== "queued" && j.json.job.status !== "running") {
        return j.json.job;
      }
      await new Promise((r) => setTimeout(r, 25));
    }
    throw new Error(`${label} 超时`);
  }

  // 场景 A：齐全 → 四步全过
  const jobA = await runBook({ presetRole: join(PRESETS, "_默认.json"), presetNotes: join(PRESETS, "_默认.notes.json") }, "A");
  const stepA = (n) => (jobA.steps || []).find((s) => s.name === n) || {};
  check("A 齐全时四步全部执行", produced.join(",") === "pdf_extract.py,curate.py,book_report.py,md_to_docx.py", produced.join(","));
  check("A 全部成功", jobA.status === "done", jobA.status);
  check("A 产出书摘与 Word", stepA("组装书摘").status === "ok" && stepA("转换 Word").status === "ok",
    `${stepA("组装书摘").status}/${stepA("转换 Word").status}`);

  // 场景 B：没选角色预设 → 书摘说明原因，Word 因缺前置跳过，不是报错
  const jobB = await runBook({ presetRole: null, presetNotes: join(PRESETS, "_默认.notes.json") }, "B");
  const stepB = (n) => (jobB.steps || []).find((s) => s.name === n) || {};
  check("B 未选角色预设时不出归类步骤", produced.join(",") === "pdf_extract.py", produced.join(","));
  check("B 书摘被跳过并说明原因",
    stepB("组装书摘").status === "skipped" && String(stepB("组装书摘").reason || "").includes("角色预设"),
    stepB("组装书摘").reason);
  check("B 转 Word 因缺 Markdown 而跳过",
    stepB("转换 Word").status === "skipped" && String(stepB("转换 Word").reason || "").includes("前置产物"),
    stepB("转换 Word").reason);
  check("B 状态为部分完成而非出错", jobB.status === "partial", jobB.status);
  check("B 的跳过提示会引导到通用提取",
    String(stepB("组装书摘").reason || "").includes("通用重点提取"), stepB("组装书摘").reason);

  // 场景 F：默认路径——通用重点提取，零配置
  const jobF = await runBook({ mode: "digest" }, "F");
  const stepF = (n) => (jobF.steps || []).find((s) => s.name === n) || {};
  check("F 通用提取不碰预设，三步走完",
    produced.join(",") === "pdf_extract.py,digest.py,md_to_docx.py", produced.join(","));
  check("F 全部成功", jobF.status === "done", jobF.status);
  check("F 产出重点提取与 Word",
    stepF("通用重点提取").status === "ok" && stepF("转换 Word").status === "ok",
    `${stepF("通用重点提取").status}/${stepF("转换 Word").status}`);
  check("F 没有归类与书摘步骤", !stepF("组装书摘").name && !jobF.steps.some((s) => s.name.includes("归类")),
    (jobF.steps || []).map((s) => s.name).join(" / "));

  // 场景 G：扫描件兜底——提取"成功"却一个段落都没有，别让 digest 去撞空文件
  const jobG = await runBook({ mode: "digest", emptyParagraphs: true }, "G");
  const stepG = (n) => (jobG.steps || []).find((s) => s.name === n) || {};
  check("G 段落为空时不再跑通用提取", produced.join(",") === "pdf_extract.py", produced.join(","));
  check("G 后续步骤标成 skipped", stepG("通用重点提取").status === "skipped", stepG("通用重点提取").status);
  check("G 跳过原因说清是扫描件、需要 OCR",
    String(stepG("通用重点提取").reason || "").includes("扫描件") &&
    String(stepG("通用重点提取").reason || "").includes("OCR"),
    stepG("通用重点提取").reason);
  check("G 不再把空段落说成「先跑 pdf_extract.py」",
    !String(stepG("通用重点提取").reason || "").includes("先跑 pdf_extract"), stepG("通用重点提取").reason);
  check("G 总体状态为部分完成而非出错", jobG.status === "partial", jobG.status);

  // 场景 H：脚本自己以退出码 3 报"扫描件"——原话要一路传到界面上
  const scanMessage = "这份 PDF 没有文本层：577 页里 577 页是图像，提取到 0 个字符。它是扫描件，需要先做 OCR 才能提取文字。";
  const scanConsole = createConsole({
    runScript: async (script, argv) => (
      script === "pdf_extract.py"
        ? { ok: false, code: 3, log: JSON.stringify({ ok: false, message: scanMessage, hasTextLayer: false, imagePages: 577 }) }
        : stub(script, argv)
    ),
  });
  const routes3 = new Map();
  scanConsole.register(makeCtx(routes3), () => false);
  const call3 = makeCaller(routes3);
  const resH = await call3("/coursekit/run", {
    method: "POST",
    body: { kind: "book", pdf, title: "扫描件", outDir, chapters: [], full: false, mode: "digest", formats: {} },
  });
  let jobH = null;
  for (let i = 0; i < 400; i += 1) {
    const j = await call3("/coursekit/job", { query: { kind: "book" } });
    if (j.json.job.id === resH.json.jobId && j.json.job.status !== "queued" && j.json.job.status !== "running") {
      jobH = j.json.job;
      break;
    }
    await new Promise((r) => setTimeout(r, 25));
  }
  const stepH = (n) => ((jobH || {}).steps || []).find((s) => s.name === n) || {};
  check("H 提取失败时把脚本的 message 原样显示", stepH("提取段落与页码").reason === scanMessage, stepH("提取段落与页码").reason);
  check("H 后续步骤的跳过原因也是这句人话", stepH("通用重点提取").reason === scanMessage, stepH("通用重点提取").reason);
  check("H 不再显示「上一步失败：提取段落与页码」",
    !String(stepH("通用重点提取").reason || "").includes("上一步失败"), stepH("通用重点提取").reason);

  // 场景 I：扫描件勾了 OCR → 第一步换成 pdf_ocr.py，后面照旧
  const jobI = await runBook({ mode: "digest", ocr: true }, "I");
  const stepI = (n) => (jobI.steps || []).find((s) => s.name === n) || {};
  check("I 勾了 OCR 时第一步走 pdf_ocr.py",
    produced.join(",") === "pdf_ocr.py,digest.py,md_to_docx.py", produced.join(","));
  check("I 提取那一步的名字变成「OCR 识别扫描页」", stepI("OCR 识别扫描页").status === "ok",
    stepI("OCR 识别扫描页").status);
  check("I 不再有「提取段落与页码」这一步", !stepI("提取段落与页码").name,
    (jobI.steps || []).map((s) => s.name).join(" / "));
  check("I OCR 的进度日志留在这一步里", String(stepI("OCR 识别扫描页").log || "").includes("[ocr]"),
    String(stepI("OCR 识别扫描页").log || "").slice(0, 40));
  check("I 全链条成功", jobI.status === "done", jobI.status);

  // 场景 J：不填输出目录 → demo/out/<书名>，而不是所有人共用的 demo/out/book
  // （以前两本书先后跑会互相覆盖同一份 paragraphs.json）
  produced.length = 0;
  emptyParagraphs = false;
  const resJ = await call("/coursekit/run", {
    method: "POST",
    body: { kind: "book", pdf, title: "冒烟书", chapters: [], full: false, mode: "digest", formats: {} },
  });
  let jobJ = null;
  for (let i = 0; i < 400; i += 1) {
    const j = await call("/coursekit/job", { query: { kind: "book" } });
    if (j.json.job.id === resJ.json.jobId && j.json.job.status !== "queued" && j.json.job.status !== "running") {
      jobJ = j.json.job;
      break;
    }
    await new Promise((r) => setTimeout(r, 25));
  }
  const expectDir = join(WORKSPACE, "demo", "out", "冒烟书");
  check("J 不填输出目录时按书名建目录",
    resolve(String(resJ.json.outDir || "")) === resolve(expectDir), resJ.json.outDir);
  check("J 产物也落在同一个目录里",
    jobJ && resolve(String(jobJ.outDir)) === resolve(expectDir) && (jobJ.artifacts || []).length > 0,
    ((jobJ || {}).artifacts || []).map((a) => a.name).join(", "));
  purge(expectDir);

  // 场景 C：选了角色预设但没注解 → 归类照跑，书摘说明缺注解
  const jobC = await runBook({ presetRole: join(PRESETS, "_默认.json"), presetNotes: null }, "C");
  const stepC = (n) => (jobC.steps || []).find((s) => s.name === n) || {};
  check("C 书摘说明缺注解预设",
    stepC("组装书摘").status === "skipped" && String(stepC("组装书摘").reason || "").includes("注解预设"),
    stepC("组装书摘").reason);

  // 场景 D：必需步骤失败 → 后续全部说明"上一步失败"，总体为 error
  const failing = createConsole({
    runScript: async (script, argv) => (script === "curate.py" ? { ok: false, log: "curate 炸了" } : stub(script, argv)),
  });
  const routes2 = new Map();
  failing.register(makeCtx(routes2), () => false);
  const call2 = makeCaller(routes2);
  const resD = await call2("/coursekit/run", {
    method: "POST",
    body: {
      kind: "book", pdf, title: "失败", outDir, chapters: [], full: false, mode: "roles",
      presetRole: join(PRESETS, "_默认.json"), presetNotes: join(PRESETS, "_默认.notes.json"),
      formats: { md: true, docx: true },
    },
  });
  let jobD = null;
  for (let i = 0; i < 400; i += 1) {
    const j = await call2("/coursekit/job", { query: { kind: "book" } });
    if (j.json.job.id === resD.json.jobId && j.json.job.status !== "queued" && j.json.job.status !== "running") {
      jobD = j.json.job;
      break;
    }
    await new Promise((r) => setTimeout(r, 25));
  }
  const stepD = (n) => (jobD.steps || []).find((s) => s.name === n) || {};
  check("D 失败步骤标红", stepD("归类演示／说明／批注").status === "error", stepD("归类演示／说明／批注").status);
  check("D 后续步骤说明是上一步失败，而不是各自报错",
    String(stepD("组装书摘").reason || "").includes("上一步失败"), stepD("组装书摘").reason);
  check("D 总体状态为出错", jobD.status === "error", jobD.status);

  // 场景 E：视频与课件作业独立存在，互不覆盖
  const state = await call("/coursekit/state");
  check("E 三类作业状态互不干扰",
    state.json.jobs.book.id > 0 && state.json.jobs.video.id === 0 && state.json.jobs.deck.id === 0,
    JSON.stringify({ book: state.json.jobs.book.id, video: state.json.jobs.video.id, deck: state.json.jobs.deck.id }));
}

// ------------------------------------------------------------------ 汇总
rmSync(SANDBOX, { recursive: true, force: true });   // 临时目录，删除安全
purge(join(PRESETS, "冒烟测试.json"));
purge(join(PRESETS, ".._逃逸.json"));
for (const path of uploads) purge(path);

const failed = results.filter((r) => !r.ok);
for (const r of results) {
  const tag = r.ok ? (r.skipped ? "SKIP" : "PASS") : "FAIL";
  console.log(`${tag}  ${r.name}${r.detail ? "  → " + r.detail : ""}`);
}
if (leftovers.length) {
  console.log("\n注意：COURSEKIT_SMOKE_NO_RM=1，以下工作区残渣没有删除，请自行清理：");
  for (const path of leftovers) console.log("  " + path);
}
console.log(`\n${results.length - failed.length}/${results.length} 通过`);
process.exit(failed.length === 0 ? 0 : 1);
