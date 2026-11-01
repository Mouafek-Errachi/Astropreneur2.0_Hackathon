// Frame format sent over WebSocket: [1 byte source tag][PCM16 mono 16 kHz little-endian]
// tag 1 = candidate (tab audio), tag 2 = HR (microphone)
const TAG_CANDIDATE = 1;
const TAG_HR = 2;

let ctx = null, ws = null, tabStream = null, micStream = null, player = null;

const status = (s, detail) =>
  chrome.runtime.sendMessage({ target: "sw", type: "audio-status", status: s, detail }).catch(() => {});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.target !== "offscreen") return;
  if (msg.type === "start") start(msg).catch((e) => { status("error", String(e)); stop(); });
  if (msg.type === "stop") stop();
});

function send(tag, pcm) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const frame = new Uint8Array(1 + pcm.byteLength);
  frame[0] = tag;
  frame.set(new Uint8Array(pcm), 1);
  ws.send(frame);
}

async function addSource(stream, tag) {
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-capture");
  node.port.onmessage = (e) => send(tag, e.data);
  const mute = ctx.createGain();          // keeps the graph running without echoing audio
  mute.gain.value = 0;
  src.connect(node).connect(mute).connect(ctx.destination);
}

async function start({ streamId, sessionId, backendUrl, ticket }) {
  if (ctx) stop();

  tabStream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
  });
  // Capturing a tab silences it for the user, so play it back.
  player = new Audio();
  player.srcObject = tabStream;
  player.play().catch(() => {});

  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
  } catch {
    micStream = null;
    status("mic-denied");
  }

  const wsUrl = backendUrl.replace(/^http/, "ws") +
    `/session/${sessionId}/audio?ticket=${encodeURIComponent(ticket)}`;
  ws = new WebSocket(wsUrl);
  ws.binaryType = "arraybuffer";
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = () => reject(new Error("Could not connect to the backend audio stream"));
  });
  ws.onmessage = (e) => {
    if (typeof e.data !== "string") return;
    try {
      chrome.runtime.sendMessage({ target: "sw", type: "push", payload: JSON.parse(e.data) }).catch(() => {});
    } catch {}
  };
  ws.onclose = () => { if (ctx) { status("error", "Audio connection closed"); stop(); } };

  ctx = new AudioContext({ sampleRate: 16000 });
  await ctx.audioWorklet.addModule("pcm-worklet.js");
  await addSource(tabStream, TAG_CANDIDATE);
  if (micStream) await addSource(micStream, TAG_HR);

  tabStream.getAudioTracks()[0].onended = () => { status("stopped"); stop(); };
  status("streaming");
}

function stop() {
  const c = ctx; ctx = null;               // null first so ws.onclose doesn't re-report
  try { ws && ws.close(); } catch {}
  [tabStream, micStream].forEach((s) => s && s.getTracks().forEach((t) => t.stop()));
  if (player) { player.srcObject = null; player = null; }
  if (c) c.close().catch(() => {});
  ws = tabStream = micStream = null;
}
