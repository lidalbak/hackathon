// Browser microphone source: getUserMedia -> AudioWorklet (PCM16, 24 kHz) -> WebSocket /ws/audio.
// The microphone is only opened by start() and is fully released by release(): every track is stopped,
// the audio graph is disconnected and the AudioContext closed, so the browser's mic indicator turns off.
(function () {
  const TARGET_RATE = 24000;
  const MAX_BUFFERED = 1 << 20; // drop audio rather than queue more than ~20 s on a stalled socket

  class RoomMic {
    constructor({ onState, onLevel, onError } = {}) {
      this.onState = onState || (() => {});
      this.onLevel = onLevel || (() => {});
      this.onError = onError || (() => {});
      this.state = 'idle'; // idle | starting | recording | stopping
      this.dropped = 0;
    }

    get active() { return this.state !== 'idle'; }

    set(state, detail) { this.state = state; this.onState(state, detail); }

    async start() {
      if (this.active) return;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.AudioWorkletNode) {
        throw new Error('Nettleseren støtter ikke mikrofonopptak her. Bruk Chrome, Edge, Safari eller Firefox på http://127.0.0.1.');
      }
      this.set('starting');
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        this.track = this.stream.getAudioTracks()[0];
        this.track.onended = () => this.fail('Mikrofonen ble koblet fra eller tilgangen trukket tilbake.');
        await this.buildGraph();
        await this.connect();
      } catch (err) {
        this.release();
        const name = err && err.name;
        if (name === 'NotAllowedError') throw new Error('Mikrofontilgang ble avslått. Tillat mikrofon for 127.0.0.1 i nettleseren.');
        if (name === 'NotFoundError') throw new Error('Fant ingen mikrofon.');
        throw err;
      }
    }

    async buildGraph() {
      // Prefer a 24 kHz context (the browser resamples well); fall back to the device rate and resample in the worklet.
      try {
        this.ctx = new AudioContext({ sampleRate: TARGET_RATE, latencyHint: 'interactive' });
        this.source = this.ctx.createMediaStreamSource(this.stream);
      } catch (_) {
        if (this.ctx) this.ctx.close();
        this.ctx = new AudioContext({ latencyHint: 'interactive' });
        this.source = this.ctx.createMediaStreamSource(this.stream);
      }
      await this.ctx.audioWorklet.addModule('/static/pcm-worklet.js');
      this.node = new AudioWorkletNode(this.ctx, 'room-pcm', { processorOptions: { targetRate: TARGET_RATE, chunkMs: 50 } });
      this.mute = this.ctx.createGain();
      this.mute.gain.value = 0; // keeps the graph pulled without playing the room back through the speakers
      this.source.connect(this.node).connect(this.mute).connect(this.ctx.destination);
      this.node.port.onmessage = (e) => this.chunk(e.data);
      if (this.ctx.state === 'suspended') await this.ctx.resume();
    }

    connect() {
      return new Promise((resolve, reject) => {
        const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/audio`);
        ws.binaryType = 'arraybuffer';
        this.ws = ws;
        let settled = false;
        const timer = setTimeout(() => { if (!settled) { settled = true; reject(new Error('Serveren svarte ikke på opptaksstart.')); } }, 15000);
        ws.onopen = () => ws.send(JSON.stringify({
          type: 'start', source: 'browser-mic', format: 'pcm16', sample_rate: TARGET_RATE,
          device: (this.track && this.track.label) || 'ukjent mikrofon',
        }));
        ws.onmessage = (e) => {
          if (typeof e.data !== 'string') return;
          const msg = JSON.parse(e.data);
          if (msg.type === 'ready') {
            settled = true; clearTimeout(timer);
            this.ready = true;
            this.set('recording', msg);
            resolve(msg);
          } else if (msg.type === 'error') {
            if (!settled) { settled = true; clearTimeout(timer); reject(new Error(msg.message)); }
            else this.fail(msg.message);
          } else if (msg.type === 'stop') {
            // Stopped elsewhere (other screen, provider error, demo end): release the mic now.
            this.release(msg.reason);
          } else if (msg.type === 'stopped') {
            this.release();
          }
        };
        ws.onerror = () => {};
        ws.onclose = () => {
          if (!settled) { settled = true; clearTimeout(timer); reject(new Error('Fikk ikke kontakt med serveren.')); return; }
          if (this.state === 'recording') this.fail('Forbindelsen til serveren ble brutt. Mikrofonen er stoppet.');
          else this.release();
        };
      });
    }

    chunk({ pcm, level }) {
      this.onLevel(level);
      const ws = this.ws;
      if (this.paused || !this.ready || !ws || ws.readyState !== WebSocket.OPEN) return;
      if (ws.bufferedAmount > MAX_BUFFERED) { this.dropped += pcm.byteLength; return; }
      ws.send(pcm);
    }

    async stop() {
      if (!this.active || this.state === 'stopping') return;
      this.set('stopping');
      // Stop capture first so no more audio leaves the browser, then let the server flush its last segment.
      this.releaseCapture();
      const ws = this.ws;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'stop' }));
        await new Promise((resolve) => { const t = setTimeout(resolve, 8000); ws.addEventListener('close', () => { clearTimeout(t); resolve(); }); });
      }
      this.release();
    }

    fail(message) {
      this.onError(message);
      this.release(message);
    }

    releaseCapture() {
      if (this.node) { this.node.port.onmessage = null; this.node.disconnect(); }
      if (this.source) this.source.disconnect();
      if (this.stream) this.stream.getTracks().forEach((t) => { t.onended = null; t.stop(); });
      if (this.ctx && this.ctx.state !== 'closed') this.ctx.close().catch(() => {});
      this.node = this.source = this.stream = this.track = this.ctx = this.mute = null;
      this.onLevel(0);
    }

    release(reason) {
      this.releaseCapture();
      this.ready = false;
      if (this.ws) {
        const ws = this.ws; this.ws = null;
        ws.onclose = null;
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close();
      }
      if (this.state !== 'idle') this.set('idle', { reason });
    }
  }

  window.RoomMic = RoomMic;
})();
