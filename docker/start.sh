#!/bin/bash
set -m
cd /app/backend && uvicorn app.main:app --host 127.0.0.1 --port 8000 &
cd /app/web && PORT=8080 HOSTNAME=0.0.0.0 node server.js &
wait -n
exit $?
