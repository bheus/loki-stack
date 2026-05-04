# Loki + Grafana Logging Stack

## Overview
This stack provides centralized log aggregation and visualization for Docker containers and system logs.

## Components
- **Loki** (port 3100): Log aggregation system
- **Promtail**: Log collector that ships Docker logs to Loki
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
docker logs promtail
docker logs grafana
```

### Check status:
```bash
docker ps | grep -E 'loki|grafana|promtail'
```

### Restart a service:
```bash
docker compose restart loki
docker compose restart promtail
docker compose restart grafana
```

## Configuration

### Loki Config
- File: `~/loki-stack/loki/loki-config.yml`
- Retention: 30 days (720 hours)
- Storage: `/var/lib/docker/volumes/loki-stack_loki-data`

### Promtail Config
- File: `~/loki-stack/promtail/promtail-config.yml`
- Discovers configured Docker containers and system logs
- Adds labels: container, stream, compose_project, compose_service

### Grafana Config
- Storage: `/var/lib/docker/volumes/loki-stack_grafana-data`
- Admin user: admin
- Admin password: configure during setup and store outside the repo

## Resource Usage

Typical resource usage:
- **Loki**: ~200-300 MB RAM
- **Promtail**: ~50-100 MB RAM
- **Grafana**: ~150-200 MB RAM
- **Total**: ~400-600 MB RAM

Storage:
- ~1-2 MB/day for typical container logs
- 30-day retention = ~30-60 MB

## Troubleshooting

### Promtail not collecting logs:
```bash
docker logs promtail
# Should show target discovery and scrape activity
```

### Loki not receiving logs:
```bash
# Check Loki is running
curl http://localhost:3100/ready

# Check Promtail can reach Loki
docker exec promtail wget -O- http://loki:3100/ready
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
3. Check Promtail is discovering containers:
   ```bash
   docker logs promtail | grep -i target
   ```

## Tips

### Live Tail
In Grafana Explore, click the "Live" button to stream logs in real-time

### Save Queries
Click "Add to dashboard" to save useful queries

### Alerts
You can set up alerts in Grafana for specific log patterns

### Labels
Promtail automatically adds these labels:
- `container`: Container name
- `stream`: stdout or stderr
- `compose_project`: Docker Compose project name
- `compose_service`: Docker Compose service name

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
