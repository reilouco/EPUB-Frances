const state = {
    book: null,
    chapterIndex: 0,
    chapters: [],
    selectedWords: [],
    selectedContext: "",
    translation: null,
    knownPhrases: [],
    saveTimer: null,
  };
  
  const $ = (selector) => document.querySelector(selector);
  
  async function api(url, options = {}) {
    const response = await fetch(url, options);
  
    if (!response.ok) {
      let message = "Ocorreu um erro.";
  
      try {
        const body = await response.json();
        message = body.detail || message;
      } catch (_) {}
  
      throw new Error(message);
    }
  
    const type = response.headers.get("content-type") || "";
  
    if (type.includes("application/json")) {
      return response.json();
    }
  
    return response;
  }
  
  function formData(data) {
    const form = new FormData();
  
    Object.entries(data).forEach(([key, value]) => {
      form.append(key, value);
    });
  
    return form;
  }
  
  function showView(id) {
    document.querySelectorAll(".view").forEach((view) => {
      view.classList.remove("active");
    });
  
    $(`#${id}`).classList.add("active");
  }
  
  function escapeHtml(value) {
    const element = document.createElement("div");
    element.textContent = value || "";
    return element.innerHTML;
  }
  
  async function loadBooks() {
    const list = $("#book-list");
    list.innerHTML = "<p>Carregando biblioteca...</p>";
  
    const books = await api("./api/books");
  
    if (!books.length) {
      list.innerHTML = `
        <div class="book-card">
          <h2>Sua biblioteca está vazia</h2>
          <p>Adicione um arquivo EPUB em francês para começar.</p>
        </div>
      `;
      return;
    }
  
    list.innerHTML = books.map((book) => `
      <article class="book-card">
        <h2>${escapeHtml(book.title)}</h2>
        <p>${escapeHtml(book.author || "Autor desconhecido")}</p>
  
        <div class="book-progress">
          <div class="progress-bar">
            <div style="width: ${book.progress_percent}%"></div>
          </div>
          <p>${book.progress_percent}% lido</p>
        </div>
  
        <div class="book-card-footer">
          <button class="open-book" data-id="${book.id}">Continuar lendo</button>
          <button class="delete-book" data-id="${book.id}">Excluir</button>
        </div>
      </article>
    `).join("");
  
    document.querySelectorAll(".open-book").forEach((button) => {
      button.addEventListener("click", () => openBook(button.dataset.id));
    });
  
    document.querySelectorAll(".delete-book").forEach((button) => {
      button.addEventListener("click", async () => {
        const confirmation = confirm(
          "Excluir este livro? O progresso e os cartões associados continuarão salvos."
        );
  
        if (!confirmation) return;
  
        await api(`./api/books/${button.dataset.id}`, {
          method: "DELETE",
        });
  
        loadBooks();
      });
    });
  }
  
  async function openBook(bookId) {
    const details = await api(`./api/books/${bookId}`);
  
    state.book = details.book;
    state.chapters = details.chapters;
    state.chapterIndex = details.progress.chapter_index || 0;
  
    $("#book-title").textContent = details.book.title;
    $("#book-subtitle").textContent = details.book.author || "";
    $("#chapter-select").innerHTML = details.chapters.map((chapter) => `
      <option value="${chapter.chapter_index}">
        ${chapter.chapter_index + 1}. ${escapeHtml(chapter.title)}
      </option>
    `).join("");
  
    $("#chapter-select").value = state.chapterIndex;
  
    await loadKnownPhrases();
    await loadChapter(state.chapterIndex, details.progress.scroll_percent || 0);
  
    showView("reader-view");
  }
  
  async function loadKnownPhrases() {
    if (!state.book) return;
  
    state.knownPhrases = await api(
      `./api/phrases?book_id=${state.book.id}`
    );
  }
  
  function normalize(text) {
    return text
      .toLowerCase()
      .replace(/[.,;:!?“”"'()[\]{}]/g, "")
      .trim();
  }
  
  function decorateWords() {
    const content = $("#reader-content");
    const walker = document.createTreeWalker(
      content,
      NodeFilter.SHOW_TEXT
    );
  
    const nodes = [];
  
    while (walker.nextNode()) {
      const node = walker.currentNode;
  
      if (node.nodeValue.trim()) {
        nodes.push(node);
      }
    }
  
    nodes.forEach((node) => {
      const fragment = document.createDocumentFragment();
      const parts = node.nodeValue.split(/(\s+)/);
  
      parts.forEach((part) => {
        if (!part.trim()) {
          fragment.appendChild(document.createTextNode(part));
          return;
        }
  
        const span = document.createElement("span");
        span.className = "word";
        span.textContent = part;
        span.dataset.word = part;
        fragment.appendChild(span);
      });
  
      node.parentNode.replaceChild(fragment, node);
    });
  
    markKnownWords();
  }
  
  function markKnownWords() {
    const knownWords = new Set();
  
    state.knownPhrases.forEach((phrase) => {
      phrase.phrase_fr.split(/\s+/).forEach((word) => {
        knownWords.add(normalize(word));
      });
    });
  
    document.querySelectorAll(".word").forEach((word) => {
      if (knownWords.has(normalize(word.textContent))) {
        word.classList.add("seen");
      }
  
      word.addEventListener("click", () => selectWord(word));
    });
  }
  
  function sentenceWordsFrom(element) {
    const paragraph = element.closest("p, li, blockquote, div") || element.parentElement;
    const words = [...paragraph.querySelectorAll(".word")];
  
    return words.length ? words : [element];
  }
  
  function selectWord(element) {
    const words = sentenceWordsFrom(element);
    const index = words.indexOf(element);
  
    // Sugestão inicial: palavra clicada + até duas palavras posteriores.
    state.selectedWords = words.slice(index, Math.min(index + 3, words.length));
    state.selectedContext = words.map((word) => word.textContent).join(" ");
  
    state.translation = null;
  
    updateSelectionUI();
    $("#translation-panel").classList.remove("hidden");
    $("#overlay").classList.remove("hidden");
  }
  
  function updateSelectionUI() {
    document.querySelectorAll(".word.selected").forEach((word) => {
      word.classList.remove("selected");
    });
  
    state.selectedWords.forEach((word) => word.classList.add("selected"));
  
    $("#selected-expression").textContent = selectedText();
    $("#translation-result").innerHTML = "";
    $("#save-phrase-button").disabled = true;
  }
  
  function selectedText() {
    return state.selectedWords.map((word) => word.textContent).join(" ");
  }
  
  function expandSelection() {
    if (!state.selectedWords.length) return;
  
    const last = state.selectedWords[state.selectedWords.length - 1];
    const words = sentenceWordsFrom(last);
    const index = words.indexOf(last);
  
    if (index < words.length - 1 && state.selectedWords.length < 12) {
      state.selectedWords.push(words[index + 1]);
      updateSelectionUI();
    }
  }
  
  function shrinkSelection() {
    if (state.selectedWords.length <= 1) return;
  
    state.selectedWords.pop();
    updateSelectionUI();
  }
  
  async function loadChapter(index, scrollPercent = 0) {
    if (!state.book) return;
  
    state.chapterIndex = Number(index);
  
    const chapter = await api(
      `./api/books/${state.book.id}/chapters/${state.chapterIndex}`
    );
  
    $("#chapter-select").value = state.chapterIndex;
    $("#reader-content").innerHTML = chapter.content_html;
  
    decorateWords();
  
    const total = state.chapters.length;
    const percent = (
      (state.chapterIndex + scrollPercent / 100) / total
    ) * 100;
  
    $("#progress-fill").style.width = `${percent}%`;
    $("#progress-label").textContent = `${Math.round(percent)}%`;
  
    requestAnimationFrame(() => {
      const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
  
      if (maxScroll > 0 && scrollPercent > 0) {
        window.scrollTo({
          top: (maxScroll * scrollPercent) / 100,
          behavior: "instant",
        });
      } else {
        window.scrollTo({ top: 0, behavior: "instant" });
      }
    });
  }
  
  function saveProgressSoon() {
    if (!state.book) return;
  
    clearTimeout(state.saveTimer);
  
    state.saveTimer = setTimeout(async () => {
      const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
      const scrollPercent = maxScroll > 0
        ? Math.min(100, (window.scrollY / maxScroll) * 100)
        : 0;
  
      const total = state.chapters.length;
      const totalPercent = (
        (state.chapterIndex + scrollPercent / 100) / total
      ) * 100;
  
      $("#progress-fill").style.width = `${totalPercent}%`;
      $("#progress-label").textContent = `${Math.round(totalPercent)}%`;
  
      try {
        await api("./api/progress", {
          method: "POST",
          body: formData({
            book_id: state.book.id,
            chapter_index: state.chapterIndex,
            scroll_percent: scrollPercent,
          }),
        });
      } catch (error) {
        console.error("Não foi possível salvar o progresso:", error);
      }
    }, 800);
  }
  
  async function translateSelection() {
    const phrase = selectedText();
  
    if (!phrase) return;
  
    $("#translate-button").textContent = "Traduzindo...";
    $("#translate-button").disabled = true;
  
    try {
      state.translation = await api("./api/translate", {
        method: "POST",
        body: formData({
          phrase_fr: phrase,
          context_fr: state.selectedContext,
          with_context: $("#context-toggle").checked,
        }),
      });
  
      let html = `
        <div class="translation">
          ${escapeHtml(state.translation.translation_pt)}
        </div>
      `;
  
      if (state.translation.context_pt) {
        html += `
          <details>
            <summary>Ver contexto traduzido</summary>
            <p><strong>FR:</strong> ${escapeHtml(state.translation.context_fr)}</p>
            <p><strong>PT:</strong> ${escapeHtml(state.translation.context_pt)}</p>
          </details>
        `;
      }
  
      $("#translation-result").innerHTML = html;
      $("#save-phrase-button").disabled = false;
    } catch (error) {
      $("#translation-result").innerHTML = `
        <p class="error">${escapeHtml(error.message)}</p>
      `;
    } finally {
      $("#translate-button").textContent = "Traduzir localmente";
      $("#translate-button").disabled = false;
    }
  }
  
  async function savePhrase() {
    if (!state.translation || !state.book) return;
  
    await api("./api/phrases", {
      method: "POST",
      body: formData({
        book_id: state.book.id,
        chapter_index: state.chapterIndex,
        phrase_fr: state.translation.phrase_fr,
        translation_pt: state.translation.translation_pt,
        context_fr: state.translation.context_fr || "",
        context_pt: state.translation.context_pt || "",
      }),
    });
  
    await loadKnownPhrases();
    markKnownWords();
  
    $("#save-phrase-button").textContent = "✓ Cartão salvo";
    $("#save-phrase-button").disabled = true;
  
    setTimeout(() => {
      $("#save-phrase-button").textContent = "★ Salvar cartão";
    }, 1800);
  }
  
  function closePanel() {
    $("#translation-panel").classList.add("hidden");
    $("#overlay").classList.add("hidden");
  
    document.querySelectorAll(".word.selected").forEach((word) => {
      word.classList.remove("selected");
    });
  
    state.selectedWords = [];
    state.translation = null;
  }
  
  async function showPhrases() {
    const phrases = await api(
      state.book ? `./api/phrases?book_id=${state.book.id}` : "./api/phrases"
    );
  
    const list = $("#phrase-list");
  
    if (!phrases.length) {
      list.innerHTML = `
        <div class="phrase-card">
          <h2>Nenhuma expressão salva ainda</h2>
          <p>Durante a leitura, clique em uma palavra, traduza uma expressão e salve-a.</p>
        </div>
      `;
    } else {
      list.innerHTML = phrases.map((phrase) => `
        <article class="phrase-card">
          <div class="french">${escapeHtml(phrase.phrase_fr)}</div>
          <div class="portuguese">${escapeHtml(phrase.translation_pt)}</div>
          ${
            phrase.context_fr
              ? `<p><small>Contexto: ${escapeHtml(phrase.context_fr)}</small></p>`
              : ""
          }
          <button class="delete-phrase" data-id="${phrase.id}">
            Excluir cartão
          </button>
        </article>
      `).join("");
  
      document.querySelectorAll(".delete-phrase").forEach((button) => {
        button.addEventListener("click", async () => {
          await api(`./api/phrases/${button.dataset.id}`, {
            method: "DELETE",
          });
  
          showPhrases();
  
          if (state.book) {
            loadKnownPhrases().then(markKnownWords);
          }
        });
      });
    }
  
    showView("phrases-view");
  }
  
  $("#epub-upload").addEventListener("change", async (event) => {
    const file = event.target.files[0];
  
    if (!file) return;
  
    $("#upload-status").textContent = "Importando e preparando o livro...";
  
    try {
      const result = await api("./api/books/upload", {
        method: "POST",
        body: formData({ file }),
      });
  
      $("#upload-status").textContent =
        `"${result.title}" foi adicionado à biblioteca.`;
  
      event.target.value = "";
      await loadBooks();
    } catch (error) {
      $("#upload-status").textContent = `Erro: ${error.message}`;
    }
  });
  
  $("#btn-home").addEventListener("click", () => {
    $("#book-title").textContent = "Leitor Francês";
    $("#book-subtitle").textContent = "Biblioteca";
    showView("library-view");
    loadBooks();
  });
  
  $("#btn-phrases").addEventListener("click", showPhrases);
  
  $("#btn-close-phrases").addEventListener("click", () => {
    showView(state.book ? "reader-view" : "library-view");
  });
  
  $("#chapter-select").addEventListener("change", (event) => {
    loadChapter(event.target.value, 0);
  });
  
  $("#prev-chapter").addEventListener("click", () => {
    if (state.chapterIndex > 0) {
      loadChapter(state.chapterIndex - 1, 0);
    }
  });
  
  $("#next-chapter").addEventListener("click", () => {
    if (state.chapterIndex < state.chapters.length - 1) {
      loadChapter(state.chapterIndex + 1, 0);
    }
  });
  
  $("#add-word").addEventListener("click", expandSelection);
  $("#remove-word").addEventListener("click", shrinkSelection);
  $("#translate-button").addEventListener("click", translateSelection);
  $("#save-phrase-button").addEventListener("click", savePhrase);
  $("#close-panel").addEventListener("click", closePanel);
  $("#overlay").addEventListener("click", closePanel);
  
  window.addEventListener("scroll", saveProgressSoon, { passive: true });
  
  window.addEventListener("beforeunload", () => {
    if (state.book) {
      saveProgressSoon();
    }
  });
  
  async function start() {
    const me = await api("./api/me");
    $("#context-toggle").checked = me.show_context_default;
    await loadBooks();
  }
  
  start().catch((error) => {
    $("#book-list").innerHTML = `
      <div class="book-card">
        <h2>Erro ao iniciar</h2>
        <p>${escapeHtml(error.message)}</p>
      </div>
    `;
  });  