# Plano de execução: corrigir o tamanho da fonte e a caixa branca do overlay

Documento de trabalho para um agente de código. Leia **tudo** antes de escrever a
primeira linha. As fases são sequenciais e cada uma tem um portão de parada.

Este plano é **independente** do `PLANO-DETECCAO.md` e pode ser executado antes,
depois ou entre as fases dele. O único ponto de contato está anotado na seção 6.

---

## 0. Contexto obrigatório

Projeto `mangatl`: traduz capítulos de manhwa EN→PT em lote e serve um leitor web
offline. Leia `README.md` antes de mexer em qualquer coisa — em especial a seção
"O leitor não mostra as fatias", que documenta as decisões de geometria do
overlay.

**Onde rodar.** O projeto mora no Windows em
`C:\Users\Perdido\.antigravity\tradução`; o WSL vê a mesma pasta em
`/mnt/c/Users/Perdido/.antigravity/tradução`. Python roda no WSL:

```bash
cd "/mnt/c/Users/Perdido/.antigravity/tradução"
source ~/.venvs/mangatl/bin/activate
```

Os testes do leitor rodam com Node, sem venv:

```bash
node --test reader/overlay.test.js     # 16 testes hoje
```

**Estilo do código — siga, não invente um novo.**

- Comentários e docstrings em português **sem acentos** (`traducao`, `balao`).
- Comentário explica *por que*, com medição real quando existir. O padrão deste
  repositório é `"medido neste capitulo, o pior caso cresceu 1,63x da altura do
  balao"`. Não escreva comentário que repete o que o código diz.
- `overlay.js` é módulo **puro**: sem DOM, sem I/O, testável com `node --test`.
  Tudo que precisa medir elemento pintado mora em `app.js` e entra em `overlay.js`
  como callback (`fitFontSize({ overflows })` já é assim). Mantenha essa fronteira.
- Modelos pydantic `frozen=True, extra="forbid"`. Campo novo com default, para não
  invalidar JSON antigo enquanto você compara.
- Teste ao lado do módulo, sufixo `_test.py`.

**Não faça:** reformatação em massa, troca de biblioteca, mudança na detecção, na
tradução ou no fatiamento. Este plano toca OCR (para colher um dado que já é
buscado e descartado), modelos, e o leitor. Nada mais.

---

## 1. O sintoma

Comparação lado a lado feita pelo usuário, numa página com três balões redondos
("CALL ME BY MY NAME TOO.", "I REFUSE.", "ALRIGHT."):

1. **A fonte traduzida é cerca de duas vezes maior que o letreiramento original.**
2. **A caixa branca é muito maior que o texto** e apaga o contorno do balão — no
   balão de cima ela cobre a área inteira e só sobra o arco de baixo.

Os dois são bugs distintos com a mesma causa raiz: **o overlay dimensiona tudo a
partir da caixa do balão, e nunca a partir do texto.**

---

## 2. O diagnóstico, medido

### 2.1 A estimativa de fonte é uma constante disfarçada

`reader/overlay.js`:

```js
export const FONT_MAX_CQW = 6;

export function estimateFontCqw(bbox, page, text) {
  const characters = Math.max(text.length, 1);
  const width  = percent(bbox.w, page.width);
  const height = percent(bbox.h, page.width);
  const byArea   = Math.sqrt((width * height) / (AVG_CHAR_WIDTH * LINE_HEIGHT * characters));
  const byHeight = height / LINE_HEIGHT;
  return clamp(Math.min(byArea, byHeight), FONT_FLOOR_CQW, FONT_MAX_CQW);
}
```

Rodando essa conta sobre os 88 blocos com bbox de `output/manhwa/001/chapter.free.json`:

| Resultado | Blocos |
|---|---|
| Travados no teto (6.0 cqw) | **81 de 88 — 92%** |
| No piso (2.8 cqw) | 0 |
| Entre piso e teto | 7 |

Exemplos do que a conta *pediu* antes do `clamp`:

```
raw= 18.6cqw  byArea= 18.6  byHeight= 34.0  bbox 45.0% x 39.1%   8 chars  'Ahahaha!'
raw= 17.1cqw  byArea= 17.1  byHeight= 38.8  bbox 45.8% x 44.6%  11 chars  'Vim ver-te.'
raw= 16.0cqw  byArea= 16.0  byHeight= 18.1  bbox 31.1% x 20.8%   4 chars  'Sim.'
```

`estimateFontCqw` não estima nada em 92% dos casos: ela satura e o `FONT_MAX_CQW`
vira o tamanho real de quase toda fala. O `fitFontSize` depois só reduz quando
transborda — e num balão grande com 8 caracteres nada transborda, então 6cqw fica.

**Por que a conta erra.** Ela responde "que fonte *preenche* esta caixa?". Para um
balão redondo com uma palavra, a resposta honesta é "gigante". A pergunta certa é
outra: **que fonte tem o mesmo tamanho do letreiramento original?** O balão é
grande porque o desenhista deixou ar em volta do texto, não porque o texto é grande.

Medindo na amostra: o original tem cerca de 3.9 cqw de corpo; o overlay pinta 6.0.
Bate com o "quase o dobro" relatado.

### 2.2 A caixa branca é a bbox do balão, não a do texto

`reader/app.js`:

```js
const box = `left:${rect.left}%;top:${rect.top}%;width:${rect.width}%;--h:${rect.height}%`;
```

`reader/style.css`:

```css
.bubble {
  min-height: var(--h);
  background: #fff;
  box-shadow: 0 0 0 2px #fff;
  border-radius: 8px;
}
```

A caixa recebe **a largura e a altura mínima da bbox inteira**. Num balão redondo a
bbox é o quadrado circunscrito: o texto ocupa talvez 60% da largura e 25% da
altura dela, e os outros 75% de branco são área que não precisava ser tapada.
Some-se o `box-shadow` de 2px para fora e o contorno do balão desaparece por
completo.

O README já registra isso como limite conhecido ("Não há inpainting"), mas o
diagnóstico ali está incompleto: o problema não é só a falta de inpainting, é que
a caixa cobre uma área várias vezes maior que a necessária.

### 2.3 O dado que resolve os dois já está sendo buscado e jogado fora

`src/mangatl/ocr.py`:

```python
data = pytesseract.image_to_data(
    crop, lang=cfg.lang, config=f"--psm {cfg.psm}", output_type=Output.DICT
)

words = [
    (word, float(conf))
    for word, conf in zip(data["text"], data["conf"], strict=True)
    if word.strip() and float(conf) >= 0
]
```

`image_to_data` devolve, por palavra: `text`, `conf`, **`left`, `top`, `width`,
`height`**. O código lê duas colunas e descarta quatro. Dessas quatro saem
exatamente as duas informações que faltam:

- **união das caixas de palavra** = região que o texto **original** ocupa → é o que
  a caixa branca precisa cobrir, e só isso;
- **mediana da altura das caixas de palavra** = corpo do letreiramento original →
  é o tamanho de fonte certo, sem estimativa e sem teto arbitrário.

Não custa nenhuma chamada nova, nenhuma dependência e nenhum modelo.

---

## FASE 1 — Alívio imediato, só no leitor (sem reprocessar nada)

Objetivo: tornar a página legível hoje, com os JSONs que já existem. É paliativo e
o código deve dizer isso. Não invente heurística nova aqui — a Fase 2 traz o dado
de verdade.

### 1.1 Baixar o teto

`FONT_MAX_CQW` de `6` para `4.0`.

Comentário obrigatório, no estilo do arquivo:

```js
export const FONT_MAX_CQW = 4.0;
/** Teto temporario. Medido em output/manhwa/001, 81 dos 88 blocos com bbox saem
    travados neste valor: a conta por area pede 15-18cqw para balao grande com
    poucos caracteres, porque ela responde "que fonte preenche a caixa" e nao "que
    fonte tem o tamanho do letreiramento". O original medido na amostra e ~3.9cqw.
    A Fase 2 substitui a estimativa pelo corpo real da fonte original e este teto
    volta a ser so uma trava de sanidade. */
```

### 1.2 A caixa branca encolhe até o texto

A caixa deixa de preencher a bbox e passa a envolver o texto, **centrada no balão**
— que é onde o texto original está.

`style.css`:

```css
/* A bolha e so ancora de posicao: nao pinta nada. Centrar o filho sobre a bbox
   poe o branco onde o texto original esta, no meio do balao. */
.bubble {
  position: absolute;
  z-index: 1;
  display: grid;
  place-items: center;
  min-height: var(--h);
  font-size: calc(var(--size) * 1cqw);
  /* ... as demais propriedades de tipografia continuam aqui ... */
}

/* O branco acompanha o texto, nao a bbox. Num balao redondo a bbox e o quadrado
   circunscrito, e tapar o quadrado inteiro apaga o contorno do balao junto. */
.bubble .t {
  width: max-content;
  max-width: 100%;
  background: #fff;
  box-shadow: 0 0 0 2px #fff;
  border-radius: 0.7em;      /* em, nao px: acompanha o corpo da fonte */
  padding: 0.15em 0.5em;
}
```

Tire `background`, `box-shadow` e `border-radius` do `.bubble` — eles migram para
o `.t`. `min-height: var(--h)` fica: é o que mantém a caixa centrada sobre o balão
e o que permite crescer quando a fala não cabe.

### 1.3 Ajustar `fitSlice` ao novo alvo

Em `app.js`, `fitSlice` mede `inner.offsetHeight` contra `limit`, a altura da bbox.
Com o branco no `.t`, `inner` continua sendo o elemento medido — confira que
`bubble.firstElementChild` ainda é o `.t` e que `offsetHeight` continua refletindo
o texto e não a ancora. Se o `place-items: center` mudar a medição, corrija aqui e
não em `overlay.js`, que deve permanecer puro.

### 1.4 Testes

`overlay.test.js` cobre geometria pura e não deve quebrar. Se algum teste fixar
`FONT_MAX_CQW = 6` como literal, troque pela constante importada — teste que
repete o valor em vez de importá-lo é o motivo de a mudança doer.

**PARE.** Rode `node --test reader/overlay.test.js`, abra o leitor no mesmo
capítulo e compare com a imagem original. Relate antes da Fase 2.

---

## FASE 2 — A correção de verdade: medir o texto original

Objetivo: o overlay passa a usar o corpo e a posição do letreiramento original, em
vez de estimar a partir do balão.

### 2.1 Colher as caixas de palavra no OCR

`src/mangatl/ocr.py`. Duas mudanças, mantendo a separação entre função pura e
chamada ao binário que o módulo já respeita.

Primeiro, `prepare_crop` precisa devolver a origem do recorte, senão não há como
mapear coordenada de volta. Hoje ela calcula e descarta:

```python
top  = max(0, box.y - cfg.padding)
left = max(0, box.x - cfg.padding)
```

Devolva `(crop, left, top)` ou introduza um pequeno `CropWindow` congelado. Escolha
uma e ajuste os testes existentes de `prepare_crop`.

Segundo, uma função **pura** para o mapeamento, testável sem Tesseract:

```python
def words_to_page_bbox(
    words: Sequence[WordBox],
    *,
    origin_x: int,
    origin_y: int,
    upscale: int,
) -> tuple[BBox, int] | None:
    """Uniao das caixas de palavra e corpo da fonte original, em pixels da pagina.

    O Tesseract mede no recorte ampliado; dividir por `upscale` e somar a origem
    do recorte devolve a coordenada da pagina. A mediana da altura, e nao a media:
    uma palavra com acento ou com descendente mede mais alto que o corpo, e basta
    uma para puxar a media de uma fala de tres palavras.

    Devolve None quando nenhuma palavra sobreviveu ao filtro - o chamador cai no
    comportamento antigo.
    """
```

`WordBox` é um dataclass/NamedTuple simples com `left, top, width, height`.

Em `read_block`, colete `data["left"]`, `data["top"]`, `data["width"]` e
`data["height"]` junto com `text` e `conf`, **filtrando pelos mesmos critérios já
usados** (`word.strip()` e `conf >= 0`), e devolva também o resultado de
`words_to_page_bbox`. A assinatura passa a ser algo como:

```python
def read_block(image, box, cfg) -> tuple[str, float, BBox | None, int | None]:
```

Se preferir, devolva um `BlockReading` congelado — é mais legível que uma tupla de
quatro e combina com o resto do projeto. Ajuste `pipeline._extract_page` e
`ocr_test.py`.

Confirme por `print(sorted(data.keys()))` numa execução real que as chaves existem
nesta versão do pytesseract antes de escrever o código em cima delas.

### 2.2 Persistir nos modelos

`src/mangatl/models.py`, em `ExtractedBlock` e em `TranslatedBlock`:

```python
text_bbox: BBox | None = None
"""Regiao ocupada pelo texto ORIGINAL dentro do balao.

Diferente de `bbox`, que e o balao inteiro. Num balao redondo a bbox e o quadrado
circunscrito e o texto ocupa uma fracao dela - tapar a bbox apaga o contorno do
balao sem necessidade. None quando o OCR nao devolveu caixa de palavra."""

source_font_px: int | None = None
"""Corpo do letreiramento original, em pixels da pagina.

Mediana da altura das caixas de palavra do Tesseract. E o unico sinal direto do
tamanho em que a pagina foi letrada; sem ele o leitor so consegue estimar a partir
do balao, e balao grande nao significa texto grande."""
```

Ambos com default `None`, para que `extract.json` e `chapter.*.json` antigos
continuem validando durante a comparação.

Propague nos dois motores:

- `engines/argos.py`, `_translate_page`: copiar `text_bbox` e `source_font_px` do
  `ExtractedBlock` para o `TranslatedBlock`.
- `engines/claude.py`, `_assemble`: hoje ele monta `boxes = {block.id: block.bbox}`.
  Monte um dicionário do bloco inteiro e copie os três campos. Fala nova
  (`block_id` nulo) fica com os três em `None` — já é o comportamento correto.

**Suba `PIPELINE_VERSION`.** Se o `PLANO-DETECCAO.md` já tiver subido para 2, vá
para 3. `is_page_current` invalida as extrações salvas sozinho.

### 2.3 O leitor usa o corpo original

`reader/overlay.js` — assinatura nova, mantendo a antiga como fallback:

```js
/** Corpo da fonte em `cqw`, preferindo o tamanho do letreiramento original.
 *
 * `sourceFontPx` vem do OCR: e a mediana da altura das caixas de palavra do texto
 * original, medida na propria pagina. Convertido em fracao da largura, ele da o
 * corpo em que a pagina foi letrada - que e o alvo. A estimativa por area so
 * responde "que fonte preenche a caixa", e para balao grande com pouco texto ela
 * satura no teto: medido em output/manhwa/001, 81 de 88 blocos.
 *
 * Sem `sourceFontPx` (JSON anterior a esta versao do pipeline) cai na estimativa
 * antiga.
 */
export function fontCqw({ bbox, page, text, sourceFontPx }) {
  if (sourceFontPx) {
    return clamp(percent(sourceFontPx, page.width) * SOURCE_FONT_RATIO,
                 FONT_FLOOR_CQW, FONT_MAX_CQW);
  }
  return estimateFontCqw(bbox, page, text);
}
```

`SOURCE_FONT_RATIO` existe porque a altura da caixa de palavra do Tesseract é a
caixa do glifo, não o corpo tipográfico — a razão entre os dois fica perto de 1,3
para caixa alta. **Calibre com medição, não com chute:** rode numa fatia, compare o
corpo pintado com o original e registre o valor medido no comentário da constante.
Comece em `1.0` e ajuste uma vez, com o número na mão.

`FONT_MAX_CQW` volta a ser trava de sanidade, não valor de operação. Depois de
calibrar, verifique quantos blocos ainda encostam nele — se for mais que uns
poucos, a razão está errada.

### 2.4 A caixa branca cobre o texto original

`reader/app.js`, `bubbleHtml`: quando `block.text_bbox` existir, posicione e
dimensione a caixa por ela; senão, pela `bbox`, como hoje.

```js
// A caixa cobre o texto original, nao o balao. Com text_bbox isso e exato; sem
// ela, a bbox do balao e o unico alvo disponivel e sobra branco.
const target = block.text_bbox ?? block.bbox;
```

Mantenha o `width: max-content` da Fase 1 com `min-width` na largura do
`text_bbox`: se a tradução ficar mais curta que o original, o branco precisa cobrir
o que sobrou do inglês embaixo. Esse é o caso que a Fase 1 sozinha não resolve.

Uma margem pequena em volta do `text_bbox` (2–3% da largura dele) evita deixar
serifa de fora por arredondamento.

O `fitSlice` passa a medir contra a altura da **bbox do balão** (o texto pode
crescer até encher o balão antes de ultrapassá-lo), mas parte do `text_bbox` para
posicionar. São dois papéis diferentes e o comentário deve dizer isso.

### 2.5 Testes

Python, sem Tesseract: `words_to_page_bbox` com caixas montadas à mão — uma
palavra, várias palavras em duas linhas, `upscale=3`, origem deslocada, lista
vazia devolvendo `None`, e a mediana ignorando uma caixa alta isolada.

JavaScript, sem DOM: `fontCqw` com `sourceFontPx` presente e ausente, no piso, no
teto, e `page.width` variando (o resultado em `cqw` não pode mudar com a largura de
exibição — é o invariante de que todo o overlay depende).

**PARE.** Rode `pytest` e `node --test reader/overlay.test.js`. Relate.

---

## FASE 3 — Verificação visual (obrigatória, é o único juiz)

Nenhum teste unitário sabe se a página ficou boa. Esta fase não escreve código de
produção.

```bash
mangatl process library/manhwa/001 --engine free --force
mangatl serve
```

Abra `http://localhost:8000/reader/?series=manhwa&chapter=001` e responda, por
escrito, sobre pelo menos 10 falas:

1. O corpo da fonte traduzida é parecido com o do letreiramento original?
2. A caixa branca ainda é visivelmente maior que o texto?
3. O contorno do balão sobrevive à caixa?
4. Sobrou texto em inglês aparecendo por fora da caixa? (fala curta em português
   cobrindo fala longa em inglês — é a regressão que esta mudança pode introduzir)
5. Alguma fala foi cortada?

Um script de diagnóstico ajuda a achar os casos ruins sem caçar no olho:
`scripts/report_overlay.py` lendo o `chapter.json` e listando as 15 falas com maior
razão `bbox.area / text_bbox.area` (onde mais branco sobra) e as 15 com maior razão
`len(pt) / len(en)` (onde mais provável transbordar).

Relate os números e junte dois recortes: um balão bom e o pior caso.

---

## 6. Ponto de contato com o PLANO-DETECCAO.md

O outro plano introduz um `Detection.text_bbox` vindo da classe `text_bubble` do
RT-DETR. É o mesmo conceito com outra fonte de dado, e os dois convivem:

- **Posição** — quando os dois existirem, prefira o `text_bbox` do OCR: ele é a
  união das caixas de palavra de fato lidas, mais apertado que a caixa da rede.
- **Corpo da fonte** — `source_font_px` só sai do OCR. O RT-DETR não mede altura de
  glifo, então este campo continua vindo daqui mesmo depois daquela migração.
- **Se a Fase 3 daquele plano acontecer** (EasyOCR text-first), o detector devolve
  polígono por linha de texto: a união dá `text_bbox` e a mediana da altura dos
  polígonos dá `source_font_px`. Os dois campos sobrevivem à troca; só muda quem os
  preenche. Não os acople ao Tesseract no modelo nem no leitor.

Ordem sugerida: **este plano primeiro.** Ele é menor, não tem dependência nova, e
conserta o que está visível na tela hoje. Detecção melhor com overlay quebrado
continua parecendo quebrado.

---

## 7. Fora do escopo (não execute)

- Inpainting com LaMa para apagar o texto original de verdade.
- Amostrar a cor do balão para parar de pintar branco em balão colorido.
- `text-transform: uppercase` no `.bubble`: caixa alta em português ocupa mais
  largura e agrava o transbordo, e as regras do motor `claude` em
  `engines/claude.py` já mandam **não** replicar a caixa alta do original. Vale
  reavaliar — mas depois, e com medição, não junto com esta mudança.

---

## Checklist

- [ ] Fase 1: `FONT_MAX_CQW` em 4.0 com o comentário da medição
- [ ] Fase 1: branco migrado de `.bubble` para `.t`, com `width: max-content`
- [ ] Fase 1: `node --test reader/overlay.test.js` verde
- [ ] Fase 2: `prepare_crop` devolve a origem do recorte
- [ ] Fase 2: `words_to_page_bbox` pura e testada sem Tesseract
- [ ] Fase 2: `text_bbox` e `source_font_px` em `ExtractedBlock` e `TranslatedBlock`, com default `None`
- [ ] Fase 2: os dois motores propagam os campos novos
- [ ] Fase 2: `PIPELINE_VERSION` subiu
- [ ] Fase 2: `fontCqw` com fallback para `estimateFontCqw`
- [ ] Fase 2: `SOURCE_FONT_RATIO` calibrado por medição, com o número no comentário
- [ ] Fase 2: caixa branca posicionada por `text_bbox` com `min-width`
- [ ] Fase 2: `pytest` verde
- [ ] Fase 3: capítulo reprocessado, 5 perguntas respondidas por escrito, recortes anexados
- [ ] Quantos blocos ainda encostam em `FONT_MAX_CQW` depois da calibração? (deve ser poucos)
