// course-kit —— 进程与路径层。
//
// 单独成文件的原因不只是整洁：宿主半的模块一旦被 Loader 加载过就留在 ESM 缓存里，
// 改动 host.js 不会热更。把"跑 Python"这件事放在这里，新增的入口模块就能直接复用它，
// 而不必依赖 host.js 重新被导入。

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url)); // …/course-kit/plugin/lib
/** engine/ 与 lib/ 同级，都在插件目录内——插件自包含，拷走就能用。 */
export const ENGINE = resolve(HERE, "..", "engine");
/** 第三方依赖装在 course-kit/.pylibs，不污染系统 site-packages。 */
export const LIB_DIR = resolve(HERE, "..", "..", ".pylibs");
/** 控制台的工作根：预设、上传、产物都收在这下面。 */
export const WORKSPACE = resolve(HERE, "..", "..");
export const OUT_ROOT = join(WORKSPACE, "demo", "out");
export const UPLOAD_DIR = join(WORKSPACE, "demo", "uploads");

const PYTHON_CANDIDATES = [
  process.env.COURSEKIT_PYTHON,
  "C:/Users/win10/AppData/Local/Programs/Python/Python310/python.exe",
  join(process.env.USERPROFILE ?? ".", ".dsh", "dsh-runtimes", "dsh-primary-runtime", "dependencies", "python", "python.exe"),
  "python",
  "python3",
].filter(Boolean);

export function findPython() {
  for (const candidate of PYTHON_CANDIDATES) {
    if (candidate === "python" || candidate === "python3") return candidate;
    if (existsSync(candidate)) return candidate;
  }
  return "python";
}

// ------------------------------------------------------------------ 进程
// 所有 Python 调用共用一条队列。抽帧与识别都是 CPU 密集的，
// 并行只会互相拖慢，也会让"进行到哪一步"难以回答。
let queue = Promise.resolve();

export function runScript(script, args, onChunk) {
  const task = queue.then(
    () =>
      new Promise((resolveRun) => {
        const file = join(ENGINE, script);
        if (!existsSync(file)) {
          resolveRun({ ok: false, code: -1, log: `找不到引擎脚本：${file}` });
          return;
        }
        const env = {
          ...process.env,
          PYTHONUTF8: "1",
          PYTHONIOENCODING: "utf-8",
          PYTHONPATH: existsSync(LIB_DIR) ? `${LIB_DIR};${process.env.PYTHONPATH ?? ""}` : process.env.PYTHONPATH ?? "",
        };
        const child = spawn(findPython(), ["-u", "-W", "ignore", file, ...args], {
          env,
          windowsHide: true,
          stdio: ["ignore", "pipe", "pipe"],
        });
        let out = "";
        let err = "";
        // onChunk 用来把进度实时透给界面：OCR 一页一秒、577 页要跑很久，
        // 只等最后那行 JSON 的话，用户全程看不到任何动静。
        child.stdout.on("data", (chunk) => {
          const text = chunk.toString("utf8");
          out += text;
          onChunk?.(text);
        });
        child.stderr.on("data", (chunk) => {
          const text = chunk.toString("utf8");
          err += text;
          onChunk?.(text);
        });
        child.on("error", (error) => resolveRun({ ok: false, code: -1, log: String(error?.message ?? error) }));
        child.on("close", (code) => {
          const tail = err.trim().split("\n").slice(-8).join("\n");
          resolveRun({ ok: code === 0, code, log: (out + (tail ? `\n[stderr]\n${tail}` : "")).trim() });
        });
      })
  );
  queue = task.catch(() => undefined);
  return task;
}

/** 引擎脚本都往 stdout 打一行 JSON 作为结果，这里把它取出来。 */
export function lastJson(log) {
  const lines = log.split("\n").filter((line) => line.trim().startsWith("{"));
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    try {
      return JSON.parse(lines[i]);
    } catch {
      /* 不是结果行，继续往前找 */
    }
  }
  return null;
}

/** 调脚本并把最后一行 JSON 当结果；没有 JSON 就把日志尾巴当结果。 */
export async function call(script, args) {
  const result = await runScript(script, args);
  const payload = lastJson(result.log);
  if (!result.ok && !payload) {
    return { isError: true, value: { ok: false, script, message: result.log.slice(-1200) } };
  }
  return { isError: false, value: payload ?? { ok: result.ok, script, log: result.log.slice(-1200) } };
}
