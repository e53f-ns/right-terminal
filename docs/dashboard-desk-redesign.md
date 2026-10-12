# Dashboard redesign: F1 DESK (2026-10-13)

Evolution of the existing RIGHT TRADE terminal, not a rewrite. Amber terminal palette kept, quieter;
green/red are reserved for profit/loss only.

## What changed
- `templates/index.html`: design tokens (palette), new first tab **F1 DESK**, SYSTEM (old AGENT panel) moved last,
  splash screen closes after 2.5 s instead of waiting for the slowest feed.
- `rt_dashboard_desk.py` (new) + route `GET /api/desk` in `web_dashboard.py`: one read-only snapshot for DESK.
  Venue data comes from `rt_upscale_actual.fetch()` (GET only); the ~20 s venue fetch runs in one background thread,
  the request returns the last snapshot immediately.
- `RT_Control.ps1`: same colours, opens `/#desk`.

## DESK shows
Status chips (agent alive, venue LIVE, SL/TP protection N/N, data feeds ok/total), equity / floating / realized PnL /
win rate, real equity curve, open positions with SL/TP and distance to them, closed real trades with the entry reason
(from `logs/closed_trades.jsonl`), agent feed, and the pending auto-review (a proposal, changes no rules).

## Access
All `/api/*`, `/ws` and `/upscale` stay behind the trusted-device session (`rt_dashboard_chat.py`: loopback + Tailscale only).
`/api/desk` additionally checks the peer IP. The dashboard sends no orders.

## Rollback
Tag `dashboard-before-desk-redesign` holds the previous version of every file touched here.

## Not self-contained
These files are the dashboard layer only. They import the rest of the project (`data/`, `analysis/`, `polymarket/`,
`logs/closed_trades.jsonl` produced by `utils/trade_review.py`) and read keys from `.env`, which is never committed.
