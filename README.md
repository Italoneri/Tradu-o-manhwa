# mangatl

Traduz capítulos de mangá/mahua de inglês para português em lote, e serve um leitor
web que funciona no PC e no celular — inclusive offline.

O processamento roda uma vez por capítulo. A leitura depois é instantânea: nenhuma
chamada de API acontece enquanto você lê.

```
imagens → [detecção de balão + OCR] → extract.json → [motor] → chapter.<motor>.json → leitor
                  local, caro            cacheado      barato
```

Os dois artefatos são separados de propósito. Trocar de motor de tradução reescreve
só o segundo — o OCR não roda de novo.

---

## Por que WSL

O Smart App Control desta máquina Windows está em modo *enforced* e bloqueia DLLs
nativas sem reputação. Na prática isso derruba todo wheel Python com extensão em C:
`numpy`, `Pillow`, `pydantic`, `opencv` e `ctranslate2` — a base inteira do pipeline.

Rodar dentro do WSL resolve sem desligar o Smart App Control, que é uma mudança
irreversível no Windows.

## Setup

```bash
# 1. No PowerShell do Windows, uma vez:
wsl --install -d Ubuntu

# 2. Dentro do Ubuntu, dependências de sistema:
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-eng python3-venv python3-pip

# 3. O resto é o script:
cd "/mnt/c/Users/Perdido/.antigravity/tradução"
bash scripts/setup-wsl.sh            # acrescente --free para incluir o Argos
source ~/.venvs/mangatl/bin/activate

# 4. Chave da API (só para o motor `claude`):
cp .env.example .env && nano .env

# 5. Conferir:
mangatl doctor
```

**O venv fica em `~/.venvs/mangatl`, no filesystem Linux — não em `/mnt/c`.** O
DrvFs não suporta as operações de permissão que o pip faz ao instalar, e um venv
criado ali falha com `OSError: [Errno 1] Operation not permitted`. O código-fonte
continua em `/mnt/c` sem problema: as imagens seguem visíveis no Explorer.

### Motor gratuito (opcional)

```bash
bash scripts/setup-wsl.sh --free
mangatl setup-free          # baixa o modelo Argos en->pt, ~100MB, uma vez
```

---

## Onde colocar os capítulos

```
library/
└─ nome-da-serie/
   ├─ glossary.json      # opcional
   ├─ 001/
   │  ├─ 001.jpg
   │  └─ 002.jpg
   └─ 002/
```

O `glossary.json` fixa nomes e termos entre capítulos — é o que impede o mesmo
personagem de mudar de nome no capítulo seguinte:

```json
{
  "Sect Master": "Mestre da Seita",
  "Lin Feng": "Lin Feng",
  "Qi": "Qi"
}
```

### Captura de rolagem (webtoon)

Se as páginas vêm de um macro que rola a tela e costura tudo numa imagem alta, use
`slice` para importar — ele corta em páginas antes do OCR:

```bash
mangatl slice "/mnt/c/Users/<voce>/.../CapturaRolagem/<data>" minha-serie 001
```

Ele fatia só as capturas altas. Numa pasta onde convivem a costura final e os prints
brutos que a geraram, os prints são ignorados — eles se sobrepõem entre si e
duplicariam as falas.

Fatiar não é opcional para esse formato. Uma captura de 1004x29799 quebra o pipeline
em três pontos: a imagem enviada à API é reduzida ao lado maior, e 29799px viram 53px
de largura (o texto deixa de existir para o modelo); os filtros de área em `[detect]`
são proporcionais à área da página, então o balão mínimo aceito fica 20x maior; e cada
página passa de 100MB descomprimida.

O corte procura a linha com menos tinta perto da altura alvo, para não partir balão ao
meio. Ele desconta as colunas de moldura da captura antes de medir — uma borda de
poucos pixels põe tinta em toda linha da página e apagaria as calhas entre painéis.

Se você já tem as imagens dentro de `library/`, o `process` fatia sozinho e guarda os
originais em `library/<serie>/<cap>/_source/`. Rodar de novo não refatia nada.

---

## Uso

```bash
mangatl doctor                                   # o que falta instalar
mangatl slice <pasta> <serie> <capitulo>         # importa captura de rolagem, já fatiada
mangatl process library/serie/001                # um capítulo, motor padrão
mangatl process library/serie/001 --engine free  # sem custo de API
mangatl process-all serie                        # a série toda; inalterados são no-op
mangatl process-all                              # a biblioteca inteira
mangatl serve                                    # leitor em http://localhost:8000/reader/
```

Opções úteis em `process` / `process-all`:

| Opção | Efeito |
|---|---|
| `--debug-boxes` | desenha as caixas detectadas em `output/<serie>/<cap>/debug/` |
| `--dry-run` | só extrai; não chama motor de tradução |
| `--force` | refaz o OCR mesmo em páginas que não mudaram |
| `--model` | sobrescreve o modelo do `config.toml` nesta execução |

### Adicionar capítulos depois

`mangatl process-all` é idempotente: cada página guarda o sha256 da imagem, e páginas
inalteradas são puladas. Rode de novo à vontade — só o que é novo custa tempo e API.

---

## Os dois motores não são equivalentes

| | `claude` (padrão) | `free` (Argos) |
|---|---|---|
| Vê a imagem da página | sim | **não** |
| Corrige OCR embaralhado | sim | **não** |
| Tom de HQ, gíria | sim | **não**, traduz literal |
| Respeita glossário | por instrução | por proteção de marcador |
| Acha falas que a detecção perdeu | sim, com `bbox` nulo | não |
| Custo | ~$0.15 / capítulo de 40 páginas | zero |
| Rede | necessária | nenhuma, 100% local |

O `free` existe para quando você não quer gastar nada. Ele não substitui o `claude`:
Argos é um modelo MT pequeno, e como não enxerga a página, texto ruim de OCR entra
e sai ruim traduzido.

Quando os dois existem para o mesmo capítulo, o leitor mostra um seletor de motor.

---

## Calibrar a detecção de balão

Este é o passo que decide a qualidade de tudo depois. Antes de gastar API:

```bash
mangatl process library/serie/001 --dry-run --debug-boxes
```

Abra os PNGs em `output/serie/001/debug/`. Duas cores, e a diferença entre elas é
que diz qual botão girar:

- **verde numerado** — virou fala, na ordem de leitura mostrada
- **vermelho** — o detector achou, e o filtro de OCR descartou por não parecer texto

Vermelho não é erro: a detecção de balão é deliberadamente solta, e o OCR é quem
decide. Muito vermelho só incomoda se estiver custando tempo. Balão *sem caixa
nenhuma* é o sintoma que importa.

| Sintoma | Ajuste |
|---|---|
| Perdeu balões | baixe `min_fill_ratio` ou `min_interior_brightness` |
| Perdeu balão de contorno claro | suba `INK_THRESHOLD` em `detect.py` |
| Fala boa descartada (aparece vermelha) | baixe `min_confidence` em `[ocr]` |
| Muito ruído de arte virando fala | suba `min_confidence` ou `min_letters` |
| Balão partido em vários | suba `merge_iou` |
| Ordem errada entre balões lado a lado | ajuste `band_overlap` em `[reading_order]` |

Balões sem borda e SFX estilizado escapam da heurística. Com o motor `claude` isso é
recuperável: ele vê a página e devolve a fala com `bbox` nulo — aparece no leitor,
mas não terá posição para a Fase 2.

---

## Ler no celular

**Sirva pelo Windows, não pelo WSL.** O `mangatl serve` roda, mas o IP que ele
imprime é o endereço interno do WSL (`172.x.x.x`), que o celular não alcança. Como os
arquivos estão em `/mnt/c`, o `http.server` da stdlib serve do lado do Windows — e ele
não usa nenhuma biblioteca nativa, então o Smart App Control não o bloqueia:

```powershell
python -m http.server 8000 --directory "C:\Users\Perdido\.antigravity\tradução"
```

Depois abra `http://<ip-do-pc>:8000/reader/` no celular (`ipconfig` mostra o IP) e use
"Adicionar à tela de início". O service worker guarda as páginas e as traduções do
capítulo visitado, então ele reabre sem rede depois da primeira visita.

`mangatl serve` continua útil para testar no próprio PC, em
`http://localhost:8000/reader/`.

---

## Estado atual

Fase 1 completa e verificada em execução: 72 testes passando, extração ponta a ponta
(detecção → ordem de leitura → OCR → `extract.json`), tradução pelo motor `free`
gerando `chapter.free.json`, reprocessamento idempotente, e o leitor servindo todos
os arquivos.

O motor `claude` tem o formato de request e o parsing cobertos por testes com cliente
dublê, mas ainda não foi exercitado contra a API real — falta a chave.

Fase 2 (texto escrito dentro do balão, com inpainting) não foi implementada. A
fundação está pronta: cada fala traduzida já carrega a `bbox` do balão de origem.
