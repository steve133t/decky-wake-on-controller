#!/usr/bin/env bash
# deploy.sh — Build the plugin and sideload it onto your Steam Deck over SSH.
#
# Run this from:
#   - WSL or Git Bash on Windows
#   - Any Linux/Mac machine on the same network as the Deck
#   - Directly on the Steam Deck itself (set DECK_IP=localhost)
#
# Prerequisites on your build machine:
#   - Node.js + pnpm  (https://pnpm.io/installation)
#   - ssh + scp
#
# Prerequisites on the Deck:
#   - Developer mode ON  (Steam Settings > System > Developer Mode)
#   - SSH enabled        (Settings > System > Enable SSH)
#
# Usage:
#   ./deploy.sh                        # uses DECK_IP env var or prompts
#   DECK_IP=192.168.1.42 ./deploy.sh
#   DECK_IP=steamdeck.local ./deploy.sh
#   DECK_IP=localhost ./deploy.sh      # building directly on the Deck

set -euo pipefail

DECK_USER="${DECK_USER:-deck}"
PLUGIN_NAME="wake-on-controller"
REMOTE_PATH="/home/${DECK_USER}/homebrew/plugins/${PLUGIN_NAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Resolve Deck IP ───────────────────────────────────────────────────────────
if [[ -z "${DECK_IP:-}" ]]; then
    read -rp "Enter Steam Deck IP (or hostname): " DECK_IP
fi

step() { echo -e "\n\033[36m==> $1\033[0m"; }
ok()   { echo -e "    \033[32m$1\033[0m"; }
err()  { echo -e "    \033[31mERROR: $1\033[0m" >&2; exit 1; }

# ── 1. Install JS dependencies ────────────────────────────────────────────────
step "Installing JS dependencies"
cd "$SCRIPT_DIR"
pnpm install
ok "Done"

# ── 2. Build frontend ─────────────────────────────────────────────────────────
step "Building frontend"
pnpm run build
ok "Built to ./dist/"

# ── 3. Test SSH (skip if deploying locally on the Deck) ──────────────────────
if [[ "$DECK_IP" != "localhost" && "$DECK_IP" != "127.0.0.1" ]]; then
    step "Testing SSH connection to ${DECK_USER}@${DECK_IP}"
    ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
        "${DECK_USER}@${DECK_IP}" "echo ok" > /dev/null \
        || err "Cannot reach Deck at ${DECK_IP}.\n  - Check the IP: Steam Settings > System > About\n  - Enable SSH: Settings > System > Enable SSH\n  - Same network?"
    ok "Connected"
fi

# ── 4. Create remote plugin directory ────────────────────────────────────────
step "Preparing remote directory"
if [[ "$DECK_IP" == "localhost" || "$DECK_IP" == "127.0.0.1" ]]; then
    mkdir -p "$REMOTE_PATH"
else
    ssh "${DECK_USER}@${DECK_IP}" "mkdir -p ${REMOTE_PATH}"
fi
ok "${REMOTE_PATH} ready"

# ── 5. Copy plugin files ──────────────────────────────────────────────────────
step "Copying plugin files"
FILES=("dist" "main.py" "plugin.json")
for item in "${FILES[@]}"; do
    [[ -e "${SCRIPT_DIR}/${item}" ]] || err "${item} not found — did the build succeed?"
    if [[ "$DECK_IP" == "localhost" || "$DECK_IP" == "127.0.0.1" ]]; then
        cp -r "${SCRIPT_DIR}/${item}" "${REMOTE_PATH}/"
    else
        scp -r -o StrictHostKeyChecking=no \
            "${SCRIPT_DIR}/${item}" "${DECK_USER}@${DECK_IP}:${REMOTE_PATH}/"
    fi
done
ok "Files uploaded"

# ── 6. Restart Decky plugin loader ───────────────────────────────────────────
step "Restarting Decky plugin loader"
RESTART_CMD="sudo systemctl restart plugin_loader"
if [[ "$DECK_IP" == "localhost" || "$DECK_IP" == "127.0.0.1" ]]; then
    $RESTART_CMD || true
else
    ssh "${DECK_USER}@${DECK_IP}" "$RESTART_CMD" || true
fi

if [[ $? -eq 0 ]]; then
    ok "plugin_loader restarted"
else
    echo "    Note: couldn't restart plugin_loader automatically."
    echo "    Restart Decky manually: Quick Access > Decky > ⚙ > Restart"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "\033[32mDeployed! Open the Wake on Controller plugin on your Deck.\033[0m"
echo "On first open it will auto-install the sudoers rules and sleep hook."
