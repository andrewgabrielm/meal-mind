#!/bin/sh
# Start the MealMind backend, replacing whatever already holds port 8000 —
# including suspended (Ctrl+Z'd) or unresponsive instances.
# --reload restarts on code changes; edit .env -> rerun this script.
cd "$(dirname "$0")"

OLD=$(lsof -t -iTCP:8000 -sTCP:LISTEN 2>/dev/null)
if [ -n "$OLD" ]; then
    kill -CONT $OLD 2>/dev/null   # wake suspended processes so signals land
    kill $OLD 2>/dev/null
    sleep 1
    STILL=$(lsof -t -iTCP:8000 -sTCP:LISTEN 2>/dev/null)
    [ -n "$STILL" ] && kill -9 $STILL 2>/dev/null && sleep 1
fi

exec uvicorn app.main:app --host 0.0.0.0 --reload
