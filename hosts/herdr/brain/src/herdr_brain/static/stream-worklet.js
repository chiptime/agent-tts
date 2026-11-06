/* AudioWorklet processor for 16 kHz PCM16 mono downsampling (PRD-03 Task T1). */

class StreamAudioProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.frameSize = 1600; // ~100 ms at 16 kHz
    this.buffer = new Int16Array(this.frameSize);
    this.bufferIndex = 0;
    this.phase = 0.0;
  }

  process(inputs, outputs, parameters) {
    var input = inputs[0];
    if (!input || !input.length) return true;
    var channel = input[0];
    if (!channel || !channel.length) return true;

    var srcRate = sampleRate; // global inside AudioWorkletGlobalScope
    var ratio = srcRate / this.targetRate;

    var inputIndex = this.phase;
    while (inputIndex < channel.length) {
      var idx = Math.floor(inputIndex);
      var frac = inputIndex - idx;
      var s0 = channel[idx];
      var s1 = (idx + 1 < channel.length) ? channel[idx + 1] : s0;
      var val = s0 + frac * (s1 - s0);

      var clamped = Math.max(-1.0, Math.min(1.0, val));
      var pcm16 = clamped < 0 ? Math.floor(clamped * 32768) : Math.floor(clamped * 32767);
      this.buffer[this.bufferIndex++] = Math.max(-32768, Math.min(32767, pcm16));

      if (this.bufferIndex >= this.frameSize) {
        var copy = new Int16Array(this.buffer);
        this.port.postMessage(copy.buffer, [copy.buffer]);
        this.bufferIndex = 0;
      }

      inputIndex += ratio;
    }
    this.phase = inputIndex - channel.length;
    return true;
  }
}

registerProcessor("stream-audio-processor", StreamAudioProcessor);
