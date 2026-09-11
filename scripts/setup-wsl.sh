#!/usr/bin/env bash
# Prepara o ambiente do mangatl dentro do WSL.
#
# O venv vive no filesystem Linux, nao em /mnt/c: o DrvFs nao suporta as
# operacoes de permissao que o pip faz ao instalar, e o install falha com
# "Operation not permitted". O codigo-fonte continua em /mnt/c normalmente.
#
# Uso:  bash scripts/setup-wsl.sh [--free]

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${MANGATL_VENV:-$HOME/.venvs/mangatl}"
WANT_FREE=0

for arg in "$@"; do
  case "$arg" in
    --free) WANT_FREE=1 ;;
    *) echo "opcao desconhecida: $arg" >&2; exit 2 ;;
  esac
done

echo "projeto: $PROJECT_DIR"
echo "venv:    $VENV_DIR"

if ! command -v tesseract >/dev/null 2>&1; then
  echo
  echo "tesseract ausente. Rode:"
  echo "  sudo apt update && sudo apt install -y tesseract-ocr tesseract-ocr-eng python3-venv"
  exit 1
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "criando venv..."
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip

cd "$PROJECT_DIR"
echo "instalando dependencias..."
if [ "$WANT_FREE" -eq 1 ]; then
  "$VENV_DIR/bin/python" -m pip install --quiet -e '.[dev,free]'
else
  "$VENV_DIR/bin/python" -m pip install --quiet -e '.[dev]'
fi

echo
echo "pronto. Ative com:"
echo "  source $VENV_DIR/bin/activate"
echo "Depois: mangatl doctor"
