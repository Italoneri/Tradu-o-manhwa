# Plano de execução: trocar a detecção de balões por um detector treinado

Documento de trabalho para um agente de código. Leia **tudo** antes de escrever a
primeira linha. As fases são sequenciais e cada uma tem um portão de parada.

---

## 0. Contexto obrigatório

Este é o projeto `mangatl`: traduz capítulos de manhwa/mahua EN→PT em lote e serve
um leitor web offline. Antes de qualquer coisa, leia `README.md` inteiro — ele
documenta decisões medidas que você não deve desfazer sem evidência.

**Onde o código roda.** O projeto mora no lado Windows, em
`C:\Users\Perdido\.antigravity\tradução`, e o WSL vê a mesma pasta em
`/mnt/c/Users/Perdido/.antigravity/tradução`. Não são cópias. Todo comando Python
roda no WSL, sempre com `wsl -d Ubuntu`, com o venv em `~/.venvs/mangatl`
(filesystem Linux, nunca em `/mnt/c`).

```bash
cd "/mnt/c/Users/Perdido/.antigravity/tradução"
source ~/.venvs/mangatl/bin/activate
```

**Estilo do código — siga, não invente um novo.** Este repositório tem um idioma
consistente e ele é parte do valor do projeto:

- Comentários e docstrings em português **sem acentos** (`traducao`, `deteccao`,
  `balao`). Mantenha.
- Comentário explica *por que*, com medição real quando existir. O padrão do
  repositório é: `"medido numa pagina real, esse halo levou o papel a 198 contra o
  corte de 200"`. Não escreva comentário que repete o que o código já diz.
- Modelos pydantic `frozen=True, extra="forbid"` para todo dado que atravessa o
  sistema. Parse na borda, confia depois.
- Módulos puros (geometria, ordenação, limpeza de texto) separados de I/O, para
  serem testáveis sem Tesseract, sem GPU e sem rede.
- Teste mora ao lado do módulo, com sufixo `_test.py`. `pytest` já está
  configurado com `testpaths = ["src/mangatl"]` e `python_files = ["*_test.py"]`.
- Import tardio para dependência opcional, como em `engines/base.py`: um motor
  com dependência faltando não pode derrubar o outro.

**Não faça:** reformatação em massa, renomeação de coisas que funcionam, troca de
biblioteca que não esteja pedida aqui, nem "melhorias" fora do escopo da fase.

---

## 1. Por que esta mudança existe

A detecção atual (`src/mangatl/detect.py`) é heurística de visão clássica: acha
região clara, fechada, razoavelmente convexa, com tinta moderada dentro. Quatro
thresholds em `[detect]` e `--debug-boxes` para calibrar a olho.

Ela está no teto. A evidência não é opinião — é o projeto FrankYomik, do Fabio
Akita, que resolve o mesmo problema. O docstring do detector dele:

```python
# FrankYomik/server/kindle/bubble_detector.py
"""Speech bubble and text detection using RT-DETR-v2.
...
Replaces the previous OpenCV contour + heuristic filter approach.
"""
```

Ele percorreu exatamente este caminho e o abandonou. E o detector de webtoon dele
diz por quê, no caso que é justamente o nosso:

```python
# FrankYomik/server/webtoon/bubble_detector.py
"""Text-first bubble detection for Korean webtoons.

Unlike manga (contour-first), webtoons have irregular/colored bubbles that
don't respond well to binary threshold + contour analysis.
"""
```

O nosso `min_interior_brightness = 200.0` exige papel branco. Balão colorido,
balão sem borda e texto sobre arte são invisíveis para o detector atual **por
construção**, não por calibração ruim.

### Baseline medido (não apague, é a métrica de sucesso)

Capítulo `manhwa/001`, estado atual em `output/manhwa/001/`:

| Métrica | Valor |
|---|---|
| Fatias (páginas) | 155 |
| Blocos extraídos | 123 |
| Fatias com **zero** falas | **85 de 155** |
| Confiança média do OCR | 84.2 |
| Distribuição de falas/fatia | 0:85, 1:46, 2:11, 3:3, 4:6, 5:3, 7:1 |

**Primeira ação da Fase 1:** copiar `output/manhwa/001/extract.json` para
`output/manhwa/001/extract.baseline.json` antes de qualquer mudança. Sem essa
cópia não há como medir o resultado, e bumpar `PIPELINE_VERSION` invalida o
arquivo atual.

---

## 2. Repositório de referência

Clone o FrankYomik **fora** da árvore do projeto, para não contaminar o git:

```bash
git clone --depth 1 https://github.com/akitaonrails/FrankYomik.git ~/ref/FrankYomik
```

Arquivos que importam, em ordem de relevância:

| Arquivo | O que tirar dele |
|---|---|
| `server/kindle/bubble_detector.py` | Carga do RT-DETR-v2, mapeamento de classes, limiares separados por classe, dedup por overlap |
| `server/webtoon/bubble_detector.py` | Abordagem text-first, clusterização, fallback em três níveis, amostragem de cor de fundo |
| `server/webtoon/ocr.py` | EasyOCR em três passes (original, contraste, invertido) e resgate de detecção de baixa confiança |
| `server/webtoon/image_utils.py` | `split_tall_image` / `stitch_detections`: fatiar **só para inferência**, com overlap, e remapear as caixas de volta |
| `server/kindle/inpainter.py` | LaMa para apagar texto sobre arte (Fase 4, não agora) |
| `server/tests/ab_sfx_detection.py` | Becos sem saída já medidos — leia antes de propor VLM para posicionamento |

O anexo no fim deste documento traz os trechos essenciais transcritos, para o caso
de o clone não estar disponível.

**Não copie a arquitetura do FrankYomik.** Ele é um servidor Go com fila Redis,
workers Python, app Flutter e extensão Chromium — 411 arquivos para um produto
diferente (ler no original com uma lupa). Nós fazemos tradução em lote com leitor
offline. Tire dele o detector e as técnicas, nada mais.

---

## FASE 0 — Pré-requisitos (rápida, obrigatória, sem dependência das outras)

### 0.1 Normalizar fim de linha

Hoje `git diff` mostra 18 arquivos com 3018 inserções e 3018 deleções: CRLF contra
LF, nenhuma mudança real de conteúdo. Enquanto isso durar, **é impossível revisar
qualquer coisa que você escrever.** Isso é pré-requisito, não faxina.

Crie `.gitattributes` na raiz:

```
* text=auto eol=lf
*.png binary
*.jpg binary
*.jpeg binary
*.webp binary
*.bmp binary
*.ttf binary
```

Depois:

```bash
git add --renormalize .
git commit -m "chore: normalise line endings across the windows/wsl boundary"
```

Confirme com `git diff --stat` limpo antes de seguir.

### 0.2 Fechar a exposição do `.env` na rede local

`cli.serve` sobe um `SimpleHTTPRequestHandler` com `directory=cfg.root` em
`0.0.0.0`. A raiz do projeto contém o `.env` com a chave da Anthropic — hoje não
existe, mas o passo 4 do setup do README manda criá-lo. Assim que existir,
`http://<ip-do-pc>:8000/.env` entrega a chave para qualquer um no Wi-Fi. O comando
`python -m http.server --directory` que o README recomenda para o celular tem o
mesmo problema.

Implemente como o resto do projeto faz: função pura, testável, separada do I/O.

Em `src/mangatl/cli.py` (ou um módulo novo `src/mangatl/serving.py`, se preferir
manter o `cli.py` fino):

```python
SERVABLE_ROOTS = ("reader", "output", "library")

def is_servable(relative_path: str) -> bool:
    """Caminhos que o leitor precisa, e nada alem disso.

    A raiz do projeto guarda o .env com a chave da API, e o servidor escuta em
    0.0.0.0 para o celular alcancar. Servir a raiz inteira publica a chave na
    rede local.
    """
```

Regras: rejeita qualquer segmento que comece com `.`; rejeita `..`; aceita só o
que estiver sob um dos `SERVABLE_ROOTS`; aceita a raiz `/` apenas para redirecionar
a `/reader/`. Ligue no handler sobrescrevendo `send_head` para responder 404 quando
`is_servable` disser não.

Escreva `src/mangatl/serving_test.py` cobrindo: `.env` negado, `.git/config`
negado, `../../etc/passwd` negado, `reader/app.js` aceito,
`output/library.json` aceito, `library/manhwa/001/p0001.jpg` aceito.

Atualize a seção "Ler no celular" do README: o comando `python -m http.server`
puro do Windows expõe a raiz — troque a recomendação por `mangatl serve` ou por
apontar o `--directory` para uma pasta que não contenha o `.env`.

**PARE.** Rode `pytest` e `git diff --stat`. Relate antes da Fase 1.

---

## FASE 1 — Detector RT-DETR-v2 atrás de uma interface

Objetivo: substituir a heurística por `ogkalu/comic-text-and-bubble-detector`
mantendo **todo o resto do pipeline intacto**, e medir contra o baseline.

O OCR continua sendo o Tesseract nesta fase. O leitor não muda nesta fase. Uma
variável de cada vez.

### 1.1 Novo tipo de detecção

Hoje `detect_bubbles` devolve `list[BBox]` e joga fora informação que o modelo dá
de graça: a classe e a confiança. Crie em `src/mangatl/models.py`:

```python
class Detection(Frozen):
    """Uma regiao detectada, antes do OCR.

    `bbox` e `text_bbox` sao diferentes de proposito. O RT-DETR devolve duas
    classes que descrevem o mesmo balao: `bubble` e o contorno inteiro e
    `text_bubble` e so o texto dentro dele. A caixa do texto da o melhor recorte
    para o OCR; a do balao da o melhor retangulo para escrever a traducao. Guardar
    so uma das duas obrigaria a escolher entre OCR pior e overlay pior.
    """

    bbox: BBox
    text_bbox: BBox
    kind: Literal["bubble", "free"]
    score: float = Field(ge=0.0, le=1.0)
```

`kind="free"` é texto fora de balão: narração sem moldura, SFX, placa. O leitor vai
tratar os dois de forma diferente na Fase 4 (caixa branca só faz sentido dentro de
balão), então a informação precisa sobreviver até o `chapter.json`.

Acrescente o campo em `ExtractedBlock` e em `TranslatedBlock`:

```python
kind: Literal["bubble", "free"] = "bubble"
```

Com default, para que `extract.json` antigo ainda valide enquanto você compara.

**Suba `PIPELINE_VERSION` de 1 para 2.** `is_page_current` invalida todas as
extrações salvas sozinho — o mecanismo já existe e é o caminho certo.

### 1.2 Pacote de detectores, no molde dos motores

Espelhe `src/mangatl/engines/`, que já resolve exatamente este problema (duas
implementações, dependências opcionais, import tardio, fábrica por nome):

```
src/mangatl/detectors/
├── __init__.py
├── base.py          # Protocol + fabrica com import tardio
├── heuristic.py     # adaptador fino sobre o detect.py atual
├── rtdetr.py        # novo
└── rtdetr_test.py   # testa as funcoes puras, sem carregar modelo
```

`base.py`, seguindo o formato de `engines/base.py`:

```python
class Detector(Protocol):
    name: str

    def detect(self, image: np.ndarray) -> list[Detection]: ...


class DetectorUnavailableError(RuntimeError):
    """Backend pedido existe mas nao pode rodar aqui (dependencia ou modelo ausente)."""


_FACTORIES: Mapping[str, Callable[[Config], Detector]] = {
    "heuristic": _make_heuristic,
    "rtdetr": _make_rtdetr,
}
```

`heuristic.py` não reimplementa nada: chama `detect.detect_bubbles` e embrulha o
resultado em `Detection`. Uma mudança pequena e justificada dentro de
`detect.py`: `_detect_enclosed_bubbles` produz `kind="bubble"` e
`_detect_text_blobs` produz `kind="free"`, e `_merge_overlapping` propaga o kind
preferindo `"bubble"` quando funde os dois. `score=1.0` para tudo (heurística não
tem confiança). O comportamento de detecção **não pode mudar** — os testes
existentes em `detect_test.py` continuam passando sem edição de expectativa.

### 1.3 `rtdetr.py`

Modelo: `ogkalu/comic-text-and-bubble-detector`. Três classes: `bubble`,
`text_bubble`, `text_free`.

Carga preguiçosa em singleton com lock, como o FrankYomik faz — carregar o modelo
custa segundos e o pipeline processa 155 páginas em sequência:

```python
_model = None
_processor = None
_device = None
_init_lock = threading.Lock()

def _get_model():
    global _model, _processor, _device
    if _model is None:
        with _init_lock:
            if _model is None:
                from transformers import RTDetrImageProcessor, RTDetrV2ForObjectDetection
                _processor = RTDetrImageProcessor.from_pretrained(MODEL_ID)
                _model = RTDetrV2ForObjectDetection.from_pretrained(MODEL_ID)
                _device = _resolve_device(...)
                _model = _model.to(_device).eval()
    return _model, _processor, _device
```

Inferência de uma página:

```python
img_pil = Image.fromarray(img_cv[:, :, ::-1])            # BGR -> RGB
inputs = processor(images=img_pil, return_tensors="pt").to(device)
with torch.no_grad():
    outputs = model(**inputs)
target_sizes = torch.tensor([(img_pil.height, img_pil.width)], device=device)
results = processor.post_process_object_detection(
    outputs, target_sizes=target_sizes, threshold=confidence
)
label = model.config.id2label[label_id.item()]
```

**Limiares separados por classe.** Copie a lógica do FrankYomik, incluindo a
justificativa:

```python
DEFAULT_CONFIDENCE = 0.35
ARTWORK_TEXT_MIN_CONFIDENCE = 0.6
# Texto sobre arte exige confianca maior: falso positivo ali desenha caixa em
# cima do desenho, o que e pior que perder um balao - balao perdido o motor
# claude ainda recupera a partir da imagem, com bbox nulo.
```

**Pareamento `bubble` × `text_bubble` — aqui divergimos do FrankYomik de
propósito.** Ele deduplica e joga a menor fora (`_deduplicate`, mantém a de maior
confiança). Nós queremos as duas, porque temos dois consumidores distintos: o OCR
quer o recorte apertado do texto e o overlay quer o retângulo do balão.

Regra, como função pura e testável:

```python
def pair_detections(raw: list[RawDetection]) -> list[Detection]:
    """Casa cada `text_bubble` com o `bubble` que o contem.

    Criterio: area do text_bubble dentro do bubble > 0.7 da area do text_bubble.
    Quando ha mais de um candidato, vence o bubble de menor area - e o balao, nao
    o painel que o contem.
    """
```

- `text_bubble` pareado → `Detection(bbox=caixa_do_balao, text_bbox=caixa_do_texto, kind="bubble", score=max(...))`
- `bubble` sem texto pareado → `Detection(bbox=b, text_bbox=b, kind="bubble")`
- `text_bubble` sem balão → `Detection(bbox=t, text_bbox=t, kind="bubble")`
- `text_free` acima de `ARTWORK_TEXT_MIN_CONFIDENCE` → `Detection(bbox=t, text_bbox=t, kind="free")`

Depois do pareamento, deduplique o que sobrou por overlap nos dois sentidos
(`overlap_ab > 0.5 or overlap_ba > 0.5`), mantendo o de maior `score` — o
FrankYomik usa overlap em vez de IoU puro e está certo: caixa aninhada tem IoU
baixo mas é duplicata.

**Faixas para imagem alta.** Implemente `split_strips` / `stitch_detections` no
molde do `webtoon/image_utils.py`: fatia a imagem em faixas de
`max_strip_height` com `strip_overlap` de sobreposição, roda o modelo em cada
faixa, remapeia as caixas somando o `y_offset` e deduplica por IoU na zona de
overlap. A imagem original nunca é tocada.

Para as nossas páginas já fatiadas (≤1568px) isso é no-op. Faça mesmo assim, por
dois motivos: desacopla a detecção do `slicing.py`, e a costura do fatiamento
deixa de ser um ponto onde balão parte ao meio para a detecção.

Funções puras a testar em `rtdetr_test.py`, **sem carregar o modelo**: `pair_detections`,
`split_strips`, `stitch_detections`, `_dedupe`, `_overlap_ratio`. Alimente com
listas de detecções cruas montadas à mão — o mesmo padrão que `claude_test.py` usa
com cliente dublê.

### 1.4 Configuração

`DetectConfig` tem `extra="forbid"`. Chave nova no `config.toml` sem campo novo no
modelo derruba o carregamento na primeira linha. Mantenha os dois em sincronia.

Use seção aninhada para não misturar com os thresholds da heurística:

```toml
[detect]
backend = "rtdetr"        # "heuristic" | "rtdetr"
# ... os thresholds existentes ficam, usados so pelo backend heuristic

[detect.rtdetr]
model_id = "ogkalu/comic-text-and-bubble-detector"
confidence = 0.35
artwork_confidence = 0.60
device = "auto"           # "auto" | "cpu" | "cuda"
max_strip_height = 1600
strip_overlap = 120
```

```python
class RtdetrConfig(Frozen):
    model_id: str = "ogkalu/comic-text-and-bubble-detector"
    confidence: float = Field(default=0.35, gt=0, lt=1)
    artwork_confidence: float = Field(default=0.60, gt=0, lt=1)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    max_strip_height: int = Field(default=1600, ge=256)
    strip_overlap: int = Field(default=120, ge=0)


class DetectConfig(Frozen):
    backend: Literal["heuristic", "rtdetr"] = "heuristic"
    # ... campos atuais, inalterados
    rtdetr: RtdetrConfig = RtdetrConfig()
```

Default `"heuristic"` no modelo e `"rtdetr"` no `config.toml`: quem não editou o
config continua com o comportamento antigo, e a máquina de desenvolvimento usa o
novo. Acrescente `--detector` em `process` e `process-all`, no molde do `--engine`,
para comparar os dois sem editar arquivo.

### 1.5 Ligar no pipeline

Em `pipeline._extract_page`, troque

```python
boxes = detect_bubbles(image, cfg.detect)
```

por um detector criado **uma vez por capítulo** (não por página — o modelo não pode
recarregar 155 vezes). Passe-o por parâmetro a partir de `extract_chapter`.

O resto muda pouco:

- `reading_order` recebe `[d.bbox for d in detections]`, assinatura intacta.
- `read_block(image, d.text_bbox, cfg.ocr)` — recorte do texto, não do balão.
- `ExtractedBlock` ganha `kind=d.kind`; `bbox` continua sendo `d.bbox`.
- `_drop_repeated_readings` continua igual.

### 1.6 `draw_boxes` precisa mostrar mais

O debug visual é o instrumento de calibração; com três categorias ele precisa de
três cores:

- **verde** numerado: virou fala (kind=`bubble`)
- **azul** numerado: virou fala (kind=`free`, texto sobre arte)
- **vermelho**: detectado e descartado pelo filtro de OCR

Escreva o score com duas casas ao lado do número. Quando `bbox != text_bbox`,
desenhe a caixa do texto com linha fina dentro da caixa do balão — é o que torna
visível se o pareamento está certo.

### 1.7 Dependências

```toml
[project.optional-dependencies]
rtdetr = ["torch>=2.4", "transformers>=4.45"]
```

Instalação, **nesta ordem** (o wheel CUDA do torch tem ~2.5GB e não serve para
nada sem GPU NVIDIA):

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e '.[rtdetr]'
```

O modelo baixa na primeira execução para `~/.cache/huggingface`, no filesystem
Linux. **Não aponte o cache para `/mnt/c`** — é o mesmo DrvFs que já quebrou o venv
e o cache do pytest.

Acrescente as verificações em `mangatl doctor`: pacote `torch`, pacote
`transformers`, device resolvido (`cpu` ou `cuda`), e se o modelo já está em
cache. Mantenha o formato `OK / FALTA` com a dica de correção.

### 1.8 README

Atualize: a seção "Calibrar a detecção de balão" passa a descrever dois backends.
A tabela de sintoma→ajuste atual vale só para o `heuristic` — diga isso. Para o
`rtdetr` a calibração é outra: `confidence` e `artwork_confidence`, mais nada.

**PARE.** Rode `pytest` e relate. Não siga para a Fase 2 sem o `pytest` verde.

---

## FASE 2 — Medir contra o baseline (portão de decisão)

Esta fase não escreve código de produção. Ela decide se a Fase 3 acontece.

### 2.1 Script de comparação

`scripts/compare_extractions.py <baseline.json> <novo.json>`, imprimindo:

- total de blocos, antes e depois
- páginas com zero falas, antes e depois
- páginas que **ganharam** falas e quantas
- páginas que **perderam** falas (regressão — é o número que importa mais)
- distribuição de `kind` no novo
- confiança média do OCR, antes e depois
- 10 páginas com maior ganho e 10 com maior perda, por nome de arquivo

### 2.2 Executar

```bash
cp output/manhwa/001/extract.json output/manhwa/001/extract.baseline.json   # se ainda nao fez
mangatl process library/manhwa/001 --dry-run --debug-boxes --detector rtdetr
python scripts/compare_extractions.py \
    output/manhwa/001/extract.baseline.json \
    output/manhwa/001/extract.json
```

### 2.3 Ler os PNGs

Abra os debug de pelo menos 15 fatias que estavam com zero falas no baseline. A
pergunta é uma só: **há balão com texto legível sem caixa nenhuma?**

- Se não há — as 85 fatias vazias são arte pura e o baseline já estava certo.
- Se há e agora tem caixa — o RT-DETR resolveu.
- Se há e continua sem caixa — vá para a Fase 3.

### 2.4 Critérios

| Resultado | Decisão |
|---|---|
| Fatias vazias caem bem e regressões ≈ 0 | Commit. Fase 3 não acontece. |
| Ganha balões mas perde outros | Ajuste `confidence` antes de concluir. Só isso. |
| Balões coloridos ou sem borda continuam invisíveis | Fase 3. |
| Detecta bem mas o Tesseract produz lixo no recorte | Fase 3 (o EasyOCR resolve os dois). |

Relate os números e a decisão. **Não comece a Fase 3 sem confirmação humana.**

---

## FASE 3 — Condicional: detecção text-first com EasyOCR

Só execute se a Fase 2 apontar para cá.

A inversão: em vez de achar o balão e depois ler o texto dentro, **acha o texto
primeiro** com um detector neural e infere o balão em volta do cluster. É o que o
FrankYomik faz para webtoon coreano, e o motivo dele é o nosso motivo.

No nosso caso o ganho é maior que no dele: nossa fonte é inglês, e o EasyOCR
**detecta e lê na mesma passada** com `easyocr.Reader(["en"])`. Isso substitui
`detect.py` e `ocr.py` de uma vez — e mata o erro que o README documenta como
insolúvel, o Tesseract lendo `SO` como `50` em fonte estilizada.

Estrutura: novo backend `detectors/easyocr_first.py`, terceiro valor de
`[detect] backend`. Os outros dois continuam funcionando.

**Três passes na mesma imagem**, copiando `webtoon/ocr.py`:

1. imagem original
2. cinza com contraste realçado — pega texto estilizado, com contorno colorido, gradiente, sombra
3. invertido + CLAHE, com limiar de confiança mais baixo — pega texto claro sobre fundo escuro

Mescla por IoU, e resgata detecção de baixa confiança que esteja **perto** de uma
detecção válida. Esse resgate é o que recupera a segunda linha de um balão.

**Clusterização** (`cluster_detections` + `_should_merge`): agrupa linhas por
proximidade vertical (`gap` configurável, 40px na referência dele) exigindo
sobreposição horizontal. Linhas do mesmo balão ficam juntas; balões diferentes não
se fundem.

**Fronteira do balão em três níveis** (`find_bubble_boundary`), do melhor para o
que sempre funciona:

1. contorno por Canny em volta do texto — balão com borda escura clara
2. flood fill a partir da área do texto — balão colorido ou sem contorno
3. bbox do cluster com padding — sempre funciona

Com a salvaguarda `_spans_image`: rejeita fronteira que cubra mais de 70% da
largura **e** da altura, que é artefato de borda da imagem, não balão.

**Cor de fundo por mediana** (`_sample_background`): amostra uma faixa de 10px ao
redor do texto e tira a mediana (robusta ao traço das letras). Isso é o que permite
o leitor pintar a caixa da cor do balão em vez de branco — aproveite e persista no
`Detection` e no `ExtractedBlock`, porque resolve metade do limite conhecido nº 1
do leitor sem precisar de inpainting.

Nesta fase o `is_usable` do Tesseract sai do caminho: o EasyOCR já devolve
confiança própria. Mantenha um filtro equivalente, mas recalibre — com detecção
confiável, `min_confidence = 45` passa a descartar fala boa. Comece em 30 e meça.

Dependência: `easyocr>=1.7,<2` em novo extra `[project.optional-dependencies]`.
Ele arrasta torch, que a Fase 1 já instalou.

Refaça a Fase 2 inteira comparando os três backends.

---

## FASE 4 — Fora do escopo deste plano (não execute)

Anotado para não virar escopo acidental de uma fase anterior:

- **Inpainting com LaMa** (`simple-lama-inpainting`, ver `kindle/inpainter.py`):
  apaga o texto original de verdade, em vez de tapar com retângulo branco.
- **Caixa colorida no leitor**: usar o `kind` e a cor de fundo amostrada para
  parar de pintar branco em balão colorido e em SFX.
- **Salvar tradução por chunk**: hoje `translate_chapter` só grava no fim; um erro
  na décima de treze requisições descarta as nove que já custaram API.
- **Service worker**: `chapter.*.json` está em cache-first como imutável, mas
  reprocessar reescreve o arquivo e o celular fica preso na versão velha.

---

## Anexo — trechos de referência do FrankYomik

### A. Carga e classes do RT-DETR-v2

```python
# server/kindle/bubble_detector.py
MODEL_ID = "ogkalu/comic-text-and-bubble-detector"
DEFAULT_CONFIDENCE = 0.35
# Artwork text (SFX, narration) needs higher confidence — false positives
# draw over artwork and are more damaging than missed speech bubbles.
ARTWORK_TEXT_MIN_CONFIDENCE = 0.6

img_rgb = img_cv[:, :, ::-1]
img_pil = Image.fromarray(img_rgb)
inputs = processor(images=img_pil, return_tensors="pt").to(device)
with torch.no_grad():
    outputs = model(**inputs)
target_sizes = torch.tensor([(img_pil.height, img_pil.width)], device=device)
results = processor.post_process_object_detection(
    outputs, target_sizes=target_sizes, threshold=confidence)

for score, label_id, box in zip(results[0]["scores"], results[0]["labels"],
                                results[0]["boxes"]):
    label = model.config.id2label[label_id.item()]
    if label in ("bubble", "text_bubble"):
        det_type, is_artwork = "speech_bubble", False
    else:
        det_type, is_artwork = "artwork_text", True
    if is_artwork and score.item() < ARTWORK_TEXT_MIN_CONFIDENCE:
        continue
```

### B. Dedup por overlap nos dois sentidos (não IoU puro)

```python
def _overlap_ratio(a, b) -> float:
    """Fraction of bbox 'a' that overlaps with bbox 'b'."""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    return inter / area_a if area_a > 0 else 0.0

# mantem o de maior confianca; caixa aninhada tem IoU baixo mas e duplicata
for det in sorted(detections, key=lambda d: d["score"], reverse=True):
    if any(_overlap_ratio(det["bbox"], k["bbox"]) > 0.5
           or _overlap_ratio(k["bbox"], det["bbox"]) > 0.5 for k in kept):
        continue
    kept.append(det)
```

### C. Faixas com overlap para imagem alta

```python
# server/webtoon/image_utils.py
def split_tall_image(img, max_height=2000, overlap=100) -> list[tuple[np.ndarray, int]]:
    h, w = img.shape[:2]
    if h <= max_height:
        return [(img, 0)]
    strips, y = [], 0
    while y < h:
        y_end = min(y + max_height, h)
        strips.append((img[y:y_end].copy(), y))
        if y_end >= h:
            break
        y = y_end - overlap
    return strips

# stitch: soma y_offset em cada bbox e deduplica por IoU 0.5 na zona de overlap
```

### D. Fallback em três níveis da fronteira do balão

```python
# server/webtoon/bubble_detector.py
"""Three-level fallback:
     Level 3: Edge-based contour detection (clear-outlined bubbles)
     Level 2: Flood fill from text area (colored/outline-less bubbles)
     Level 1: Padded text cluster bbox (always works)
"""

def _spans_image(bbox, img_w, img_h, threshold=0.7) -> bool:
    """Reject boundaries that span most of the image (likely image-edge artifacts).
    A single speech bubble never spans 70%+ in both directions simultaneously."""
```

### E. Cor de fundo por mediana

```python
def _sample_background(img_cv, text_bbox) -> tuple[int, int, int]:
    """Sample the dominant background color from a band around the text area."""
    band = 10
    # faixas acima, abaixo, esquerda e direita do texto
    pixels = np.concatenate([r.reshape(-1, 3) for r in regions if r.size > 0])
    median = np.median(pixels, axis=0).astype(int)   # mediana: robusta ao traco das letras
    return (int(median[2]), int(median[1]), int(median[0]))
```

### F. Becos sem saída já medidos — leia antes de propor VLM

```
# server/tests/ab_sfx_detection.py
CONCLUSION (2026-02-25):
  None of the 4 approaches reliably detect large artistic brush-stroke SFX.
  - A (current EasyOCR): catches nothing useful on these pages
  - B (aggressive EasyOCR): floods with garbage (@, $, {, %) — worse than A
  - C (VLM): correctly read 1/5 pages, partially 1/5, wrong on 3/5.
    Slow: 10-24s per page.
  - D (hybrid): VLM text same quality as C, contour positioning unreliable
    (picks background regions, panel borders)
```

Conclusão para nós: **não peça bbox ao modelo de linguagem.** Detector para
posição, LLM para texto e tom. Nosso motor `claude` já cobre o caso da fala que o
detector perdeu, devolvendo-a com `bbox` nulo — essa é a rede de segurança, e ela
já existe.

---

## Checklist final

- [ ] Fase 0.1: `.gitattributes` criado, `git add --renormalize`, `git diff --stat` limpo
- [ ] Fase 0.2: `is_servable` implementada e testada; `.env` retorna 404; README corrigido
- [ ] `output/manhwa/001/extract.baseline.json` salvo
- [ ] `PIPELINE_VERSION` em 2
- [ ] `Detection` e `kind` nos modelos
- [ ] `detectors/` com `base`, `heuristic`, `rtdetr`
- [ ] `heuristic` produz resultado idêntico ao de hoje; `detect_test.py` passa sem editar expectativa
- [ ] Funções puras do `rtdetr` testadas sem carregar o modelo
- [ ] `[detect] backend` e `[detect.rtdetr]` no `config.toml` **e** no `DetectConfig`
- [ ] `--detector` em `process` e `process-all`
- [ ] Modelo carregado uma vez por capítulo, não por página
- [ ] `draw_boxes` com três cores, score e caixa de texto interna
- [ ] `mangatl doctor` verifica torch, transformers, device e cache do modelo
- [ ] `pytest` verde; `node --test reader/overlay.test.js` verde (16 testes)
- [ ] `compare_extractions.py` escrito e executado; números relatados
- [ ] README atualizado nas duas seções
