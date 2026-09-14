/* Posicao e tamanho do texto traduzido sobre a fatia.

   Tudo aqui e puro e em unidades relativas. A fatia e exibida numa largura que
   depende da tela - 998px de origem viram ~430px no celular e ~1100px no PC - e
   nenhuma coordenada em pixel sobrevive a isso. A bbox vira porcentagem da
   pagina, e o tamanho da fonte vira `cqw`: 1cqw e 1% da largura da fatia. Com
   isso o overlay acompanha qualquer largura sem recalcular nada no resize. */

export const FONT_MAX_CQW = 8;
/** Trava de sanidade do corpo medido, nao valor de operacao. Medido em
    output/manhwa/001 com o corpo ja calibrado: 3 dos 88 blocos encostam nas
    travas, e os tres sao OCR quebrado - 'wre' e 'NANG' lidos sobre SFX, com caixa
    de palavra de 150px e 82px numa pagina cujo letreiramento tem 44px. Abaixo de 8
    ficariam 18, e 15 deles sao dialogo comum: o corpo desta obra da 6.1cqw. */

export const ESTIMATE_MAX_CQW = 4.0;
/** Teto da estimativa, que e coisa diferente: ela satura e precisa de teto baixo.
    Medido em output/manhwa/001, 86 dos 88 blocos com bbox saem travados aqui - a
    conta por area pede 15-18cqw para balao grande com poucos caracteres, porque
    responde "que fonte preenche a caixa" e nao "que fonte tem o tamanho do
    letreiramento". So vale para JSON anterior a esta versao do pipeline, que nao
    traz `source_font_px`. */

export const FONT_FLOOR_CQW = 2.8;
/** ~12px no mesmo celular, ~28px na resolucao de origem - perto do tamanho em que
    a pagina foi letrada. Descer mais deixaria a fala ilegivel no celular, e como
    a caixa cresce quando o texto nao cabe, o custo de um piso alto e arte tapada,
    nao fala cortada. */

export const FONT_SHRINK_RATIO = 0.88;
/** Passo do encolhimento. Mais agressivo pula o tamanho bom; mais suave custa iteracoes. */

const LINE_HEIGHT = 1.15;
const AVG_CHAR_WIDTH = 0.55;
/** Largura media de caractere como fracao do tamanho da fonte, medida em caixa
    alta - que e como fala de HQ costuma ser composta. */

function percent(value, total) {
  // Multiplica antes de dividir: 300/1000*100 da 30.000000000000004 em ponto
  // flutuante, e esse lixo vaza para o style inline.
  return (value * 100) / total;
}

function clamp(value, low, high) {
  return Math.min(Math.max(value, low), high);
}

/** Retangulo da bbox em porcentagem da pagina, pronto para `style` inline. */
export function bubbleRect(bbox, page) {
  const left = clamp(percent(bbox.x, page.width), 0, 100);
  const top = clamp(percent(bbox.y, page.height), 0, 100);
  return {
    left,
    top,
    width: clamp(percent(bbox.w, page.width), 0, 100 - left),
    height: clamp(percent(bbox.h, page.height), 0, 100 - top),
  };
}

/** Tamanho de fonte em `cqw` que deve caber o texto na bbox.
 *
 * Resolve para `f` a condicao "as linhas empilhadas cabem na altura":
 *
 *     linhas        = caracteres / (largura / (AVG_CHAR_WIDTH * f))
 *     altura ocupada = linhas * LINE_HEIGHT * f  <=  altura
 *
 * O que da `f = sqrt(largura * altura / (AVG_CHAR_WIDTH * LINE_HEIGHT * n))`.
 *
 * E uma estimativa: ignora onde cada palavra quebra, entao erra para cima em
 * texto de palavras longas. `fitFontSize` corrige medindo o que foi pintado.
 */
export function estimateFontCqw(bbox, page, text) {
  // Uma fala sempre ocupa ao menos um caractere de espaco; texto vazio nao pode
  // dividir por zero.
  const characters = Math.max(text.length, 1);

  // Os dois lados contra `page.width`, inclusive a altura: a conta precisa sair
  // na mesma unidade do resultado, e `cqw` mede a largura do container.
  const width = percent(bbox.w, page.width);
  const height = percent(bbox.h, page.width);

  const byArea = Math.sqrt((width * height) / (AVG_CHAR_WIDTH * LINE_HEIGHT * characters));

  // A conta por area ignora a altura quando o texto e curto, e manda fonte de
  // 64px para uma faixa de 45px. Uma linha nunca pode ser mais alta que a caixa.
  const byHeight = height / LINE_HEIGHT;

  return clamp(Math.min(byArea, byHeight), FONT_FLOOR_CQW, ESTIMATE_MAX_CQW);
}

export const SOURCE_FONT_RATIO = 1.39;
/** Corpo tipografico como multiplo da altura da caixa de palavra do Tesseract.
    A caixa de palavra e a mancha de tinta, e `font-size` e o corpo: em caixa alta
    sem acento a tinta ocupa 0.72em, entao pintar no mesmo tamanho pede 1/0.72.
    Medido nas 88 falas deste capitulo com a Inter 600 do leitor, palavra a palavra:
    mediana 1.389, com o primeiro e o terceiro quartil no mesmo valor - so as falas
    com acento maiusculo saem do grupo, porque o acento estica a mancha. */

export const TEXT_MARGIN = 0.025;
/** Folga em volta do `text_bbox`, como fracao da largura dele. A uniao das caixas
    de palavra encosta no glifo; sem folga o arredondamento para pixel de tela come
    serifa na borda. */

/** Corpo da fonte em `cqw`, preferindo o tamanho do letreiramento original.
 *
 * `sourceFontPx` vem do OCR: e a mediana da altura das caixas de palavra do texto
 * original, medida na propria pagina. Convertido em fracao da largura, ele da o
 * corpo em que a pagina foi letrada - que e o alvo. A estimativa por area so
 * responde "que fonte preenche a caixa", e para balao grande com pouco texto ela
 * satura no teto: medido em output/manhwa/001, 86 de 88 blocos.
 *
 * Sem `sourceFontPx` (JSON anterior a esta versao do pipeline) cai na estimativa
 * antiga.
 */
export function fontCqw({ bbox, page, text, sourceFontPx }) {
  if (!sourceFontPx) return estimateFontCqw(bbox, page, text);
  return clamp(percent(sourceFontPx, page.width) * SOURCE_FONT_RATIO, FONT_FLOOR_CQW, FONT_MAX_CQW);
}

/** A bbox com uma folga proporcional a largura dela, ainda dentro da pagina. */
export function grownBBox(bbox, fraction = TEXT_MARGIN) {
  const margin = Math.round(bbox.w * fraction);
  const x = Math.max(0, bbox.x - margin);
  const y = Math.max(0, bbox.y - margin);
  return { x, y, w: bbox.w + (bbox.x - x) + margin, h: bbox.h + (bbox.y - y) + margin };
}

/** Onde pintar a traducao, a partir das medidas que o pipeline guardou no bloco.
 *
 * Os eixos vem de medidas diferentes porque erram de formas diferentes. Medido nas
 * 88 falas de output/manhwa/001: na horizontal o texto original esta centrado no
 * balao - o centro dele desvia 1% da folga na mediana e 5% no terceiro quartil -
 * entao a largura do balao e a caixa certa, e ela ainda deixa a traducao crescer
 * quando o portugues sai mais longo que o ingles. Na vertical o desvio chega a 38%
 * no terceiro quartil, entao ali vale a faixa medida e nao o centro do balao.
 *
 * `limit` e sempre a altura do balao: e ate onde a fala pode crescer antes de
 * tapar arte, e nao tem relacao com onde o branco comeca.
 *
 * `minWidth` cobre o texto original inteiro. Sem ele uma traducao mais curta que o
 * ingles deixaria o resto da fala original aparecendo em volta do branco.
 *
 * `overflow_bottom` e a fala que a emenda entre paginas cortou: ela comeca nesta
 * fatia e continua na seguinte. A tira nao recorta a fatia, entao somar o
 * transbordo a altura e o bastante para a caixa atravessar a emenda.
 */
export function overlayBox(block, page) {
  const bubble = bubbleRect(block.bbox, page);
  const overflow = percent(block.overflow_bottom ?? 0, page.height);
  const limit = bubble.height + overflow;

  if (!block.text_bbox) {
    return { ...bubble, height: limit, limit, minWidth: 0 };
  }

  const text = bubbleRect(grownBBox(block.text_bbox), page);
  return {
    left: bubble.left,
    top: text.top,
    width: bubble.width,
    height: text.height,
    limit,
    minWidth: bubble.width === 0 ? 0 : clamp(percent(text.width, bubble.width), 0, 100),
  };
}

/** Maior tamanho da serie decrescente que `overflows` aceita.
 *
 * `overflows` mede o elemento ja pintado - e a unica coisa que sabe onde o
 * texto de fato quebrou. A serie e fechada e termina no piso, para o laco ter
 * fim mesmo se tudo transbordar. No piso a fala continua inteira e a caixa e que
 * cresce: cortar tradução seria perder conteudo, e a caixa crescida so custa um
 * pedaco de arte.
 */
export function fitFontSize({ start, overflows, floor = FONT_FLOOR_CQW, ratio = FONT_SHRINK_RATIO }) {
  // Quantos passos cabem entre `start` e o piso. Calcular antes deixa o laco
  // visivelmente finito, em vez de depender de `ratio < 1` para terminar.
  const steps = Math.ceil(Math.log(floor / start) / Math.log(ratio));

  for (let step = 0; step < steps; step += 1) {
    const size = start * ratio ** step;
    if (!overflows(size)) return size;
  }
  return floor;
}
