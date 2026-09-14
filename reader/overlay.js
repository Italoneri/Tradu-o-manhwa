/* Posicao e tamanho do texto traduzido sobre a fatia.

   Tudo aqui e puro e em unidades relativas. A fatia e exibida numa largura que
   depende da tela - 998px de origem viram ~430px no celular e ~1100px no PC - e
   nenhuma coordenada em pixel sobrevive a isso. A bbox vira porcentagem da
   pagina, e o tamanho da fonte vira `cqw`: 1cqw e 1% da largura da fatia. Com
   isso o overlay acompanha qualquer largura sem recalcular nada no resize. */

export const FONT_MAX_CQW = 4.0;
/** Teto temporario. Medido em output/manhwa/001, 81 dos 88 blocos com bbox saem
    travados neste valor: a conta por area pede 15-18cqw para balao grande com
    poucos caracteres, porque ela responde "que fonte preenche a caixa" e nao "que
    fonte tem o tamanho do letreiramento". O original medido na amostra e ~3.9cqw.
    A Fase 2 substitui a estimativa pelo corpo real da fonte original e este teto
    volta a ser so uma trava de sanidade. */

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

  return clamp(Math.min(byArea, byHeight), FONT_FLOOR_CQW, FONT_MAX_CQW);
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
