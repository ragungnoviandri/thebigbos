#!/usr/bin/env bash
# deBigBos Cross-Platform Installer
# Run: curl -fsSL https://raw.githubusercontent.com/ragungnoviandri/deBigBos/main/install.sh | bash

set -e

INSTALL_DIR="${deBigBos_HOME:-$HOME/.local/share/deBigBos}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/deBigBos"
REPO_URL="https://github.com/ragungnoviandri/deBigBos.git"
PYTHON_VERSION="3.11"
BIN_DIR="$HOME/.local/bin"

echo ""
echo "============================================"
echo "  deBigBos Installer"
echo "============================================"
echo ""

# 1. Detect OS + prerequisites
echo "[1/6] Checking prerequisites..."

if ! command -v git &>/dev/null; then
    echo "  ERROR: git not found. Install with your package manager."
    echo "    Ubuntu: sudo apt install git"
    echo "    macOS:  brew install git"
    exit 1
fi
echo "  git: $(which git)"

# Find Python
if command -v python3.11 &>/dev/null; then
    PYTHON=python3.11
elif command -v python3 &>/dev/null; then
    PYTHON=python3
else
    echo "  WARNING: python3 not found. Attempting to install..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install python@3.11
        PYTHON=python3.11
    elif [[ -f /etc/debian_version ]]; then
        sudo add-apt-repository -y ppa:deadsnakes/ppa
        sudo apt update && sudo apt install -y python3.11 python3.11-venv
        PYTHON=python3.11
    else
        echo "  ERROR: Please install Python 3.11+ manually."
        exit 1
    fi
fi
echo "  python: $(which $PYTHON) ($($PYTHON --version))"

# 2. Create directories
echo "[2/6] Creating directories..."
mkdir -p "$INSTALL_DIR/repo"
mkdir -p "$INSTALL_DIR/bin"
mkdir -p "$INSTALL_DIR/versions"
mkdir -p "$CONFIG_DIR/skills"
mkdir -p "$CONFIG_DIR/agents"
mkdir -p "$CONFIG_DIR/tools"
mkdir -p "$BIN_DIR"
echo "  Install: $INSTALL_DIR"
echo "  Config:  $CONFIG_DIR"

# 3. Clone repository
echo "[3/6] Cloning repository..."
if [ -d "$INSTALL_DIR/repo/.git" ]; then
    echo "  Repo exists, pulling latest..."
    git -C "$INSTALL_DIR/repo" pull origin main
else
    git clone "$REPO_URL" "$INSTALL_DIR/repo"
fi
echo "  Repository ready"

# 4. Create venv + install
echo "[4/6] Creating virtual environment..."
VENV_DIR="$INSTALL_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -e "$INSTALL_DIR/repo" --quiet
echo "  Dependencies installed"

# 5. Create wrapper script
echo "[5/6] Creating wrapper..."
cat > "$INSTALL_DIR/bin/deBigBos" << 'WRAPPER'
#!/usr/bin/env bash
TB_HOME="${deBigBos_HOME:-$HOME/.local/share/deBigBos}"
exec "$TB_HOME/venv/bin/python" -m debigbos "$@"
WRAPPER
chmod +x "$INSTALL_DIR/bin/deBigBos"
ln -sf "$INSTALL_DIR/bin/deBigBos" "$BIN_DIR/deBigBos"
echo "  Wrapper: $BIN_DIR/deBigBos"

# 6. Default config + skills
echo "[6/6] Setting up config and skills..."
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    cp "$INSTALL_DIR/repo/deBigBos.json" "$CONFIG_DIR/config.json"
    echo "  Created default config: $CONFIG_DIR/config.json"
else
    echo "  Config already exists"
fi

# Copy bundled skills to global config
if [ -d "$INSTALL_DIR/repo/.debigbos/skills" ]; then
    skill_count=0
    for skill_dir in "$INSTALL_DIR/repo/.debigbos/skills"/*/; do
        skill_name=$(basename "$skill_dir")
        if [ ! -d "$CONFIG_DIR/skills/$skill_name" ]; then
            cp -r "$skill_dir" "$CONFIG_DIR/skills/$skill_name"
            skill_count=$((skill_count + 1))
        fi
    done
    echo "  Installed $skill_count skills to $CONFIG_DIR/skills"
fi

# PATH reminder
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "  [!] Add to your shell profile:"
    echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo ""
echo "============================================"
echo "  deBigBos installed!"
echo "============================================"
echo ""
echo "  Run: deBigBos setup"
echo "       deBigBos"
echo ""
