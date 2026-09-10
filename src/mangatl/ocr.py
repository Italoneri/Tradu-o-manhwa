"""OCR de um recorte de balao com Tesseract.

Tesseract na pagina inteira de HQ produz lixo: ele segmenta arte e screentone
como texto e devolve ordem errada. Aqui ele so ve um balao ja recortado, com
--psm 6 ("bloco uniforme de texto"), que e o cenario em que ele acerta.

A limpeza de texto e uma funcao pura separada da chamada ao binario, para poder
ser testada sem Tesseract instalado.
"""

from __future__ import annotations

import re
import shutil

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


def prepare_crop(image: np.ndarray, box: BBox, cfg: OcrConfig) -> np.ndarray:
    """Recorta, amplia e binariza o balao para a altura de caixa que o Tesseract espera."""
    height, width = image.shape[:2]
    top = max(0, box.y - cfg.padding)
    left = max(0, box.x - cfg.padding)
    crop = image[top : min(height, box.bottom + cfg.padding), left : min(width, box.right + cfg.padding)]
    if crop.size == 0:
        return crop

    gray = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if cfg.upscale > 1:
        gray = cv2.resize(gray, None, fx=cfg.upscale, fy=cfg.upscale, interpolation=cv2.INTER_LANCZOS4)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    return binary


def read_block(image: np.ndarray, box: BBox, cfg: OcrConfig) -> tuple[str, float]:
    """Texto e confianca media (0-100) do balao em `box`."""
    crop = prepare_crop(image, box, cfg)
    if crop.size == 0:
        return "", 0.0

    data = pytesseract.image_to_data(
        crop, lang=cfg.lang, config=f"--psm {cfg.psm}", output_type=Output.DICT
    )

    words = [
        (word, float(conf))
        for word, conf in zip(data["text"], data["conf"], strict=True)
        if word.strip() and float(conf) >= 0
    ]
    if not words:
        return "", 0.0

    text = clean_ocr_text(" ".join(word for word, _ in words))
    confidence = sum(conf for _, conf in words) / len(words)
    return text, confidence


def is_usable(text: str, confidence: float, cfg: OcrConfig) -> bool:
    """Aceita so o que parece fala: confianca minima E letras de verdade.

    A deteccao de balao produz falso-positivo em arte clara - manto branco, fundo
    palido - e o OCR devolve simbolo solto com confianca baixa. Filtrar aqui, e nao
    afrouxar a deteccao, e o ponto certo: um balao perdido o motor `claude` recupera
    sozinho a partir da imagem (com bbox nulo), enquanto um bloco de ruido nao tem
    como ser desfeito depois - ele custa token e polui a lista de falas.
    """
    if confidence < cfg.min_confidence:
        return False
    return sum(char.isalpha() for char in text) >= cfg.min_letters
