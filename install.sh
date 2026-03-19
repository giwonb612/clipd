#!/usr/bin/env bash
set -e

REPO="giwonb612/clipd"
PKG="git+https://github.com/${REPO}.git"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[clipd]${NC} $*"; }
warn()  { echo -e "${YELLOW}[clipd]${NC} $*"; }
error() { echo -e "${RED}[clipd]${NC} $*" >&2; exit 1; }

# ── macOS check ───────────────────────────────────────────────────────────────
[[ "$(uname)" == "Darwin" ]] || error "macOS 전용 앱입니다."

# ── Python 3.11+ check ────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3; do
  if command -v "$cmd" &>/dev/null; then
    ver=$("$cmd" -c 'import sys; print(sys.version_info >= (3,11))')
    if [[ "$ver" == "True" ]]; then
      PYTHON="$cmd"
      break
    fi
  fi
done
[[ -n "$PYTHON" ]] || error "Python 3.11 이상이 필요합니다. https://www.python.org/downloads/"

info "Python: $($PYTHON --version)"

# ── pipx install / ensure ─────────────────────────────────────────────────────
if ! command -v pipx &>/dev/null; then
  warn "pipx가 없습니다. 설치합니다..."
  if command -v brew &>/dev/null; then
    brew install pipx
    pipx ensurepath
  else
    "$PYTHON" -m pip install --user pipx
    "$PYTHON" -m pipx ensurepath
  fi
fi

# ── install clipd ─────────────────────────────────────────────────────────────
info "clipd 설치 중..."
pipx install "$PKG" --python "$PYTHON" 2>/dev/null || pipx upgrade clipd 2>/dev/null || {
  pipx uninstall clipd 2>/dev/null || true
  pipx install "$PKG" --python "$PYTHON"
}

# ── PATH check ────────────────────────────────────────────────────────────────
if ! command -v clipd &>/dev/null; then
  warn "PATH에 clipd가 없습니다. 아래를 ~/.zshrc 또는 ~/.bashrc에 추가하세요:"
  echo ""
  echo '  export PATH="$HOME/.local/bin:$PATH"'
  echo ""
fi

# ── done ─────────────────────────────────────────────────────────────────────
echo ""
info "✅ 설치 완료!"
echo ""
echo "  시작하기:"
echo "    clipd daemon start   # 백그라운드 데몬 실행"
echo "    clipd list           # 히스토리 조회"
echo "    clipd --help         # 전체 명령어 보기"
echo ""
