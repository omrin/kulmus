#!/bin/sh
# Installs Kulmus (קולמוס) for the current user: ~/.local/opt/Kulmus + a menu entry.
#   tar -xzf Kulmus-Linux-x64.tar.gz && ./Kulmus/install-linux.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/opt/Kulmus"
mkdir -p "$HOME/.local/opt" "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
if [ "$HERE" != "$DEST" ]; then rm -rf "$DEST"; cp -r "$HERE" "$DEST"; fi
ln -sf "$DEST/Kulmus" "$HOME/.local/bin/Kulmus"
cp "$DEST/icon.png" "$HOME/.local/share/icons/hicolor/256x256/apps/kulmus.png"
sed "s|^Exec=.*|Exec=$DEST/Kulmus|" "$DEST/kulmus.desktop" > "$HOME/.local/share/applications/kulmus.desktop"
echo "קולמוס הותקן. אפשר להפעיל מתפריט היישומים או בפקודה: Kulmus"
