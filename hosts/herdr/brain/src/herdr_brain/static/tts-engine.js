/**
 * TTS (Text-to-Speech) Interactivo y Karaoke para brain-tts
 * Portado desde la especificación RFC-02 (modular-saas)
 * - Síntesis instantánea frase a frase con Web Speech API (es-ES)
 * - Segmentación atómica no destructiva del DOM usando splitText()
 * - Click-to-Speech instantáneo sin retrasos
 * - Auto-scroll suave manteniendo centrada la frase activa
 * - Salto inteligente de diagramas (Mermaid) y bloques de código
 * - Síntesis semántica estructurada de tablas
 * - Barra flotante de control (Play, Pausa, Anterior, Siguiente, Velocidad, Contador)
 */

(function (global) {
  'use strict';

  function createTtsEngine(options) {
    options = options || {};
    var doc = options.doc || (typeof document !== 'undefined' ? document : null);
    var win = options.win || (typeof window !== 'undefined' ? window : null);
    var onSpeechStateChange = options.onSpeechStateChange || null;

    var speechSynth = (win && win.speechSynthesis) || null;
    var supported = !!(speechSynth && win && win.SpeechSynthesisUtterance);

    var state = {
      sentences: [],       // [ { id, text, elements: [], isDiagram, title } ]
      currentIndex: -1,
      isPlaying: false,
      isPaused: false,
      rate: 1.0,           // 1.0, 1.25, 1.5, 1.75, 2.0, 0.85
      voice: null,
      isOpen: false,
      activeContainer: null
    };

    function getBestVoice() {
      if (!speechSynth || !speechSynth.getVoices) return null;
      var voices = speechSynth.getVoices();
      if (!voices || voices.length === 0) return null;

      var best = null;
      for (var i = 0; i < voices.length; i++) {
        var v = voices[i];
        if (v.lang === 'es-ES' && v.name && v.name.indexOf('Google') !== -1) return v;
      }
      for (var j = 0; j < voices.length; j++) {
        var v2 = voices[j];
        if (v2.lang === 'es-ES' && (!v2.name || v2.name.indexOf('Compact') === -1)) return v2;
      }
      for (var k = 0; k < voices.length; k++) {
        var v3 = voices[k];
        if (v3.lang === 'es-ES') return v3;
      }
      for (var l = 0; l < voices.length; l++) {
        var v4 = voices[l];
        if (v4.lang && v4.lang.indexOf('es') === 0) return v4;
      }
      return null;
    }

    function updateVoice() {
      state.voice = getBestVoice();
    }

    if (speechSynth && 'onvoiceschanged' in speechSynth) {
      speechSynth.onvoiceschanged = updateVoice;
    }
    updateVoice();

    // Segmentador de frases usando Intl.Segmenter con fallback regex
    function splitIntoSentences(text) {
      if (typeof Intl !== 'undefined' && Intl.Segmenter) {
        try {
          var segmenter = new Intl.Segmenter('es', { granularity: 'sentence' });
          var segments = Array.from(segmenter.segment(text));
          return segments.map(function (s) {
            return { text: s.segment, index: s.index };
          });
        } catch (e) {
          /* fallback below */
        }
      }

      var regex = /([^.?!]+[.?!]+(?:\s|$)|[^.?!]+$)/g;
      var matches = [];
      var match;
      while ((match = regex.exec(text)) !== null) {
        matches.push({ text: match[0], index: match.index });
      }
      return matches.length > 0 ? matches : [{ text: text, index: 0 }];
    }

    // Limpieza de caracteres y sintaxis para pronunciación humana natural
    function cleanTextForSpeech(text) {
      if (!text) return '';
      return text
        .replace(/[¶#*`_~]/g, '')           // Eliminar símbolos markdown y anclas permalink
        .replace(/\[\s*\]|\(\s*\)/g, '')   // Eliminar paréntesis vacíos
        .replace(/\s+/g, ' ')              // Espacios dobles
        .trim();
    }

    // Limpiar marcado de oraciones previo de forma segura sin destruir texto
    function clearExistingMarkup(root) {
      if (!root || !root.querySelectorAll) return;
      var existingSpans = root.querySelectorAll('span.tts-sentence');
      for (var i = 0; i < existingSpans.length; i++) {
        var span = existingSpans[i];
        var parent = span.parentNode;
        if (!parent) continue;
        while (span.firstChild) {
          parent.insertBefore(span.firstChild, span);
        }
        parent.removeChild(span);
        if (parent.normalize) parent.normalize();
      }

      var processedBlocks = root.querySelectorAll('.tts-processed, .tts-table-row, .tts-active');
      for (var j = 0; j < processedBlocks.length; j++) {
        var b = processedBlocks[j];
        b.classList.remove('tts-processed', 'tts-sentence', 'tts-table-row', 'tts-active');
        b.removeAttribute('data-tts-id');
      }
    }

    // Indexar el contenido de un contenedor para lectura estructurada
    function indexContainer(root) {
      if (!root || !root.querySelectorAll) return 0;

      clearExistingMarkup(root);

      state.sentences = [];
      state.currentIndex = -1;
      state.activeContainer = root;

      var sentenceIdCounter = 0;
      var diagramCounter = 0;

      var blocks = root.querySelectorAll(
        'h1, h2, h3, h4, h5, h6, p, li, blockquote, .mermaid-card, .mermaid, table, pre'
      );

      for (var bIdx = 0; bIdx < blocks.length; bIdx++) {
        var block = blocks[bIdx];
        if (block.closest && block.closest('.tts-processed')) continue;
        if (block.matches && block.matches('li') && block.querySelector('p')) {
          continue; // Los párrafos internos se procesan independientemente
        }

        // 1. Manejo de DIAGRAMAS (Mermaid)
        if ((block.matches && block.matches('.mermaid-card, .mermaid, pre.mermaid, div.mermaid')) ||
            block.classList.contains('mermaid-card') ||
            block.classList.contains('mermaid')) {
          diagramCounter++;
          var diagramTitle = 'Diagrama ' + diagramCounter;
          var spokenText = diagramTitle + ', omitido.';

          block.classList.add('tts-processed', 'tts-sentence');
          block.setAttribute('data-tts-id', sentenceIdCounter);
          block.setAttribute('title', 'Toca para saltar diagrama');

          state.sentences.push({
            id: sentenceIdCounter,
            text: spokenText,
            elements: [block],
            isDiagram: true,
            title: diagramTitle
          });

          sentenceIdCounter++;
          continue;
        }

        // 2. Manejo de TABLAS (Reglas, especificaciones, pares clave-valor)
        if (block.matches && block.matches('table')) {
          var headers = Array.from(block.querySelectorAll('thead th')).map(function (th) {
            return th.textContent.trim();
          });
          var rows = block.querySelectorAll('tbody tr');

          for (var rIdx = 0; rIdx < rows.length; rIdx++) {
            var tr = rows[rIdx];
            if (tr.closest && tr.closest('.tts-processed')) continue;
            var cells = Array.from(tr.querySelectorAll('td')).map(function (td) {
              return td.textContent.trim();
            });
            if (cells.length === 0) continue;

            var spoken = '';
            if (cells.length === 2 && cells[0].length <= 30) {
              spoken = cells[0] + ': ' + cells[1];
            } else {
              spoken = cells
                .map(function (c, i) { return headers[i] ? headers[i] + ': ' + c : c; })
                .join('. ');
            }

            var cleanSpoken = cleanTextForSpeech(spoken);
            if (!cleanSpoken || cleanSpoken.length < 2) continue;

            tr.classList.add('tts-processed', 'tts-sentence', 'tts-table-row');
            tr.setAttribute('data-tts-id', sentenceIdCounter);
            tr.setAttribute('title', 'Toca para escuchar esta fila');

            state.sentences.push({
              id: sentenceIdCounter,
              text: cleanSpoken,
              elements: [tr],
              isDiagram: false,
              title: cells[0] || 'Tabla'
            });

            sentenceIdCounter++;
          }

          block.classList.add('tts-processed');
          continue;
        }

        // 3. Bloques de código preformateado (no mermaid)
        if (block.matches && block.matches('pre')) {
          block.classList.add('tts-processed', 'tts-sentence');
          block.setAttribute('data-tts-id', sentenceIdCounter);
          block.setAttribute('title', 'Toca para saltar bloque de código');

          state.sentences.push({
            id: sentenceIdCounter,
            text: 'Bloque de código, omitido.',
            elements: [block],
            isDiagram: true,
            title: 'Código'
          });

          sentenceIdCounter++;
          continue;
        }

        // 4. Manejo de TEXTO enriquecido (Párrafos, Listas, Títulos, Citas)
        if (!doc || !doc.createTreeWalker) continue;

        var textNodes = [];
        var walker = doc.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
          acceptNode: function (node) {
            if (!node.textContent || !node.textContent.trim()) return NodeFilter.FILTER_REJECT;
            if (node.parentElement && node.parentElement.closest &&
                node.parentElement.closest('.headerlink, .mermaid, .mermaid-card, pre, svg, script, style')) {
              return NodeFilter.FILTER_REJECT;
            }
            return NodeFilter.FILTER_ACCEPT;
          }
        });

        var tNode;
        while ((tNode = walker.nextNode())) {
          textNodes.push(tNode);
        }

        if (textNodes.length === 0) continue;

        var fullText = textNodes.map(function (n) { return n.textContent; }).join('');
        var segments = splitIntoSentences(fullText);
        if (segments.length === 0) continue;

        // Calcular límites de corte exactos en el texto
        var boundaries = [];
        segments.forEach(function (s) {
          boundaries.push(s.index);
          boundaries.push(s.index + s.text.length);
        });
        var uniqueSortedBoundaries = Array.from(new Set(boundaries)).sort(function (a, b) {
          return a - b;
        });

        // Dividir los nodos de texto en los límites físicos usando splitText()
        var currentOffset = 0;
        var nodeIdx = 0;

        for (var bi = 0; bi < uniqueSortedBoundaries.length; bi++) {
          var b = uniqueSortedBoundaries[bi];
          while (nodeIdx < textNodes.length) {
            var node = textNodes[nodeIdx];
            var nodeLength = node.textContent.length;
            var nodeEnd = currentOffset + nodeLength;

            if (b > currentOffset && b < nodeEnd) {
              var splitAt = b - currentOffset;
              var secondPart = node.splitText(splitAt);
              textNodes.splice(nodeIdx + 1, 0, secondPart);
              currentOffset += splitAt;
              nodeIdx++;
              break;
            } else if (b >= nodeEnd) {
              currentOffset = nodeEnd;
              nodeIdx++;
            } else {
              break;
            }
          }
        }

        // Envolver cada trozo de texto en su span de frase sin alterar etiquetas padre
        var offset = 0;
        var sentenceMap = new Map();

        textNodes.forEach(function (node) {
          var len = node.textContent.length;
          if (!node.textContent.trim()) {
            offset += len;
            return;
          }

          var segIdx = segments.findIndex(function (s) {
            return offset >= s.index && offset < s.index + s.text.length;
          });

          if (segIdx !== -1) {
            var span = doc.createElement('span');
            span.className = 'tts-sentence';
            span.setAttribute('data-tts-id', sentenceIdCounter + segIdx);

            if (node.parentNode) {
              node.parentNode.replaceChild(span, node);
              span.appendChild(node);
            }

            if (!sentenceMap.has(segIdx)) {
              sentenceMap.set(segIdx, []);
            }
            sentenceMap.get(segIdx).push(span);
          }

          offset += len;
        });

        // Registrar las frases del bloque en el catálogo de reproducción
        segments.forEach(function (seg, sIdx) {
          var segClean = cleanTextForSpeech(seg.text);
          if (!segClean || segClean.length < 2) return;

          var spans = sentenceMap.get(sIdx) || [];
          if (spans.length > 0) {
            state.sentences.push({
              id: sentenceIdCounter + sIdx,
              text: segClean,
              elements: spans,
              isDiagram: false,
              title: ''
            });
          }
        });

        sentenceIdCounter += segments.length;
        block.classList.add('tts-processed');
      }

      updateStatusUI();
      return state.sentences.length;
    }

    // Efecto Karaoke: resaltado visual y auto-scroll suave manteniendo centrado
    function highlightSentence(sentence) {
      if (!doc) return;
      var prevActive = doc.querySelectorAll('.tts-active');
      for (var i = 0; i < prevActive.length; i++) {
        prevActive[i].classList.remove('tts-active');
      }

      if (!sentence || !sentence.elements || sentence.elements.length === 0) return;

      sentence.elements.forEach(function (el) {
        el.classList.add('tts-active');
      });

      var targetEl = sentence.elements[0];
      if (targetEl && targetEl.getBoundingClientRect && win) {
        var rect = targetEl.getBoundingClientRect();
        var isOutOfView = rect.top < 100 || rect.bottom > (win.innerHeight - 120);

        if (isOutOfView && targetEl.scrollIntoView) {
          targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }
    }

    // Reproducción frase a frase con protección contra los bugs de Chromium
    function playFromIndex(index) {
      if (!supported) return;

      if (index < 0 || index >= state.sentences.length) {
        stopPlayback();
        return;
      }

      state.currentIndex = index;
      state.isPlaying = true;
      state.isPaused = false;
      openPlayerBar();

      var sentence = state.sentences[index];
      highlightSentence(sentence);
      updatePlayPauseButton();
      updateStatusUI();

      if (onSpeechStateChange) {
        onSpeechStateChange({ isPlaying: true, index: index, total: state.sentences.length });
      }

      // Cancelar cualquier audio previo
      speechSynth.cancel();

      // Retardo de 40ms para que Chromium limpie la cola interna y no cancele de inmediato
      setTimeout(function () {
        if (!state.isPlaying || state.currentIndex !== index) return;

        if (speechSynth.resume) speechSynth.resume();

        var utterance = new win.SpeechSynthesisUtterance(sentence.text);
        utterance.rate = state.rate;
        utterance.lang = 'es-ES';

        var voice = getBestVoice();
        if (voice) {
          utterance.voice = voice;
        }

        utterance.onend = function () {
          if (state.isPlaying && state.currentIndex === index) {
            playFromIndex(index + 1);
          }
        };

        utterance.onerror = function (err) {
          // Ignorar cancelaciones e interrupciones voluntarias para evitar bucle infinito
          if (err.error === 'interrupted' || err.error === 'canceled') {
            return;
          }
          console.warn('TTS error en síntesis:', err.error);
          state.isPlaying = false;
          state.isPaused = false;
          updatePlayPauseButton();
          if (onSpeechStateChange) onSpeechStateChange({ isPlaying: false, index: index });
        };

        speechSynth.speak(utterance);
      }, 40);
    }

    function pausePlayback() {
      if (!supported) return;
      state.isPlaying = false;
      state.isPaused = true;
      speechSynth.pause();
      updatePlayPauseButton();
      if (onSpeechStateChange) onSpeechStateChange({ isPlaying: false, isPaused: true, index: state.currentIndex });
    }

    function resumePlayback() {
      if (!supported) return;
      if (state.currentIndex === -1) {
        if (state.sentences.length === 0 && state.activeContainer) {
          indexContainer(state.activeContainer);
        }
        if (state.sentences.length > 0) {
          playFromIndex(0);
        }
        return;
      }

      state.isPlaying = true;
      state.isPaused = false;

      if (speechSynth.paused && speechSynth.resume) {
        speechSynth.resume();
        updatePlayPauseButton();
        if (onSpeechStateChange) onSpeechStateChange({ isPlaying: true, index: state.currentIndex });
      } else {
        playFromIndex(state.currentIndex);
      }
    }

    function stopPlayback() {
      if (!supported) return;
      state.isPlaying = false;
      state.isPaused = false;
      state.currentIndex = -1;
      speechSynth.cancel();

      if (doc) {
        var activeEls = doc.querySelectorAll('.tts-active');
        for (var i = 0; i < activeEls.length; i++) {
          activeEls[i].classList.remove('tts-active');
        }
      }

      updatePlayPauseButton();
      updateStatusUI();
      if (onSpeechStateChange) onSpeechStateChange({ isPlaying: false, isPaused: false, index: -1 });
    }

    function togglePlayPause() {
      if (state.isPlaying) {
        pausePlayback();
      } else {
        resumePlayback();
      }
    }

    function nextSentence() {
      if (state.currentIndex < state.sentences.length - 1) {
        playFromIndex(state.currentIndex + 1);
      }
    }

    function prevSentence() {
      if (state.currentIndex > 0) {
        playFromIndex(state.currentIndex - 1);
      }
    }

    function cycleSpeed() {
      var speeds = [1.0, 1.25, 1.5, 1.75, 2.0, 0.85];
      var currIdx = speeds.indexOf(state.rate);
      var nextIdx = (currIdx + 1) % speeds.length;
      state.rate = speeds[nextIdx];

      var speedBtn = doc ? doc.getElementById('tts-speed-btn') : null;
      if (speedBtn) speedBtn.textContent = state.rate + 'x';

      if (state.isPlaying && state.currentIndex >= 0) {
        playFromIndex(state.currentIndex);
      }
    }

    function openPlayerBar(containerToPlay) {
      state.isOpen = true;
      var bar = doc ? doc.getElementById('tts-bar') : null;
      if (bar) bar.classList.add('tts-open');
      if (containerToPlay) {
        indexContainer(containerToPlay);
      }
      updateStatusUI();
      updatePlayPauseButton();
    }

    function closePlayerBar() {
      stopPlayback();
      state.isOpen = false;
      var bar = doc ? doc.getElementById('tts-bar') : null;
      if (bar) bar.classList.remove('tts-open');
      if (state.activeContainer) {
        clearExistingMarkup(state.activeContainer);
        state.activeContainer = null;
      }
      state.sentences = [];
      state.currentIndex = -1;
    }

    function updatePlayPauseButton() {
      if (!doc) return;
      var playBtn = doc.getElementById('tts-play-btn');
      if (!playBtn) return;

      if (state.isPlaying) {
        playBtn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';
        playBtn.setAttribute('title', 'Pausar');
        playBtn.setAttribute('aria-label', 'Pausar');
      } else {
        playBtn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
        playBtn.setAttribute('title', 'Reproducir');
        playBtn.setAttribute('aria-label', 'Reproducir');
      }
    }

    function updateStatusUI() {
      if (!doc) return;
      var statusText = doc.getElementById('tts-status-text');
      if (!statusText) return;

      var total = state.sentences.length;
      var current = state.currentIndex >= 0 ? state.currentIndex + 1 : 0;

      if (current > 0) {
        statusText.textContent = 'Frase ' + current + ' de ' + total;
      } else {
        statusText.textContent = total + ' frases listas · Toca una frase para escuchar';
      }
    }

    // Delegación global de click para reproducir instantáneamente la frase tocada
    function setupClickDelegation() {
      if (!doc || doc.__ttsClickDelegationSetup) return;
      doc.__ttsClickDelegationSetup = true;

      doc.addEventListener('click', function (e) {
        if (!state.isOpen) return;

        // Ignorar clics dentro de los propios controles de TTS o botones de acción nativos
        if (e.target.closest && (
          e.target.closest('#tts-bar') ||
          e.target.closest('#tts-fab') ||
          e.target.closest('button:not(.tts-sentence)') ||
          e.target.closest('input') ||
          e.target.closest('textarea')
        )) {
          return;
        }

        var sentenceEl = e.target.closest && e.target.closest('.tts-sentence, .mermaid-card, .mermaid, [class*="mermaid"]');
        if (!sentenceEl) return;

        var rawId = sentenceEl.getAttribute('data-tts-id');
        if (!rawId) return;

        var id = parseInt(rawId, 10);
        var sentenceItemIndex = state.sentences.findIndex(function (s) { return s.id === id; });

        if (sentenceItemIndex !== -1) {
          if (sentenceEl.tagName === 'A' || e.target.tagName === 'A') {
            e.preventDefault();
          }
          e.stopPropagation();
          playFromIndex(sentenceItemIndex);
        }
      }, true);
    }

    // Inyectar el markup HTML del reproductor flotante (#tts-bar) y botón flotante (#tts-fab)
    function injectUI(mountTarget) {
      if (!doc) return;

      var container = mountTarget || doc.body;

      if (!doc.getElementById('tts-fab')) {
        var fab = doc.createElement('button');
        fab.id = 'tts-fab';
        fab.setAttribute('title', 'Lectura de voz interactiva');
        fab.setAttribute('aria-label', 'Lectura de voz interactiva');
        fab.innerHTML =
          '<svg viewBox="0 0 24 24">' +
            '<path d="M12 3v9.28a4 4 0 1 0 2 3.47V6h4V3h-6zM3 9v6h4l5 5V4L7 9H3z"/>' +
          '</svg>';
        fab.addEventListener('click', function (ev) {
          ev.stopPropagation();
          if (!state.isOpen) {
            var target = options.getActiveContainer ? options.getActiveContainer() : null;
            openPlayerBar(target);
            if (!state.isPlaying) {
              if (state.sentences.length > 0) playFromIndex(0);
              else resumePlayback();
            }
          } else {
            togglePlayPause();
          }
        });
        if (container) container.appendChild(fab);
      }

      if (doc.getElementById('tts-bar')) return;

      var bar = doc.createElement('div');
      bar.id = 'tts-bar';
      bar.innerHTML =
        '<div class="tts-controls-row">' +
          '<div class="tts-btn-group">' +
            '<button class="tts-btn" id="tts-prev-btn" title="Frase anterior" aria-label="Frase anterior">' +
              '<svg viewBox="0 0 24 24"><path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/></svg>' +
            '</button>' +
            '<button class="tts-btn tts-btn-play" id="tts-play-btn" title="Reproducir" aria-label="Reproducir">' +
              '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>' +
            '</button>' +
            '<button class="tts-btn" id="tts-next-btn" title="Siguiente frase" aria-label="Siguiente frase">' +
              '<svg viewBox="0 0 24 24"><path d="m6 18 8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>' +
            '</button>' +
            '<button class="tts-btn" id="tts-stop-btn" title="Detener" aria-label="Detener">' +
              '<svg viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg>' +
            '</button>' +
          '</div>' +
          '<button class="tts-speed-badge" id="tts-speed-btn" title="Cambiar velocidad">1.0x</button>' +
          '<button class="tts-btn" id="tts-close-btn" title="Cerrar barra" aria-label="Cerrar barra">' +
            '<svg viewBox="0 0 24 24"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>' +
          '</button>' +
        '</div>' +
        '<div class="tts-status-row">' +
          '<span id="tts-status-text">Cargando lectura...</span>' +
          '<span class="tts-hint">💡 Toca cualquier texto para saltar</span>' +
        '</div>';

      var container = mountTarget || doc.body;
      if (container) container.appendChild(bar);

      var playBtn = doc.getElementById('tts-play-btn');
      var stopBtn = doc.getElementById('tts-stop-btn');
      var prevBtn = doc.getElementById('tts-prev-btn');
      var nextBtn = doc.getElementById('tts-next-btn');
      var speedBtn = doc.getElementById('tts-speed-btn');
      var closeBtn = doc.getElementById('tts-close-btn');

      if (playBtn) playBtn.addEventListener('click', togglePlayPause);
      if (stopBtn) stopBtn.addEventListener('click', stopPlayback);
      if (prevBtn) prevBtn.addEventListener('click', prevSentence);
      if (nextBtn) nextBtn.addEventListener('click', nextSentence);
      if (speedBtn) speedBtn.addEventListener('click', cycleSpeed);
      if (closeBtn) closeBtn.addEventListener('click', closePlayerBar);

      setupClickDelegation();
    }

    return {
      supported: supported,
      state: state,
      indexContainer: indexContainer,
      clearExistingMarkup: clearExistingMarkup,
      playFromIndex: playFromIndex,
      pausePlayback: pausePlayback,
      resumePlayback: resumePlayback,
      stopPlayback: stopPlayback,
      togglePlayPause: togglePlayPause,
      nextSentence: nextSentence,
      prevSentence: prevSentence,
      cycleSpeed: cycleSpeed,
      openPlayerBar: openPlayerBar,
      closePlayerBar: closePlayerBar,
      injectUI: injectUI,
      splitIntoSentences: splitIntoSentences,
      cleanTextForSpeech: cleanTextForSpeech
    };
  }

  var api = {
    createTtsEngine: createTtsEngine
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    global.TtsEngine = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
