// AudioWorklet: microphone Float32 -> mono PCM16 at targetRate, posted in ~50 ms chunks with an RMS level.
// Runs on the audio thread. If the AudioContext already runs at targetRate this is a passthrough.
class RoomPcm extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.target = o.targetRate || 24000;
    this.step = sampleRate / this.target;          // input samples per output sample
    this.size = Math.round(this.target * (o.chunkMs || 50) / 1000);
    this.out = new Int16Array(this.size);
    this.n = 0;
    this.t = 0;                                     // fractional read position into [last, ...block]
    this.last = 0;
    this.sumSq = 0;
    // Biquad low-pass before downsampling to limit aliasing (RBJ cookbook, Q = 0.707).
    this.lp = this.step > 1 ? this.biquad(0.45 * this.target, sampleRate) : null;
    this.port.onmessage = (e) => { if (e.data === 'flush') this.flush(); };
  }

  biquad(fc, fs) {
    const w = 2 * Math.PI * fc / fs, a = Math.sin(w) / (2 * Math.SQRT1_2), c = Math.cos(w), a0 = 1 + a;
    return { b0: (1 - c) / 2 / a0, b1: (1 - c) / a0, b2: (1 - c) / 2 / a0, a1: -2 * c / a0, a2: (1 - a) / a0, x1: 0, x2: 0, y1: 0, y2: 0 };
  }

  filter(x) {
    const f = this.lp;
    const y = f.b0 * x + f.b1 * f.x1 + f.b2 * f.x2 - f.a1 * f.y1 - f.a2 * f.y2;
    f.x2 = f.x1; f.x1 = x; f.y2 = f.y1; f.y1 = y;
    return y;
  }

  push(s) {
    s = Math.max(-1, Math.min(1, s));
    this.sumSq += s * s;
    this.out[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    if (this.n === this.size) this.flush();
  }

  flush() {
    if (!this.n) return;
    const pcm = this.n === this.size ? this.out : this.out.slice(0, this.n);
    const level = Math.sqrt(this.sumSq / this.n);
    this.port.postMessage({ pcm: pcm.buffer, level }, [pcm.buffer]);
    this.out = new Int16Array(this.size);
    this.n = 0;
    this.sumSq = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input.length || !input[0]) return true;
    const len = input[0].length;
    let mono = input[0];
    if (input.length > 1) {
      mono = new Float32Array(len);
      for (const ch of input) for (let i = 0; i < len; i++) mono[i] += ch[i] / input.length;
    }
    if (this.step === 1) {
      for (let i = 0; i < len; i++) this.push(mono[i]);
      return true;
    }
    const x = this.lp ? mono.map((v) => this.filter(v)) : mono;
    let t = this.t;
    while (t < len) {
      const i = Math.floor(t);
      const a = i === 0 ? this.last : x[i - 1];
      this.push(a + (x[i] - a) * (t - i));
      t += this.step;
    }
    this.t = t - len;
    this.last = x[len - 1];
    return true;
  }
}

registerProcessor('room-pcm', RoomPcm);
