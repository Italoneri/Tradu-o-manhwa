/* Cache do leitor: o celular precisa reabrir um capitulo ja visitado sem rede.

   Duas politicas, porque os arquivos tem naturezas diferentes:
   - imagem de pagina nao muda de conteudo sob o mesmo nome -> cache primeiro
   - todo o resto muda quando o capitulo e reprocessado -> rede primeiro, cache
     como rede de seguranca

   O `chapter.*.json` estava do lado errado dessa divisao. A premissa era que uma
   traducao nao muda depois de gerada, e ela e falsa: `--force`, troca de motor e
   PIPELINE_VERSION novo reescrevem o arquivo sob o mesmo nome, e o leitor que ja
   tinha aberto o capitulo continuava mostrando a traducao antiga para sempre.
   Rede primeiro custa uma revalidacao de ~50KB por abertura e, offline, cai no
   cache exatamente como antes - o ganho de ler sem rede esta nas imagens, que
   sao o volume.
*/

const VERSION = "mangatl-v3";
const SHELL = ["./", "./index.html", "./app.js", "./overlay.js", "./style.css", "./manifest.webmanifest", "./icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((name) => name !== VERSION).map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  );
});

function isImmutable(url) {
  // Trocar a arte de uma pagina sem trocar o nome do arquivo e o unico jeito de
  // furar isto; nesse caso subir o VERSION acima e a saida.
  return /\.(jpe?g|png|webp|bmp)$/i.test(url.pathname);
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(VERSION);
    cache.put(request, response.clone());
  }
  return response;
}

async function networkFirst(request) {
  try {
    // no-cache revalida com o servidor em vez de confiar no cache HTTP do browser.
    // Sem isso o shell atualizado fica preso atras de uma copia velha e so aparece
    // depois de uma recarga forcada - o cache do service worker nao e o culpado,
    // o do browser e.
    const response = await fetch(new Request(request, { cache: "no-cache" }));
    if (response.ok) {
      const cache = await caches.open(VERSION);
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  event.respondWith(isImmutable(url) ? cacheFirst(request) : networkFirst(request));
});
