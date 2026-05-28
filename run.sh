#!/usr/bin/env bash
# Crash-recovery wrapper: if the booth dies, restart it after a short pause.
# Cmd+Shift+Q from inside the app quits cleanly (and the loop exits because
# we trap the "deliberate exit" code 0).

set -u
cd "$(dirname "$0")"

while true; do
  .venv/bin/python -m src.main
  rc=$?
  if [ $rc -eq 0 ]; then
    echo "photobooth exited cleanly; stopping wrapper."
    break
  fi
  echo "photobooth crashed (rc=$rc); restarting in 2s…"
  sleep 2
done
