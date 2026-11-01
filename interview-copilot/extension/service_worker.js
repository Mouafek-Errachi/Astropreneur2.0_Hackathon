async function getConfig() {
  const { backendUrl = "http://localhost:8010", token = "" } =
    await chrome.storage.local.get(["backendUrl", "token"]);
  return { backendUrl: backendUrl.replace(/\/$/, ""), token };
}

// ---------- Audio capture orchestration ----------
async function ensureOffscreen() {
  const ctxs = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (ctxs.length) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA", "AUDIO_PLAYBACK"],
    justification: "Capture and stream interview audio to the backend",
  });
}

async function notifyTab(tabId, payload) {
  try { await chrome.tabs.sendMessage(tabId, { type: "audio-status", ...payload }); } catch {}
}

async function startCapture(tabId, sessionId) {
  // Throws unless the user just invoked the extension (toolbar icon) on this tab.
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  await ensureOffscreen();
  const { backendUrl, token } = await getConfig();
  // One-time ticket for the audio socket: the JWT itself never goes into the WebSocket URL
  const res = await fetch(`${backendUrl}/session/${sessionId}/audio-ticket`, {
    method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Could not open the audio stream (${res.status})`);
  const { ticket } = await res.json();
  await chrome.storage.session.set({ capture: { tabId, sessionId } });
  chrome.runtime.sendMessage({ target: "offscreen", type: "start", streamId, sessionId, backendUrl, ticket });
}

async function stopCapture() {
  await chrome.storage.session.remove(["capture", "pending"]);
  try { await chrome.runtime.sendMessage({ target: "offscreen", type: "stop" }); } catch {}
  try { await chrome.offscreen.closeDocument(); } catch {}
}

chrome.action.onClicked.addListener(async (tab) => {
  const { pending } = await chrome.storage.session.get("pending");
  if (!pending || pending.tabId !== tab.id) {
    notifyTab(tab.id, { status: "no-session" });
    return;
  }
  try {
    await startCapture(tab.id, pending.sessionId);
    await chrome.storage.session.remove("pending");
  } catch (e) {
    notifyTab(tab.id, { status: "error", detail: String(e) });
  }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const { capture } = await chrome.storage.session.get("capture");
  if (capture?.tabId === tabId) stopCapture(); // tab closed = call ended
});

// ---------- Messages ----------
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.target === "offscreen") return;

  if (msg?.type === "api") {
    (async () => {
      try {
        const { backendUrl, token } = await getConfig();
        const res = await fetch(backendUrl + msg.path, {
          method: msg.method || "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: msg.body ? JSON.stringify(msg.body) : undefined,
        });
        const data = await res.json().catch(() => ({}));
        sendResponse({ ok: res.ok, status: res.status, data });
      } catch (err) {
        sendResponse({ ok: false, status: 0, data: { detail: String(err) } });
      }
    })();
    return true;
  }

  if (msg?.type === "download") {
    // Binary (PDF/DOCX): fetch with the JWT here, hand base64 to the page (messages can't carry Blobs)
    (async () => {
      try {
        const { backendUrl, token } = await getConfig();
        const res = await fetch(backendUrl + msg.path, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          sendResponse({ ok: false, status: res.status, detail: data.detail });
          return;
        }
        const bytes = new Uint8Array(await res.arrayBuffer());
        let bin = "";
        for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
        const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "")?.[1] || "interview-summary";
        sendResponse({ ok: true, base64: btoa(bin), filename: name, mime: res.headers.get("Content-Type") || "application/octet-stream",
                       saved: res.headers.get("X-Saved-To-Profile") === "true" });
      } catch (err) {
        sendResponse({ ok: false, status: 0, detail: String(err) });
      }
    })();
    return true;
  }

  if (msg?.type === "audio-start") {
    (async () => {
      try {
        await startCapture(sender.tab.id, msg.sessionId);
        sendResponse({ ok: true });
      } catch {
        await chrome.storage.session.set({ pending: { tabId: sender.tab.id, sessionId: msg.sessionId } });
        sendResponse({ ok: false, needsClick: true });
      }
    })();
    return true;
  }

  if (msg?.type === "audio-stop") {
    stopCapture().then(() => sendResponse({ ok: true }));
    return true;
  }

  if (msg?.target === "sw" && msg.type === "push") {
    (async () => {
      const { capture } = await chrome.storage.session.get("capture");
      if (capture) try { await chrome.tabs.sendMessage(capture.tabId, { type: "push", payload: msg.payload }); } catch {}
    })();
    return;
  }

  if (msg?.target === "sw" && msg.type === "audio-status") {
    (async () => {
      const { capture } = await chrome.storage.session.get("capture");
      if (capture) notifyTab(capture.tabId, { status: msg.status, detail: msg.detail });
      if (msg.status === "stopped" || msg.status === "error") stopCapture();
    })();
  }
});
