"""Falas cortadas pela emenda entre duas paginas.

Uma captura que chega ja fatiada da origem pode ter cortado uma fala no meio, e o
detector roda por pagina: ele nunca ve as duas metades juntas. Medido na emenda
entre p0001 e p0002 de manhwa/001, ele acha nada acima do corte e um bloco de
101px abaixo, e o Tesseract le `'my Collen Eyes activated...'` - metade da frase,
com a palavra de cima perdida. Montando uma faixa que atravessa a emenda, o mesmo
detector acha o bloco inteiro e a leitura sai
`'Just in case, I kept my Golden Eyes activated...'`.

Note que a correcao nao e do fatiador deste projeto. O `slicing.py` procura a
costura onde ha menos tinta justamente para nao cortar balao; o problema aparece
quando as paginas chegam ja cortadas e ninguem escolheu onde.

A faixa nao roda em toda emenda. O sinal de corte e ter bloco encostado na borda
compartilhada, de um lado ou do outro: medido no mesmo capitulo, 26 das 154
emendas, e nas outras 128 nao se paga deteccao nenhuma.

Tudo aqui e puro. Quem le imagem e chama o detector e o `pipeline`.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import BBox

EDGE_TOLERANCE = 2
"""Folga, em pixels, para considerar uma caixa encostada na borda.

O detector devolve coordenada inteira arredondada, e uma fala cortada pode sobrar
um pixel longe da borda sem deixar de estar cortada.
"""


def touches_top(box: BBox) -> bool:
    return box.y <= EDGE_TOLERANCE


def touches_bottom(box: BBox, page_height: int) -> bool:
    return box.bottom >= page_height - EDGE_TOLERANCE


def band_heights(upper_height: int, lower_height: int, fraction: float) -> tuple[int, int]:
    """Quantas linhas tomar de cada pagina para montar a faixa.

    Ao menos uma linha de cada lado, senao a faixa nao atravessa emenda nenhuma e
    a deteccao rodaria de graca.
    """
    return (
        max(1, min(upper_height, round(upper_height * fraction))),
        max(1, min(lower_height, round(lower_height * fraction))),
    )


def band_bbox_to_page(bbox: BBox, *, upper_take: int, upper_height: int) -> tuple[BBox, int] | None:
    """Traz uma caixa da faixa para as coordenadas da pagina de cima.

    A linha `r` da faixa e a linha `upper_height - upper_take + r` da pagina de
    cima enquanto `r < upper_take`, e a linha `r - upper_take` da pagina de baixo
    depois disso.

    Devolve a caixa recortada na pagina de cima e quantos pixels sobram para baixo.
    `None` quando a caixa nao atravessa a emenda - nesse caso ela vive inteira
    dentro de uma das paginas, que ja a detectou por conta propria.
    """
    if not (bbox.y < upper_take < bbox.bottom):
        return None

    y = upper_height - upper_take + bbox.y
    return BBox(x=bbox.x, y=y, w=bbox.w, h=upper_height - y), bbox.bottom - upper_take


def improves_on(seam_text: str, covered: Sequence[str]) -> bool:
    """A leitura da faixa vale mais que as metades que ela substituiria?

    A faixa nem sempre le melhor. Medido na emenda entre p0003 e p0004 de
    manhwa/001 com uma faixa de 25%, ela devolveu `'THROUGH THE DOOR THE CAT LED
    ME TO...'` enquanto a pagina sozinha tinha lido a frase inteira, com o `'AND
    WHEN I STEPPED'` na frente: aceitar a faixa ali era trocar texto completo por
    texto cortado.

    Comprimento e o criterio porque e o que a faixa deveria estar melhorando: ela
    existe para recuperar a metade que faltava. Confianca nao serve - a do trecho
    curto costuma ser mais alta justamente por ter menos o que errar.
    """
    return len(seam_text) >= max((len(text) for text in covered), default=0)


def covered_by_seam(box: BBox, seam: BBox, *, overflow: int, page_height: int, on_lower: bool) -> bool:
    """A caixa e a mesma fala que o bloco de emenda ja cobre?

    O bloco de emenda vive na pagina de cima e passa da base dela, entao comparar
    com um bloco da pagina de baixo exige trazer os dois para o mesmo sistema: a
    linha `y` de baixo e a linha `page_height + y` de cima.

    Metade da area da caixa menor e o criterio. Exigir IoU alto nao serve aqui: as
    duas caixas descrevem a mesma fala vista por recortes de tamanhos bem
    diferentes, e a metade que sobrou numa pagina pode ser uma fracao pequena do
    bloco inteiro.
    """
    full = BBox(x=seam.x, y=seam.y, w=seam.w, h=seam.h + overflow)
    shifted = BBox(x=box.x, y=page_height + box.y, w=box.w, h=box.h) if on_lower else box
    overlap = full.intersection_area(shifted)
    return overlap > 0 and overlap / min(full.area, shifted.area) >= 0.5
