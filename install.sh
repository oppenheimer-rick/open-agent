#!/usr/bin/env bash
# ============================================================================
# Open-Agent Installer
# ============================================================================
# Installation script for Linux, macOS, and WSL.
# Sets up a virtual environment, installs dependencies, and creates
# the terminal shortcuts 'openagent' and 'op'.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/oppenheimer-rick/open-agent/main/install.sh | bash
# ============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Configuration
REPO_URL="https://github.com/oppenheimer-rick/open-agent.git"
INSTALL_DIR="$HOME/.openagent"
BIN_DIR="$HOME/.local/bin"

print_banner() {
    echo -e "${MAGENTA}${BOLD}"
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│             ░▒▓ Open-Agent Installer ▓▒░                │"
    echo "├─────────────────────────────────────────────────────────┤"
    echo "│  A local-first, privacy-focused terminal IDE agent.     │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo -e "${NC}"
}

log_info() {
    echo -e "${CYAN}→${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

detect_os() {
    OS="$(uname -s)"
    case "$OS" in
        Linux*)
            OS_NAME="Linux"
            if grep -qMish "microsoft" /proc/version 2>/dev/null; then
                OS_NAME="WSL"
            fi
            ;;
        Darwin*)
            OS_NAME="macOS"
            ;;
        *)
            OS_NAME="Unknown"
            ;;
    esac
    log_success "Detected System: ${BOLD}${OS_NAME}${NC}"
}

check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check Git
    if ! command -v git &>/dev/null; then
        log_error "Git is not installed. Please install git and try again."
        exit 1
    fi
    
    # Check Python 3
    if ! command -v python3 &>/dev/null; then
        log_error "Python 3 is not installed. Please install Python 3.10+ and try again."
        exit 1
    fi
    
    # Verify Python version (>= 3.10)
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
    if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]; }; then
        log_error "Python 3.10+ is required. Found Python $PYTHON_VERSION"
        exit 1
    fi
    
    # Check venv module
    if ! python3 -m venv --help &>/dev/null; then
        log_error "Python 3 'venv' module is not installed."
        if command -v apt-get &>/dev/null; then
            log_info "You can install it using: sudo apt-get install python3-venv"
        fi
        exit 1
    fi
    
    log_success "Prerequisites satisfied."
}

clone_repository() {
    if [ -d "$INSTALL_DIR/.git" ]; then
        log_info "Repository already exists at $INSTALL_DIR. Pulling latest changes..."
        cd "$INSTALL_DIR"
        git pull
    else
        log_info "Cloning Open-Agent to $INSTALL_DIR..."
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi
    log_success "Repository prepared."
}

setup_virtualenv() {
    log_info "Setting up Python virtual environment..."
    python3 -m venv "$INSTALL_DIR/venv"
    
    log_info "Upgrading pip and installing packages..."
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    "$INSTALL_DIR/venv/bin/pip" install -e "$INSTALL_DIR"
    
    log_success "Virtual environment set up."
}

create_shortcuts() {
    log_info "Creating executable shortcuts..."
    mkdir -p "$BIN_DIR"
    
    # Symlink openagent
    ln -sf "$INSTALL_DIR/venv/bin/openagent" "$BIN_DIR/openagent"
    # Symlink op
    ln -sf "$INSTALL_DIR/venv/bin/op" "$BIN_DIR/op"
    
    log_success "Shortcuts created: $BIN_DIR/openagent and $BIN_DIR/op"
}

check_path() {
    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        log_warn "$BIN_DIR is not in your PATH."
        echo ""
        echo -e "To run ${BOLD}openagent${NC} or ${BOLD}op${NC} from anywhere, add it to your profile:"
        echo ""
        
        # Detect shell
        CURRENT_SHELL=$(basename "$SHELL")
        case "$CURRENT_SHELL" in
            zsh)
                echo -e "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
                echo -e "  source ~/.zshrc"
                ;;
            bash)
                echo -e "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
                echo -e "  source ~/.bashrc"
                ;;
            *)
                echo -e "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.profile"
                echo -e "  source ~/.profile"
                ;;
        esac
        echo ""
    fi
}

main() {
    print_banner
    detect_os
    check_prerequisites
    clone_repository
    setup_virtualenv
    create_shortcuts
    
    echo ""
    log_success "Open-Agent has been installed successfully!"
    check_path
    
    echo -e "To start, configure your local LLM backend (default: http://localhost:8083/v1) and run:"
    echo -e "  ${BOLD}openagent${NC}  or  ${BOLD}op${NC}"
    echo ""
}

main
