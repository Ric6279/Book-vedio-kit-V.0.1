// course-kit —— 浏览器控制台（Client half）
//
// 三个页签对应三类产物，各自独立成一次作业：
//   书籍 → 书摘（Markdown / Word）
//   视频 → 文稿与摘要（Markdown / Word / SRT）
//   课件 → pptx（可选生成后渲染自检）
// 做书不影响做视频，做课件也不需要重跑前两者——只要段落文件已经在。
//
// 所有动作通过同源 fetch 打到宿主半的 /coursekit/* 路由；
// 提取与识别都在本地 Python 里跑，页面只负责收集参数与显示进度。
//
// 说明：静态客户端包要求预先构建好的 CJS 包体（window.__ModuleLoader__.load 格式），
// 因此本文件不 import 任何 npm 包，只用 shell 提供的 react 模块 + 浏览器原生 API。
// 配色一律走 shell 的主题变量（--dsw-alias-*），换肤与深色模式自动跟随。

window.__ModuleLoader__.load({
  id: "dsh-course-kit",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });

    const react = require("react");
    const { useState, useEffect, useRef, useCallback } = react;
    const h = react.createElement;

    const API = {
      state: "coursekit/state",
      upload: "coursekit/upload",
      outline: "coursekit/outline",
      run: "coursekit/run",
      job: "coursekit/job",
      files: "coursekit/files",
    };

    const C = {
      bg: "var(--dsw-alias-bg-base)",
      card: "var(--dsw-alias-bg-layer-1)",
      card2: "var(--dsw-alias-bg-layer-2)",
      border: "var(--dsw-alias-border-l1)",
      border2: "var(--dsw-alias-border-l2)",
      brand: "var(--dsw-alias-brand-primary)",
      text: "var(--dsw-alias-label-primary)",
      dim: "var(--dsw-alias-label-secondary)",
      ok: "var(--dsw-alias-state-success-primary)",
      warn: "var(--dsw-alias-state-warn-primary)",
      err: "var(--dsw-alias-state-error-primary)",
      idle: "var(--dsw-alias-state-idle-primary)",
    };

    // ------------------------------------------------------------------ 基础件
    function jsonFetch(url, options) {
      return fetch(url, options).then((r) => r.json());
    }

    function postRun(body) {
      return jsonFetch(API.run, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
    }

    function Card(props) {
      return h("div", {
        style: { background: C.card, border: "1px solid " + C.border, borderRadius: 10, padding: "14px 16px", marginBottom: 14 },
      }, props.children);
    }

    function SectionTitle(props) {
      return h("div", { style: { display: "flex", alignItems: "baseline", gap: 8, marginBottom: 10 } },
        h("span", { style: { fontSize: 13, fontWeight: 600, color: C.text } }, props.title),
        props.hint ? h("span", { style: { fontSize: 11.5, color: C.dim } }, props.hint) : null);
    }

    function Label(props) {
      return h("div", { style: { fontSize: 11.5, color: C.dim, marginBottom: 4 } }, props.children);
    }

    function Input(props) {
      return h("input", {
        value: props.value,
        onChange: (e) => props.onChange(e.target.value),
        placeholder: props.placeholder,
        spellCheck: false,
        style: {
          width: "100%", boxSizing: "border-box", background: C.bg, color: C.text,
          border: "1px solid " + C.border2, borderRadius: 6, padding: "6px 9px",
          fontSize: 12.5, outline: "none", fontFamily: "inherit",
          ...(props.style || {}),
        },
      });
    }

    function Select(props) {
      return h("select", {
        value: props.value,
        onChange: (e) => props.onChange(e.target.value),
        style: {
          width: "100%", boxSizing: "border-box", background: C.bg, color: C.text,
          border: "1px solid " + C.border2, borderRadius: 6, padding: "6px 9px",
          fontSize: 12.5, fontFamily: "inherit",
        },
      }, props.children);
    }

    function Button(props) {
      const disabled = props.disabled;
      return h("button", {
        onClick: props.onClick,
        disabled: disabled,
        style: {
          cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1,
          background: props.variant === "ghost" ? "transparent" : C.brand,
          color: props.variant === "ghost" ? C.dim : "#fff",
          border: props.variant === "ghost" ? "1px solid " + C.border2 : "none",
          borderRadius: 6, padding: props.small ? "4px 10px" : "7px 18px",
          fontSize: props.small ? 11.5 : 12.5, fontWeight: 600, fontFamily: "inherit",
          marginRight: 8,
        },
      }, props.children);
    }

    function Toggle(props) {
      return h("label", {
        style: { display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12.5, color: C.text },
      },
        h("input", {
          type: "checkbox", checked: props.checked,
          onChange: (e) => props.onChange(e.target.checked),
          style: { accentColor: C.brand, width: 14, height: 14, cursor: "pointer" },
        }),
        props.children);
    }

    function Chip(props) {
      return h("label", {
        style: {
          display: "inline-flex", alignItems: "center", gap: 5, cursor: "pointer",
          fontSize: 12, color: C.text, border: "1px solid " + C.border2,
          borderRadius: 999, padding: "3px 11px", background: props.checked ? C.card2 : "transparent",
        },
      },
        h("input", {
          type: "checkbox", checked: props.checked,
          onChange: (e) => props.onChange(e.target.checked),
          style: { accentColor: C.brand, width: 13, height: 13, cursor: "pointer" },
        }),
        props.children);
    }

    function statusColor(status) {
      if (status === "running") return C.warn;
      if (status === "ok") return C.ok;
      if (status === "error") return C.err;
      return C.idle;
    }

    function Dot(props) {
      return h("span", {
        style: { width: 8, height: 8, borderRadius: "50%", flex: "0 0 auto", background: statusColor(props.status), display: "inline-block", marginTop: 5 },
      });
    }

    function formatSize(bytes) {
      if (bytes > 1048576) return (bytes / 1048576).toFixed(1) + " MB";
      if (bytes > 1024) return (bytes / 1024).toFixed(0) + " KB";
      return bytes + " B";
    }

    function DropZone(props) {
      const [over, setOver] = useState(false);
      const inputRef = useRef(null);
      const take = useCallback((file) => { if (file) props.onFile(file); }, [props]);
      return h("div", {
        onDragOver: (e) => { e.preventDefault(); setOver(true); },
        onDragLeave: () => setOver(false),
        onDrop: (e) => {
          e.preventDefault(); setOver(false);
          take(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
        },
        onClick: () => inputRef.current && inputRef.current.click(),
        style: {
          border: "1px dashed " + (over ? C.brand : C.border2),
          background: over ? C.card2 : "transparent",
          borderRadius: 8, padding: "16px 14px", textAlign: "center", cursor: "pointer",
        },
      },
        h("input", {
          ref: inputRef, type: "file", accept: props.accept, style: { display: "none" },
          onChange: (e) => { take(e.target.files && e.target.files[0]); e.target.value = ""; },
        }),
        h("div", { style: { fontSize: 12.5, color: C.text } }, props.busy ? "正在上传…" : props.label),
        h("div", { style: { fontSize: 11, color: C.dim, marginTop: 3 } }, props.hint));
    }

    /** 上传一件事，成功就把路径写回 state。 */
    function useUpload(patch) {
      const [busy, setBusy] = useState(false);
      const [message, setMessage] = useState("");
      const upload = useCallback(async (file) => {
        setBusy(true);
        setMessage("上传 " + file.name + "（" + formatSize(file.size) + "）…");
        try {
          const res = await fetch(API.upload, {
            method: "POST",
            headers: { "x-filename": encodeURIComponent(file.name) },
            body: await file.arrayBuffer(),
          });
          const data = await res.json();
          if (data.ok) {
            patch({ path: data.path, title: file.name.replace(/\.[^.]+$/, "") });
            setMessage("已放入：" + data.path);
          } else {
            setMessage("上传失败：" + (data.message || "未知错误"));
          }
        } catch (e) {
          setMessage("上传失败：" + String(e && e.message ? e.message : e));
        } finally {
          setBusy(false);
        }
      }, [patch]);
      return { busy, message, upload, setMessage };
    }

    // ------------------------------------------------------------------ 放入新预设
    // 每本书的段落划分都不一样，预设定制是常态而不是例外，
    // 所以把"上传一份预设"和"按模板新建一份"都放到下拉框旁边。
    const ROLE_TEMPLATE = {
      _说明: "为这本书写的角色预设。demo_runs 填演示段落的段落号区间（段落号看 paragraphs.md 里的 [n]），annotations 填作者批注的段落号，sources 按单元号覆写出处。",
      lead_in_marks: ["——", "：", ":"],
      lead_in_max_chars: 150,
      demo_max_chars: 900,
      annotation_after_demo_max: 280,
      author_voice: ["我想", "我发现", "读完", "读着", "看来", "不过", "其实"],
      overrides: {
        "章节名子串": { demo_runs: [[1, 1]], annotations: [], sources: {} },
      },
    };
    const NOTES_TEMPLATE = {
      _说明: "为这本书写的注解。键是章节名的子串；gist 是章旨，skills 是可迁移的技能点，units 按单元号写要点，explain_gists 按段落号给说明段落写一句话概括。",
      chapters: {
        "章节名子串": {
          title: "章节显示名",
          gist: "这一章在讲什么",
          skills: ["技能点一", "技能点二"],
          units: { 1: { note: "这个演示单元的要点" } },
          explain_gists: { 1: "这一段的一句话概括" },
        },
      },
    };

    function PresetAdder(props) {
      const [open, setOpen] = useState(false);
      const [name, setName] = useState("");
      const [busy, setBusy] = useState(false);
      const [note, setNote] = useState("");
      const fileRef = useRef(null);
      const kind = props.kind; // "role" | "notes"
      const label = kind === "notes" ? "注解预设" : "角色预设";

      const save = useCallback(async (presetName, content) => {
        setBusy(true); setNote("");
        try {
          const data = await jsonFetch("coursekit/preset/save", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ name: presetName, kind, content }),
          });
          if (data.ok) {
            setNote("已保存 " + data.file);
            setName("");
            if (props.onSaved) props.onSaved(data);
          } else {
            setNote("保存失败：" + (data.message || "未知错误"));
          }
        } catch (e) {
          setNote("保存失败：" + String(e));
        } finally {
          setBusy(false);
        }
      }, [kind, props]);

      const onFile = useCallback(async (file) => {
        if (!file) return;
        const text = await file.text();
        const stem = file.name.replace(/\.notes\.json$/i, "").replace(/\.json$/i, "");
        await save(stem, text);
      }, [save]);

      return h("div", { style: { marginTop: 10 } },
        h("button", {
          onClick: () => setOpen(!open),
          style: {
            background: "transparent", border: "none", color: C.brand,
            fontSize: 12, cursor: "pointer", padding: 0, fontFamily: "inherit",
          },
        }, open ? "收起" : "＋ 放入新" + label),
        open ? h("div", {
          style: {
            marginTop: 10, padding: "13px 14px", border: "1px solid " + C.border,
            borderRadius: 8, background: C.bg, lineHeight: 1.8,
          },
        },
          h("div", { style: { fontSize: 11.5, color: C.dim, marginBottom: 12, lineHeight: 1.85 } },
            "每本书的段落划分都不同，预设就是用来记这本书的判断的。",
            "可以先只勾「全文识别」跑一遍，看 paragraphs.md 里的段落号，再回来填。"),
          h("div", { style: { display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" } },
            h(Button, { small: true, onClick: () => fileRef.current && fileRef.current.click() }, "上传预设 JSON"),
            h("input", {
              ref: fileRef, type: "file", accept: ".json", style: { display: "none" },
              onChange: (e) => { onFile(e.target.files && e.target.files[0]); e.target.value = ""; },
            }),
            h("span", { style: { fontSize: 11.5, color: C.dim } }, "或"),
            h("div", { style: { flex: "1 1 160px", minWidth: 140 } },
              h(Input, { value: name, onChange: setName, placeholder: "预设名，如 金枝" })),
            h(Button, {
              small: true, variant: "ghost", disabled: !name || busy,
              onClick: () => save(name, JSON.stringify(kind === "notes" ? NOTES_TEMPLATE : ROLE_TEMPLATE, null, 2)),
            }, "按模板新建")),
          note ? h("div", { style: { fontSize: 11.5, color: C.dim, marginTop: 12, lineHeight: 1.8 } }, note) : null)
          : null);
    }


    // ------------------------------------------------------------------ 书籍
    function BookPanel(props) {
      const s = props.state;
      const set = props.patch;
      const [chapters, setChapters] = useState(null);
      // 记下"哪一份 PDF 是扫描件"，换书之后提示不会错挂在新书上
      const [scanNote, setScanNote] = useState(null);
      const [loading, setLoading] = useState(false);
      const { busy, message, upload, setMessage } = useUpload(
        useCallback((p) => set({ pdf: p.path, title: p.title }), [set])
      );

      // 角色预设与注解预设成对出现；选前者自动带上后者，避免"配错了对"
      const rolePresets = (props.presets || []).filter((p) => !p.notes && !p.deck);
      const notesPresets = (props.presets || []).filter((p) => p.notes);
      const stem = (name) => String(name).replace(/\.notes\.json$/i, "").replace(/\.json$/i, "");

      const readOutline = useCallback(async () => {
        if (!s.pdf) { setMessage("请先放入电子书"); return; }
        setLoading(true); setMessage("读取目录…");
        try {
          const data = await jsonFetch(API.outline + "?path=" + encodeURIComponent(s.pdf));
          setChapters(data.chapters || []);
          // 扫描件读不出目录，跟"这本书没有目录"是两回事，界面得说清是哪一种
          const scan = data.hasTextLayer === false;
          setScanNote(scan ? {
            path: s.pdf,
            message: (data.message || "这份 PDF 没有文本层（扫描件），要先做 OCR 才能提取文字。")
              + "已经替你打开下面的「扫描件：先做 OCR 再提取」，可以直接开始。",
          } : null);
          if (scan) set({ ocr: true });   // 已经知道是扫描件，就别让用户再想一次
          else set({ ocr: false });
          setMessage(
            data.chapters && data.chapters.length
              ? "读到 " + data.chapters.length + " 条目录，勾选要识别的章节"
              : scan
                ? "读不出目录：这份 PDF 没有文本层"
                : (data.message || "这本书没有内置目录，勾「全文识别」即可")
          );
        } catch (e) {
          setMessage("读取目录失败：" + String(e));
        } finally {
          setLoading(false);
        }
      }, [s.pdf, setMessage]);

      const leafChapters = (chapters || []).filter(
        (c) => c.level >= 2 || !(chapters || []).some((o) => o.level > c.level)
      );
      const picked = s.chapters || [];

      return h("div", null,
        h(Card, null,
          h(SectionTitle, { title: "1. 放入电子书", hint: "PDF；拖进来或点击选择" }),
          h(DropZone, {
            accept: ".pdf,application/pdf",
            label: "把电子书拖到这里，或点击选择",
            hint: "文件复制到插件工作目录，原文件不动",
            busy, onFile: upload,
          }),
          h("div", { style: { height: 10 } }),
          h(Label, null, "或者直接填绝对路径"),
          h(Input, { value: s.pdf || "", onChange: (v) => { set({ pdf: v }); setChapters(null); setScanNote(null); }, placeholder: "E:/…/某本书.pdf" })),

        h(Card, null,
          h(SectionTitle, { title: "2. 选择要识别的章节", hint: "不勾选则按全文识别" }),
          h("div", { style: { display: "flex", gap: 10, alignItems: "center", marginBottom: 10, flexWrap: "wrap" } },
            h(Button, { onClick: readOutline, disabled: loading || !s.pdf, small: true }, loading ? "读取中…" : "读取目录"),
            h(Toggle, { checked: Boolean(s.full), onChange: (v) => set({ full: v, chapters: v ? [] : picked }) }, "全文识别"),
            picked.length ? h(Button, { variant: "ghost", small: true, onClick: () => set({ chapters: [] }) }, "清空选择") : null,
            h("span", { style: { fontSize: 11.5, color: C.dim } }, picked.length ? "已选 " + picked.length + " 章" : "")),
          // 扫描件的解释要显眼：它决定了这一轮到底能不能跑，而不是一句普通的提示
          scanNote && scanNote.path === s.pdf
            ? h("div", {
                style: {
                  marginBottom: 10, padding: "9px 12px", borderRadius: 8,
                  border: "1px solid " + C.warn, background: C.card2,
                  fontSize: 12, color: C.text, lineHeight: 1.9,
                },
              }, scanNote.message)
            : null,
          chapters && chapters.length
            ? h("div", { style: { maxHeight: 240, overflowY: "auto", border: "1px solid " + C.border, borderRadius: 8, padding: "8px 10px", background: C.bg } },
                leafChapters.map((c, i) => h("label", {
                  key: i,
                  style: { display: "flex", gap: 8, alignItems: "flex-start", padding: "4px 2px", cursor: "pointer", fontSize: 12.5, color: C.text },
                },
                  h("input", {
                    type: "checkbox", checked: picked.includes(c.title),
                    onChange: () => set({
                      chapters: picked.includes(c.title) ? picked.filter((x) => x !== c.title) : picked.concat([c.title]),
                      full: false,
                    }),
                    style: { accentColor: C.brand, marginTop: 3, cursor: "pointer" },
                  }),
                  h("span", { style: { flex: "1 1 auto" } }, c.title,
                    h("span", { style: { color: C.dim, marginLeft: 8, fontSize: 11 } }, "p" + c.start + "–" + c.end)))))
            : h("div", { style: { fontSize: 11.5, color: C.dim } },
                scanNote && scanNote.path === s.pdf
                  ? "扫描件读不出目录；勾「全文识别」也提不出文字，得先做 OCR。"
                  : chapters ? "没有可用目录条目，可直接勾「全文识别」。" : "点「读取目录」后在这里勾章节。")),

        h(Card, null,
          h(SectionTitle, { title: "3. 提取方式", hint: "默认那条不用配置，任何书都能跑" }),
          h("div", { style: { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 } },
            h(Chip, { checked: s.mode !== "roles", onChange: () => set({ mode: "digest" }) }, "通用重点提取"),
            h(Chip, { checked: s.mode === "roles", onChange: () => set({ mode: "roles" }) }, "演示／说明／批注（需预设）")),

          // OCR 与"提取方式"是两件正交的事：它换的是取字那一步，下游完全一样
          h("div", { style: { marginBottom: 12, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" } },
            h(Toggle, { checked: Boolean(s.ocr), onChange: (v) => set({ ocr: v }) }, "扫描件：先做 OCR 再提取"),
            s.ocr
              ? h("span", { style: { fontSize: 11.5, color: C.dim } }, "清晰度")
              : null,
            s.ocr
              ? h(Input, {
                  value: String(s.ocrDpi ?? 200), onChange: (v) => set({ ocrDpi: v }),
                  placeholder: "200", style: { width: 74 },
                })
              : null),
          h("div", { style: { fontSize: 11.5, color: C.dim, marginBottom: 12, lineHeight: 1.9 } },
            s.ocr
              ? "每页图片逐页认字，在本机跑，不联网：一页三四秒，300 页约 20 分钟。" +
                "只勾了章节就只识别那几章。书名、页码、书眉会在识别后清掉；" +
                "中断了也不白跑——已识别的页有缓存，再点一次接着来。"
              : "PDF 里若是整页图片（扫描件），点上面的开关改走 OCR。"),

          h("div", { style: { fontSize: 11.5, color: C.dim, marginBottom: 12, lineHeight: 1.85 } },
            s.mode === "roles"
              ? "这套角色是从《大师们的写作课》那类写作教材里抽出来的：先引一段原文，再点评。" +
                "换成人类学、历史、科普这类体裁往往就没有这种结构，那时要么给这本书写一份预设，要么退回「通用重点提取」。"
              : "不看体裁，只看段落的通用特征：长度是否适中、是不是章节的开头或结尾、有没有引文与数字、" +
                "连接词密度、以及它对本章词汇的代表性。产出「关键词 + 要点摘句 + 重点段落原文」，原文逐字保留并标页码。"),

          s.mode === "roles"
            ? h("div", null,
                s.splitPresets
                  ? h("div", null,
                      h(Label, null, "角色预设（决定演示／说明／批注怎么分）"),
                      h(Select, { value: s.presetRole || "", onChange: (v) => set({ presetRole: v }) },
                        h("option", { value: "" }, "（不使用：只提取段落与页码）"),
                        rolePresets.map((p, i) => h("option", { key: i, value: p.path },
                          (p.builtin ? "内置默认 · " : "") + p.name))),
                      h(PresetAdder, { kind: "role", onSaved: props.reloadPresets }),
                      h("div", { style: { marginTop: 22 } },
                        h(Label, null, "书摘注解（章旨、技能点、单元要点）"),
                        h(Select, { value: s.presetNotes || "", onChange: (v) => set({ presetNotes: v }) },
                          h("option", { value: "" }, "（不出注解：只有原文，没有评注）"),
                          notesPresets.map((p, i) => h("option", { key: i, value: p.path },
                            (p.builtin ? "内置默认 · " : "") + p.name))),
                        h(PresetAdder, { kind: "notes", onSaved: props.reloadPresets })))
                  : h("div", null,
                      h(Select, {
                        value: s.presetRole || "",
                        onChange: (v) => {
                          const leaf = v ? String(v).split(/[\\/]/).pop() : "";
                          const match = notesPresets.find((p) => stem(p.name) === stem(leaf));
                          set({ presetRole: v, presetNotes: v && match ? match.path : "" });
                        },
                      },
                        h("option", { value: "" }, "（不使用预设：只提取段落与页码）"),
                        rolePresets.map((p, i) => h("option", { key: i, value: p.path },
                          (p.builtin ? "内置默认 · " : "") + p.name +
                          (!p.builtin && notesPresets.some((n) => stem(n.name) === stem(p.name)) ? "　＋ 注解" : "")))),
                      h(PresetAdder, { kind: "role", onSaved: props.reloadPresets })),

                // 开关与说明各占一行，用 margin 而不是固定高度的容器——
                // 固定高度会把比它高的 label 压到下面那行文字上
                h("div", { style: { marginTop: 18, marginBottom: 2 } },
                  h(Toggle, { checked: Boolean(s.splitPresets), onChange: (v) => set({ splitPresets: v }) },
                    "分开选择角色预设与注解预设")),

                h("div", {
                  style: {
                    marginTop: 12, paddingTop: 11,
                    borderTop: "1px solid " + C.border,
                    fontSize: 12, color: C.dim, lineHeight: 1.85,
                  },
                },
                  !s.presetRole
                    ? (s.presetNotes
                        ? "选了注解但没有角色预设：不会生成 sections.json，书摘无从组装。新书先选「内置默认」。"
                        : "会产出：段落清单（paragraphs.md）。书摘要先有归类结果，所以得选一个角色预设。")
                    : !s.presetNotes
                      ? "会产出：段落清单 + 演示／说明／批注归类。没有注解预设，书摘不出章旨与要点。"
                      : "会产出：段落清单 + 演示／说明／批注归类 + 书摘（" +
                        (s.splitPresets ? "按上面分别选定的两份文件" : "按配对选定的两份文件") + "）。"))
            : h("div", { style: { maxWidth: 240 } },
                h(Label, null, "每章保留几段重点段落"),
                h(Input, { value: String(s.perChapter ?? 6), onChange: (v) => set({ perChapter: v }), placeholder: "6" }))),

        h(Card, null,
          h(SectionTitle, { title: "4. 输出" }),
          h("div", { style: { display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" } },
            h(Chip, { checked: Boolean(s.formats && s.formats.md), onChange: (v) => set({ formats: { ...s.formats, md: v } }) }, "Markdown"),
            h(Chip, { checked: Boolean(s.formats && s.formats.docx), onChange: (v) => set({ formats: { ...s.formats, docx: v } }) }, "Word")),
          h(Label, null, "输出目录"),
          h(Input, {
            value: s.outDir || "",
            onChange: (v) => set({ outDir: v }),
            placeholder: (props.outRoot || "") + "\\" + (s.title || "书名"),
          }),
          h("div", { style: { fontSize: 11.5, color: C.dim, marginTop: 6, lineHeight: 1.85 } },
            "留空就放到 " + (props.outRoot || "out") + "\\" + (s.title || "书名") +
            " —— 一本书一个目录，两本书不会互相覆盖。"),
          h("div", { style: { height: 14 } }),
          h(Button, {
            onClick: async () => {
              setMessage("");
              const data = await postRun({
                kind: "book", pdf: s.pdf, title: s.title, chapters: picked, full: s.full,
                mode: s.mode, perChapter: s.perChapter,
                ocr: Boolean(s.ocr), ocrDpi: s.ocrDpi,
                presetRole: s.presetRole || null, presetNotes: s.presetNotes || null,
                outDir: s.outDir, formats: s.formats,
              });
              if (!data.ok) setMessage(data.message || "启动失败");
            },
            disabled: !s.pdf || props.running || busy,
          }, props.running ? "有任务在跑…" : "开始做书摘")),

        message ? h("div", { style: { fontSize: 12, color: C.dim, marginBottom: 10 } }, message) : null);
    }

    // ------------------------------------------------------------------ 视频
    function VideoPanel(props) {
      const s = props.state;
      const set = props.patch;
      const { busy, message, upload, setMessage } = useUpload(
        useCallback((p) => set({ file: p.path, url: "", title: p.title }), [set])
      );
      const notesPresets = (props.presets || []).filter((p) => p.notes);

      return h("div", null,
        h(Card, null,
          h(SectionTitle, { title: "1. 放入视频或粘链接", hint: "本地文件，或 B 站地址 / BV 号" }),
          h(DropZone, {
            accept: "video/*",
            label: "把视频拖到这里，或点击选择",
            hint: "上传后复制到插件工作目录；大文件建议直接填路径，省一次拷贝",
            busy, onFile: upload,
          }),
          h("div", { style: { height: 10 } }),
          h(Label, null, "视频链接（B 站）"),
          h(Input, { value: s.url || "", onChange: (v) => set({ url: v }), placeholder: "https://www.bilibili.com/video/BV………… 或 BV 号" }),
          h("div", { style: { height: 10 } }),
          h(Label, null, "或者本地视频绝对路径"),
          h(Input, { value: s.file || "", onChange: (v) => set({ file: v }), placeholder: "E:/…/某个视频.mp4" })),

        h(Card, null,
          h(SectionTitle, { title: "2. 要识别的范围", hint: "不填时间段即全片" }),
          h("div", { style: { marginBottom: 10 } },
            h(Toggle, { checked: Boolean(s.full), onChange: (v) => set({ full: v }) }, "全片识别")),
          s.full ? null : h("div", { style: { display: "flex", gap: 12, flexWrap: "wrap" } },
            h("div", { style: { flex: "1 1 150px" } },
              h(Label, null, "开始时间（mm:ss 或秒）"),
              h(Input, { value: s.start || "", onChange: (v) => set({ start: v }), placeholder: "00:00" })),
            h("div", { style: { flex: "1 1 150px" } },
              h(Label, null, "结束时间"),
              h(Input, { value: s.end || "", onChange: (v) => set({ end: v }), placeholder: "07:00" }))),
          h("div", { style: { height: 10 } }),
          h("div", { style: { maxWidth: 240 } },
            h(Label, null, "画面抽帧数量（供核对字幕与文字卡）"),
            h(Input, { value: String(s.frames ?? 12), onChange: (v) => set({ frames: v }), placeholder: "12" }))),

        h(Card, null,
          h(SectionTitle, { title: "3. 文稿注解", hint: "可选：分节标题、专名勘误、画面文字抄录、摘要要点" }),
          h(Select, { value: s.presetNotes || "", onChange: (v) => set({ presetNotes: v }) },
            h("option", { value: "" }, "（不出注解，只出逐句文稿）"),
            notesPresets.map((p, i) => h("option", { key: i, value: p.path }, (p.builtin ? "内置默认 · " : "") + p.name))),
          h(PresetAdder, { kind: "notes", onSaved: props.reloadPresets })),

        h(Card, null,
          h(SectionTitle, { title: "4. 输出" }),
          h("div", { style: { display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" } },
            h(Chip, { checked: Boolean(s.formats && s.formats.md), onChange: (v) => set({ formats: { ...s.formats, md: v } }) }, "Markdown"),
            h(Chip, { checked: Boolean(s.formats && s.formats.docx), onChange: (v) => set({ formats: { ...s.formats, docx: v } }) }, "Word"),
            h(Chip, { checked: Boolean(s.formats && s.formats.srt), onChange: (v) => set({ formats: { ...s.formats, srt: v } }) }, "SRT 字幕")),
          h(Label, null, "输出目录"),
          h(Input, {
            value: s.outDir || "",
            onChange: (v) => set({ outDir: v }),
            placeholder: (props.outRoot || "") + "\\" + (s.title || "视频名"),
          }),
          h("div", { style: { fontSize: 11.5, color: C.dim, marginTop: 6, lineHeight: 1.85 } },
            "留空就放到 " + (props.outRoot || "out") + "\\" + (s.title || "视频名") + "。"),
          h("div", { style: { height: 14 } }),
          h(Button, {
            onClick: async () => {
              setMessage("");
              const data = await postRun({
                kind: "video", url: s.url || null, file: s.url ? null : s.file || null,
                title: s.title, full: s.full, start: s.start, end: s.end, frames: s.frames,
                presetNotes: s.presetNotes || null, outDir: s.outDir, formats: s.formats,
              });
              if (!data.ok) setMessage(data.message || "启动失败");
            },
            disabled: (!s.url && !s.file) || props.running || busy,
          }, props.running ? "有任务在跑…" : "开始做文稿")),

        message ? h("div", { style: { fontSize: 12, color: C.dim, marginBottom: 10 } }, message) : null);
    }

    // ------------------------------------------------------------------ 课件
    function DeckPanel(props) {
      const s = props.state;
      const set = props.patch;
      const [message, setMessage] = useState("");
      const [guessed, setGuessed] = useState(false);
      const deckPresets = (props.presets || []).filter((p) => p.deck);

      // 段落文件就在上一次做书的输出目录下；能猜到就替用户填上，只猜一次
      useEffect(() => {
        if (guessed || s.sections || s.paragraphs) return;
        const base = (props.jobs && props.jobs.book && props.jobs.book.outDir) || "";
        if (!base) return;
        set({ sections: base + "\\book\\sections.json", paragraphs: base + "\\book\\paragraphs.json" });
        setGuessed(true);
      }, [props.jobs, s.sections, s.paragraphs, set, guessed]);

      return h("div", null,
        h(Card, null,
          h(SectionTitle, { title: "1. 课件规格", hint: "规格决定页序与每页讲什么；长段原文用引用写法，不必抄进规格" }),
          h(Select, {
            value: s.spec || "",
            onChange: (v) => {
              const stem = v ? String(v).split(/[\\/]/).pop().replace(/\.json$/i, "").replace(/^deck[_.]/i, "") : "";
              set({ spec: v, outName: stem ? "03_课件_" + stem : "03_课件" });
            },
          },
            h("option", { value: "" }, "（选择一个规格）"),
            deckPresets.map((p, i) => h("option", { key: i, value: p.path }, p.name))),
          deckPresets.length ? null : h("div", { style: { fontSize: 11.5, color: C.dim, marginTop: 6 } },
            "presets 目录下还没有课件规格（文件名以 deck_ 开头的 JSON）。")),

        h(Card, null,
          h(SectionTitle, { title: "2. 原文出处", hint: "可选：规格里的引用靠它们换成真实原文" }),
          h(Label, null, "sections.json（演示／说明／批注归类结果）"),
          h(Input, { value: s.sections || "", onChange: (v) => set({ sections: v, sectionsTouched: true }), placeholder: "…\\book\\sections.json" }),
          h("div", { style: { height: 8 } }),
          h(Label, null, "paragraphs.json（段落与页码）"),
          h(Input, { value: s.paragraphs || "", onChange: (v) => set({ paragraphs: v }), placeholder: "…\\book\\paragraphs.json" }),
          h("div", { style: { fontSize: 11.5, color: C.dim, marginTop: 6, lineHeight: 1.6 } },
            "两者都不填也能出课件，但规格里用引用写法的页面会拿不到原文。")),

        h(Card, null,
          h(SectionTitle, { title: "3. 输出" }),
          h(Label, null, "文件名（不含扩展名）"),
          h(Input, { value: s.outName || "", onChange: (v) => set({ outName: v }), placeholder: "03_课件" }),
          h("div", { style: { height: 10 } }),
          h(Label, null, "输出目录"),
          h(Input, { value: s.outDir || "", onChange: (v) => set({ outDir: v }), placeholder: (props.outRoot || "out") + "\\deck" }),
          h("div", { style: { height: 10 } }),
          h(Toggle, { checked: Boolean(s.preview), onChange: (v) => set({ preview: v }) }, "生成后渲染自检（报告文字有没有溢出文本框）"),
          h("div", { style: { height: 14 } }),
          h(Button, {
            onClick: async () => {
              setMessage("");
              const data = await postRun({
                kind: "deck", spec: s.spec, outName: s.outName, sections: s.sections || null,
                paragraphs: s.paragraphs || null, outDir: s.outDir, preview: s.preview,
              });
              if (!data.ok) setMessage(data.message || "启动失败");
            },
            disabled: !s.spec || props.running,
          }, props.running ? "有任务在跑…" : "开始做课件")),

        message ? h("div", { style: { fontSize: 12, color: C.dim, marginBottom: 10 } }, message) : null);
    }

    // ------------------------------------------------------------------ 任务与产物
    function JobPanel(props) {
      const job = props.job || { status: "idle", steps: [] };
      const [openStep, setOpenStep] = useState(-1);
      const running = job.status === "running" || job.status === "queued";
      const summary =
        job.status === "queued" ? "排队中（有别的作业在跑）"
          : running ? "进行中…"
            : job.status === "done" ? "已完成"
              : job.status === "partial" ? "部分完成（有步骤被跳过）"
                : job.status === "error" ? "出错" : "空闲";
      const summaryColor = running || job.status === "partial" ? C.warn : job.status === "error" ? C.err : C.dim;

      return h(Card, null,
        h(SectionTitle, { title: "本次作业" }),
        h("div", { style: { fontSize: 11.5, color: summaryColor, marginTop: -6, marginBottom: 8 } }, summary),
        (job.steps || []).length === 0
          ? h("div", { style: { fontSize: 11.5, color: C.dim } }, "还没有作业。左边选好素材，点开始。")
          : h("div", null, (job.steps || []).map((step, i) => h("div", {
              key: i, style: { borderTop: i ? "1px solid " + C.border : "none", padding: "7px 0" },
            },
              h("div", {
                onClick: () => setOpenStep(openStep === i ? -1 : i),
                style: { display: "flex", gap: 8, alignItems: "flex-start", cursor: step.log ? "pointer" : "default" },
              },
                h(Dot, { status: step.status }),
                h("div", { style: { flex: "1 1 auto" } },
                  h("div", { style: { fontSize: 12.5, color: C.text } }, step.name),
                  step.reason ? h("div", { style: { fontSize: 11, color: C.dim, marginTop: 2, lineHeight: 1.55 } }, step.reason) : null,
                  step.status === "error" ? h("div", { style: { fontSize: 11, color: C.err, marginTop: 2 } }, "这一步失败了，点开看日志") : null),
                step.log ? h("span", { style: { fontSize: 11, color: C.dim } }, openStep === i ? "收起" : "日志") : null),
              openStep === i && step.log
                ? h("pre", {
                    style: {
                      marginTop: 6, marginBottom: 0, maxHeight: 200, overflow: "auto",
                      background: C.bg, border: "1px solid " + C.border, borderRadius: 6,
                      padding: "8px 10px", fontSize: 11, color: C.dim, whiteSpace: "pre-wrap",
                    },
                  }, step.log)
                : null))),
        h("div", { style: { height: 12 } }),
        h(SectionTitle, { title: "产物", hint: job.outDir || props.outRoot }),
        (props.files || []).length === 0
          ? h("div", { style: { fontSize: 11.5, color: C.dim } }, "还没有产物。")
          : h("div", null, (props.files || []).map((file, i) => h("div", {
              key: i,
              style: { display: "flex", gap: 10, alignItems: "baseline", padding: "5px 0", borderTop: i ? "1px solid " + C.border : "none", fontSize: 12.5 },
            },
              h("span", { style: { flex: "1 1 auto", color: C.text, wordBreak: "break-all" } }, file.name),
              h("span", { style: { color: C.dim, fontSize: 11, flex: "0 0 auto" } }, formatSize(file.size)),
              h("button", {
                onClick: () => { if (navigator.clipboard) navigator.clipboard.writeText(file.path); },
                style: { background: "transparent", border: "none", color: C.brand, fontSize: 11, cursor: "pointer", flex: "0 0 auto", fontFamily: "inherit" },
              }, "复制路径")))));
    }

    // ------------------------------------------------------------------ 主面板
    const TABS = [
      { id: "book", label: "书籍 → 书摘" },
      { id: "video", label: "视频 → 文稿" },
      { id: "deck", label: "课件 → PPT" },
    ];

    function Console() {
      const [tab, setTab] = useState("book");
      const [presets, setPresets] = useState([]);
      const [outRoot, setOutRoot] = useState("");
      const [jobs, setJobs] = useState({});
      const [files, setFiles] = useState([]);
      const [book, setBook] = useState({
        pdf: "", title: "", chapters: [], full: false,
        mode: "digest", perChapter: 6, ocr: false, ocrDpi: 200,
        presetRole: "", presetNotes: "", splitPresets: false,
        outDir: "", formats: { md: true, docx: true },
      });
      const [video, setVideo] = useState({
        url: "", file: "", title: "", full: true, start: "", end: "", frames: 12,
        presetNotes: "", outDir: "", formats: { md: true, docx: true, srt: false },
      });
      const [deck, setDeck] = useState({
        spec: "", outName: "03_课件", sections: "", paragraphs: "", outDir: "", preview: true,
      });

      const job = jobs[tab] || { status: "idle", steps: [] };
      const busy = (j) => j && (j.status === "running" || j.status === "queued");
      const running = Object.values(jobs).some(busy);

      useEffect(() => {
        jsonFetch(API.state).then((data) => {
          if (!data || !data.ok) return;
          setPresets(data.presets || []);
          setOutRoot(data.outRoot || "");
          setJobs(data.jobs || {});
          setBook((b) => ({ ...b, outDir: b.outDir || data.outRoot || "" }));
          setVideo((v) => ({ ...v, outDir: v.outDir || data.outRoot || "" }));
          setDeck((d) => ({ ...d, outDir: d.outDir || data.outRoot || "" }));
        }).catch(() => undefined);
      }, []);

      // 放入新预设后重新拉一次列表，下拉框立刻能看到它
      const reloadPresets = useCallback(() => {
        jsonFetch(API.state)
          .then((data) => { if (data && data.ok) setPresets(data.presets || []); })
          .catch(() => undefined);
      }, []);

      useEffect(() => {
        let alive = true;
        const tick = async () => {
          try {
            const data = await jsonFetch(API.state);
            if (alive && data && data.ok) setJobs(data.jobs || {});
          } catch {
            /* 轮询失败不该打断界面，下一拍再试 */
          }
        };
        tick();
        const timer = setInterval(tick, running ? 1200 : 5000);
        return () => { alive = false; clearInterval(timer); };
      }, [running]);

      const stepCount = (job.steps || []).length;
      useEffect(() => {
        if (!job.outDir) { setFiles([]); return; }
        let alive = true;
        jsonFetch(API.files + "?dir=" + encodeURIComponent(job.outDir))
          .then((data) => { if (alive && data && data.ok) setFiles(data.files || []); })
          .catch(() => undefined);
        return () => { alive = false; };
      }, [job.outDir, job.status, stepCount]);

      const patchBook = useCallback((p) => setBook((b) => ({ ...b, ...p })), []);
      const patchVideo = useCallback((p) => setVideo((v) => ({ ...v, ...p })), []);
      const patchDeck = useCallback((p) => setDeck((d) => ({ ...d, ...p })), []);

      const tabStyle = (active) => ({
        padding: "6px 16px", fontSize: 12.5, fontWeight: 600, cursor: "pointer",
        color: active ? C.brand : C.dim, fontFamily: "inherit", background: "transparent", border: "none",
        borderBottom: "2px solid " + (active ? C.brand : "transparent"),
      });

      const shared = { presets, outRoot, running, jobs, reloadPresets };

      return h("div", {
        style: {
          height: "100%", overflowY: "auto", background: C.bg, color: C.text,
          padding: "22px 26px 40px", boxSizing: "border-box",
          fontFamily: '-apple-system, "Segoe UI", "Microsoft YaHei", sans-serif',
        },
      },
        h("div", { style: { maxWidth: 1160, margin: "0 auto" } },
          h("div", { style: { marginBottom: 16 } },
            h("div", { style: { fontSize: 19, fontWeight: 700, color: C.text } }, "书籍、视频摘要"),
            h("div", { style: { fontSize: 12.5, color: C.dim, marginTop: 4 } },
              "书摘、文稿、课件各做各的，互不影响。提取与识别都在本机跑，素材不出这台电脑。")),
          h("div", { style: { display: "flex", gap: 4, borderBottom: "1px solid " + C.border, marginBottom: 16 } },
            TABS.map((t) => h("button", { key: t.id, onClick: () => setTab(t.id), style: tabStyle(tab === t.id) }, t.label))),
          h("div", { style: { display: "grid", gridTemplateColumns: "minmax(0, 1.35fr) minmax(0, 1fr)", gap: 18, alignItems: "start" } },
            h("div", null,
              tab === "book" ? h(BookPanel, { state: book, patch: patchBook, ...shared })
                : tab === "video" ? h(VideoPanel, { state: video, patch: patchVideo, ...shared })
                  : h(DeckPanel, { state: deck, patch: patchDeck, ...shared })),
            h(JobPanel, { job, files, outRoot }))));
    }

    // ------------------------------------------------------------------ 侧栏图标
    function PanelIcon() {
      return h("span", {
        style: { display: "inline-flex", alignItems: "center", justifyContent: "center" },
        title: "书籍、视频摘要",
      },
        h("svg", { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.6 },
          h("path", { d: "M3.5 5.2 10 3.4l10.5 2v14L10 21.2 3.5 19.4z", strokeLinejoin: "round" }),
          h("path", { d: "M10 3.4v17.8M6 8h2M6 12h2M6 16h2" }),
          h("path", { d: "M15 8.5v6l5-3z", fill: "currentColor", stroke: "none" })));
    }

    // ------------------------------------------------------------------ 入口
    const inject = ["slots"];

    function apply(ctx) {
      ctx.slots.inject("sidebar.panellist", () => ctx.slots.register({
        name: "sidebar.panellist",
        id: "course-kit",
        order: 3,
        label: "书籍、视频摘要",
      }, PanelIcon));
      ctx.slots.inject("main", () => ctx.slots.register({
        name: "main",
        key: "course-kit",
      }, Console));
    }

    exports.apply = apply;
    exports.inject = inject;
    return module.exports;
  },
});
