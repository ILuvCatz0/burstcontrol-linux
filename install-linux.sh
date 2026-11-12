#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "1. Installing udev rules for Turtle Beach Burst II Air (10f5:5002 & 10f5:5004)..."
sudo install -Dm644 "$REPO_DIR/udev/99-burst-ii-air.rules" /etc/udev/rules.d/99-burst-ii-air.rules
sudo udevadm control --reload-rules
sudo udevadm trigger

echo "2. Creating Python virtual environment and installing hidapi..."
python3 -m venv --system-site-packages "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install --upgrade pip
"$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"

echo "3. Creating ~/.local/bin/burstcontrol launcher..."
mkdir -p "$HOME/.local/bin"
cat << WRAPPER > "$HOME/.local/bin/burstcontrol"
#!/usr/bin/env bash
PYTHONPATH="$REPO_DIR" exec "$REPO_DIR/.venv/bin/python" -m burstcontrol.cli "\$@"
WRAPPER
chmod +x "$HOME/.local/bin/burstcontrol"

echo "Done! You can now run 'burstcontrol' (CLI) or 'burstcontrol --gui' (GTK4 app)."

echo "4. Installing desktop launcher..."
mkdir -p "$HOME/.local/share/applications"
cat << DESKTOP > "$HOME/.local/share/applications/burst-control.desktop"
[Desktop Entry]
Type=Application
Name=Burst Control
Comment=Configure Turtle Beach Burst II Air mouse
Exec=$HOME/.local/bin/burstcontrol --gui
Icon=input-mouse
Categories=Settings;HardwareSettings;Utility;
Terminal=false
DESKTOP
