/* Leitor estatico. Le os JSONs gerados pelo pipeline; nao chama API nenhuma. */

import { bubbleRect, estimateFontCqw, fitFontSize } from "./overlay.js";

const ROOT = "..";
const params = new URLSearchParams(location.search);

const el = {
  main: document.getElementById("main"),
  status: document.getElementById("status"),
  title: document.getElementById("title"),
  subtitle: document.getElementById("subtitle"),
  back: document.getElementById("back"),
  enginePicker: document.getElementById("engine-picker"),
  engine: document.getElementById("engine"),
  toggleOverlay: document.getElementById("toggle-overlay"),
  pager: document.getElementById("pager"),
  prev: document.getElementById("prev"),
  next: document.getElementById("next"),
  progress: document.getElementById("progress"),
};

/* ---------- estado leve em localStorage, sempre tolerante a falha ---------- */

const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* modo privado ou storage bloqueado: a leitura funciona sem memoria */
    }
  },
};

/* ---------- carregamento ---------- */

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${response.status} em ${url}`);
  return response.json();
}

function chapterUrl(library, series, chapter, engine) {
  return `${ROOT}/${library.output_base}/${encodeURIComponent(series)}/${encodeURIComponent(chapter)}/chapter.${engine}.json`;
}

function pageImageUrl(library, series, chapter, image) {
  return `${ROOT}/${library.library_base}/${encodeURIComponent(series)}/${encodeURIComponent(chapter)}/${encodeURIComponent(image)}`;
}

function readerHref(series, chapter, engine) {
  const query = new URLSearchParams({ series, chapter });
  if (engine) query.set("engine", engine);
  return `index.html?${query}`;
}

/* ---------- biblioteca ---------- */

function renderLibrary(library) {
  el.title.textContent = "mangatl";
  el.subtitle.textContent = `${library.series.length} serie(s)`;
  document.title = "mangatl";

  if (!library.series.length) {
    el.main.innerHTML = `<p class="empty">Nenhum capitulo processado ainda.<br>Rode <code>mangatl process library/&lt;serie&gt;/&lt;capitulo&gt;</code>.</p>`;
    return;
  }

  el.main.innerHTML = library.series
    .map(
      (series) => `
      <section class="series">
        <h2>${escapeHtml(series.series)}</h2>
        <div class="chapters">
          ${series.chapters
            .map(
              (chapter) => `
            <a class="chapter" href="${escapeHtml(readerHref(series.series, chapter.chapter))}">
              <strong>${escapeHtml(chapter.chapter)}</strong>
              <span>${Number(chapter.page_count)} paginas</span>
              <div class="tags">${chapter.engines.map((name) => `<span class="tag">${escapeHtml(name)}</span>`).join("")}</div>
            </a>`,
            )
            .join("")}
        </div>
      </section>`,
    )
    .join("");
}

/* ---------- capitulo ---------- */

function flatten(library) {
  return library.series.flatMap((series) =>
    series.chapters.map((chapter) => ({ series: series.series, ...chapter })),
  );
}

function pickEngine(entry, requested) {
  const preferred = requested || store.get("mangatl.engine", null);
  if (preferred && entry.engines.includes(preferred)) return preferred;
  return entry.engines.includes("claude") ? "claude" : entry.engines[0];
}

function renderChapter(library, chapterData, entry, engine) {
  const { series, chapter } = entry;

  el.back.hidden = false;
  el.title.textContent = series;
  el.subtitle.textContent = `${chapter} · ${engine}${chapterData.model ? ` · ${chapterData.model}` : ""}`;
  document.title = `${series} ${chapter}`;

  el.enginePicker.hidden = entry.engines.length < 2;
  el.engine.innerHTML = entry.engines
    .map((name) => `<option value="${escapeHtml(name)}"${name === engine ? " selected" : ""}>${escapeHtml(name)}</option>`)
    .join("");

  el.toggleOverlay.hidden = false;

  const slices = chapterData.pages.map((page) => sliceHtml(library, series, chapter, page)).join("");
  el.main.innerHTML = `<div class="strip">${slices}</div>${orphansHtml(chapterData.pages)}`;

  fitOnScroll();
  setupPager(library, entry, engine);
  restoreScroll(series, chapter);
}

/* ---------- tira continua ---------- */

/** Quatro decimais bastam num style inline; o resto e lixo de ponto flutuante. */
function round(value) {
  return Math.round(value * 1e4) / 1e4;
}

function bubbleHtml(block, page) {
  const rect = bubbleRect(block.bbox, page);
  const size = estimateFontCqw(block.bbox, page, block.text);
  const box = `left:${round(rect.left)}%;top:${round(rect.top)}%;width:${round(rect.width)}%;--h:${round(rect.height)}%`;

  return `<span class="bubble" style="${box};--size:${round(size)}" title="${escapeHtml(block.source_text)}"
      ><span class="t">${escapeHtml(block.text)}</span></span>`;
}

/* Fatia sem margem nem moldura. A fronteira entre fatias e detalhe do
   processamento: o capitulo e uma tira so, e e assim que ele deve aparecer. */
function sliceHtml(library, series, chapter, page) {
  const bubbles = page.blocks
    .filter((block) => block.bbox)
    .map((block) => bubbleHtml(block, page))
    .join("");

  return `<figure class="slice" id="fatia-${Number(page.index)}">
    <img src="${escapeHtml(pageImageUrl(library, series, chapter, page.image))}"
         width="${Number(page.width)}" height="${Number(page.height)}"
         alt="Trecho ${Number(page.index)} do capitulo" loading="lazy" decoding="async">
    ${bubbles}
  </figure>`;
}

/* Fala que o motor achou e a deteccao local nao: sem bbox nao ha onde sobrepor.
   Vai para o fim do capitulo em vez de desaparecer. */
function orphansHtml(pages) {
  const orphans = pages.flatMap((page) =>
    page.blocks.filter((block) => !block.bbox).map((block) => ({ page: page.index, ...block })),
  );
  if (!orphans.length) return "";

  return `<details class="orphans">
    <summary>${orphans.length} fala(s) sem posicao na pagina</summary>
    <ol>${orphans
      .map(
        (block) => `<li>
          <span class="n">${Number(block.page)}</span>
          <div>
            <p class="pt">${escapeHtml(block.text)}</p>
            <p class="en">${escapeHtml(block.source_text)}</p>
          </div>
        </li>`,
      )
      .join("")}</ol>
  </details>`;
}

/* O ajuste de fonte le o layout ja pintado, entao acontece por fatia, quando ela
   chega perto da tela. Medir as 155 na abertura travaria o capitulo. */
function fitOnScroll() {
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        fitSlice(entry.target);
      }
    },
    { rootMargin: "400px 0px" },
  );

  for (const slice of document.querySelectorAll(".slice")) observer.observe(slice);
}

function fitSlice(slice) {
  for (const bubble of slice.querySelectorAll(".bubble")) {
    const inner = bubble.firstElementChild;
    const start = Number(bubble.style.getPropertyValue("--size"));
    // A altura da caixa acompanha o texto, entao o alvo e a bbox do balao - o
    // piso da caixa - e nao o que ela mede agora. Vem da fatia e nao de
    // `getComputedStyle`, que devolve `min-height` percentual ainda em `%`.
    const limit = (parseFloat(bubble.style.getPropertyValue("--h")) / 100) * slice.clientHeight;

    // Mede o filho, e nao `scrollHeight` da caixa: o texto e centrado, e
    // transbordo centrado sai pelos dois lados - scrollHeight so ve um.
    const fitted = fitFontSize({
      start,
      overflows: (size) => {
        bubble.style.setProperty("--size", String(size));
        return inner.offsetHeight > limit;
      },
    });

    bubble.style.setProperty("--size", String(round(fitted)));
  }
}

function setupPager(library, entry, engine) {
  const all = flatten(library);
  const position = all.findIndex((item) => item.series === entry.series && item.chapter === entry.chapter);
  const previous = position > 0 ? all[position - 1] : null;
  const following = position < all.length - 1 ? all[position + 1] : null;

  el.pager.hidden = false;
  el.progress.textContent = `${position + 1} / ${all.length}`;

  const go = (target) => {
    if (target) location.href = readerHref(target.series, target.chapter, engine);
  };

  el.prev.disabled = !previous;
  el.next.disabled = !following;
  el.prev.onclick = () => go(previous);
  el.next.onclick = () => go(following);

  document.addEventListener("keydown", (event) => {
    if (event.target.tagName === "SELECT") return;
    if (event.key === "ArrowLeft") go(previous);
    if (event.key === "ArrowRight") go(following);
  });

  enableSwipe(() => go(following), () => go(previous));
}

/* Swipe horizontal so dispara quando o gesto e claramente horizontal,
   para nao roubar o scroll vertical que e o modo normal de leitura. */
function enableSwipe(onLeft, onRight) {
  const MIN_DISTANCE = 80;
  let startX = 0;
  let startY = 0;

  document.addEventListener(
    "touchstart",
    (event) => {
      startX = event.changedTouches[0].clientX;
      startY = event.changedTouches[0].clientY;
    },
    { passive: true },
  );

  document.addEventListener(
    "touchend",
    (event) => {
      const deltaX = event.changedTouches[0].clientX - startX;
      const deltaY = event.changedTouches[0].clientY - startY;
      if (Math.abs(deltaX) < MIN_DISTANCE || Math.abs(deltaX) < Math.abs(deltaY) * 1.5) return;
      if (deltaX < 0) onLeft();
      else onRight();
    },
    { passive: true },
  );
}

function scrollKey(series, chapter) {
  return `mangatl.scroll.${series}/${chapter}`;
}

function restoreScroll(series, chapter) {
  const saved = store.get(scrollKey(series, chapter), 0);
  if (saved > 0) requestAnimationFrame(() => window.scrollTo(0, saved));

  let pending = null;
  window.addEventListener(
    "scroll",
    () => {
      if (pending) return;
      pending = setTimeout(() => {
        pending = null;
        store.set(scrollKey(series, chapter), Math.round(window.scrollY));
      }, 400);
    },
    { passive: true },
  );
}

/* ---------- utilitarios ---------- */

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function fail(message) {
  el.main.innerHTML = `<p class="empty">${escapeHtml(message)}</p>`;
  el.pager.hidden = true;
}

/* ---------- inicializacao ---------- */

function setupOverlayToggle() {
  const visible = store.get("mangatl.overlay", true);
  document.body.classList.toggle("hide-overlay", !visible);
  el.toggleOverlay.setAttribute("aria-pressed", String(visible));

  el.toggleOverlay.onclick = () => {
    const next = document.body.classList.contains("hide-overlay");
    document.body.classList.toggle("hide-overlay", !next);
    el.toggleOverlay.setAttribute("aria-pressed", String(next));
    store.set("mangatl.overlay", next);
  };
}

async function main() {
  setupOverlayToggle();

  let library;
  try {
    library = await fetchJson(`${ROOT}/output/library.json`);
  } catch {
    fail("Nao achei output/library.json. Rode `mangatl build-library` e recarregue.");
    return;
  }

  const series = params.get("series");
  const chapter = params.get("chapter");
  if (!series || !chapter) {
    renderLibrary(library);
    return;
  }

  const entry = flatten(library).find((item) => item.series === series && item.chapter === chapter);
  if (!entry) {
    fail(`Capitulo ${series}/${chapter} nao esta na biblioteca.`);
    return;
  }

  const engine = pickEngine(entry, params.get("engine"));
  el.engine.onchange = () => {
    store.set("mangatl.engine", el.engine.value);
    location.href = readerHref(series, chapter, el.engine.value);
  };

  try {
    const chapterData = await fetchJson(chapterUrl(library, series, chapter, engine));
    renderChapter(library, chapterData, entry, engine);
  } catch {
    fail(`Nao consegui carregar a traducao '${engine}' de ${series}/${chapter}.`);
  }
}

main();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(() => {
      /* sem service worker o leitor funciona, so nao guarda offline */
    });
  });
}
