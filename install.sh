#!/usr/bin/env bash
set -euo pipefail
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/xfreerdp-gui.desktop" <<'DESKTOP'
[Desktop Entry]
Name=XFreeRDP GUI
Comment=Graphical frontend for FreeRDP
Exec=%SRC%/run.sh
Icon=%SRC%/icon.svg
Terminal=false
Type=Application
Categories=Network;RemoteAccess;
Keywords=rdp;freerdp;remote;desktop;
DESKTOP
sed -i "s|%SRC%|$SRC_DIR|g" "$HOME/.local/share/applications/xfreerdp-gui.desktop"
echo "Installed: ~/.local/share/applications/xfreerdp-gui.desktop"
