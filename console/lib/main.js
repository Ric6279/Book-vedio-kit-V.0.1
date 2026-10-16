// dsh-course-kit-console —— /coursekit/* 路由的宿主实现。
//
// 为什么整份实现都在这个文件里、而不是从 dsh-course-kit 里 import：
// Loader 加载过的宿主模块会被 ESM 缓存，改文件内容不会重新导入，
// 只有"换一个文件路径"才会。把实现放进入口，改控制台就只需要换入口文件名，
// 不必去猜哪些依赖被缓存了。
//
// 进程层复用 ../../plugin/lib/py.js（那份是稳定的，不随控制台改动）。

import { createWriteStream, existsSync } from "node:fs";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

import { OUT_ROOT, UPLOAD_DIR, WORKSPACE, runScript } from "../../plugin/lib/py.js";

const MAX_UPLOAD_BYTES = 512 * 1024 * 1024;
const MAX_BODY_BYTES = 4 * 1024 * 1024;
const KINDS = ["book", "video", "deck"];
/** 排队中也算"占用中"：作业一提交就要立刻占据这个槽位，否则轮询会读到上一次的结果。 */
const BUSY = new Set(["queued", "running"]);
/** 探针：确认当前进程里跑的是哪一版控制台。 */
const BUILD = "console-4-out";

/**
 * 取脚本打在 stdout 末尾的那行 JSON。
 * 引擎脚本都靠它汇报结果（ok / message / 统计数字），失败时 message 往往就是
 * 最该给用户看的那句话——比"上一步失败：提取段落与页码"有用得多。
 */
function lastJsonObject(log) {
  const lines = String(log ?? "").split("\n").filter((line) => line.trim().startsWith("{"));
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    try {
      return JSON.parse(lines[i]);
    } catch {
      /* 不是结果行，继续往前找 */
    }
  }
  return null;
}

function sendJson(res, status, payload) {
  res.statusCode = status;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("cache-control", "no-store");
  res.end(JSON.stringify(payload));
}

async function readJsonBody(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.byteLength;
    if (size > MAX_BODY_BYTES) return null;
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks, size).toString("utf8"));
}

/** 把上传体原样落盘。用文件名头而不是 multipart——少一层解析，也少一处出错的地方。 */
async function receiveUpload(req, dir) {
  if (!existsSync(dir)) await mkdir(dir, { recursive: true });
  const raw = req.headers["x-filename"];
  const name = decodeURIComponent(String(raw ?? "upload.bin")).replace(/[\\/:*?"<>|]/g, "_");
  const target = join(dir, `${Date.now()}-${name}`);
  const handle = createWriteStream(target);
  let size = 0;
  for await (const chunk of req) {
    size += chunk.byteLength;
    if (size > MAX_UPLOAD_BYTES) {
      handle.destroy();
      return { ok: false, message: `文件超过 ${Math.round(MAX_UPLOAD_BYTES / 1048576)} MB 上限` };
    }
    if (!handle.write(chunk)) await new Promise((r) => handle.once("drain", r));
  }
  await new Promise((r) => handle.end(r));
  return { ok: true, path: target, size, name };
}

function mmssToSeconds(value) {
  if (value === undefined || value === null || value === "") return null;
  const text = String(value).trim();
  if (/^\d+(\.\d+)?$/.test(text)) return Number(text);
  const parts = text.split(":").map((x) => Number(x));
  if (parts.some((x) => Number.isNaN(x))) return null;
  return parts.reduce((acc, x) => acc * 60 + x, 0);
}

/** 去掉扩展名与上传时加的时间戳前缀，用来命名产物。 */
function baseName(pathOrUrl) {
  if (!pathOrUrl) return "未命名";
  const clean = String(pathOrUrl).split("?")[0];
  const last = clean.split(/[\\/]/).filter(Boolean).pop() ?? "未命名";
  const stem = last.replace(/\.[^.]+$/, "") || "未命名";
  return stem.replace(/^\d{10,}-/, "") || "未命名";
}

/** 预设名只留安全字符，防止写到 presets 目录之外。 */
function safePresetName(name) {
  return String(name ?? "").trim().replace(/[\\/:*?"<>|]/g, "_").replace(/\.(notes\.)?json$/i, "");
}

/**
 * 产物目录名：书名里常有《》：· 这类字符，做目录名要先挡掉非法的，
 * 末尾的点和空格也要去掉（Windows 会自己吃掉，导致路径对不上）。
 */
function safeDirName(name) {
  const clean = String(name ?? "").trim().replace(/[\\/:*?"<>|]/g, "_").replace(/[. ]+$/, "");
  return clean.slice(0, 80) || "未命名";
}

function createConsole({ runScript }) {
  const jobs = Object.fromEntries(
    KINDS.map((k) => [k, { id: 0, kind: k, status: "idle", steps: [], artifacts: [], outDir: null }])
  );
  let chain = Promise.resolve();

  const presetsDir = resolve(WORKSPACE, "presets");

  async function listPresets() {
    try {
      const names = await readdir(presetsDir);
      return names
        .filter((n) => n.toLowerCase().endsWith(".json"))
        .map((n) => ({
          name: n,
          path: join(presetsDir, n),
          notes: /\.notes\.json$/i.test(n),
          deck: /^deck[_.]/i.test(n),
          builtin: n.startsWith("_"),
        }))
        .sort((a, b) => {
          // 下划线开头的内置预设排在最前，其余按名字
          if (a.builtin !== b.builtin) return a.builtin ? -1 : 1;
          return a.name.localeCompare(b.name, "zh");
        });
    } catch {
      return [];
    }
  }

  async function listArtifacts(dir) {
    if (!dir) return [];
    try {
      const names = await readdir(dir);
      const rows = [];
      for (const name of names) {
        const path = join(dir, name);
        const info = await stat(path);
        if (!info.isFile()) continue;
        rows.push({ name, path, size: info.size, mtime: info.mtimeMs });
      }
      rows.sort((a, b) => b.mtime - a.mtime);
      return rows;
    } catch {
      return [];
    }
  }

  /** 依次执行；前置未产出或必需步骤失败时停下，并把原因写进对应步骤。 */
  async function runChain(job, steps) {
    job.status = "running";
    let halted = null;
    let hardFailure = false;

    for (const step of steps) {
      if (halted) {
        job.steps.push({ name: step.name, status: "skipped", log: "", reason: halted });
        continue;
      }
      // 明知不会成的步骤：直接说明原因，不跑，也不让后面的步骤白等
      if (step.skip) {
        job.steps.push({ name: step.name, status: "skipped", log: "", reason: step.skip });
        continue;
      }
      const missing = (step.requires ?? []).filter((p) => !existsSync(p));
      if (missing.length > 0) {
        const reason = `前置产物不存在：${missing.map((p) => basename(p)).join("、")}`;
        job.steps.push({ name: step.name, status: "skipped", log: "", reason });
        if (!step.optional) halted = reason;
        continue;
      }
      const entry = { name: step.name, status: "running", log: "" };
      job.steps.push(entry);
      // 边跑边把脚本的输出贴进这一步的日志：OCR 一页一秒，整本可能几十分钟，
      // 只等最后那行 JSON 的话界面全程是一潭死水。
      let live = "";
      const result = await runScript(step.script, step.argv, (chunk) => {
        live = (live + chunk).slice(-4000);
        entry.log = live;
      });
      entry.log = result.log.slice(-4000);
      const payload = lastJsonObject(result.log);
      if (result.ok) {
        entry.status = "ok";
        // 脚本退出码为 0 不等于这一步真的产出了东西：产物本身还要过一道判断。
        // 判断不通过就把后面的步骤停下，并带上原因——别让下一步去撞一个空文件。
        if (typeof step.after === "function") {
          const reason = await step.after();
          if (reason) {
            entry.reason = reason;
            halted = reason;
          }
        }
      } else if (step.optional) {
        entry.status = "skipped";
        entry.reason = payload?.message ? `可选步骤失败：${payload.message}` : "可选步骤失败，点开日志看原因";
      } else {
        // 脚本自己写的 message 是给人看的，优先用它，而不是"上一步失败：xxx"
        const message = payload?.message ? String(payload.message) : "";
        entry.status = "error";
        if (message) entry.reason = message;
        hardFailure = true;
        halted = message || `上一步失败：${step.name}`;
      }
    }

    job.status = hardFailure ? "error" : job.steps.some((s) => s.status === "skipped") ? "partial" : "done";
    job.endedAt = Date.now();
    job.artifacts = await listArtifacts(job.outDir);
  }

  /**
   * 作业对象在入队时就建好并挂上去，而不是等队列轮到它才建。
   * 否则 POST /run 之后到真正开跑之间，轮询 /job 会读到上一次的作业，
   * 界面就会先显示上一轮的结果再跳变。
   */
  function enqueue(kind, steps, outDir) {
    const id = jobs[kind].id + 1;
    const job = {
      id,
      kind,
      status: "queued",
      steps: [],
      artifacts: [],
      outDir,
      startedAt: Date.now(),
      endedAt: null,
    };
    jobs[kind] = job;
    chain = chain
      .then(() => runChain(job, steps))
      .catch((error) => {
        job.status = "error";
        job.endedAt = Date.now();
        job.steps.push({ name: "内部错误", status: "error", log: String(error?.message ?? error) });
      });
    return id;
  }

  // ------------------------------------------------------------ 作业构造
  /**
   * 提取步骤之后的一道判断：段落文件必须是实打实的内容。
   * 扫描件的 pdf_extract.py 自己会以退出码 3 报明原因，这道判断是兜底——
   * 万一某本书提取"成功"却一个段落都没有，也要停在这里说清楚，别让 digest 去撞空文件。
   */
  function guardParagraphs(paragraphsPath) {
    return async () => {
      let rows;
      try {
        rows = JSON.parse(await readFile(paragraphsPath, "utf8"));
      } catch (error) {
        return `段落文件读不出来（${String(error?.message ?? error)}）：${paragraphsPath}`;
      }
      if (!Array.isArray(rows)) return `段落文件格式不对，不是段落数组：${paragraphsPath}`;
      if (rows.length === 0) {
        return "这份书的段落是空的：PDF 很可能是扫描件（没有文本层，每页都是一张图）。"
          + "回到「提取方式」勾上「扫描件：先做 OCR 再提取」，再跑一次即可。";
      }
      return null;
    };
  }

  function bookSteps(body) {
    const pdf = String(body.pdf ?? "");
    const title = String(body.title || baseName(pdf));
    // 产物一律收到 demo/out 下，而且**一本书一个目录**：
    // 以前默认写 demo/out/book，两本书先后跑会互相覆盖同一份 paragraphs.json。
    const outDir = resolve(String(body.outDir || join(OUT_ROOT, safeDirName(title))));
    const bookDir = join(outDir, "book");
    const formats = body.formats ?? {};
    const sections = join(bookDir, "sections.json");
    const steps = [];

    // 扫描件走 OCR：渲染每页再认字，一页一秒上下。默认那条（读 PDF 文字对象）只适用于文字版。
    const ocr = Boolean(body.ocr);
    const extract = ["--pdf", pdf, "--out", bookDir];
    if (Array.isArray(body.chapters) && body.chapters.length > 0 && !body.full) {
      extract.push("--chapters", body.chapters.join(","));
    }
    if (ocr && body.ocrDpi) extract.push("--dpi", String(body.ocrDpi));
    steps.push({
      name: ocr ? "OCR 识别扫描页" : "提取段落与页码",
      script: ocr ? "pdf_ocr.py" : "pdf_extract.py",
      argv: extract,
      after: guardParagraphs(join(bookDir, "paragraphs.json")),
    });

    // 默认走通用提取：不看体裁、不需要预设，任何书都能跑。
    // 「演示／说明／批注」那套角色是从写作教材里抽出来的，换一本书往往就不适用，
    // 所以它退成可选项，而不是必经之路。
    const mode = body.mode === "roles" ? "roles" : "digest";
    const digestName = `01_重点提取_${title}`;
    const digestMd = join(outDir, `${digestName}.md`);
    const reportPath = mode === "roles" ? join(outDir, `01_书籍摘要_${title}.md`) : digestMd;

    if (mode === "digest") {
      steps.push({
        name: "通用重点提取",
        script: "digest.py",
        argv: [
          "--paragraphs", join(bookDir, "paragraphs.json"),
          "--out", outDir,
          "--name", digestName,
          "--per-chapter", String(body.perChapter ?? 6),
          "--title", title,
        ],
        requires: [join(bookDir, "paragraphs.json")],
      });
    } else {
      if (body.presetRole) {
        steps.push({
          name: "归类演示／说明／批注",
          script: "curate.py",
          argv: ["--paragraphs", join(bookDir, "paragraphs.json"), "--spec", String(body.presetRole), "--out", bookDir],
          requires: [join(bookDir, "paragraphs.json")],
        });
      }

      // 书摘必须有 sections.json。没选角色预设时不出这一步，但要说明原因——
      // 直接跳过而不解释，用户只会看到"少了个文件"。
      if (body.presetRole && body.presetNotes) {
        steps.push({
          name: "组装书摘",
          script: "book_report.py",
          argv: ["--sections", sections, "--notes", String(body.presetNotes), "--out", reportPath, "--title", title],
          requires: [sections],
        });
      } else {
        steps.push({
          name: "组装书摘",
          script: "book_report.py",
          argv: [],
          skip: !body.presetRole
            ? "未选择角色预设，不会生成 sections.json；这一轮只产出段落清单（paragraphs.md）。不想选预设，就把提取方式换成「通用重点提取」"
            : "未选择注解预设，书摘没有章旨与要点可写",
        });
      }
    }

    if (formats.docx) {
      steps.push({
        name: "转换 Word",
        script: "md_to_docx.py",
        argv: ["--md", reportPath, "--out", reportPath.replace(/\.md$/, ".docx")],
        optional: true,
        requires: [reportPath],
      });
    }
    return { outDir, steps };
  }

  function videoSteps(body) {
    const title = String(body.title || baseName(body.url || body.file));
    // 与书籍同一条约定：一个素材一个目录，都在 demo/out 下
    const outDir = resolve(String(body.outDir || join(OUT_ROOT, safeDirName(title))));
    const work = join(outDir, "video");
    const formats = body.formats ?? {};
    const reportPath = join(outDir, `02_视频文稿与摘要_${title}.md`);
    const steps = [];

    const fetch = ["--out", work, "--frames", String(body.frames ?? 12)];
    if (body.url) fetch.push("--url", String(body.url));
    if (body.file) fetch.push("--file", String(body.file));
    if (!body.full) {
      const start = mmssToSeconds(body.start);
      const end = mmssToSeconds(body.end);
      if (start !== null) fetch.push("--start", String(start));
      if (end !== null) fetch.push("--end", String(end));
    }
    steps.push({ name: "取回视频与音频", script: "video_fetch.py", argv: fetch });
    steps.push({
      name: "本地语音识别",
      script: "video_asr.py",
      argv: ["--wav", join(work, "audio_16k.wav"), "--out", work],
      requires: [join(work, "audio_16k.wav")],
    });
    const report = ["--asr", join(work, "asr.json"), "--manifest", join(work, "manifest.json"), "--out", reportPath];
    if (body.presetNotes) report.push("--notes", String(body.presetNotes));
    steps.push({
      name: "组装文稿与摘要",
      script: "video_report.py",
      argv: report,
      requires: [join(work, "asr.json")],
    });

    if (formats.docx) {
      steps.push({
        name: "转换 Word",
        script: "md_to_docx.py",
        argv: ["--md", reportPath, "--out", reportPath.replace(/\.md$/, ".docx")],
        optional: true,
        requires: [reportPath],
      });
    }
    if (formats.srt) {
      steps.push({
        name: "导出字幕",
        script: "copy_file.py",
        argv: ["--from", join(work, "asr.srt"), "--to", join(outDir, `02_字幕_${title}.srt`)],
        optional: true,
        requires: [join(work, "asr.srt")],
      });
    }
    return { outDir, steps };
  }

  function deckSteps(body) {
    const spec = String(body.spec ?? "");
    const outDir = resolve(String(body.outDir || join(OUT_ROOT, "deck")));
    const stem = String(body.outName || baseName(spec)).replace(/\.pptx$/i, "");
    const pptx = join(outDir, `${stem}.pptx`);
    const argv = ["--spec", spec, "--out", pptx];
    if (body.sections) argv.push("--sections", String(body.sections));
    if (body.paragraphs) argv.push("--paragraphs", String(body.paragraphs));
    const steps = [{ name: "生成课件", script: "deck_build.py", argv }];
    if (body.preview) {
      steps.push({
        name: "渲染自检",
        script: "deck_preview.py",
        argv: ["--pptx", pptx, "--out", join(outDir, "preview")],
        optional: true,
        requires: [pptx],
      });
    }
    return { outDir, steps };
  }

  // ------------------------------------------------------------ 路由
  function register(ctx, rejected) {
    const route = (path, label, handler) => {
      ctx.effect(
        () =>
          ctx.webServer.register({
            kind: "exact",
            path,
            handler: async (req, res) => {
              if (rejected(req, res)) return;
              try {
                await handler(req, res);
              } catch (error) {
                sendJson(res, 500, { ok: false, message: String(error?.message ?? error) });
              }
            },
          }),
        `course-kit: ${label}`
      );
    };

    route("/coursekit/version", "GET /coursekit/version", async (req, res) => {
      sendJson(res, 200, { ok: true, build: BUILD, kinds: KINDS });
    });

    route("/coursekit/state", "GET /coursekit/state", async (req, res) => {
      if (req.method !== "GET") {
        sendJson(res, 405, { ok: false, message: "只支持 GET" });
        return;
      }
      sendJson(res, 200, {
        ok: true,
        presets: await listPresets(),
        outRoot: OUT_ROOT,
        uploadDir: UPLOAD_DIR,
        workspace: WORKSPACE,
        jobs,
      });
    });

    route("/coursekit/upload", "POST /coursekit/upload", async (req, res) => {
      if (req.method !== "POST") {
        res.statusCode = 405;
        res.end();
        return;
      }
      const result = await receiveUpload(req, UPLOAD_DIR);
      sendJson(res, result.ok ? 200 : 413, result);
    });

    route("/coursekit/outline", "GET /coursekit/outline", async (req, res) => {
      const url = new URL(req.url, "http://localhost");
      const pdf = url.searchParams.get("path");
      if (!pdf) {
        sendJson(res, 400, { ok: false, message: "缺少 path 参数" });
        return;
      }
      const result = await runScript("pdf_extract.py", ["--pdf", pdf, "--list"]);
      const payload = lastJsonObject(result.log);
      const chapters = Array.isArray(payload?.chapters) ? payload.chapters : [];
      // 目录为空时把脚本的解释一起带上去：扫描件读不出目录，跟"这本书没有目录"是两回事
      sendJson(res, 200, {
        ok: result.ok,
        chapters,
        pages: payload?.pages ?? null,
        hasTextLayer: payload?.hasTextLayer ?? null,
        textPages: payload?.textPages ?? null,
        imagePages: payload?.imagePages ?? null,
        message: payload?.message ?? "",
        log: result.log.slice(-1500),
      });
    });

    // 放入新预设：接受上传文件的内容，或界面按模板生成的骨架
    route("/coursekit/preset/save", "POST /coursekit/preset/save", async (req, res) => {
      if (req.method !== "POST") {
        res.statusCode = 405;
        res.end();
        return;
      }
      const body = await readJsonBody(req);
      if (!body) {
        sendJson(res, 400, { ok: false, message: "请求体不是合法 JSON" });
        return;
      }
      const stem = safePresetName(body.name);
      if (!stem) {
        sendJson(res, 400, { ok: false, message: "预设名不能为空" });
        return;
      }
      const text = String(body.content ?? "");
      try {
        JSON.parse(text);
      } catch (error) {
        sendJson(res, 400, { ok: false, message: "内容不是合法 JSON：" + String(error?.message ?? error) });
        return;
      }
      const file = body.kind === "notes" ? `${stem}.notes.json` : `${stem}.json`;
      if (!existsSync(presetsDir)) await mkdir(presetsDir, { recursive: true });
      await writeFile(join(presetsDir, file), text, "utf8");
      sendJson(res, 200, { ok: true, file, path: join(presetsDir, file), presets: await listPresets() });
    });

    route("/coursekit/preset/read", "GET /coursekit/preset/read", async (req, res) => {
      const url = new URL(req.url, "http://localhost");
      const target = url.searchParams.get("path");
      if (!target) {
        sendJson(res, 400, { ok: false, message: "缺少 path 参数" });
        return;
      }
      const wanted = resolve(target);
      if (!wanted.startsWith(presetsDir)) {
        sendJson(res, 403, { ok: false, message: "只允许读取 presets 目录内的预设" });
        return;
      }
      try {
        sendJson(res, 200, { ok: true, content: await readFile(wanted, "utf8") });
      } catch (error) {
        sendJson(res, 404, { ok: false, message: String(error?.message ?? error) });
      }
    });

    route("/coursekit/run", "POST /coursekit/run", async (req, res) => {
      if (req.method !== "POST") {
        res.statusCode = 405;
        res.end();
        return;
      }
      const body = await readJsonBody(req);
      if (!body) {
        sendJson(res, 400, { ok: false, message: "请求体不是合法 JSON" });
        return;
      }
      const kind = KINDS.includes(body.kind) ? body.kind : "book";
      if (Object.values(jobs).some((j) => BUSY.has(j.status))) {
        sendJson(res, 409, { ok: false, message: "已有任务在跑，等它结束再发下一个" });
        return;
      }
      if (kind === "book" && !body.pdf) {
        sendJson(res, 400, { ok: false, message: "请先放入电子书（或填绝对路径）" });
        return;
      }
      if (kind === "video" && !body.url && !body.file) {
        sendJson(res, 400, { ok: false, message: "请先放入视频或填视频链接" });
        return;
      }
      if (kind === "deck" && !body.spec) {
        sendJson(res, 400, { ok: false, message: "请选择一个课件规格 JSON" });
        return;
      }
      const built = kind === "book" ? bookSteps(body) : kind === "video" ? videoSteps(body) : deckSteps(body);
      const id = enqueue(kind, built.steps, built.outDir);
      sendJson(res, 200, { ok: true, kind, jobId: id, outDir: built.outDir, steps: built.steps.map((s) => s.name) });
    });

    route("/coursekit/job", "GET /coursekit/job", async (req, res) => {
      const url = new URL(req.url, "http://localhost");
      const kind = url.searchParams.get("kind");
      if (kind && KINDS.includes(kind)) {
        sendJson(res, 200, { ok: true, job: jobs[kind] });
        return;
      }
      sendJson(res, 200, { ok: true, jobs });
    });

    route("/coursekit/files", "GET /coursekit/files", async (req, res) => {
      const url = new URL(req.url, "http://localhost");
      const dir = url.searchParams.get("dir") || OUT_ROOT;
      sendJson(res, 200, { ok: true, dir, files: await listArtifacts(dir) });
    });
  }

  return { register, listPresets, listArtifacts, mmssToSeconds, baseName };
}

// ------------------------------------------------------------------ Loader 条目
/** 在 Loader 中的名字，与 cordis.patch.yml 里的 id 对应。 */
const name = "course-kit-console";
/** webServer 挂路由，connection 提供信任闸。 */
const inject = ["webServer", "connection"];

function apply(ctx) {
  const rejected = (req, res) => {
    const rejection = ctx.connection.requestRejection(req);
    if (rejection === undefined) return false;
    res.statusCode = rejection;
    res.end();
    return true;
  };
  createConsole({ runScript }).register(ctx, rejected);
}

// createConsole 也导出，是为了让 test/smoke.mjs 能注入一个假的 runScript——
// 这样作业链条的跳过与中止逻辑可以在不启动任何 Python 进程的情况下逐条验证。
export { apply, createConsole, inject, name };
