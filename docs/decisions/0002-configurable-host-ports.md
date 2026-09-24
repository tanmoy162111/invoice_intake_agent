# 0002 — Host ports are configurable in Compose
Date: 2026-09-25 · Status: accepted

## Context
Developer machines often already run something on 8000, 3000 or 5432 (this one does).

## Decision
`docker-compose.yml` maps host ports from `API_PORT`, `WEB_PORT`, `DB_PORT`, defaulting to 8000/3000/5432.

## Why
Lets `docker compose up` work anywhere without editing tracked files.

## Consequences
The README tells people to set these in `.env` on a clash.
