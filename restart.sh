#!/bin/bash

# Restart Tarjimon bot (webhook mode)

echo "Stopping existing process on port 8080..."
kill $(lsof -t -i :8080) 2>/dev/null && echo "Process stopped" || echo "No process running"

sleep 1

cd "$(dirname "$0")"

echo "Starting webhook server..."
python webhook.py
