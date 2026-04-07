#!/usr/bin/env bash
set -euo pipefail

PLIST_NAME="com.crows-nest.mcp"
PLIST_SRC="$(cd "$(dirname "$0")/../launchd" && pwd)/${PLIST_NAME}.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
LOG_DIR="$(cd "$(dirname "$0")/.." && pwd)/logs"

case "${1:-install}" in
    install)
        mkdir -p "$LOG_DIR"
        mkdir -p "$(dirname "$PLIST_DST")"

        # Unload if already loaded
        launchctl bootout "gui/$(id -u)/${PLIST_NAME}" 2>/dev/null || true

        cp "$PLIST_SRC" "$PLIST_DST"
        launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
        echo "Installed and started ${PLIST_NAME}"
        echo "  Logs: ${LOG_DIR}/mcp-server.{stdout,stderr}.log"
        echo "  Status: launchctl print gui/$(id -u)/${PLIST_NAME}"
        ;;

    uninstall)
        launchctl bootout "gui/$(id -u)/${PLIST_NAME}" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "Uninstalled ${PLIST_NAME}"
        ;;

    status)
        launchctl print "gui/$(id -u)/${PLIST_NAME}" 2>&1 | head -20
        ;;

    restart)
        launchctl kickstart -k "gui/$(id -u)/${PLIST_NAME}"
        echo "Restarted ${PLIST_NAME}"
        ;;

    *)
        echo "Usage: $0 {install|uninstall|status|restart}"
        exit 1
        ;;
esac
