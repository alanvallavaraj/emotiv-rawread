#!/bin/bash
# Headset control panel - double-click this file in Finder to start.
#
# Starts the local server, waits until it answers, then opens the browser.
# Keep the Terminal window that appears OPEN: closing it, or pressing Ctrl-C,
# stops the panel.

cd "$(dirname "$0")" || exit 1

printf '\n  Headset control panel\n'
printf '  =====================\n\n'

# --- find a Python 3 interpreter -------------------------------------------
# /usr/bin/python3 is tried last: on a Mac without the Xcode command line
# tools it pops an install prompt instead of running.
PY=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
                 "$HOME/miniconda3/bin/python3" /usr/bin/python3; do
    if [ -x "$candidate" ]; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
    PY="$(command -v python3 2>/dev/null)"
fi
if [ -z "$PY" ]; then
    printf '  Could not find Python 3 on this Mac.\n'
    printf '  Install it, or run:  python3 server.py\n\n'
    printf '  Press Return to close this window.'
    read -r _
    exit 1
fi
printf '  Python:    %s\n' "$PY"

# --- pick a port that is actually free --------------------------------------
PORT=""
for p in 8777 8778 8779 8780 8781; do
    if ! lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then PORT="$p"; break; fi
done
if [ -z "$PORT" ]; then
    printf '  Ports 8777-8781 are all busy. Close whatever is using them.\n\n'
    printf '  Press Return to close this window.'
    read -r _
    exit 1
fi
printf '  Port:      %s\n' "$PORT"

URL="http://127.0.0.1:$PORT"

# --- start the server --------------------------------------------------------
"$PY" server.py --port "$PORT" &
SERVER_PID=$!

CLEANED=""
cleanup() {
    # The trap fires for both the signal and the subsequent exit; only act once.
    [ -n "$CLEANED" ] && return
    CLEANED="yes"
    printf '\n  Stopping the control panel...\n'
    kill "$SERVER_PID" 2>/dev/null
    # Give it a moment to close its sockets, then insist. Without this the
    # server can outlive a closed Terminal window and hold the port.
    for _ in 1 2 3 4; do
        kill -0 "$SERVER_PID" 2>/dev/null || break
        sleep 0.5
    done
    kill -9 "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
    printf '  Stopped.\n\n'
}
# HUP matters: closing the Terminal window sends it.
trap cleanup EXIT INT TERM HUP

# --- wait for it to answer, then open the browser ---------------------------
printf '  Starting'
READY=""
for _ in $(seq 1 40); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi   # server died
    if curl -s -o /dev/null --max-time 1 "$URL/api/status" 2>/dev/null; then
        READY="yes"; break
    fi
    printf '.'
    sleep 0.5
done
printf '\n'

if [ -z "$READY" ]; then
    printf '\n  The server did not start. Scroll up for the reason.\n\n'
    printf '  Press Return to close this window.'
    read -r _
    exit 1
fi

printf '  Ready:     %s\n' "$URL"
if [ -z "$PANEL_NO_OPEN" ]; then
    open "$URL"
    printf '  Browser:   opened\n'
fi

printf '\n  ---------------------------------------------------------\n'
printf '  Leave this window open while you use the panel.\n'
printf '  Press Ctrl-C here (or close the window) to stop it.\n'
printf '  ---------------------------------------------------------\n\n'

wait "$SERVER_PID"
