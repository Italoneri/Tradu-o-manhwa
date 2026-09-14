"""OCR de um recorte de balao com Tesseract.

Tesseract na pagina inteira de HQ produz lixo: ele segmenta arte e screentone
como texto e devolve ordem errada. Aqui ele so ve um balao ja recortado, com
--psm 6 ("bloco uniforme de texto"), que e o cenario em que ele acerta.

A limpeza de texto e o mapeamento de coordenada sao funcoes puras separadas da
chamada ao binario, para poderem ser testadas sem Tesseract instalado.
"""

from __future__ import annotations

import math
import re
import shutil
from collections.abc import Sequence
from statistics import median
from typing import NamedTuple

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

from .config import OcrConfig
from .models import BBox

_WHITESPACE = re.compile(r"\s+")
_HYPHEN_BREAK = re.compile(r"(\w)-\s+(\w)")
_PIPE_AS_I = re.compile(r"(?<!\|)\|(?!\|)")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def clean_ocr_text(raw: str) -> str:
    """Corrige os erros que o Tesseract comete de forma sistematica em HQ.

    Balao quebra a frase em varias linhas, entao hifen de quebra vira palavra
    partida; e a barra vertical e a confusao mais comum com o "I" maiusculo,
    que e frequentissimo em texto de HQ em ingles (all caps).
    """
    text = _CONTROL_CHARS.sub("", raw)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _PIPE_AS_I.sub("I", text)
    return _WHITESPACE.sub(" ", text).strip()


class CropWindow(NamedTuple):
    """O recorte binarizado e o canto dele na pagina.

    A origem viaja junto porque o Tesseract mede dentro do recorte ampliado: sem
    saber de onde ele saiu, coordenada de palavra nao volta para a pagina.
    """

    image: np.ndarray
    left: int
    top: int


class WordBox(NamedTuple):
    """Caixa de uma palavra, nas coordenadas do recorte ampliado."""

    left: int
    top: int
    width: int
    height: int


class BlockReading(NamedTuple):
    """O que o OCR sabe sobre um balao depois de ler.

    `text_bbox` e `source_font_px` sao None quando nenhuma palavra passou pelo
    filtro - nao ha o que medir, e o chamador segue sem eles.
    """

    text: str
    confidence: float
    text_bbox: BBox | None = None
    source_font_px: int | None = None


def prepare_crop(image: np.ndarray, box: BBox, cfg: OcrConfig) -> CropWindow:
    """Recorta, amplia e binariza o balao para a altura de caixa que o Tesseract espera."""
    height, width = image.shape[:2]
    top = max(0, box.y - cfg.padding)
    left = max(0, box.x - cfg.padding)
    crop = image[top : min(height, box.bottom + cfg.padding), left : min(width, box.right + cfg.padding)]
    if crop.size == 0:
        return CropWindow(crop, left, top)

    gray = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if cfg.upscale > 1:
        gray = cv2.resize(gray, None, fx=cfg.upscale, fy=cfg.upscale, interpolation=cv2.INTER_LANCZOS4)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    return CropWindow(binary, left, top)


def words_to_page_bbox(
    words: Sequence[WordBox],
    *,
    origin_x: int,
    origin_y: int,
    upscale: int,
) -> tuple[BBox, int] | None:
    """Uniao das caixas de palavra e corpo da fonte original, em pixels da pagina.

    O Tesseract mede no recorte ampliado; dividir por `upscale` e somar a origem do
    recorte devolve a coordenada da pagina. A uniao arredonda para fora, senao o
    retangulo entra um pixel dentro do glifo e deixa serifa de fora.

    A mediana da altura, e nao a media: uma palavra com acento ou com descendente
    mede mais alto que o corpo, e basta uma para puxar a media de uma fala de tres
    palavras.

    Devolve None quando nenhuma palavra sobreviveu ao filtro - o chamador cai no
    comportamento antigo.
    """
    if not words:
        return None

    left = min(word.left for word in words)
    top = min(word.top for word in words)
    right = max(word.left + word.width for word in words)
    bottom = max(word.top + word.height for word in words)

    x = left // upscale
    y = top // upscale
    bbox = BBox(
        x=origin_x + x,
        y=origin_y + y,
        # `max(1, ...)` porque BBox exige lado positivo: uma palavra mais estreita
        # que o fator de ampliacao arredondaria para zero.
        w=max(1, math.ceil(right / upscale) - x),
        h=max(1, math.ceil(bottom / upscale) - y),
    )
    return bbox, max(1, round(median(word.height for word in words) / upscale))


def read_block(image: np.ndarray, box: BBox, cfg: OcrConfig) -> BlockReading:
    """Texto, confianca media (0-100) e geometria do letreiramento original."""
    window = prepare_crop(image, box, cfg)
    if window.image.size == 0:
        return BlockReading("", 0.0)

    data = pytesseract.image_to_data(
        window.image, lang=cfg.lang, config=f"--psm {cfg.psm}", output_type=Output.DICT
    )

    # As caixas saem da mesma passada que ja acontecia: `image_to_data` sempre
    # devolveu left/top/width/height por palavra, e o codigo lia so duas colunas.
    words = [
        (word, float(conf), WordBox(left, top, width, height))
        for word, conf, left, top, width, height in zip(
            data["text"],
            data["conf"],
            data["left"],
            data["top"],
            data["width"],
            data["height"],
            strict=True,
        )
        if word.strip() and float(conf) >= 0
    ]
    if not words:
        return BlockReading("", 0.0)

    measured = words_to_page_bbox(
        [box for _, _, box in words],
        origin_x=window.left,
        origin_y=window.top,
        upscale=cfg.upscale,
    )
    text_bbox, source_font_px = measured if measured else (None, None)

    return BlockReading(
        text=clean_ocr_text(" ".join(word for word, _, _ in words)),
        confidence=sum(conf for _, conf, _ in words) / len(words),
        text_bbox=text_bbox,
        source_font_px=source_font_px,
    )


def _longest_word(text: str) -> int:
    """Maior sequencia de letras seguidas.

    Contar letras soltas nao separa fala de ruido: "I I" e "r I" tem duas letras
    cada e nenhuma palavra. Toda fala real tem pelo menos uma palavra, entao medir
    a maior sequencia rejeita esse tipo de ruido sem poder descartar dialogo.
    """
    longest = current = 0
    for char in text:
        current = current + 1 if char.isalpha() else 0
        longest = max(longest, current)
    return longest


def is_usable(text: str, confidence: float, cfg: OcrConfig) -> bool:
    """Aceita so o que parece fala: confianca minima E letras de verdade.

    O `min_confidence` acompanha a qualidade da deteccao. Medido no capitulo
    manhwa/001 com o detector treinado: em 45 o filtro descartava tres falas
    corretas, entre elas "YOU CAN USE INFORMAL SPEECH." lida inteira com
    confianca 40; em 30 entravam tres lixos, inclusive a marca d'agua do site.
    40 e o ponto onde as tres voltam sem nenhum ruido junto.

    A deteccao de balao produz falso-positivo em arte clara - manto branco, fundo
    palido - e o OCR devolve simbolo solto com confianca baixa. Filtrar aqui, e nao
    afrouxar a deteccao, e o ponto certo: um balao perdido o motor `claude` recupera
    sozinho a partir da imagem (com bbox nulo), enquanto um bloco de ruido nao tem
    como ser desfeito depois - ele custa token e polui a lista de falas.
    """
    if confidence < cfg.min_confidence:
        return False
    return _longest_word(text) >= cfg.min_letters
