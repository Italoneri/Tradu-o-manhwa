/* Painel: criar obra, subir capitulo e mandar traduzir, sem terminal.

   Mesmo padrao do app.js - modulo ES nativo, sem framework e sem build - e a
   mesma folha de estilo. O que muda e que aqui tudo escreve: cada acao e uma
   chamada a `/api/`, que so responde para a propria maquina.

   O que esta tela deliberadamente nao faz: apagar obra, apagar capitulo,
   renomear, reordenar pagina, editar traducao. Cada um e destrutivo ou grande, e
   nenhum deles fica melhor escondido atras de um botao pequeno.
*/

const API = "/api";

/** A ordem de leitura vem do nome do arquivo, e tem de bater com a do back.
 *
 * `numeric: true` da o mesmo resultado do `_natural_key` do `store.py` para nome
 * de pagina: 2 antes de 10. Se as duas divergirem, a tela mostra uma ordem e o
 * capitulo sai em outra - que e pior do que nao mostrar ordem nenhuma.
 */
const byName = new Intl.Collator("pt-BR", { numeric: true, sensitivity: "base" });

const ARCHIVE_SUFFIXES = [".zip", ".cbz"];

const el = {
  flash: document.getElementById("flash"),
  health: document.getElementById("health"),

  seriesList: document.getElementById("series-list"),
  toggleNewSeries: document.getElementById("toggle-new-series"),
  newSeries: document.getElementById("new-series"),
  newSlug: document.getElementById("new-slug"),
  newTitle: document.getElementById("new-title"),

  seriesCard: document.getElementById("series-card"),
  seriesName: document.getElementById("series-name"),
  seriesSlug: document.getElementById("series-slug"),
  seriesMeta: document.getElementById("series-meta"),
  metaTitle: document.getElementById("meta-title"),
  metaStatus: document.getElementById("meta-status"),
  coverInput: document.getElementById("cover-input"),
  chapterList: document.getElementById("chapter-list"),
  glossaryRows: document.getElementById("glossary-rows"),
  glossaryAdd: document.getElementById("glossary-add"),
  glossarySave: document.getElementById("glossary-save"),

  uploadCard: document.getElementById("upload-card"),
  chapterNumber: document.getElementById("chapter-number"),
  drop: document.getElementById("drop"),
  fileInput: document.getElementById("file-input"),
  orderNote: document.getElementById("order-note"),
  fileList: document.getElementById("file-list"),
  uploadActions: document.getElementById("upload-actions"),
  uploadStart: document.getElementById("upload-start"),
  uploadDiscard: document.getElementById("upload-discard"),
  uploadStatus: document.getElementById("upload-status"),
  uploadTrack: document.getElementById("upload-track"),
  uploadFill: document.getElementById("upload-fill"),

  jobCard: document.getElementById("job-card"),
  jobTarget: document.getElementById("job-target"),
  jobEngine: document.getElementById("job-engine"),
  jobForce: document.getElementById("job-force"),
  jobDry: document.getElementById("job-dry"),
  jobStart: document.getElementById("job-start"),
  engineNote: document.getElementById("engine-note"),
  jobView: document.getElementById("job-view"),
  jobState: document.getElementById("job-state"),
  jobPhase: document.getElementById("job-phase"),
  jobFill: document.getElementById("job-fill"),
  jobLog: document.getElementById("job-log"),
  jobRead: document.getElementById("job-read"),
};

const state = {
  health: null,
  series: [],
  selected: null,
  /** Arquivos escolhidos e ainda nao enviados, com o resultado de cada um. */
  staged: [],
  chapter: null,
  polling: null,
};

/* ---------- conversa com a API ---------- */

/** Chamada ao painel, com a mensagem do servidor preservada.
 *
 * O back devolve `{"error": "..."}` em toda recusa justamente para a tela poder
 * mostrar o motivo; engolir isso e trocar "capitulo 001 ja existe" por "falhou".
 */
async function api(path, { method = "GET", body = null, raw = false } = {}) {
  const options = { method, cache: "no-store" };
  if (body !== null) {
    options.body = raw ? body : JSON.stringify(body);
    if (!raw) options.headers = { "Content-Type": "application/json" };
  }

  const response = await fetch(`${API}${path}`, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `${response.status} em ${path}`);
  return payload;
}

function seriesPath(slug, suffix = "") {
  return `/series/${encodeURIComponent(slug)}${suffix}`;
}

function flash(message, kind = "erro") {
  el.flash.textContent = message;
  el.flash.className = `flash flash-${kind}`;
  el.flash.hidden = false;
}

function clearFlash() {
  el.flash.hidden = true;
}

/** Roda a acao e transforma falha em recado, em vez de erro silencioso no console. */
async function guard(action) {
  try {
    clearFlash();
    await action();
  } catch (error) {
    flash(error.message);
  }
}

/* ---------- obras ---------- */

function coverHtml(entry) {
  if (!entry.cover) {
    const initial = (entry.title || entry.series).trim().charAt(0) || "?";
    return `<div class="cover-empty" aria-hidden="true">${escapeHtml(initial)}</div>`;
  }
  const url = entry.cover.split("/").map(encodeURIComponent).join("/");
  return `<img src="../${url}" alt="" loading="lazy" decoding="async">`;
}

function renderSeries() {
  if (!state.series.length) {
    el.seriesList.innerHTML = `<p class="empty">Nenhuma obra ainda. Crie a primeira acima.</p>`;
    return;
  }

  el.seriesList.innerHTML = state.series
    .map((entry) => {
      const pending = entry.chapters.filter((chapter) => !chapter.engines.length).length;
      const marks = [
        `${entry.chapters.length} ${entry.chapters.length === 1 ? "capítulo" : "capítulos"}`,
        pending ? `${pending} sem tradução` : "",
      ].filter(Boolean);

      return `<button class="work${entry.series === state.selected ? " chosen" : ""}" data-slug="${escapeHtml(entry.series)}">
        <div class="cover">${coverHtml(entry)}</div>
        <div>
          <h2>${escapeHtml(entry.title || entry.series)}</h2>
          <p>${marks.join(" · ")}</p>
        </div>
      </button>`;
    })
    .join("");
}

function selected() {
  return state.series.find((entry) => entry.series === state.selected) || null;
}

function renderChapters() {
  const entry = selected();
  if (!entry) return;

  if (!entry.chapters.length) {
    el.chapterList.innerHTML = `<p class="empty">Nenhum capítulo. Suba o primeiro abaixo.</p>`;
    return;
  }

  el.chapterList.innerHTML = entry.chapters
    .map((chapter) => {
      const tags = chapter.engines.map((name) => `<span class="tag">${escapeHtml(name)}</span>`).join("");
      const pending = chapter.incoming
        ? `<span class="tag tag-warn">upload aberto</span>`
        : chapter.engines.length
          ? ""
          : `<span class="tag tag-warn">sem tradução</span>`;

      return `<div class="chapter">
        <strong>${escapeHtml(chapter.chapter)}</strong>
        <div class="tags">${tags}${pending}</div>
        <span class="pages">${chapter.image_count} ${chapter.image_count === 1 ? "imagem" : "imagens"}</span>
        <button class="btn btn-secondary" data-translate="${escapeHtml(chapter.chapter)}">Traduzir</button>
      </div>`;
    })
    .join("");
}

function glossaryRowHtml(term = "", translation = "") {
  return `<tr>
    <td><input class="term" value="${escapeHtml(term)}" autocomplete="off"></td>
    <td><input class="translation" value="${escapeHtml(translation)}" autocomplete="off"></td>
    <td><button class="btn btn-ghost" data-drop-row>remover</button></td>
  </tr>`;
}

function readGlossary() {
  const terms = {};
  for (const row of el.glossaryRows.querySelectorAll("tr")) {
    const term = row.querySelector(".term").value.trim();
    if (term) terms[term] = row.querySelector(".translation").value;
  }
  return terms;
}

async function selectSeries(slug) {
  state.selected = slug;
  state.staged = [];
  state.chapter = null;
  renderSeries();
  renderChapters();

  const entry = selected();
  el.seriesName.textContent = entry.title || entry.series;
  el.seriesSlug.textContent = `pasta: ${entry.series}`;
  el.seriesCard.hidden = false;
  el.uploadCard.hidden = false;
  el.jobCard.hidden = true;
  renderStaged();

  const [meta, glossary] = await Promise.all([
    api(seriesPath(slug, "/series.json")),
    api(seriesPath(slug, "/glossary")),
  ]);
  el.metaTitle.value = meta.title || "";
  el.metaStatus.value = meta.status || "";

  const rows = Object.entries(glossary);
  el.glossaryRows.innerHTML = (rows.length ? rows : [["", ""]])
    .map(([term, translation]) => glossaryRowHtml(term, translation))
    .join("");
}

async function loadSeries(keepSelection = true) {
  const payload = await api("/series");
  state.series = payload.series;
  renderSeries();
  if (keepSelection && state.selected && selected()) {
    renderChapters();
  } else if (!selected()) {
    state.selected = null;
    el.seriesCard.hidden = true;
    el.uploadCard.hidden = true;
  }
}

/* ---------- capitulo novo ---------- */

function isArchive(file) {
  return ARCHIVE_SUFFIXES.some((suffix) => file.name.toLowerCase().endsWith(suffix));
}

function humanBytes(total) {
  if (total < 1024) return `${total} B`;
  if (total < 1024 * 1024) return `${Math.round(total / 1024)} KB`;
  return `${(total / (1024 * 1024)).toFixed(1)} MB`;
}

function stageFiles(files) {
  // Ordenado aqui e nao na chegada: o navegador nao garante a ordem em que
  // entrega os arquivos arrastados, e a ordem que vale e a do nome.
  state.staged = [...files]
    .sort((a, b) => byName.compare(a.name, b.name))
    .map((file) => ({ file, state: "pendente", error: null }));
  renderStaged();
}

function renderStaged() {
  const items = state.staged;
  el.orderNote.hidden = !items.length;
  el.uploadActions.hidden = !items.length;
  el.fileList.innerHTML = items
    .map((item) => {
      const thumb = item.file.type.startsWith("image/")
        ? `<img src="${URL.createObjectURL(item.file)}" alt="" loading="lazy">`
        : `<span class="cover-empty" aria-hidden="true">zip</span>`;
      const note = item.error ? `<span class="tag tag-warn">${escapeHtml(item.error)}</span>` : "";

      return `<li class="file file-${item.state}">
        <span class="thumb">${thumb}</span>
        <span class="name">${escapeHtml(item.file.name)}</span>
        <span class="pages">${humanBytes(item.file.size)}</span>
        ${note}
      </li>`;
    })
    .join("");

  const done = items.filter((item) => item.state === "enviado").length;
  el.uploadStatus.textContent = items.length ? `${done} de ${items.length}` : "";
}

function setBar(fill, track, done, total) {
  track.hidden = !total;
  fill.style.width = total ? `${Math.round((done / total) * 100)}%` : "0";
}

async function sendOne(slug, chapter, item) {
  const path = `${seriesPath(slug)}/chapters/${encodeURIComponent(chapter)}`;
  if (isArchive(item.file)) {
    await api(`${path}/archive`, { method: "POST", body: item.file, raw: true });
  } else {
    await api(`${path}/files/${encodeURIComponent(item.file.name)}`, {
      method: "PUT",
      body: item.file,
      raw: true,
    });
  }
}

async function uploadStaged() {
  const slug = state.selected;
  const asked = el.chapterNumber.value.trim();

  // Corpo vazio pede a sugestao ao servidor, que e quem sabe quais capitulos
  // existem. O nome escolhido volta na resposta.
  const created = await api(`${seriesPath(slug)}/chapters`, {
    method: "POST",
    body: asked ? { chapter: asked } : {},
  });
  state.chapter = created.chapter;
  el.chapterNumber.value = created.chapter;

  const pending = state.staged.filter((item) => item.state !== "enviado");
  let done = state.staged.length - pending.length;
  setBar(el.uploadFill, el.uploadTrack, done, state.staged.length);

  for (const item of pending) {
    try {
      await sendOne(slug, state.chapter, item);
      item.state = "enviado";
      item.error = null;
    } catch (error) {
      // Para no primeiro erro em vez de seguir: a area de espera sobrevive, e
      // continuar mandando por cima de uma falha esconde qual arquivo quebrou.
      item.state = "falhou";
      item.error = error.message;
      renderStaged();
      throw new Error(`${item.file.name}: ${error.message} — corrija e clique em enviar de novo`);
    }
    done += 1;
    setBar(el.uploadFill, el.uploadTrack, done, state.staged.length);
    renderStaged();
  }

  const committed = await api(`${seriesPath(slug)}/chapters/${encodeURIComponent(state.chapter)}/commit`, {
    method: "POST",
  });
  state.staged = [];
  renderStaged();
  setBar(el.uploadFill, el.uploadTrack, 0, 0);
  flash(`Capítulo ${committed.chapter} pronto com ${committed.files.length} páginas.`, "ok");

  await loadSeries();
  openJob(committed.chapter);
}

/* ---------- traduzir ---------- */

function openJob(chapter) {
  state.chapter = chapter;
  el.jobCard.hidden = false;
  el.jobTarget.textContent = `${state.selected} · capítulo ${chapter}`;
  el.jobView.hidden = true;
  el.jobRead.hidden = true;
  el.jobCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderJob(job) {
  el.jobView.hidden = false;
  el.jobState.textContent = job.state;
  el.jobPhase.textContent = job.progress.total
    ? `${job.progress.phase} ${job.progress.done}/${job.progress.total} · ${job.progress.detail}`
    : `${job.progress.phase} · ${job.progress.detail}`;
  setBar(el.jobFill, el.jobFill.parentElement, job.progress.done, job.progress.total || 1);

  el.jobLog.textContent = job.log.join("\n");
  el.jobLog.scrollTop = el.jobLog.scrollHeight;

  el.jobStart.disabled = job.state === "running";
  if (job.state === "failed") flash(job.error || "o processamento falhou");
  if (job.state === "done") {
    const href = `index.html?series=${encodeURIComponent(job.series)}&chapter=${encodeURIComponent(job.chapter)}`;
    el.jobRead.href = href;
    el.jobRead.hidden = false;
  }
}

/** Uma consulta por segundo. Sem SSE nem WebSocket: a pagina fica aberta no mesmo
 *  PC que serve, o custo do polling e nulo e o codigo extra nao e. */
function pollJob(id) {
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    const job = await api(`/jobs/${id}`).catch(() => null);
    if (!job) return;
    renderJob(job);
    if (job.state !== "running") {
      clearInterval(state.polling);
      state.polling = null;
      loadSeries().catch(() => {});
    }
  }, 1000);
}

async function startJob() {
  const job = await api("/jobs", {
    method: "POST",
    body: {
      series: state.selected,
      chapter: state.chapter,
      engine: el.jobEngine.value,
      force: el.jobForce.checked,
      dry_run: el.jobDry.checked,
    },
  });
  renderJob(job.job);
  pollJob(job.job_id);
}

/* ---------- ligacao ---------- */

function escapeHtml(value) {
  return String(value).replace(
    /[&<>"']/g,
    (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
  );
}

function wire() {
  el.toggleNewSeries.onclick = () => {
    el.newSeries.hidden = !el.newSeries.hidden;
    if (!el.newSeries.hidden) el.newSlug.focus();
  };

  el.newSeries.onsubmit = (event) => {
    event.preventDefault();
    guard(async () => {
      const created = await api("/series", {
        method: "POST",
        body: { slug: el.newSlug.value.trim(), title: el.newTitle.value.trim() },
      });
      el.newSlug.value = el.newTitle.value = "";
      el.newSeries.hidden = true;
      await loadSeries(false);
      await selectSeries(created.slug);
    });
  };

  el.seriesList.onclick = (event) => {
    const card = event.target.closest("[data-slug]");
    if (card) guard(() => selectSeries(card.dataset.slug));
  };

  el.chapterList.onclick = (event) => {
    const button = event.target.closest("[data-translate]");
    if (button) openJob(button.dataset.translate);
  };

  el.seriesMeta.onsubmit = (event) => {
    event.preventDefault();
    guard(async () => {
      await api(seriesPath(state.selected, "/series.json"), {
        method: "PUT",
        body: { title: el.metaTitle.value.trim(), status: el.metaStatus.value.trim() },
      });
      await loadSeries();
      flash("Título salvo.", "ok");
    });
  };

  el.coverInput.onchange = () => {
    const file = el.coverInput.files[0];
    if (!file) return;
    guard(async () => {
      await api(seriesPath(state.selected, "/cover"), { method: "PUT", body: file, raw: true });
      el.coverInput.value = "";
      await loadSeries();
      flash("Capa trocada.", "ok");
    });
  };

  el.glossaryAdd.onclick = (event) => {
    event.preventDefault();
    el.glossaryRows.insertAdjacentHTML("beforeend", glossaryRowHtml());
  };

  el.glossaryRows.onclick = (event) => {
    const button = event.target.closest("[data-drop-row]");
    if (!button) return;
    event.preventDefault();
    button.closest("tr").remove();
  };

  el.glossarySave.onclick = (event) => {
    event.preventDefault();
    guard(async () => {
      const terms = await api(seriesPath(state.selected, "/glossary"), {
        method: "PUT",
        body: readGlossary(),
      });
      flash(`Glossário salvo com ${Object.keys(terms).length} termos.`, "ok");
    });
  };

  el.fileInput.onchange = () => stageFiles(el.fileInput.files);

  for (const name of ["dragenter", "dragover"]) {
    el.drop.addEventListener(name, (event) => {
      event.preventDefault();
      el.drop.classList.add("over");
    });
  }
  for (const name of ["dragleave", "drop"]) {
    el.drop.addEventListener(name, () => el.drop.classList.remove("over"));
  }
  el.drop.addEventListener("drop", (event) => {
    event.preventDefault();
    stageFiles(event.dataTransfer.files);
  });

  el.uploadStart.onclick = (event) => {
    event.preventDefault();
    el.uploadStart.disabled = true;
    guard(uploadStaged).finally(() => {
      el.uploadStart.disabled = false;
    });
  };

  el.uploadDiscard.onclick = (event) => {
    event.preventDefault();
    guard(async () => {
      if (state.chapter) {
        await api(
          `${seriesPath(state.selected)}/chapters/${encodeURIComponent(state.chapter)}/incoming`,
          { method: "DELETE" },
        ).catch(() => {});
      }
      state.staged = [];
      state.chapter = null;
      renderStaged();
      setBar(el.uploadFill, el.uploadTrack, 0, 0);
      await loadSeries();
    });
  };

  el.jobStart.onclick = (event) => {
    event.preventDefault();
    guard(startJob);
  };
}

async function main() {
  wire();

  state.health = await api("/health").catch(() => null);
  if (!state.health) {
    el.health.textContent = "servidor fora do ar";
    flash("O painel só responde na máquina que roda o servidor. Suba o `mangatl serve` e recarregue.");
    return;
  }

  el.health.textContent = `${state.health.root} · detector ${state.health.detector}`;
  el.jobEngine.innerHTML = state.health.engines
    .map((name) => {
      const blocked = name === "claude" && !state.health.has_api_key;
      return `<option value="${escapeHtml(name)}"${blocked ? " disabled" : ""}>${escapeHtml(name)}</option>`;
    })
    .join("");
  if (!state.health.has_api_key) {
    el.engineNote.hidden = false;
    el.engineNote.textContent =
      "O motor claude está desligado porque não há ANTHROPIC_API_KEY no ambiente do servidor. Copie .env.example para .env, preencha a chave e reinicie.";
  }

  await guard(() => loadSeries(false));
}

main();
