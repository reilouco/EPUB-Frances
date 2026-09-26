const state = {
  book: null,
  chapterIndex: 0,
  chapters: [],
  selectedWords: [],
  selectedContext: "",
  translation: null,
  marks: { difficulty: {}, saved: new Set(), notes: {} },
  trCache: new Map(),
  trSeq: 0,
  trTimer: null,
  saveTimer: null,
  noteTimer: null,
};

const $ = (s) => document.querySelector(s);
const LEVEL_LABEL = { facil: "Fácil", medio: "Médio", dificil: "Difícil" };

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = "Ocorreu um erro.";
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response;
}

function formData(data) {
  const form = new FormData();
  Object.entries(data).forEach(([k, v]) => form.append(k, v));
  return form;
}

function showView(id) {
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  $(`#${id}`).classList.add("active");
}

function escapeHtml(value) {
  const el = document.createElement("div");
  el.textContent = value || "";
  return el.innerHTML;
}

/* ---------- Normalização (igual ao backend) ---------- */
const normWord = (w) =>
  w.toLowerCase().replace(/’/g, "'").replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
const phraseKey = (t) => t.split(/\s+/).map(normWord).filter(Boolean).join(" ");

/* ---------- TTS ---------- */
const player = new Audio();
function speak(text, slow = false) {
  if (!text) return;
  player.pause();
  player.src = `./api/tts?text=${encodeURIComponent(text)}&slow=${slow ? 1 : 0}`;
  player.play().catch(() => {});
}
player.onerror = () => alert("Não foi possível gerar o áudio (edge-tts e Kokoro falharam).");

/* ---------- Biblioteca ---------- */
function hue(text) {
  let h = 0;
  for (const c of text) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}

async function loadBooks() {
  const list = $("#book-list");
  list.innerHTML = "<p>Carregando biblioteca...</p>";
  const books = await api("./api/books");

  if (!books.length) {
    list.innerHTML = `<div class="empty-card"><h2>Sua biblioteca está vazia</h2>
      <p>Adicione um arquivo EPUB em francês para começar.</p></div>`;
    return;
  }

  list.innerHTML = books.map((b) => {
    const p = b.progress_percent;
    const h = hue(b.title);
    const cover = b.cover_url
      ? `<img src="${b.cover_url}" alt="" loading="lazy" />`
      : `<div class="cover-placeholder" style="--h:${h}">
           <span>${escapeHtml(b.title)}</span>
           <small>${escapeHtml(b.author || "")}</small>
         </div>`;
    return `
      <article class="book-card">
        <div class="cover open-book" data-id="${b.id}">
          ${cover}
          <div class="ring" style="--p:${p}"><span>${Math.round(p)}%</span></div>
          <div class="cover-progress"><div style="width:${p}%"></div></div>
        </div>
        <div class="book-meta">
          <h2 title="${escapeHtml(b.title)}">${escapeHtml(b.title)}</h2>
          <p>${escapeHtml(b.author || "Autor desconhecido")}</p>
        </div>
        <div class="book-actions">
          <button class="open-book primary" data-id="${b.id}">${p > 0 ? "Continuar" : "Começar"}</button>
          <label class="tool" title="Alterar capa (JPG)">🖼
            <input type="file" class="cover-input" data-id="${b.id}" accept="image/jpeg,.jpg,.jpeg,image/png" />
          </label>
          <button class="tool delete-book" data-id="${b.id}" title="Excluir">🗑</button>
        </div>
      </article>`;
  }).join("");

  list.querySelectorAll(".open-book").forEach((el) =>
    el.addEventListener("click", () => openBook(el.dataset.id)));

  list.querySelectorAll(".cover-input").forEach((input) =>
    input.addEventListener("change", async () => {
      const file = input.files[0];
      if (!file) return;
      try {
        await api(`./api/books/${input.dataset.id}/cover`, {
          method: "POST", body: formData({ file }),
        });
        loadBooks();
      } catch (e) { alert(e.message); }
    }));

  list.querySelectorAll(".delete-book").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!confirm("Excluir este livro? Os cartões salvos continuarão disponíveis.")) return;
      await api(`./api/books/${btn.dataset.id}`, { method: "DELETE" });
      loadBooks();
    }));
}

/* ---------- Leitor ---------- */
async function openBook(bookId) {
  const details = await api(`./api/books/${bookId}`);
  state.book = details.book;
  state.chapters = details.chapters;
  state.chapterIndex = details.progress.chapter_index || 0;

  $("#book-title").textContent = details.book.title;
  $("#book-subtitle").textContent = details.book.author || "";
  $("#chapter-select").innerHTML = details.chapters.map((c) =>
    `<option value="${c.chapter_index}">${c.chapter_index + 1}. ${escapeHtml(c.title)}</option>`
  ).join("");

  showView("reader-view");
  await loadMarks();
  await loadChapter(state.chapterIndex, details.progress.scroll_percent || 0);
}

async function loadMarks() {
  const m = await api("./api/marks");
  state.marks = { difficulty: m.difficulty, saved: new Set(m.saved), notes: m.notes || {} };
}

function decorateWords() {
  const content = $("#reader-content");
  const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (walker.currentNode.nodeValue.trim()) nodes.push(walker.currentNode);

  nodes.forEach((node) => {
    const frag = document.createDocumentFragment();
    node.nodeValue.split(/(\s+)/).forEach((part) => {
      if (!part.trim()) { frag.appendChild(document.createTextNode(part)); return; }
      const span = document.createElement("span");
      span.className = "word";
      span.textContent = part;
      span.dataset.norm = normWord(part);
      frag.appendChild(span);
    });
    node.parentNode.replaceChild(frag, node);
  });

  applyMarks();
}

/* Pinta todas as ocorrências das expressões marcadas (maior correspondência primeiro) */
function applyMarks() {
  const spans = [...document.querySelectorAll("#reader-content .word")];
  spans.forEach((s) => {
    s.classList.remove("diff-facil", "diff-medio", "diff-dificil", "saved", "saved-end");
    s._match = null;
  });

  const keys = new Set([...Object.keys(state.marks.difficulty), ...state.marks.saved]);
  if (!keys.size) return;

  const byFirst = new Map();
  keys.forEach((k) => {
    const t = k.split(" ");
    if (!byFirst.has(t[0])) byFirst.set(t[0], []);
    byFirst.get(t[0]).push(t);
  });
  byFirst.forEach((l) => l.sort((a, b) => b.length - a.length));

  const seq = spans.filter((s) => s.dataset.norm);
  for (let i = 0; i < seq.length; i++) {
    const cands = byFirst.get(seq[i].dataset.norm);
    if (!cands) continue;
    for (const t of cands) {
      if (i + t.length > seq.length) continue;
      let ok = true;
      for (let j = 1; j < t.length; j++) {
        if (seq[i + j].dataset.norm !== t[j]) { ok = false; break; }
      }
      if (!ok) continue;

      const key = t.join(" ");
      const group = seq.slice(i, i + t.length);
      const level = state.marks.difficulty[key];
      const saved = state.marks.saved.has(key);
      group.forEach((s) => {
        if (level) s.classList.add(`diff-${level}`);
        if (saved) s.classList.add("saved");
        if (!s._match) s._match = group;
      });
      if (saved) group[group.length - 1].classList.add("saved-end");
      break;
    }
  }
}

function blockWords(el) {
  const block = el.closest("p, li, blockquote, h1, h2, h3, h4, h5, h6, div") || el.parentElement;
  const words = [...block.querySelectorAll(".word")];
  return words.length ? words : [el];
}

function selectWord(el) {
  const words = blockWords(el);
  const index = words.indexOf(el);

  state.selectedWords = el._match
    ? [...el._match]
    : words.slice(index, Math.min(index + 3, words.length));
  state.selectedContext = words.map((w) => w.textContent).join(" ");

  $("#translation-panel").classList.remove("hidden");
  $("#overlay").classList.remove("hidden");
  updateSelectionUI();
}

const selectedText = () => state.selectedWords.map((w) => w.textContent).join(" ");
const selectedKey = () => phraseKey(selectedText());

function updateSelectionUI() {
  document.querySelectorAll(".word.selected").forEach((w) => w.classList.remove("selected"));
  state.selectedWords.forEach((w) => w.classList.add("selected"));
  $("#selected-expression").textContent = selectedText();
  $("#note-input").value = state.marks.notes[selectedKey()] || "";
  closeEdit();
  updatePanelState();
  scheduleTranslate();
}

function updatePanelState() {
  const key = selectedKey();
  const level = state.marks.difficulty[key];
  document.querySelectorAll(".diff-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.level === level));

  const btn = $("#save-phrase-button");
  if (state.marks.saved.has(key)) {
    btn.textContent = "✓ Cartão salvo · remover";
    btn.classList.add("is-saved");
    btn.disabled = false;
  } else {
    btn.textContent = "★ Salvar cartão";
    btn.classList.remove("is-saved");
    btn.disabled = !state.translation;
  }
}

function expandSelection() {
  const last = state.selectedWords.at(-1);
  if (!last) return;
  const words = blockWords(last);
  const i = words.indexOf(last);
  if (i < words.length - 1 && state.selectedWords.length < 12) {
    state.selectedWords.push(words[i + 1]);
    updateSelectionUI();
  }
}

function shrinkSelection() {
  if (state.selectedWords.length <= 1) return;
  state.selectedWords.pop();
  updateSelectionUI();
}

/* ---------- Tradução automática ---------- */
function scheduleTranslate() {
  clearTimeout(state.trTimer);
  const phrase = selectedText();
  if (!phrase) return;

  const withCtx = $("#context-toggle").checked;
  const cacheKey = `${withCtx ? 1 : 0}|${phrase}`;
  const seq = ++state.trSeq;
  state.translation = null;

  if (state.trCache.has(cacheKey)) {
    renderTranslation(state.trCache.get(cacheKey));
    return;
  }

  $("#translation-result").innerHTML = `<div class="translation loading">Traduzindo…</div>`;
  updatePanelState();

  state.trTimer = setTimeout(async () => {
    try {
      const result = await api("./api/translate", {
        method: "POST",
        body: formData({
          phrase_fr: phrase,
          context_fr: state.selectedContext,
          with_context: withCtx,
        }),
      });
      state.trCache.set(cacheKey, result);
      if (seq === state.trSeq) renderTranslation(result);
    } catch (e) {
      if (seq === state.trSeq) {
        $("#translation-result").innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
      }
    }
  }, 250);
}

function renderTranslation(t) {
  state.translation = t;
  let html = `<div class="translation ${t.is_custom ? "custom" : ""}">
      <span>${escapeHtml(t.translation_pt)}</span>
      <button class="tr-edit" title="Corrigir tradução">✏️</button>
    </div>`;
  if (t.is_custom) {
    html += `<div class="custom-row"><small>✎ Tradução corrigida</small>
      <button class="tr-restore">↺ Restaurar automática</button></div>`;
  }
  if (t.context_pt) {
    html += `<details><summary>Ver contexto traduzido</summary>
      <p><strong>FR:</strong> ${escapeHtml(t.context_fr)}</p>
      <p><strong>PT:</strong> ${escapeHtml(t.context_pt)}</p></details>`;
  }
  $("#translation-result").innerHTML = html;
  $("#translation-result .tr-edit").onclick = openEdit;
  const r = $("#translation-result .tr-restore");
  if (r) r.onclick = restoreTranslation;
  updatePanelState();
}

/* ---------- Correção de tradução ---------- */
function openEdit() {
  if (!state.translation) return;
  $("#translation-input").value = state.translation.translation_pt;
  $("#translation-edit").classList.remove("hidden");
  $("#translation-input").focus();
}

const closeEdit = () => $("#translation-edit").classList.add("hidden");

async function saveCorrection() {
  const text = $("#translation-input").value.trim();
  if (!text || !state.translation) return;
  try {
    await api("./api/translations", {
      method: "POST",
      body: formData({ phrase_fr: state.translation.phrase_fr, translation_pt: text }),
    });
    state.trCache.clear();
    closeEdit();
    scheduleTranslate();
  } catch (e) { alert(e.message); }
}

async function restoreTranslation() {
  if (!state.translation) return;
  if (!confirm("Voltar à tradução automática? Cartões salvos também voltarão.")) return;
  try {
    await api("./api/translations/restore", {
      method: "POST",
      body: formData({ phrase_fr: state.translation.phrase_fr }),
    });
    state.trCache.clear();
    scheduleTranslate();
  } catch (e) { alert(e.message); }
}

/* ---------- Notas ---------- */
function onNoteInput() {
  const key = selectedKey();
  const notes = $("#note-input").value;
  if (notes.trim()) state.marks.notes[key] = notes;
  else delete state.marks.notes[key];
  if (!state.marks.saved.has(key)) return; // será enviada junto com o cartão
  clearTimeout(state.noteTimer);
  const phrase = selectedText();
  state.noteTimer = setTimeout(() =>
    api("./api/phrases/note", { method: "POST", body: formData({ phrase_fr: phrase, notes }) })
      .catch((e) => console.error(e)), 600);
}

/* ---------- Dificuldade e cartões ---------- */
async function setDifficulty(level) {
  const phrase = selectedText();
  const key = selectedKey();
  if (!key) return;

  const next = state.marks.difficulty[key] === level ? "" : level;
  try {
    await api("./api/marks", { method: "POST", body: formData({ phrase_fr: phrase, difficulty: next }) });
    if (next) state.marks.difficulty[key] = next;
    else delete state.marks.difficulty[key];
    applyMarks();
    updatePanelState();
  } catch (e) { alert(e.message); }
}

async function toggleSaved() {
  const key = selectedKey();
  if (!key || !state.book) return;

  try {
    if (state.marks.saved.has(key)) {
      await api("./api/phrases/unsave", { method: "POST", body: formData({ phrase_fr: selectedText() }) });
      state.marks.saved.delete(key);
      delete state.marks.notes[key];
      $("#note-input").value = "";
    } else {
      if (!state.translation) return;
      await api("./api/phrases", {
        method: "POST",
        body: formData({
          book_id: state.book.id,
          chapter_index: state.chapterIndex,
          phrase_fr: state.translation.phrase_fr,
          translation_pt: state.translation.translation_pt,
          context_fr: state.translation.context_fr || "",
          context_pt: state.translation.context_pt || "",
          notes: $("#note-input").value,
        }),
      });
      state.marks.saved.add(key);
    }
    applyMarks();
    updatePanelState();
  } catch (e) { alert(e.message); }
}

function closePanel() {
  $("#translation-panel").classList.add("hidden");
  $("#overlay").classList.add("hidden");
  document.querySelectorAll(".word.selected").forEach((w) => w.classList.remove("selected"));
  clearTimeout(state.trTimer);
  closeEdit();
  state.trSeq++;
  state.selectedWords = [];
  state.translation = null;
}

/* ---------- Capítulos e progresso ---------- */
function updateProgressUI(scrollPercent) {
  const pct = ((state.chapterIndex + scrollPercent / 100) / state.chapters.length) * 100;
  $("#progress-fill").style.width = `${pct}%`;
  $("#progress-label").textContent = `${Math.round(pct)}%`;
}

function currentScrollPercent() {
  const max = document.documentElement.scrollHeight - window.innerHeight;
  return max > 0 ? Math.min(100, (window.scrollY / max) * 100) : 0;
}

async function loadChapter(index, scrollPercent = 0) {
  if (!state.book) return;
  state.chapterIndex = Number(index);

  const chapter = await api(`./api/books/${state.book.id}/chapters/${state.chapterIndex}`);
  $("#chapter-select").value = state.chapterIndex;
  $("#reader-content").innerHTML = chapter.content_html;
  decorateWords();
  updateProgressUI(scrollPercent);

  requestAnimationFrame(() => setTimeout(() => {
    const max = document.documentElement.scrollHeight - window.innerHeight;
    window.scrollTo({ top: max > 0 ? (max * scrollPercent) / 100 : 0, behavior: "instant" });
    if (scrollPercent === 0) saveProgressNow();
  }, 30));
}

function progressPayload() {
  return formData({
    book_id: state.book.id,
    chapter_index: state.chapterIndex,
    scroll_percent: currentScrollPercent(),
  });
}

async function saveProgressNow() {
  if (!state.book) return;
  clearTimeout(state.saveTimer);
  updateProgressUI(currentScrollPercent());
  try {
    await api("./api/progress", { method: "POST", body: progressPayload() });
  } catch (e) { console.error("Falha ao salvar progresso:", e); }
}

function saveProgressSoon() {
  if (!state.book || !$("#reader-view").classList.contains("active")) return;
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveProgressNow, 800);
}

function flushProgress() {
  if (state.book && $("#reader-view").classList.contains("active")) {
    navigator.sendBeacon("./api/progress", progressPayload());
  }
}

/* ---------- Lista de cartões ---------- */
async function showPhrases() {
  const phrases = await api("./api/phrases");
  const list = $("#phrase-list");

  if (!phrases.length) {
    list.innerHTML = `<div class="phrase-card"><h2>Nenhuma expressão salva ainda</h2>
      <p>Durante a leitura, clique em uma palavra e salve o cartão.</p></div>`;
  } else {
    list.innerHTML = phrases.map((p) => `
      <article class="phrase-card ${p.difficulty ? `lvl-${p.difficulty}` : ""}">
        <div class="phrase-top">
          <div class="french">${escapeHtml(p.phrase_fr)}
            <button class="speak" data-text="${escapeHtml(p.phrase_fr)}" title="Ouvir">🔊</button>
          </div>
          ${p.difficulty ? `<span class="badge">${LEVEL_LABEL[p.difficulty]}</span>` : ""}
        </div>
        <div class="portuguese">${escapeHtml(p.translation_pt)}</div>
        ${p.context_fr ? `<p><small>Contexto: ${escapeHtml(p.context_fr)}</small></p>` : ""}
        ${p.book_title ? `<p><small>📖 ${escapeHtml(p.book_title)}</small></p>` : ""}
        <textarea class="note-input card-note" data-fr="${escapeHtml(p.phrase_fr)}" rows="2"
          placeholder="📝 Adicionar nota…">${escapeHtml(p.notes || "")}</textarea>
        <button class="delete-phrase" data-id="${p.id}">Excluir cartão</button>
      </article>`).join("");

    list.querySelectorAll(".speak").forEach((b) =>
      b.addEventListener("click", () => speak(b.dataset.text)));

    list.querySelectorAll(".card-note").forEach((ta) => {
      let t;
      ta.addEventListener("input", () => {
        clearTimeout(t);
        t = setTimeout(async () => {
          try {
            await api("./api/phrases/note", {
              method: "POST",
              body: formData({ phrase_fr: ta.dataset.fr, notes: ta.value }),
            });
            await loadMarks();
          } catch (e) { console.error(e); }
        }, 600);
      });
    });

    list.querySelectorAll(".delete-phrase").forEach((btn) =>
      btn.addEventListener("click", async () => {
        await api(`./api/phrases/${btn.dataset.id}`, { method: "DELETE" });
        await loadMarks();
        applyMarks();
        showPhrases();
      }));
  }
  showView("phrases-view");
}

/* ---------- Eventos ---------- */
$("#reader-content").addEventListener("click", (e) => {
  const word = e.target.closest(".word");
  if (word) selectWord(word);
});

$("#epub-upload").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  $("#upload-status").textContent = "Importando e preparando o livro...";
  try {
    const r = await api("./api/books/upload", { method: "POST", body: formData({ file }) });
    $("#upload-status").textContent = `"${r.title}" foi adicionado à biblioteca.`;
    event.target.value = "";
    await loadBooks();
  } catch (e) {
    $("#upload-status").textContent = `Erro: ${e.message}`;
  }
});

$("#btn-home").addEventListener("click", async () => {
  closePanel();
  await saveProgressNow();
  $("#book-title").textContent = "Leitor Francês";
  $("#book-subtitle").textContent = "Biblioteca";
  showView("library-view");
  loadBooks();
});

$("#btn-phrases").addEventListener("click", showPhrases);
$("#btn-close-phrases").addEventListener("click", () =>
  showView(state.book ? "reader-view" : "library-view"));

$("#chapter-select").addEventListener("change", (e) => loadChapter(e.target.value, 0));
$("#prev-chapter").addEventListener("click", () => {
  if (state.chapterIndex > 0) loadChapter(state.chapterIndex - 1, 0);
});
$("#next-chapter").addEventListener("click", () => {
  if (state.chapterIndex < state.chapters.length - 1) loadChapter(state.chapterIndex + 1, 0);
});

$("#add-word").addEventListener("click", expandSelection);
$("#remove-word").addEventListener("click", shrinkSelection);
$("#context-toggle").addEventListener("change", () => {
  if (state.selectedWords.length) scheduleTranslate();
});
document.querySelectorAll(".diff-btn").forEach((b) =>
  b.addEventListener("click", () => setDifficulty(b.dataset.level)));
$("#save-phrase-button").addEventListener("click", toggleSaved);
$("#close-panel").addEventListener("click", closePanel);
$("#overlay").addEventListener("click", closePanel);

// TTS, correção e notas
$("#tts-play").addEventListener("click", () => speak(selectedText()));
$("#tts-slow").addEventListener("click", () => speak(selectedText(), true));
$("#tr-save").addEventListener("click", saveCorrection);
$("#tr-cancel").addEventListener("click", closeEdit);
$("#translation-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); saveCorrection(); }
});
$("#note-input").addEventListener("input", onNoteInput);

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#translation-edit").classList.contains("hidden")) closeEdit();
  else closePanel();
});

window.addEventListener("scroll", saveProgressSoon, { passive: true });
window.addEventListener("pagehide", flushProgress);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flushProgress();
});

async function start() {
  const me = await api("./api/me");
  $("#context-toggle").checked = me.show_context_default;
  $("#greeting").textContent = `Bonjour, ${me.user.name}! Continue de onde parou.`;
  await loadMarks();
  await loadBooks();
}

start().catch((e) => {
  $("#book-list").innerHTML = `<div class="empty-card"><h2>Erro ao iniciar</h2>
    <p>${escapeHtml(e.message)}</p></div>`;
});
