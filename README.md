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

## Onde o projeto mora

Existe **uma cópia só** do projeto, no lado Windows:

```
C:\Users\Perdido\.antigravity\tradução
```

O WSL enxerga essa mesma pasta em `/mnt/c/Users/Perdido/.antigravity/tradução`.
Não é cópia nem sincronização — é o mesmo arquivo visto por dois caminhos. Editar
de um lado aparece no outro na hora.

A divisão de trabalho segue daí:

| Tarefa | Onde rodar |
| --- | --- |
| `git`, editor, Explorer | Windows, no caminho `C:\...` |
| `python`, `pip`, `mangatl`, `tesseract` | WSL, no caminho `/mnt/c/...` |

**Sempre `wsl -d Ubuntu`.** Se a distro padrão desta máquina for a
`docker-desktop` (é o caso aqui), um `wsl` sem a flag cai nela — e ela não tem
bash, então qualquer comando morre com
`execvpe(bash) failed: No such file or directory`. Para rodar algo do pipeline a
partir do Windows:

```bash
wsl -d Ubuntu -e bash -lc 'cd "/mnt/c/Users/Perdido/.antigravity/tradução" && source ~/.venvs/mangatl/bin/activate && mangatl doctor'
```

**Não clone o repositório dentro do home do Linux.** Uma cópia em `~/traducao`
ou parecido não recebe os commits feitos do lado Windows: ela congela no estado
do dia em que foi criada e, pior, o checkout Linux grava LF onde o Windows gravou
CRLF, então `git status` acusa o arquivo inteiro como modificado sem nenhuma
mudança real de conteúdo. Uma cópia dessas chegou a existir em `~/traducao` nesta
máquina; se ainda estiver lá, apague com `rm -rf ~/traducao`. O venv em
`~/.venvs/mangatl` já aponta para o caminho `/mnt/c` em modo editável, que é o
arranjo correto.

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

**A pasta inteira é tratada como uma tira só.** Um macro de rolagem corta a captura
num teto fixo de altura, e esse corte é cego: medido numa captura real, um balão
terminava com o arco no fim de um arquivo e o texto no começo do seguinte, virando
duas metades que o OCR lê como ruído. O que sobra de um arquivo é carregado para o
início do próximo antes de procurar a próxima costura, então a fronteira do macro
nunca vira fronteira de página. As fatias são numeradas em sequência contínua
(`p0001.jpg`, `p0002.jpg`, …) porque uma fatia pode atravessar dois arquivos.

Isso pressupõe que uma pasta é um capítulo. Se você capturar dois capítulos na mesma
sessão, eles serão emendados — capture cada capítulo separado.

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

Abra os PNGs em `output/serie/001/debug/`. Três cores, e a diferença entre elas é
que diz qual botão girar:

- **verde numerado** — virou fala dentro de balão, na ordem de leitura mostrada
- **azul numerado** — virou fala sobre a arte (SFX, narração sem moldura, placa)
- **vermelho** — o detector achou, e o filtro de OCR descartou por não parecer texto

O número traz a confiança do detector ao lado. Quando a caixa do texto difere a do
balão, ela aparece fina por dentro — é assim que se vê se o pareamento das duas
está certo.

Vermelho não é erro: a detecção é deliberadamente solta, e o OCR é quem decide.
Muito vermelho só incomoda se estiver custando tempo. Balão *sem caixa nenhuma* é o
sintoma que importa.

### Dois backends

`[detect] backend` escolhe entre eles, e `--detector` sobrescreve sem editar arquivo:

| Backend | O que é | Quando usar |
|---|---|---|
| `rtdetr` | RT-DETR-v2 treinado em HQ (`ogkalu/comic-text-and-bubble-detector`) | Padrão. Vê balão colorido, balão sem borda e texto sobre arte |
| `heuristic` | Visão clássica: região clara, fechada, convexa, com tinta moderada | Sem `torch` instalado, ou para comparar |

```bash
mangatl process library/serie/001 --dry-run --debug-boxes --detector heuristic
python scripts/compare_extractions.py <extracao-antiga>.json output/serie/001/extract.json
```

Calibrar o `rtdetr` são dois números em `[detect.rtdetr]`, e nada mais:

| Sintoma | Ajuste |
|---|---|
| Perdeu balões | baixe `confidence` |
| Ruído de arte virando fala | suba `confidence` |
| Caixa desenhada em cima do desenho | suba `artwork_confidence` |
| SFX ou narração sem moldura perdidos | baixe `artwork_confidence` |

`artwork_confidence` é maior que `confidence` de propósito: falso positivo sobre a
arte desenha caixa em cima do desenho, e isso é pior que perder um balão — balão
perdido o motor `claude` ainda recupera a partir da imagem.

### Calibrar o backend `heuristic`

A tabela abaixo e os thresholds de `[detect]` valem **só** para este backend. O
`min_interior_brightness = 200` é o que exige papel branco, e é por isso que balão
colorido e balão sem borda são invisíveis para ele por construção.

| Sintoma | Ajuste |
|---|---|
| Perdeu balões | baixe `min_fill_ratio` ou `min_interior_brightness` |
| Perdeu balão de contorno claro | suba `INK_THRESHOLD` em `detect.py` |
| Fala boa descartada (aparece vermelha) | baixe `min_confidence` em `[ocr]` |
| Muito ruído de arte virando fala | suba `min_confidence` ou `min_letters` |

`min_letters` conta **letras seguidas**, não letras somadas: `"I I"` tem duas letras e
nenhuma palavra. Toda fala real tem ao menos uma palavra, então subir esse valor
rejeita ruído sem poder descartar diálogo.
| Balão partido em vários | suba `merge_iou` |
| Texto estilizado ou SFX perdido | baixe `min_interior_brightness` |
| Ordem errada entre balões lado a lado | ajuste `band_overlap` em `[reading_order]` |

Balões sem borda e SFX estilizado escapam da heurística. Com o motor `claude` isso é
recuperável: ele vê a página e devolve a fala com `bbox` nulo — sem coordenada não há
onde sobrepor, então o leitor a lista no fim do capítulo em vez de escondê-la.

### Um erro que não dá para corrigir localmente

O Tesseract confunde letra com dígito em fonte estilizada: `SO` vira `50` ou `90`.
Medido nesta captura, blacklistar dígitos acerta a palavra em 2 de 3 casos — mas a
confiança não diz qual está certo (num deles a leitura errada pontua *mais* alto), e
blacklistar sempre corromperia números legítimos como `50 YEARS`.

Não há correção local segura. O motor `claude` resolve porque vê a página e
reconstrói a fala antes de traduzir; o `free` não tem como perceber.

---

## O leitor não mostra as fatias

O fatiamento é etapa interna. O leitor monta o capítulo como uma tira contínua: as
fatias entram coladas, sem moldura, margem ou borda, e a emenda cai justamente na
linha de menos tinta que o corte escolheu — invisível no pixel. Cada fatia é
sobreposta em 1px sobre a anterior, senão o arredondamento da altura em escala abre
uma linha de fundo entre elas.

A fala traduzida é escrita **dentro do balão**, numa caixa branca posicionada pela
`bbox`. Tudo em unidade relativa: a posição em porcentagem da fatia, o tamanho da
fonte em `cqw` (fração da largura da tira). Por isso o overlay acompanha qualquer
largura de tela sem recalcular nada — 998px de origem viram 430px no celular e as
coordenadas continuam certas.

O botão **tradução** liga e desliga o overlay, e o estado fica guardado. Desligado,
a arte aparece intacta.

### Três limites conhecidos

**Não há inpainting.** A caixa é branca e retangular, e o contorno do balão
desaparece debaixo dela. Funciona porque a detecção só aceita balão de interior
claro (`min_interior_brightness`), então o branco encosta na cor que já estava lá —
mas num balão colorido ou em SFX a caixa fica visível.

**O português é mais longo que o inglês.** Quando a fala não cabe, a fonte encolhe
até o piso de legibilidade (`FONT_FLOOR_CQW`, ~28px na resolução de origem) e a
partir dali **a caixa cresce** em vez de cortar o texto. Medido neste capítulo, o
pior caso cresceu 1,63x da altura do balão. Perder um pedaço de arte é melhor que
perder metade da fala; se preferir o contrário, baixe o piso em `reader/overlay.js`.

**A bbox às vezes é do painel, não do balão.** Quando isso acontece a caixa branca
tapa arte. O botão de tradução é a saída.

A geometria e o dimensionamento são puros e testados:

```bash
node --test reader/overlay.test.js
```

---

## Ler no celular

**Sirva pelo Windows, não pelo WSL.** O `mangatl serve` roda, mas o IP que ele
imprime é o endereço interno do WSL (`172.x.x.x`), que o celular não alcança. Como os
arquivos estão em `/mnt/c`, o `scripts/serve.py` serve do lado do Windows — e ele é só
stdlib, então roda no python do sistema, sem o venv, e o Smart App Control não o bloqueia:

```powershell
python scripts\serve.py 8000
```

**Não use `python -m http.server --directory <raiz>`.** Ele publica a raiz do projeto
inteira em `0.0.0.0`, e a raiz contém o `.env` — qualquer um no mesmo Wi-Fi baixa a sua
chave da Anthropic em `http://<ip-do-pc>:8000/.env`. O `scripts/serve.py` e o
`mangatl serve` usam o mesmo filtro (`src/mangatl/serving.py`): só `reader/`, `output/`
e `library/` saem na rede, e a raiz redireciona para `/reader/` em vez de se listar.

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

Fase 2 parcial: o texto traduzido já é escrito dentro do balão, sobre a tira
contínua, verificado em execução no capítulo de teste (123 falas posicionadas, 16
testes de geometria passando). Falta o inpainting — a caixa é branca e retangular,
e apaga o contorno do balão junto com o texto original.
