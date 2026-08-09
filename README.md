# Loki + Grafana Logging Stack

## Overview
This stack provides centralized log aggregation and visualization for Docker containers and system logs.

## Components
- **Loki** (port 3100): Log aggregation system
- **Alloy** (ports 1514 syslog, 12345 UI): Log collector that ships Docker, journald and router syslog logs to Loki
- **Grafana** (port 3000): Web UI for viewing and querying logs

## Access

### Grafana Web UI
- **URL**: `http://<docker-host>:3000`
- **Username**: admin
- **Password**: set during your deployment workflow

## Quick Start

### 1. Access Grafana
Open `http://<docker-host>:3000` in your browser

### 2. Add Loki Data Source (First Time Only)
1. Click the menu (☰) → Connections → Data Sources
2. Click "Add data source"
3. Select "Loki"
4. Set URL to: `http://loki:3100`
5. Click "Save & Test"

### 3. View Logs
1. Click the menu (☰) → Explore
2. Select "Loki" from the data source dropdown
3. Use the Log browser to select containers
4. Or use LogQL queries (examples below)

## Example LogQL Queries

### View all logs from a specific container:
```
{container="your-app"}
```

### View logs from multiple containers:
```
{container=~"app1|app2|app3"}
```

### Search for errors:
```
{container="your-app"} |~ "(?i)error"
```

### Filter by log level:
```
{container="your-app"} | json | level="ERROR"
```

### Count errors in last hour:
```
count_over_time({container="your-app"} |~ "(?i)error" [1h])
```

### View logs from last 5 minutes:
```
{container="your-app"} [5m]
```

## Management

### Start the stack:
```bash
cd ~/loki-stack
docker compose up -d
```

### Stop the stack:
```bash
cd ~/loki-stack
docker compose down
```

### View logs:
```bash
docker logs loki
docker logs alloy
docker logs grafana
```

### Check status:
```bash
docker ps | grep -E 'loki|grafana|alloy'
```

### Restart a service:
```bash
docker compose restart loki
docker compose restart alloy
docker compose restart grafana
```

## Configuration

### Loki Config
- File: `~/loki-stack/loki/loki-config.yml`
- Retention: 30 days (720 hours)
- Storage: `/var/lib/docker/volumes/loki-stack_loki-data`

### Alloy Config
- File: `~/loki-stack/alloy/config.alloy`
- Discovers Docker containers over the Docker API, plus journald and remote router syslog
- Adds labels: container, stream, level, job (see [Labels](#labels))
- Live pipeline view: `http://<docker-host>:12345`

### Grafana Config
- Storage: `/var/lib/docker/volumes/loki-stack_grafana-data`
- Admin user: admin
- Admin password: configure during setup and store outside the repo

## Resource Usage

Typical resource usage:
- **Loki**: ~200-300 MB RAM
- **Alloy**: ~100-150 MB RAM
- **Grafana**: ~150-200 MB RAM
- **Total**: ~400-600 MB RAM

Storage:
- ~1-2 MB/day for typical container logs
- 30-day retention = ~30-60 MB

## Troubleshooting

### Alloy not collecting logs:
```bash
docker logs alloy
# Should show component evaluation and target discovery
```
Open `http://<docker-host>:12345` for the component graph — each component shows
its health, its current targets and, for `loki.process`, the entries it handled.

### Loki not receiving logs:
```bash
# Check Loki is running
curl http://localhost:3100/ready

# Check Alloy can reach Loki
docker exec alloy wget -O- http://loki:3100/ready
```

### Grafana can't connect to Loki:
1. Make sure Loki data source URL is `http://loki:3100`
2. Check both containers are on the same network:
   ```bash
   docker network inspect loki-stack_loki
   ```

### No logs showing in Grafana:
1. Check time range (top right in Explore)
2. Verify containers are running and generating logs
3. Check Alloy is discovering containers, either in the UI at
   `http://<docker-host>:12345/component/discovery.docker.containers` or with:
   ```bash
   docker logs alloy | grep -i target
   ```

## Migration Runbook

Two migrations land together: the storage schema moves from BoltDB-shipper/v11 to
TSDB/v13, and Promtail is replaced by Alloy. They are independent — do them in
either order.

### 1. Storage schema: BoltDB-shipper v11 → TSDB v13

`schema_config` now holds two periods. Loki selects a period by the chunk's
timestamp, so old data keeps being read through boltdb-shipper/v11 and only new
writes go to TSDB/v13. **Schema changes cannot be rolled back.**

**Step 1 (this change):** the TSDB/v13 period is dated `2026-08-12`, and
`allow_structured_metadata` stays `false`. Deploy and restart Loki whenever you
like — nothing changes until the cutover date.

**Step 2 (on or after 2026-08-12 UTC):** set `allow_structured_metadata: true`
in `limits_config` and restart Loki.

The order matters. Loki validates `allow_structured_metadata` against the schema
period active *at that moment*, not the newest one in the list, so enabling it
before the cutover date makes Loki refuse to start:

```
CONFIG ERROR: schema v13 is required to store Structured Metadata and use native
OTLP ingestion, your schema version is v11.
```

If you deploy later than planned, that is fine — a `from` date in the past just
means the period is already active. Only move the date *forward*, and remember
it is read as 00:00:00 UTC: if it is already past UTC midnight of the date you
pick, that period activates immediately and chunks written earlier that day
become unreadable.

No storage migration or downtime is needed. TSDB index and cache directories are
derived from `common.path_prefix`, so they appear under the existing
`loki-stack_loki-data` volume on first write.

### 2. Promtail → Alloy

Label parity is deliberate: `job`, `container`, `stream`, `level`, `unit`,
`hostname`, `syslog_identifier`, `host`, `facility` and `application` all keep
the names and values Promtail produced, so saved queries and dashboards need no
edits.

```bash
docker compose up -d --remove-orphans
```

`--remove-orphans` is what stops the old `promtail` container, since the service
no longer exists in the compose file.

**Expect a one-time burst of duplicate container logs, roughly one hour deep.**
Alloy keeps its own read positions under `/var/lib/alloy/data` (the
`loki-stack_alloy-data` volume) and cannot inherit Promtail's. With no position
recorded, `loki.source.docker` asks the Docker API for each container's logs
`since=0` and re-ships everything the host still has on disk.

Almost all of that is rejected on arrival. Loki computes an out-of-order cutoff
per stream of `highestTs - max_chunk_age/2` (`pkg/ingester/stream.go`), which is
one hour with the default 2h `max_chunk_age`. Because the labels above are
unchanged, Alloy writes into the same streams Promtail was filling, whose newest
entry is from seconds ago — so only the last hour or so per container can land
as a duplicate. `reject_old_samples_max_age` never becomes the binding limit.

For the first minute or two after cutover, Alloy logs a burst of HTTP 400s from
Loki reading `entry too far behind, oldest acceptable timestamp is: ...`. That is
the cutoff doing its job. Loki returns 400 rather than 429, so Alloy drops those
entries instead of retrying, and the burst stops once each container's tail
catches up.

Journald and syslog are unaffected: the journal source is capped at
`max_age = 12h`, and syslog is a live listener with no backlog.

If you ever do need to suppress the duplicates entirely, temporarily lower
`reject_old_samples_max_age` in `limits_config` (for example `5m`) and restart
Loki *before* starting Alloy, then remove the override afterwards. Under GitOps
this costs two extra merges and briefly drops journald backlog, so it is rarely
worth it.

Once you are satisfied with the cutover, the old positions volume can go:

```bash
docker volume rm loki-stack_promtail-positions
```

## Tips

### Live Tail
In Grafana Explore, click the "Live" button to stream logs in real-time

### Save Queries
Click "Add to dashboard" to save useful queries

### Alerts
You can set up alerts in Grafana for specific log patterns

### Labels
Alloy adds these labels:

| Label | Sources | Notes |
|---|---|---|
| `job` | all | `docker`, `systemd-journal`, `router-syslog-tcp`, `router-syslog-udp` |
| `container` | docker | Container name |
| `stream` | docker | stdout or stderr |
| `level` | all | Normalized to one canonical lowercase set: `trace`, `debug`, `info`, `notice`, `warning`, `error`, `critical`, `alert`, `emergency`, `fatal`. Spelling variants are folded in — `warn`→`warning`, `informational`→`info`, `err`→`error`, `crit`→`critical`, `emerg`→`emergency`, `panic`→`fatal` — so one severity is always one label value regardless of source. Absent when a container line declares no level. |
| `unit`, `hostname`, `syslog_identifier` | journald | |
| `host`, `facility`, `application`, `syslog_identifier` | router syslog | |

## Next Steps

1. **Verify Grafana admin credentials** and store them in your password manager
2. **Create dashboards** for your most-used queries
3. **Set up alerts** for errors or important events
4. **Explore LogQL** - it's very powerful\!

## Documentation

- Loki: https://grafana.com/docs/loki/latest/
- LogQL: https://grafana.com/docs/loki/latest/query/
- Grafana: https://grafana.com/docs/grafana/latest/

## Date Created
2026-01-23
