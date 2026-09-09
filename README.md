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

# 2. Já dentro do Ubuntu:
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-eng python3-venv python3-pip

cd "/mnt/c/Users/Perdido/.antigravity/tradução"
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# 3. Chave da API (só para o motor `claude`):
cp .env.example .env && nano .env

# 4. Conferir:
mangatl doctor
```

O projeto fica em `/mnt/c/...` de propósito: as imagens continuam visíveis no
Explorer do Windows e você pode servir o leitor pelos dois lados.

### Motor gratuito (opcional)

```bash
pip install -e '.[free]'
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

---

## Uso

```bash
mangatl doctor                                   # o que falta instalar
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

Abra os PNGs em `output/serie/001/debug/`. As caixas vermelhas numeradas mostram o
que foi detectado e em que ordem de leitura. Ajuste `[detect]` no `config.toml`:

| Sintoma | Ajuste |
|---|---|
| Perdeu balões | baixe `min_fill_ratio` ou `min_interior_brightness` |
| Pegou arte como balão | suba `min_fill_ratio`, estreite `min_ink_ratio`/`max_ink_ratio` |
| Balão partido em vários | suba `merge_iou` |
| Ordem errada entre balões lado a lado | ajuste `band_overlap` em `[reading_order]` |

Balões sem borda e SFX estilizado escapam da heurística. Com o motor `claude` isso é
recuperável: ele vê a página e devolve a fala com `bbox` nulo — aparece no leitor,
mas não terá posição para a Fase 2.

---

## Ler no celular

`mangatl serve` imprime o endereço da LAN. Abra no celular, e use "Adicionar à tela
de início" — o service worker guarda as páginas e as traduções do capítulo visitado,
então ele reabre sem rede depois da primeira visita.

Se o servidor rodar dentro do WSL e o celular não alcançar, sirva pelo Windows: como
os arquivos estão em `/mnt/c`, o `http.server` da stdlib funciona lá sem depender de
nenhuma biblioteca nativa.

```powershell
python -m http.server 8000 --directory "C:\Users\Perdido\.antigravity\tradução"
```

---

## Estado atual

Fase 1 completa: extração, tradução pelos dois motores, e leitor com o texto ao lado
da página.

Fase 2 (texto escrito dentro do balão, com inpainting) ainda não foi implementada. A
fundação está pronta — cada fala traduzida já carrega a `bbox` do balão de origem.
