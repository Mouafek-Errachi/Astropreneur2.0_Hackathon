(() => {
  if (window.__icBubble) return;
  window.__icBubble = true;

  const state = { sessionId: null, busy: false, lastPrompt: "" };

  const api = (path, body, method = "POST") =>
    new Promise((resolve) =>
      chrome.runtime.sendMessage({ type: "api", path, method, body }, (r) =>
        resolve(r || { ok: false, status: 0, data: { detail: "No response from extension" } })
      )
    );

  const el = (tag, props = {}, ...kids) => {
    const n = Object.assign(document.createElement(tag), props);
    kids.forEach((k) => n.append(k));
    return n;
  };

  // ---------- Shadow DOM host ----------
  const host = el("div");
  const shadow = host.attachShadow({ mode: "open" });
  const root = el("div", { className: "root" });
  shadow.append(root);
  document.documentElement.append(host);
  fetch(chrome.runtime.getURL("content/bubble.css"))
    .then((r) => r.text())
    .then((css) => shadow.prepend(el("style", { textContent: css })));

  // ---------- Launcher ----------
  const launcher = el("button", { className: "launcher", title: "Interview Copilot", ariaLabel: "Open Interview Copilot", textContent: "✦" });

  // ---------- Panel ----------
  const dot = el("span", { className: "dot" });
  const minBtn = el("button", { className: "icon", title: "Collapse", ariaLabel: "Collapse", textContent: "–" });
  const endBtn = el("button", { className: "icon hidden", title: "End interview", textContent: "End interview" });
  const head = el("div", { className: "head" }, dot, el("span", { className: "title", textContent: "Interview Copilot" }), endBtn, minBtn);

  // consent / setup view
  const jobTitle = el("input", { type: "text", placeholder: "e.g. Senior Backend Engineer" });
  const jobDesc = el("textarea", { rows: 5, placeholder: "Paste the job description" });
  const candidateId = el("input", { type: "text", placeholder: "Optional, e.g. 42" });
  const language = el("select", {},
    ...[["en", "English"], ["fr", "Français"], ["ar", "العربية"], ["", "Detect automatically"]]
      .map(([value, label]) => el("option", { value, textContent: label })));
  const startBtn = el("button", { className: "primary", textContent: "Start interview" });
  const setupErr = el("div", { className: "error", role: "alert" });
  const consent = el("div", { className: "consent" },
    el("h3", { textContent: "New interview" }),
    el("label", { className: "field" }, "Job title", jobTitle),
    el("label", { className: "field" }, "Job description", jobDesc),
    el("label", { className: "field" }, "Candidate ID on the HR platform (the file is saved to their profile)", candidateId),
    el("label", { className: "field" }, "Interview language", language),
    setupErr, startBtn
  );

  // chat view
  const log = el("div", { className: "log", role: "log", ariaLive: "polite" });
  const input = el("textarea", { placeholder: "Ask the AI… e.g. “ask about leadership”", rows: 1 });
  input.setAttribute("dir", "auto");
  const sendBtn = el("button", { className: "primary", textContent: "Send" });
  const chat = el("div", { className: "chat hidden" }, log, el("div", { className: "composer" }, input, sendBtn));

  // review view (after the interview ends)
  const SECTIONS = [
    { key: "background", label: "Background", list: false },
    { key: "strengths", label: "Key strengths", list: true },
    { key: "concerns", label: "Concerns or gaps", list: true },
    { key: "notable_answers", label: "Notable answers", list: true },
    { key: "role_fit", label: "Role fit", list: false },
  ];
  const fields = {};
  const sectionEls = SECTIONS.map((sec) => {
    const ta = el("textarea", { className: "sumfield", rows: 3 });
    ta.setAttribute("dir", "auto");
    ta.addEventListener("input", () => autosize(ta));
    fields[sec.key] = ta;
    return el("label", { className: "field" }, sec.label + (sec.list ? " (one per line)" : ""), ta);
  });
  const reviseInput = el("input", { type: "text", placeholder: "e.g. make it shorter, or add a note on Kafka" });
  reviseInput.setAttribute("dir", "auto");
  const reviseBtn = el("button", { className: "primary", textContent: "Revise" });
  const undoBtn = el("button", { className: "linkbtn hidden", textContent: "Undo" });
  const reviseStatus = el("div", { className: "status", role: "status" }, undoBtn);
  const notesTa = el("textarea", { rows: 3, placeholder: "Your own notes, one per line. They appear separately in the file." });
  notesTa.setAttribute("dir", "auto");
  const pdfBtn = el("button", { className: "primary", textContent: "PDF" });
  const docxBtn = el("button", { className: "primary", textContent: "Word" });
  const dlStatus = el("div", { className: "status", role: "status" });
  const review = el("div", { className: "review hidden" },
    el("h3", { textContent: "Interview summary" }),
    ...sectionEls,
    el("h3", { textContent: "Want to change anything or add a note?" }),
    el("div", { className: "muted", textContent: "Edit the text above directly, or ask the AI to revise it." }),
    el("div", { className: "row" }, reviseInput, reviseBtn),
    reviseStatus,
    el("label", { className: "field" }, "Your notes", notesTa),
    el("h3", { textContent: "Download it?" }),
    el("div", { className: "row" }, pdfBtn, docxBtn),
    dlStatus
  );

  const panel = el("div", { className: "panel hidden" }, head, consent, chat, review);
  root.append(launcher, panel);

  // ---------- Messages ----------
  function addMsg(kind, text) {
    const m = el("div", { className: `msg ${kind}` });
    m.setAttribute("dir", "auto");
    m.append(el("div", { className: "body", textContent: text }));
    if (kind === "ai") {
      const actions = el("div", { className: "actions" },
        el("button", { textContent: "Copy", onclick: async (e) => {
          await navigator.clipboard.writeText(text);
          e.target.textContent = "Copied";
          setTimeout(() => (e.target.textContent = "Copy"), 1200);
        } }),
        el("button", { textContent: "Dismiss", onclick: () => m.remove() }),
        el("button", { textContent: "Ask another", onclick: () =>
          send("Give me another follow-up question on a different topic.", { silent: true }) })
      );
      m.append(actions);
    }
    log.append(m);
    log.scrollTop = log.scrollHeight;
    return m;
  }

  async function send(text, { silent = false } = {}) {
    if (!text || state.busy || !state.sessionId) return;
    state.busy = true;
    if (!silent) addMsg("me", text);
    const typing = addMsg("sys", "Thinking…");
    typing.classList.add("typing");
    const r = await api(`/session/${state.sessionId}/chat`, { message: text });
    typing.remove();
    state.busy = false;
    if (r.ok) addMsg("ai", r.data.reply);
    else addMsg("sys", r.data?.detail?.toString() || "Something went wrong. Try again.");
  }

  sendBtn.onclick = () => { const t = input.value.trim(); input.value = ""; send(t); };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendBtn.click(); }
  });

  startBtn.onclick = async () => {
    setupErr.textContent = "";
    startBtn.disabled = true;
    const r = await api("/session/start", {
      job_title: jobTitle.value.trim(),
      job_description: jobDesc.value.trim(),
      platform: location.hostname,
      candidate_id: candidateId.value.trim() || null,
      language: language.value,
    });
    if (!r.ok) {
      setupErr.textContent = r.status === 401
        ? "Not signed in. Add your token in the extension settings."
        : r.status === 0
        ? "Can't reach the backend. Check the URL in the extension settings."
        : (r.data?.detail?.toString() || "Could not start the session.");
      startBtn.disabled = false;
      return;
    }
    state.sessionId = r.data.session_id;
    consent.classList.add("hidden");
    chat.classList.remove("hidden");
    endBtn.classList.remove("hidden");
    addMsg("sys", "Session started. Ask me for questions any time.");
    startAudio();
    send("Suggest 2-3 opening questions based on the job description.", { silent: true });
  };

  // ---------- Live pushes from the backend ----------
  function addSuggestions({ observation, suggestions }) {
    const card = el("div", { className: "msg ai card" });
    if (observation) card.append(el("div", { className: "obs", dir: "auto", textContent: observation }));
    suggestions.forEach((sg) => {
      const row = el("div", { className: "sugg" });
      const drop = () => { row.remove(); if (!card.querySelector(".sugg")) card.remove(); };
      row.append(
        el("div", { className: "q", dir: "auto", textContent: sg.question }),
        el("div", { className: "why", dir: "auto", textContent: sg.reason }),
        el("div", { className: "actions" },
          el("button", { textContent: "Copy", onclick: async (e) => {
            await navigator.clipboard.writeText(sg.question);
            e.target.textContent = "Copied";
            setTimeout(() => (e.target.textContent = "Copy"), 1200);
          } }),
          el("button", { textContent: "Dismiss", onclick: drop }),
          el("button", { textContent: "Ask another", onclick: () => {
            drop();
            send(`Give me another question to replace this one: "${sg.question}"`, { silent: true });
          } })
        )
      );
      card.append(row);
    });
    log.append(card);
    log.scrollTop = log.scrollHeight;
    if (panel.classList.contains("hidden")) launcher.classList.add("alert");
  }

  function addEndPrompt() {
    const m = el("div", { className: "msg ai card" },
      el("div", { dir: "auto", textContent: "It sounds like the call has ended. End the interview and prepare the summary?" }),
      el("div", { className: "actions" },
        el("button", { textContent: "Yes, end it", onclick: () => { m.remove(); endInterview(); } }),
        el("button", { textContent: "Not yet", onclick: () => m.remove() })
      )
    );
    log.append(m);
    log.scrollTop = log.scrollHeight;
    if (panel.classList.contains("hidden")) launcher.classList.add("alert");
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg?.type !== "push") return;
    const p = msg.payload || {};
    if (p.type === "suggestions") addSuggestions(p);
    else if (p.type === "call_end_suspected") addEndPrompt();
    else if (p.type === "error") addMsg("sys", p.detail || "Something went wrong.");
  });

  // ---------- Audio capture ----------
  async function startAudio() {
    const r = await new Promise((res) =>
      chrome.runtime.sendMessage({ type: "audio-start", sessionId: state.sessionId }, (x) => res(x || {})));
    if (r.needsClick) {
      addMsg("sys", "To capture audio, click the Interview Copilot icon in Chrome's toolbar (under the puzzle-piece menu if hidden).");
    }
  }

  const STATUS_TEXT = {
    streaming: "Capturing audio.",
    stopped: "Audio capture stopped.",
    "mic-denied": "Microphone unavailable, so only the candidate is captured. Allow it in the extension's Options page.",
    "no-session": "Start an interview in the bubble first, then click the icon.",
  };
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg?.type !== "audio-status") return;
    if (msg.status === "streaming") dot.classList.add("live");
    if (msg.status === "stopped" || msg.status === "error") dot.classList.remove("live");
    addMsg("sys", STATUS_TEXT[msg.status] || `Audio problem: ${msg.detail || msg.status}`);
  });

  // ---------- Collapse / expand ----------
  const open = () => { launcher.classList.remove("alert"); launcher.classList.add("hidden"); panel.classList.remove("hidden"); };
  const close = () => { panel.classList.add("hidden"); launcher.classList.remove("hidden"); };
  launcher.onclick = open;
  minBtn.onclick = close;

  // ---------- Drag (works for launcher + panel header) ----------
  function makeDraggable(handle) {
    let sx, sy, ox, oy, moved = false;
    handle.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button") && handle === head) return;
      const rect = root.getBoundingClientRect();
      sx = e.clientX; sy = e.clientY; ox = rect.left; oy = rect.top; moved = false;
      handle.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const dx = ev.clientX - sx, dy = ev.clientY - sy;
        if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
        const w = root.offsetWidth, h = root.offsetHeight;
        const x = Math.min(Math.max(0, ox + dx), innerWidth - w);
        const y = Math.min(Math.max(0, oy + dy), innerHeight - h);
        root.style.insetInlineEnd = "auto"; root.style.insetBlockEnd = "auto";
        root.style.left = x + "px"; root.style.top = y + "px";
      };
      const up = () => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
    });
    // swallow the click that ends a drag so the launcher doesn't open
    handle.addEventListener("click", (e) => { if (moved) { e.stopImmediatePropagation(); moved = false; } }, true);
  }
  makeDraggable(head);
  makeDraggable(launcher);

  // ---------- End of interview: summary review ----------
  function autosize(ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight + 2, 220) + "px"; }
  const toLines = (t) => t.split("\n").map((x) => x.trim()).filter(Boolean);
  let prevSummary = null;

  function fillSummary(sum) {
    SECTIONS.forEach((sec) => {
      const v = sum[sec.key];
      fields[sec.key].value = sec.list ? (v || []).join("\n") : v || "";
      autosize(fields[sec.key]);
    });
  }
  function readSummary() {
    const out = {};
    SECTIONS.forEach((sec) => {
      const t = fields[sec.key].value.trim();
      out[sec.key] = sec.list ? toLines(fields[sec.key].value) : t;
    });
    return out;
  }
  const setStatus = (node, text, isError = false) => {
    node.firstChild?.nodeType === 3 && node.firstChild.remove();
    node.prepend(document.createTextNode(text));
    node.classList.toggle("error", isError);
  };

  async function saveEdits() {
    return api(`/session/${state.sessionId}/summary`, { summary: readSummary(), notes: toLines(notesTa.value) }, "PUT");
  }

  function showReview(sum) {
    chat.classList.add("hidden");
    endBtn.classList.add("hidden");
    review.classList.remove("hidden");
    fillSummary(sum);
    review.scrollTop = 0;
  }

  async function endInterview() {
    if (state.ending || !state.sessionId) return;
    state.ending = true;
    chrome.runtime.sendMessage({ type: "audio-stop" });
    dot.classList.remove("live");
    endBtn.disabled = true;
    const wait = addMsg("sys", "Interview ended. Writing the summary… this can take up to a minute.");
    wait.classList.add("typing");
    const r = await api(`/session/${state.sessionId}/end`, null);
    wait.remove();
    if (r.ok && r.data.summary) { showReview(r.data.summary); return; }
    // keep the chat visible so nothing is lost, and let HR retry
    state.ending = false;
    endBtn.disabled = false;
    const m = addMsg("sys", r.ok ? (r.data.summary_error || "Could not write the summary.") : "Could not reach the server for the summary.");
    m.append(el("button", { className: "linkbtn", textContent: "Try again", onclick: () => { m.remove(); endInterview(); } }));
  }

  reviseBtn.onclick = async () => {
    const instruction = reviseInput.value.trim();
    if (!instruction || reviseBtn.disabled) return;
    reviseBtn.disabled = true;
    setStatus(reviseStatus, "Revising…");
    const before = readSummary();
    const r = await api(`/session/${state.sessionId}/summary/revise`, { instruction, summary: before });
    reviseBtn.disabled = false;
    if (!r.ok) { setStatus(reviseStatus, r.data?.detail?.toString() || "Could not revise. Try again.", true); return; }
    prevSummary = before;
    fillSummary(r.data.summary);
    reviseInput.value = "";
    setStatus(reviseStatus, (r.data.comment || "Done.") + " ");
    undoBtn.classList.remove("hidden");
  };
  reviseInput.addEventListener("keydown", (e) => { if (e.key === "Enter") reviseBtn.click(); });
  undoBtn.onclick = () => {
    if (!prevSummary) return;
    fillSummary(prevSummary);
    prevSummary = null;
    undoBtn.classList.add("hidden");
    setStatus(reviseStatus, "Reverted. ");
  };

  async function download(format, btn) {
    const buttons = [pdfBtn, docxBtn];
    buttons.forEach((b) => (b.disabled = true));
    setStatus(dlStatus, "Preparing file…");
    const saved = await saveEdits();     // the file is built from what the server holds, so save first
    if (!saved.ok) {
      setStatus(dlStatus, saved.data?.detail?.toString() || "Could not save your edits.", true);
      buttons.forEach((b) => (b.disabled = false));
      return;
    }
    const r = await new Promise((res) =>
      chrome.runtime.sendMessage({ type: "download", path: `/session/${state.sessionId}/summary/export?format=${format}` },
        (x) => res(x || { ok: false, detail: "No response from extension" })));
    buttons.forEach((b) => (b.disabled = false));
    if (!r.ok) { setStatus(dlStatus, r.detail?.toString() || "Could not create the file.", true); return; }
    const bytes = Uint8Array.from(atob(r.base64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: r.mime }));
    const a = el("a", { href: url, download: r.filename });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    setStatus(dlStatus, r.saved
      ? `Downloaded ${r.filename}. Saved to the candidate profile.`
      : `Downloaded ${r.filename}. It could not be saved to the profile, so keep this copy.`, !r.saved);
  }
  pdfBtn.onclick = () => download("pdf");
  docxBtn.onclick = () => download("docx");

  endBtn.onclick = endInterview;
})();
