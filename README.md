# Voltane（油电簿）

[English](README.md) | [中文](README.zh-CN.md)

> Self-hosted fuel & charging log for plug-in hybrids, EVs and petrol cars. Accurate per-segment fuel *and* electricity consumption for PHEVs, cost tracking, reminders and shared vehicles for the whole family.

## Highlights

- **Hybrid-aware consumption**: tank and battery are treated as inventories, so every segment reports L/100km and kWh/100km over the same distance, plus cost per km, equivalent fuel use and electric share.
- **Self-calibrating battery factor** learned from charging sessions (grid kWh per 1% SOC).
- **Idle / sentry drain** tracked separately via status snapshots; driving vs. overall consumption shown side by side.
- **Works with incomplete history**: start mid-life, add lump-sum summaries, detect missed refuels, graded results (A exact / B estimated / C rough / D insufficient).
- **Shared vehicles** with owner / editor / viewer roles, invite codes, activity feed, conflict and duplicate detection.
- **Fast entry**: auto-fill (two of quantity/price/amount, fill-to-full volume, time-of-use tariffs), favourite places, two-step charging, drafts, offline queue.
- Stats, calendar, monthly & yearly reports, reminders by date and/or mileage, CSV/JSON export, daily backups, PWA.

## Quick start

```bash
git clone https://github.com/fange173/voltane.git && cd voltane
# set SECRET_KEY in docker-compose.yml
docker compose up -d
```

Open `http://<host>:8086` and sign in with `admin / admin123`, then change the password.

Stack: FastAPI + SQLite, zero-build ES-module frontend, Docker image `fange173/voltane` (amd64/arm64). License: MIT.
