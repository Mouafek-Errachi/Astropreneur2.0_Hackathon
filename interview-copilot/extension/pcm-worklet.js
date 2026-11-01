// Collects 128-sample blocks into 100 ms chunks (1600 samples @ 16 kHz) of 16-bit PCM.
class PCMCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Int16Array(1600);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const v = Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      if (this.n === this.buf.length) {
        this.port.postMessage(this.buf.buffer.slice(0));
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-capture", PCMCapture);
