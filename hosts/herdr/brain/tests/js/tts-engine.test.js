"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const TtsEngine = require("../../src/herdr_brain/static/tts-engine.js");

test("TtsEngine module exports createTtsEngine", () => {
  assert.equal(typeof TtsEngine.createTtsEngine, "function");
});

test("splitIntoSentences segments Spanish text into natural sentences", () => {
  const engine = TtsEngine.createTtsEngine();
  const text = "Primera frase completa. Segunda frase con detalles; ¿y una tercera frase? ¡Sí!";
  const segments = engine.splitIntoSentences(text);
  assert.ok(segments.length >= 3);
  assert.ok(segments[0].text.includes("Primera frase completa"));
});

test("cleanTextForSpeech strips permalinks, markdown tags, empty parens and excess spaces", () => {
  const engine = TtsEngine.createTtsEngine();
  const dirty = "  # Título principal [¶] con *negrita* y _cursiva_ () []  y   espacios dobles. ";
  const clean = engine.cleanTextForSpeech(dirty);
  assert.equal(clean, "Título principal con negrita y cursiva y espacios dobles.");
});

test("mock Web Speech state transitions and playback", () => {
  const voices = [
    { name: "Google Español", lang: "es-ES" },
    { name: "Siri Spanish", lang: "es-ES" }
  ];

  let spokenUtterances = [];
  let cancelled = 0;

  const mockSynth = {
    getVoices: () => voices,
    cancel: () => { cancelled++; },
    resume: () => {},
    pause: () => {},
    speak: (utt) => { spokenUtterances.push(utt); }
  };

  function MockUtterance(text) {
    this.text = text;
    this.rate = 1.0;
    this.lang = "es-ES";
  }

  const mockWin = {
    speechSynthesis: mockSynth,
    SpeechSynthesisUtterance: MockUtterance,
    innerHeight: 800
  };

  const engine = TtsEngine.createTtsEngine({ win: mockWin });
  assert.equal(engine.supported, true);

  // Manually push mock sentences to simulate an indexed document
  engine.state.sentences = [
    { id: 0, text: "Frase cero.", elements: [] },
    { id: 1, text: "Frase uno.", elements: [] }
  ];

  engine.playFromIndex(0);
  assert.equal(engine.state.currentIndex, 0);
  assert.equal(engine.state.isPlaying, true);
  assert.equal(cancelled >= 1, true);

  // Speed cycling
  assert.equal(engine.state.rate, 1.0);
  engine.cycleSpeed();
  assert.equal(engine.state.rate, 1.25);
  engine.cycleSpeed();
  assert.equal(engine.state.rate, 1.5);

  // Stop playback
  engine.stopPlayback();
  assert.equal(engine.state.isPlaying, false);
  assert.equal(engine.state.currentIndex, -1);
});
