# Plano de execução: adicionar e traduzir capítulos pela própria aplicação web

Documento de trabalho para um agente de código. Leia **tudo** antes de escrever a
primeira linha. As fases são sequenciais e cada uma tem um portão de parada.

Pré-requisito: `PLANO-OVERLAY.md` e as fases já executadas de `PLANO-DETECCAO.md`
estão commitadas. Este plano parte do código como ele está hoje — em especial de
`src/mangatl/serving.py`, que já existe e é a fundação de tudo aqui.

---

## 0. Contexto obrigatório

Projeto `mangatl`: traduz capítulos de manhwa EN→PT em lote e serve um leitor web
offline. Leia `README.md` antes de qualquer coisa.

**Onde rodar.** O projeto mora no Windows em
`C:\Users\Perdido\.antigravity\tradução`; o WSL vê a mesma pasta em
`/mnt/c/Users/Perdido/.antigravity/tradução`. Python roda no WSL:

```bash
cd "/mnt/c/Users/Perdido/.antigravity/tradução"
source ~/.venvs/mangatl/bin/activate
```

**Estilo do código — siga, não invente um novo.**

- Comentários e docstrings em português **sem acentos** (`traducao`, `capitulo`).
- Comentário explica *por que*, com medição real quando existir. Não escreva
  comentário que repete o que o código diz.
- Modelos pydantic `frozen=True, extra="forbid"`; campo novo com default.
- Função pura separada de I/O, para ser testável sem rede, sem disco e sem
  Tesseract. `serving.py` já faz isso certo: `is_servable` é pura e
  `ReaderHandler` só a consulta. Mantenha o padrão.
- Teste ao lado do módulo, sufixo `_test.py`.
- **Stdlib apenas** no caminho do servidor. Nada de FastAPI, Flask ou uvicorn.
  O motivo está escrito no topo do `serving.py` e continua valendo.

**Não faça:** reformatação em massa, troca de biblioteca, mudança na detecção, no
OCR, na tradução ou na geometria do overlay. Este plano acrescenta uma camada de
API e uma tela; não mexe no pipeline a não ser para injetar progresso.

---

## 1. O problema

Hoje, adicionar um capítulo custa isto:

1. abrir o terminal do Windows
2. `wsl -d Ubuntu -e bash -lc '...'` com o caminho `/mnt/c` inteiro
3. ativar o venv
4. copiar as imagens para `library/<serie>/<cap>/` na mão
5. `mangatl process library/<serie>/<cap> --engine free`
6. lembrar de reiniciar ou reconstruir a biblioteca

O leitor já está no ar e funcionando. A pasta já está arrumada — a série
`Eu me tornei a Neta Desprezada` tem `cover.jpeg` e o capítulo `001`. O que falta
é parar de precisar do terminal para crescer a biblioteca.

**Objetivo:** dentro da própria aplicação web, abrir uma série, adicionar o
próximo capítulo enviando as imagens, escolher o motor e disparar a tradução,
acompanhando o progresso — sem terminal e sem editar arquivo.

### Decisões já tomadas (não reabra)

Estas foram decididas com o dono do projeto. O agente **não** deve redesenhar:

| Decisão | Valor | Por quê |
|---|---|---|
| Origem das imagens | **Upload pelo navegador** (arrastar imagens ou um `.zip`/`.cbz`) | Não depende de site nenhum, não quebra quando um layout muda, e não precisa de navegador headless |
| Quem pode administrar | **Só o PC onde o servidor roda** | O celular continua só lendo. Evita expor a chave da API e o disco na LAN |
| Raspagem de URL | **Fora de escopo** | Deixe o ponto de extensão documentado e não implemente |

---

## 2. Arquitetura — leia antes de escrever

### 2.1 Dois servidores, dois papéis. Não os unifique.

O `serving.py` documenta no topo que existe um `scripts/serve.py` rodando no
**python do Windows**, que não enxerga o venv do projeto. Ele existe porque o IP
que o WSL imprime (`172.x`) não é alcançável pelo celular.

Isso vira uma regra de arquitetura:

```
scripts/serve.py      python do Windows, sem venv    LEITOR, somente leitura, 0.0.0.0, o celular usa
mangatl serve         WSL, com venv                  LEITOR + PAINEL, painel so em 127.0.0.1
```

O módulo do painel **não pode ser importado** por `scripts/serve.py`, direta ou
indiretamente. Ele depende do venv (pydantic, cv2, o pipeline inteiro) e aquele
processo não tem venv. Coloque o painel num módulo novo e mantenha `serving.py`
livre dele: `serving.py` continua sendo o que os dois processos compartilham.

Se `scripts/serve.py` hoje importa de `mangatl`, confira que só importa
`serving.py` e que `serving.py` não ganhou import novo. Um `import cv2` que vaze
para lá derruba o servidor do celular com `ModuleNotFoundError` — e o Smart App
Control do Windows é justamente o motivo de aquele processo existir.

### 2.2 O servidor precisa virar multi-thread

`serve_reader` usa `socketserver.TCPServer`, que atende **uma conexão por vez**.
Um job de tradução leva minutos. Com o servidor bloqueado, o navegador não
consegue nem buscar o progresso nem carregar a página: a tela congela e parece
que travou.

Troque por `http.server.ThreadingHTTPServer` (stdlib, subclasse de
`ThreadingMixIn` + `HTTPServer`, já com `daemon_threads`). É uma linha, e é
pré-requisito de tudo que vem depois.

```python
class _Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
```

### 2.3 O painel só responde para a própria máquina

O servidor escuta em `0.0.0.0` porque o celular precisa alcançá-lo. O painel, não.
Toda rota sob `/api/` responde **403** quando o cliente não é local.

Função pura, testável:

```python
LOCAL_CLIENTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

def is_local_client(address: str) -> bool:
    """Se o pedido veio da propria maquina.

    O leitor escuta em 0.0.0.0 para o celular alcancar, e o painel escreve em
    disco e dispara o pipeline. Um `192.168.x` que chegue aqui e alguem do mesmo
    Wi-Fi, nao o dono - e a lista da LAN nao e credencial nenhuma.
    """
```

No handler: `self.client_address[0]`. Teste os casos `127.0.0.1`, `::1`,
`192.168.0.10`, string vazia.

Isso substitui autenticação por enquanto e é honesto sobre o que protege: quem
tem shell na máquina já tinha tudo. Escreva isso no docstring do módulo.

### 2.4 Sem multipart

`http.server` não parseia `multipart/form-data`, e o `cgi` da stdlib saiu no
Python 3.13. Não escreva um parser de multipart e não instale um.

O upload manda **um arquivo por requisição**, com o corpo cru:

```
PUT /api/series/<slug>/chapters/<cap>/files/<nome.jpg>     corpo = bytes da imagem
POST /api/series/<slug>/chapters/<cap>/archive             corpo = bytes do .zip/.cbz
```

Ler o corpo é `self.rfile.read(int(self.headers["Content-Length"]))`. Sem
`Content-Length`, responda 411. Com `Transfer-Encoding: chunked`, responda 411
também — o `fetch` do navegador manda `Content-Length` para `Blob`.

De brinde, isso dá barra de progresso por arquivo no front sem esforço.

### 2.5 Área de espera, para nunca processar meio capítulo

As imagens sobem para `library/<serie>/<cap>.incoming/`. Só quando o navegador
confirma que mandou tudo é que a pasta é renomeada para `library/<serie>/<cap>/`.

Sem isso, um upload interrompido deixa um capítulo pela metade que o
`discover_chapters` acha e o `process-all` traduz — e como o pipeline é
idempotente por sha, as páginas que faltavam entram depois sem refazer o resto,
mas a numeração das falas já saiu errada.

**Consequência obrigatória:** `store.discover_chapters` precisa ignorar diretório
terminado em `.incoming`. Se esquecer disso, a área de espera vira exatamente o
bug que ela existe para evitar.

### 2.6 Um job por vez

O motor `free` carrega um modelo Argos na memória; o `claude` consome cota. Dois
jobs simultâneos não trazem nada e podem estourar a memória do WSL.

Registro em memória, um job ativo, `409 Conflict` quando já há um rodando. O
histórico de jobs morre com o processo e isso está certo: o resultado que
interessa já está em `output/`.

---

## FASE 0 — Fundação (rápida, sem UI, sem upload)

### 0.1 `ThreadingHTTPServer`

Em `serving.py`, troque `socketserver.TCPServer` por `ThreadingHTTPServer`
conforme 2.2. Confirme que `scripts/serve.py` continua funcionando.

### 0.2 Roteador mínimo

`ReaderHandler` hoje só tem `send_head`. O painel precisa de `do_POST`, `do_PUT`
e de rotas em `do_GET` que não sejam arquivo.

Crie `src/mangatl/panel.py` (nome sugerido) com um roteador por tabela, não por
cadeia de `if`:

```python
Route = tuple[str, re.Pattern[str], Handler]

ROUTES: tuple[Route, ...] = (
    ("GET",  re.compile(r"^/api/health$"),  _health),
    ...
)
```

A função que casa caminho com rota é **pura** e testada sem servidor: recebe
método e caminho, devolve `(handler, grupos)` ou `None`. Teste que
`/api/series/a%2Fb/...` não escapa e que método errado dá 405, não 404.

O handler HTTP fica fino: acha a rota, valida cliente local, lê o corpo, chama a
função, serializa a resposta. Toda a lógica mora em funções que recebem `Config`
e argumentos já validados.

Composição: o handler do painel **estende** `ReaderHandler`. Pedido que não casa
com `/api/` cai no `super()` e é servido como arquivo, como hoje.

### 0.3 Nomes vindos da rede são hostis

Três funções puras, em `panel.py`, cada uma com teste:

```python
def safe_component(name: str) -> str | None:
    """Nome de serie ou capitulo aceitavel como nome de pasta.

    Recusa vazio, `.`, `..`, qualquer barra, barra invertida, dois-pontos e
    caractere de controle. Recusa nome que comece com ponto, que e como o
    `is_servable` marca o que nao sai na rede. Limite de 120 caracteres.

    Acento e espaco passam: a serie que ja existe se chama
    "Eu me tornei a Neta Desprezada" e renomear a pasta quebraria os JSONs.
    """

def safe_page_name(name: str) -> str | None:
    """Nome de arquivo de pagina: `safe_component` mais extensao em IMAGE_SUFFIXES."""

def safe_archive_members(names: Sequence[str]) -> list[str] | None:
    """Entradas de um zip que podem ser extraidas, ou None se alguma for hostil.

    Zip carrega caminho dentro de si, e a stdlib extrai o que estiver escrito -
    inclusive `../../.env`. Aceita so o nome-base, recusa caminho absoluto,
    recusa `..` em qualquer segmento, ignora diretorio e ignora o
    `__MACOSX/` que o Finder enfia em todo zip.
    """
```

Tabela de casos que os testes devem cobrir: `..`, `../x`, `/etc/passwd`,
`C:\x`, `a/b`, `a\b`, `.env`, `""`, `"."`, nome de 300 caracteres, `x.exe`,
`x.jpg.exe`, `x.JPG` (deve passar), `Eu me tornei a Neta Desprezada` (deve passar).

### 0.4 Tetos

Constantes no topo de `panel.py`, com o número justificado:

```python
MAX_PAGE_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
MAX_PAGES_PER_CHAPTER = 400
MAX_ARCHIVE_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
"""Teto do descompactado, checado somando `ZipInfo.file_size` ANTES de extrair.

Um zip de 1MB pode declarar 100GB. Somar o declarado nao e garantia contra zip
que mente, entao o loop de extracao tambem conta os bytes escritos e aborta."""
```

### 0.5 Primeiras rotas, só leitura

```
GET /api/health   -> {ok, root, engines, detector, has_api_key, python}
GET /api/series   -> o mesmo conteudo de library.json, gerado na hora
```

`GET /api/series` chama `build_library(cfg)` em vez de ler o JSON salvo: o painel
mostra a verdade do disco, e o `library.json` continua sendo o que o leitor
consome.

**PARE.** Rode `pytest`, suba `mangatl serve`, teste com `curl`:

```bash
curl -s localhost:8000/api/health
curl -s -H 'X-Forwarded-For: 1.2.3.4' localhost:8000/api/health   # deve ignorar o header
curl -s http://<ip-da-lan>:8000/api/health                        # deve dar 403
curl -s http://<ip-da-lan>:8000/reader/                           # deve continuar servindo
```

Relate. **O 403 vindo da LAN é o teste que não pode falhar.**

---

## FASE 1 — Metadados da série

A série já tem `cover.jpeg` e `SeriesEntry` já tem o campo `cover`. Falta o
título de exibição: hoje o nome da pasta é o título, e renomear a pasta quebra
todos os caminhos dos JSONs.

### 1.1 `series.json`

Em `library/<slug>/series.json`, opcional:

```json
{
  "title": "Eu me tornei a Neta Desprezada",
  "cover": "cover.jpeg",
  "status": "em andamento"
}
```

Em `store.py`, uma função de carga tolerante no molde de `load_glossary`:
ausente devolve os defaults (`title` = nome da pasta, `cover` = a primeira
imagem `cover.*` que existir). Acrescente `title: str` em `SeriesEntry` com
default igual ao slug, para que `library.json` antigo continue validando.

O leitor passa a mostrar `title`; o `slug` continua sendo a chave em toda URL e
em todo caminho de disco. Não os confunda — é a diferença entre poder renomear a
série e ter que reprocessar tudo.

### 1.2 Rotas

```
POST /api/series                      {slug, title}     cria a pasta
PUT  /api/series/<slug>/series.json   objeto            grava titulo/status
PUT  /api/series/<slug>/cover         bytes da imagem   grava cover.<ext>
GET  /api/series/<slug>/glossary      -> objeto
PUT  /api/series/<slug>/glossary      objeto            grava glossary.json
```

O glossário entra aqui porque é o arquivo que mais precisa de edição recorrente —
é ele que impede o personagem de mudar de nome no capítulo seguinte — e é o
segundo motivo de abrir o terminal hoje.

Valide o glossário na borda: objeto JSON de string para string, chaves não
vazias, no máximo 500 entradas. Erro devolve 422 com mensagem legível, não 500.

**PARE.** `pytest`. Relate.

---

## FASE 2 — Upload

### 2.1 Rotas

```
POST   /api/series/<slug>/chapters                       {chapter}  -> cria <cap>.incoming/
PUT    /api/series/<slug>/chapters/<cap>/files/<nome>     bytes      -> grava uma pagina
POST   /api/series/<slug>/chapters/<cap>/archive          bytes      -> extrai zip/cbz
POST   /api/series/<slug>/chapters/<cap>/commit                      -> renomeia para <cap>/
DELETE /api/series/<slug>/chapters/<cap>/incoming                    -> descarta a area de espera
GET    /api/series/<slug>/chapters/<cap>/incoming                    -> {files: [...], bytes}
```

`POST /chapters` sugere o próximo número quando o corpo vem vazio: maior capítulo
numérico existente + 1, formatado com a mesma largura (`001` → `002`). Use o
`_natural_key` que já existe em `store.py`; não escreva outro ordenador.

`commit` recusa com 422 se a área de espera estiver vazia, e recusa se
`library/<slug>/<cap>/` já existir — sobrescrever capítulo é destrutivo e não faz
parte deste plano.

`DELETE .../incoming` é a única remoção que este plano permite, e só apaga o que
o próprio painel escreveu.

### 2.2 Extração de arquivo compactado

`zipfile` da stdlib. Ordem obrigatória:

1. abrir e listar
2. `safe_archive_members` sobre os nomes — qualquer recusa aborta tudo, sem
   extrair nada
3. somar `file_size` declarado; acima de `MAX_ARCHIVE_EXPANDED_BYTES`, aborta
4. extrair um a um **pelo nome-base**, contando bytes realmente escritos e
   abortando se o total passar do teto
5. em qualquer erro, apagar a `.incoming` inteira

Não use `ZipFile.extractall`. Ele obedece ao caminho gravado dentro do zip.

`.cbz` é zip com outro nome; aceite as duas extensões e decida pelo conteúdo
(`zipfile.is_zipfile`), não pelo nome.

### 2.3 Ordem das páginas

A ordem de leitura vem do nome do arquivo, via `_natural_key` — `2.jpg` antes de
`10.jpg`. O navegador não garante a ordem em que entrega os arquivos arrastados.

Regra: **o front ordena por nome antes de enviar, e o back nunca depende da ordem
de chegada.** `list_page_images` já ordena por `_natural_key` na hora de
processar, então o back já está certo; o que o front precisa é mostrar ao usuário
a ordem que vai valer, para ele perceber antes de traduzir se os nomes estão
ruins.

Se o usuário arrastar capturas de rolagem altas, não faça nada de especial: o
`extract_chapter` já chama `slice_chapter_in_place` e fatia sozinho, guardando os
originais em `_source/`. Diga isso na interface em uma linha.

**PARE.** Teste subindo um `.zip` e um punhado de `.jpg` por `curl`, e confira o
disco. Relate.

---

## FASE 3 — Jobs e progresso

### 3.1 Progresso injetado, não global

O pipeline hoje loga `operation=extract_page page=%d image=%s`. Raspar log é
frágil. Injete um callback, no mesmo espírito com que `fitFontSize` recebe
`overflows`:

```python
class Progress(Frozen):
    phase: Literal["slice", "extract", "translate", "library"]
    done: int
    total: int
    detail: str = ""


ProgressFn = Callable[[Progress], None]
```

`extract_chapter` e `translate_chapter` ganham `progress: ProgressFn | None = None`.
Default `None` mantém todo chamador existente funcionando sem mudança.

- O CLI passa um callback que imprime — e aí o terminal ganha progresso de
  quebra, o que hoje não existe.
- O painel passa um que atualiza o registro do job.

O motor `claude` traduz por bloco de páginas (`chunk_pages`); reporte por chunk
concluído. O `free` traduz página a página; reporte por página.

### 3.2 Registro de jobs

```python
@dataclass
class Job:
    id: str
    series: str
    chapter: str
    engine: str
    state: Literal["running", "done", "failed", "cancelled"]
    progress: Progress
    log: deque[str]          # maxlen=200
    started_at: str
    finished_at: str | None
    error: str | None
```

Um `threading.Lock` em volta do registro. O job roda numa thread; a thread só
escreve no próprio `Job`, e o handler só lê. Nada de estado global fora disso.

O log sai de um `logging.Handler` anexado aos loggers `mangatl.*` **durante** o
job e removido no `finally` — sem isso, dois jobs seguidos duplicam linha.

### 3.3 Rotas

```
POST /api/jobs        {series, chapter, engine, force, dry_run}  -> 202 {job_id}
GET  /api/jobs        -> ultimos 20
GET  /api/jobs/<id>   -> o job
```

`POST /api/jobs` responde **409** se já houver job `running`, e **422** se o
motor não estiver em `available_engines()` ou se o capítulo não existir.

O front consulta `GET /api/jobs/<id>` a cada 1s. Não implemente SSE nem
WebSocket: a página fica aberta no mesmo PC, o custo de polling é nulo e o código
extra não é.

Cancelamento fica **fora de escopo**. Não há ponto de interrupção seguro no meio
de um OCR, e matar a thread corrompe o `extract.json` pela metade. Diga isso na
interface: "deixe terminar ou reinicie o servidor".

### 3.4 Ao terminar

Na thread, no `finally` do caminho de sucesso: `save_library(cfg, build_library(cfg))`,
exatamente como o comando `process` já faz. Sem isso o capítulo novo não aparece
para o leitor.

### 3.5 A chave da API

O motor `claude` precisa de `ANTHROPIC_API_KEY`. `cli._load()` chama
`load_dotenv()`, então o processo do servidor já tem a chave no ambiente.

`GET /api/health` devolve **`has_api_key: true|false`**, nunca o valor. O front
desabilita o motor `claude` quando for `false` e explica por quê. Se a chave
vazar para qualquer resposta, o trabalho da Fase 0 do `PLANO-DETECCAO.md` foi
desfeito.

**PARE.** Dispare um job pelo `curl`, acompanhe o progresso, confirme que o
capítulo aparece em `library.json`. Relate.

---

## FASE 4 — A tela

### 4.1 Onde mora

`reader/admin.html` + `reader/admin.js`, reaproveitando `reader/style.css`. Sem
framework, sem build: é o mesmo padrão do `app.js`, que é módulo ES nativo.

### 4.2 O botão só aparece onde funciona

Na biblioteca (`app.js`), mostre o link para o painel apenas quando a página
estiver sendo servida localmente:

```js
// O painel so responde para 127.0.0.1. Mostrar o link no celular seria oferecer
// um botao que responde 403.
const isLocal = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
```

### 4.3 Fluxo

Uma tela, três blocos empilhados:

**Série.** Lista as séries com capa e título. Selecionar uma abre os capítulos que
já existem, com os motores de cada um. Botão para criar série nova. Campo de
título e upload de capa. Editor de glossário: tabela de duas colunas com
adicionar e remover linha, e um botão salvar — não um `<textarea>` de JSON cru,
que é exatamente o tipo de coisa que faz voltar para o terminal.

**Capítulo novo.** Número pré-preenchido com a sugestão da API, editável. Uma área
de arrastar-e-soltar que aceita imagens e `.zip`/`.cbz`. Depois de soltar, mostra
**a lista ordenada pelo nome, na ordem que vai valer**, com miniatura e tamanho.
Envia um arquivo por vez, com barra de progresso, e chama `commit` no fim.

Falha no meio: mostre qual arquivo falhou e ofereça reenviar só ele. A
`.incoming` sobrevive entre recarregamentos da página, então dá para continuar.

**Traduzir.** Seletor de motor (`free` / `claude`, com o `claude` desabilitado e
explicado quando não houver chave), caixas para `--force` e `--dry-run`, e o
botão. Enquanto roda: fase atual, `done/total`, barra, e as últimas linhas de log
num bloco monoespaçado rolando. No fim, um link direto para ler o capítulo.

### 4.4 O que a tela não faz

Apagar série, apagar capítulo, renomear, reordenar página, editar tradução à mão.
Cada um desses é destrutivo ou grande. Se aparecer vontade no meio do caminho,
anote e não implemente.

### 4.5 Service worker

`reader/sw.js` intercepta todo GET da mesma origem. Uma resposta de
`GET /api/jobs/<id>` no cache é progresso congelado na tela.

Acrescente a primeira coisa no `fetch`:

```js
// O painel nunca passa pelo cache: /api/ e estado vivo, e progresso cacheado e
// barra que nao anda.
if (url.pathname.startsWith("/api/")) return;
```

E deixe `admin.html` / `admin.js` **fora** do `SHELL`: o painel só funciona
online, com o servidor de pé, e guardá-lo offline só cria uma tela morta.

Suba o `VERSION` do service worker de `mangatl-v3` para `mangatl-v4`.

**PARE.** Relate.

---

## FASE 5 — Verificação ponta a ponta

Não é teste unitário; é o único juiz. Execute e responda por escrito:

1. Do PC, criar uma série nova com capa e título. Ela aparece na biblioteca com o
   título certo?
2. Adicionar um capítulo arrastando 20 imagens fora de ordem alfabética
   (`2.jpg`, `10.jpg`, `1.jpg`). A ordem de leitura saiu certa?
3. Adicionar um capítulo por `.cbz`. Mesma pergunta.
4. Traduzir com `free`. A barra anda? O log aparece? O capítulo abre no fim?
5. Recarregar a página no meio do job. O progresso volta ao reabrir?
6. Disparar dois jobs. O segundo recebe 409 com mensagem legível?
7. Fechar a aba no meio do job. O job termina mesmo assim e o capítulo aparece?
8. **Do celular**, abrir o leitor. O capítulo novo está lá, o botão do painel
   **não** aparece, e `http://<ip>:8000/api/health` responde 403?
9. Subir um zip com uma entrada chamada `../../.env`. Foi recusado sem extrair
   nada?
10. Interromper um upload no meio e recarregar. A `.incoming` está lá e dá para
    continuar? Nenhum capítulo pela metade apareceu na biblioteca?

Os itens 8 e 9 são os que não podem falhar.

---

## 6. Fora de escopo (anote, não implemente)

- **Baixar capítulo de URL.** Deixe o ponto de extensão explícito: uma função
  `import_from_url(url) -> list[Path]` que não existe ainda, com um comentário
  dizendo que qualquer site sério precisa de navegador headless e quebra a cada
  mudança de layout. Não crie a rota.
- Painel acessível do celular (exige rede espelhada do WSL2 ou `portproxy` no
  Windows, mais token de verdade).
- Apagar ou renomear série e capítulo.
- Editar tradução à mão pelo leitor.
- Fila com mais de um job.
- Cancelar job em andamento.

---

## Checklist

- [ ] `ThreadingHTTPServer` no lugar do `TCPServer`
- [ ] `scripts/serve.py` continua subindo no python do Windows, sem import novo
- [ ] `is_local_client` pura e testada; `/api/` responde 403 da LAN
- [ ] Roteador por tabela, casamento de rota testado sem servidor
- [ ] `safe_component`, `safe_page_name`, `safe_archive_members` com a tabela de casos hostis
- [ ] Tetos de tamanho aplicados antes de escrever em disco
- [ ] `discover_chapters` ignora `*.incoming`
- [ ] `series.json` com `title`, e `SeriesEntry.title` com default = slug
- [ ] Glossário editável por tabela, validado na borda
- [ ] Upload arquivo a arquivo, sem multipart; zip extraído entrada a entrada, sem `extractall`
- [ ] `commit` recusa capítulo já existente e área de espera vazia
- [ ] `Progress` injetado em `extract_chapter` e `translate_chapter`, com o CLI também usando
- [ ] Um job por vez, 409 no segundo; log por `logging.Handler` removido no `finally`
- [ ] `save_library` roda ao fim do job
- [ ] `has_api_key` booleano; a chave nunca sai numa resposta
- [ ] `admin.html` fora do SHELL; `/api/` fora do service worker; `VERSION` em v4
- [ ] Link do painel escondido fora do localhost
- [ ] `pytest` verde; `node --test reader/overlay.test.js` verde
- [ ] As 10 perguntas da Fase 5 respondidas por escrito
