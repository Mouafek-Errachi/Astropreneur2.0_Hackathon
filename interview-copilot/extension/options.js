const $ = (id) => document.getElementById(id);
chrome.storage.local.get(["backendUrl", "token"]).then((v) => {
  $("url").value = v.backendUrl || "http://localhost:8010";
  $("tok").value = v.token || "";
});
$("save").onclick = async () => {
  const backendUrl = $("url").value.trim().replace(/\/$/, "");
  let origin;
  try { origin = new URL(backendUrl).origin; } catch { $("ok").textContent = "Enter a valid URL"; return; }
  // Only localhost is granted at install; any other backend is asked for here, one origin at a time.
  if (!/^http:\/\/(localhost|127\.0\.0\.1):8010$/.test(origin)) {
    const granted = await chrome.permissions.request({ origins: [origin + "/*"] }).catch(() => false);
    if (!granted) { $("ok").textContent = "Access to that backend was not allowed"; return; }
  }
  await chrome.storage.local.set({ backendUrl, token: $("tok").value.trim() });
  $("ok").textContent = "Saved";
};

$("mic").onclick = async () => {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
    $("micOk").textContent = "Microphone allowed";
  } catch (e) {
    $("micOk").textContent = "Blocked. Allow it in Chrome site settings for this extension.";
  }
};
