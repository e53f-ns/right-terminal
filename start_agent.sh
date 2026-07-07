#!/bin/bash
cd /Users/e53f/trading-agent

echo "════════════════════════════════════════"
echo "  TRADING AGENT — $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Press Ctrl+C to stop"
echo "════════════════════════════════════════"
echo ""

.venv/bin/python main.py
echo ""
echo "Agent stopped."
