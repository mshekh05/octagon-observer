# Running Octagon Edge on Dokploy (homelab LXC 150)

Octagon Edge runs as a **Compose** service in the Dokploy project "Octagon Edge". Dokploy builds it from
GitHub `main` and gives you **Start**, **Stop**, **Redeploy** and **Logs**.

## Layout

| What | Where |
|---|---|
| Secrets and settings (mode 600) | `/srv/appdata/octagon-edge/octagon-edge.env` |
| App data (VAPID keys, caches, models, snapshots) | `/srv/appdata/octagon-edge/data` |
| Reports | `/srv/appdata/octagon-edge/reports` |
| Nightly database dumps (14 days kept) | `/srv/appdata/octagon-edge/backups` |
| Postgres | docker volume `octagon-edge_pgdata` (fixed name, survives redeploys) |
| Web app | `127.0.0.1:8000`, published by `tailscale serve` at `https://apps.wolverine-firefighter.ts.net:8445` |

Everything under `/srv/appdata` is in the restic tier-1 backup. The repo folder Dokploy clones is
throwaway: nothing persistent lives there.

Dokploy's environment for the service holds **paths only**, never secrets:

```
ENV_FILE=/srv/appdata/octagon-edge/octagon-edge.env
DATA_DIR=/srv/appdata/octagon-edge/data
REPORTS_DIR=/srv/appdata/octagon-edge/reports
BACKUP_DIR=/srv/appdata/octagon-edge/backups
```

## Everyday

- **Pause / resume:** Stop or Start in Dokploy. Stop takes down api, worker and db; data stays.
- **Deploy a new version:** merge to `main`, then **Redeploy** in Dokploy. (Auto-deploy on push can't work:
  Dokploy only listens on the tailnet, so GitHub's webhook can't reach it.) After a frontend change,
  close and reopen the app on your phone so the service worker picks up the new build.
- **Change a setting** (e.g. `ODDS_API_BUDGET`, `ACTIVE_CARD`): edit the env file on apps, then Redeploy
  (or Stop + Start).
- **Run the CLI** (doctor, settle, backtests): on apps, in the folder Dokploy deploys to,

  ```bash
  cd /etc/dokploy/compose/<app-name>/code
  docker compose -p <app-name> --env-file .env run --rm cli doctor --no-send
  ```

  `<app-name>` is shown on the service's General tab in Dokploy.

## Never run the old watcher alongside it

`octagon-edge watch` (the Phase-0 command-line watcher) polls the same prices and sends the same alerts as
the worker. Running both doubles the ntfy alerts and the Odds API spend.

## Rollback

Stop the service in Dokploy, then from a checkout of an earlier commit:

```bash
ENV_FILE=/srv/appdata/octagon-edge/octagon-edge.env DATA_DIR=/srv/appdata/octagon-edge/data \
REPORTS_DIR=/srv/appdata/octagon-edge/reports BACKUP_DIR=/srv/appdata/octagon-edge/backups \
docker compose -p octagon-edge up -d --build
```

It uses the same `octagon-edge_pgdata` volume, so nothing is lost. To restore a dump instead:
`gunzip -c backups/octagon-<date>.sql.gz | docker compose exec -T db psql -U octagon octagon`.
