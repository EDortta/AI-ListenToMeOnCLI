#!/usr/bin/env bash
# install.sh — instala ListenToMeOnCLI como serviço do usuário (systemd)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin/listentomecli"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE="$SERVICE_DIR/listen.service"

echo "==> Copiando script para $BIN"
mkdir -p "$HOME/.local/bin"
cp "$SCRIPT_DIR/listen.py" "$BIN"
chmod +x "$BIN"

echo "==> Copiando assets para ~/.config/listentomecli/assets/"
mkdir -p "$HOME/.config/listentomecli/assets"
cp -r "$SCRIPT_DIR/assets/"* "$HOME/.config/listentomecli/assets/"

echo "==> Instalando unit file em $SERVICE"
mkdir -p "$SERVICE_DIR"
cp "$SCRIPT_DIR/listen.service" "$SERVICE"

echo "==> Habilitando serviço systemd (usuário)"
systemctl --user daemon-reload
systemctl --user enable listen
systemctl --user start listen

echo ""
echo "Instalado. Comandos úteis:"
echo "  systemctl --user status listen       # status"
echo "  systemctl --user stop listen         # parar"
echo "  systemctl --user start listen        # iniciar"
echo "  journalctl --user -u listen -f       # ver logs em tempo real"
echo "  systemctl --user disable listen      # desinstalar do autostart"
echo ""
echo "Ctrl+Space → armar/desarmar gravação, como antes."
