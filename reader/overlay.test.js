/* Geometria e dimensionamento do overlay. Sao as unicas partes com logica
   propria: o resto de app.js e DOM, verificado no navegador. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  ESTIMATE_MAX_CQW,
  FONT_FLOOR_CQW,
  FONT_MAX_CQW,
  FONT_SHRINK_RATIO,
  SOURCE_FONT_RATIO,
  bubbleRect,
  estimateFontCqw,
  fitFontSize,
  fontCqw,
  grownBBox,
  overlayBox,
} from "./overlay.js";

const page = { width: 1000, height: 2000 };

describe("bubbleRect", () => {
  const cases = [
    {
      name: "converte a bbox em porcentagem da pagina",
      bbox: { x: 100, y: 200, w: 300, h: 120 },
      expected: { left: 10, top: 10, width: 30, height: 6 },
    },
    {
      name: "mantem o canto superior esquerdo na origem",
      bbox: { x: 0, y: 0, w: 500, h: 100 },
      expected: { left: 0, top: 0, width: 50, height: 5 },
    },
    {
      name: "encosta na borda oposta sem passar dela",
      bbox: { x: 500, y: 1000, w: 500, h: 1000 },
      expected: { left: 50, top: 50, width: 50, height: 50 },
    },
  ];

  for (const { name, bbox, expected } of cases) {
    it(name, () => {
      assert.deepEqual(bubbleRect(bbox, page), expected);
    });
  }

  it("recorta bbox que passa da pagina", () => {
    // O JSON e uma fronteira: o leitor nao produz a bbox, so a consome.
    const rect = bubbleRect({ x: 900, y: 1900, w: 400, h: 400 }, page);
    assert.deepEqual(rect, { left: 90, top: 95, width: 10, height: 5 });
  });
});

describe("estimateFontCqw", () => {
  const bubble = { x: 100, y: 200, w: 300, h: 120 };

  it("encolhe quando o texto cresce", () => {
    const short = estimateFontCqw(bubble, page, "NAO");
    const long = estimateFontCqw(bubble, page, "NAO PASSARAS DAQUI HOJE DE JEITO NENHUM");
    assert.ok(long < short, `esperava ${long} < ${short}`);
  });

  it("cresce quando o balao cresce", () => {
    // Os dois baloes ficam abaixo do teto de proposito: acima dele a conta satura
    // e o crescimento some, que e o bug que FONT_MAX_CQW documenta.
    const text = "VOCE NAO VAI PASSAR";
    const small = estimateFontCqw({ x: 100, y: 200, w: 180, h: 60 }, page, text);
    const big = estimateFontCqw({ x: 100, y: 200, w: 225, h: 75 }, page, text);
    assert.ok(big > small, `esperava ${big} > ${small}`);
  });

  it("nao deixa uma linha mais alta que o balao", () => {
    // Uma linha nunca cabe inteira numa caixa da propria altura: sobra a entrelinha.
    const low = { x: 0, y: 0, w: 900, h: 36 };
    const heightCqw = (low.h * 100) / page.width;
    const size = estimateFontCqw(low, page, "!");
    assert.ok(size < ESTIMATE_MAX_CQW, `esperava menos que o teto, veio ${size}`);
    assert.ok(size * 1.15 <= heightCqw, `uma linha de ${size}cqw nao cabe em ${heightCqw}cqw`);
  });

  it("nao passa do teto com texto curto em balao grande", () => {
    assert.equal(estimateFontCqw({ x: 0, y: 0, w: 900, h: 900 }, page, "!"), ESTIMATE_MAX_CQW);
  });

  it("nao passa do piso com texto longo em balao pequeno", () => {
    const tiny = { x: 0, y: 0, w: 30, h: 20 };
    assert.equal(estimateFontCqw(tiny, page, "x".repeat(200)), FONT_FLOOR_CQW);
  });

  it("escala com a largura da pagina, nao com o pixel", () => {
    // cqw e fracao da largura da fatia: a mesma bbox proporcional da o mesmo valor.
    const half = estimateFontCqw({ x: 50, y: 100, w: 150, h: 60 }, { width: 500, height: 1000 }, "UM TEXTO");
    const full = estimateFontCqw(bubble, page, "UM TEXTO");
    assert.equal(half, full);
  });

  it("trata texto vazio sem dividir por zero", () => {
    assert.equal(estimateFontCqw(bubble, page, ""), ESTIMATE_MAX_CQW);
  });
});

describe("fontCqw", () => {
  const bubble = { x: 100, y: 200, w: 300, h: 120 };

  it("usa o corpo medido no lugar da estimativa", () => {
    // 30px numa pagina de 1000 e 3cqw. A estimativa para a mesma caixa satura no
    // teto dela; o valor medido nao depende do tamanho do balao.
    const measured = fontCqw({ bbox: bubble, page, text: "OI", sourceFontPx: 30 });
    assert.equal(measured, 3 * SOURCE_FONT_RATIO);
    assert.notEqual(measured, estimateFontCqw(bubble, page, "OI"));
  });

  it("cai na estimativa sem corpo medido", () => {
    for (const missing of [undefined, null, 0]) {
      assert.equal(
        fontCqw({ bbox: bubble, page, text: "UM TEXTO", sourceFontPx: missing }),
        estimateFontCqw(bubble, page, "UM TEXTO"),
        `sourceFontPx=${missing}`,
      );
    }
  });

  it("nao desce do piso nem sobe do teto", () => {
    const tiny = fontCqw({ bbox: bubble, page, text: "OI", sourceFontPx: 1 });
    const huge = fontCqw({ bbox: bubble, page, text: "OI", sourceFontPx: 900 });
    assert.equal(tiny, FONT_FLOOR_CQW);
    assert.equal(huge, FONT_MAX_CQW);
  });

  it("escala com a largura da pagina, nao com o pixel", () => {
    // O invariante de que todo o overlay depende: a mesma pagina em outra
    // resolucao letra no mesmo corpo relativo.
    const full = fontCqw({ bbox: bubble, page, text: "OI", sourceFontPx: 40 });
    const half = fontCqw({
      bbox: { x: 50, y: 100, w: 150, h: 60 },
      page: { width: 500, height: 1000 },
      text: "OI",
      sourceFontPx: 20,
    });
    assert.equal(half, full);
  });
});

describe("grownBBox", () => {
  it("abre a mesma folga dos dois lados", () => {
    // 2.5% de 200 e 5px, e a folga sai proporcional a largura em ambos os eixos:
    // e a largura que diz o tamanho do glifo, nao a altura da linha.
    assert.deepEqual(grownBBox({ x: 100, y: 50, w: 200, h: 40 }), {
      x: 95,
      y: 45,
      w: 210,
      h: 50,
    });
  });

  it("nao empurra a caixa para fora da pagina", () => {
    // Recortar a folga em vez de aceitar coordenada negativa: a borda esquerda
    // para em zero e a direita continua com a folga inteira.
    const grown = grownBBox({ x: 2, y: 0, w: 200, h: 40 });
    assert.equal(grown.x, 0);
    assert.equal(grown.y, 0);
    assert.equal(grown.x + grown.w, 207);
    assert.equal(grown.y + grown.h, 45);
  });

  it("aceita a folga como parametro", () => {
    assert.deepEqual(grownBBox({ x: 100, y: 100, w: 100, h: 100 }, 0.1), {
      x: 90,
      y: 90,
      w: 120,
      h: 120,
    });
  });
});

describe("overlayBox", () => {
  const bbox = { x: 100, y: 200, w: 300, h: 120 };

  it("cai no balao inteiro sem caixa de texto", () => {
    const box = overlayBox(bbox, null, page);
    assert.deepEqual(box, { left: 10, top: 10, width: 30, height: 6, limit: 6, minWidth: 0 });
  });

  it("toma a largura do balao e a faixa vertical do texto", () => {
    // O texto medido ocupa 180x60 a partir de (160, 230); com a folga de 2.5% da
    // largura ele vira 190x70 a partir de (155, 225).
    const box = overlayBox(bbox, { x: 160, y: 230, w: 180, h: 60 }, page);

    assert.equal(box.left, 10, "a esquerda continua a do balao");
    assert.equal(box.width, 30, "a largura continua a do balao");
    assert.equal(box.top, 11.25, "o topo vem da faixa de texto");
    assert.equal(box.height, 3.5, "a altura vem da faixa de texto");
  });

  it("mede o crescimento contra o balao, nao contra a faixa de texto", () => {
    const box = overlayBox(bbox, { x: 160, y: 230, w: 180, h: 60 }, page);
    assert.equal(box.limit, 6);
  });

  it("exige do branco a largura do texto original", () => {
    // 190 de 300 da 63.3%: e o quanto do balao o ingles ocupava, e o branco nao
    // pode encolher mais que isso sem deixar a fala original aparecendo.
    const box = overlayBox(bbox, { x: 160, y: 230, w: 180, h: 60 }, page);
    assert.equal(box.minWidth, (190 * 100) / 300);
  });

  it("nao pede branco mais largo que o balao", () => {
    // Texto medido mais largo que o proprio balao acontece quando a deteccao
    // aperta a bbox; 100% e o maximo que o filho pode ocupar da ancora.
    const box = overlayBox(bbox, { x: 100, y: 230, w: 400, h: 60 }, page);
    assert.equal(box.minWidth, 100);
  });
});

describe("fitFontSize", () => {
  it("devolve o inicio quando nada transborda", () => {
    assert.equal(fitFontSize({ start: 5, overflows: () => false }), 5);
  });

  it("devolve o piso quando tudo transborda", () => {
    assert.equal(fitFontSize({ start: 5, overflows: () => true }), FONT_FLOOR_CQW);
  });

  it("para no primeiro tamanho que cabe", () => {
    const tried = [];
    const fitted = fitFontSize({
      start: 5,
      overflows: (size) => {
        tried.push(size);
        return size > 4;
      },
    });

    assert.equal(fitted, 5 * FONT_SHRINK_RATIO ** 2);
    assert.deepEqual(tried, [5, 5 * FONT_SHRINK_RATIO, 5 * FONT_SHRINK_RATIO ** 2]);
  });

  it("limita o numero de tentativas", () => {
    let calls = 0;
    fitFontSize({
      start: FONT_MAX_CQW,
      overflows: () => {
        calls += 1;
        return true;
      },
    });

    const room = Math.log(FONT_FLOOR_CQW / FONT_MAX_CQW) / Math.log(FONT_SHRINK_RATIO);
    assert.ok(calls <= Math.ceil(room) + 1, `${calls} tentativas para ${room.toFixed(1)} passos`);
  });

  it("nunca devolve menos que o piso", () => {
    assert.ok(fitFontSize({ start: FONT_FLOOR_CQW / 2, overflows: () => true }) >= FONT_FLOOR_CQW);
  });
});
