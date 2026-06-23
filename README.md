# service-monitoring

Monitoring stack — Loki, Prometheus, Grafana, Node Exporter — deployed via Podman Quadlet. Runs rootless under the `monitoring` user.

## Architecture

All services attach to `shared-network` (10.89.0.0/24).

```
  Node Exporter (9100) ──┐
                          ├── Prometheus (9090) ── Grafana (3000)
  Loki (3100) ────────────┘         ↑
                              alerting rules (CPU, disk, memory, systemd)
```

| Service | Image | Port | Purpose |
|---|---|---|---|
| Loki | grafana/loki:3 | 3100 (localhost) | Log aggregation |
| Prometheus | prom/prometheus:latest | 9090 (localhost) | Metrics + alerting |
| Grafana | grafana/grafana:latest | 3000 (all) | Dashboard UI |
| Node Exporter | prom/node-exporter:latest | 9100 (pod-internal) | Host metrics |

## Task Reference (8 tasks)

| # | Module | Purpose | Rationale |
|---|--------|---------|-----------|
| 1 | `copy` (loop 2) | Deploy static Quadlet files (`monitoring.pod`, `shared-network.network`) | Pod + network definitions are static |
| 2 | `template` (loop 4) | Render `.container.j2` → `.container` | Image tags and auto-update injected via vars |
| 3 | `file` | Ensure `configs/` directory | Host path for bind-mounted config files |
| 4 | `file` | Ensure `configs/dashboards/` directory | Grafana dashboard JSON files go here |
| 5 | `copy` (loop 5) | Deploy config files | Loki, Prometheus (scrape + rules), Grafana (datasources + dashboards) |
| 6 | `copy` (loop 1) | Deploy dashboard JSON | Node Exporter dashboard with 7 panels |
| 7 | `command` | `machinectl shell ... systemctl --user daemon-reload` | Re-read Quadlet files |
| 8 | `systemd` | Restart `user@<uid>.service` | Triggers Quadlet generator |

## Role Contract

Inherited from `site.yml`:

| Var | Description |
|---|---|
| `service_name` | `monitoring` |
| `service_user` | `monitoring` |
| `service_uid` | 1002 (default) |
| `service_home` | `/var/services/monitoring` |
| `service_repo` | `../service-monitoring` |

## Configuration

| Variable | Default | Controls |
|---|---|---|
| `monitoring_service_auto_update` | `registry` | Podman auto-update |
| `monitoring_service_grafana_image` | `grafana/grafana:latest` | Grafana image |
| `monitoring_service_loki_image` | `grafana/loki:3` | Loki image |
| `monitoring_service_prometheus_image` | `prom/prometheus:latest` | Prometheus image |
| `monitoring_service_node_exporter_image` | `prom/node-exporter:latest` | Node Exporter image |
| `monitoring_service_grafana_max_conns` | `2` | Grafana datasource proxy conns |

## Generalization Gaps

| What | Where | Hardcoded |
|---|---|---|
| Network subnet | `shared-network.network` | `10.89.0.0/24` |
| Grafana port (all interfaces) | `monitoring.pod` | `3000:3000` |
| Loki port (localhost only) | `monitoring.pod` | `127.0.0.1:3100:3100` |
| Prometheus port (localhost only) | `monitoring.pod` | `127.0.0.1:9090:9090` |
| Alert thresholds | `prometheus-rules.yaml` | CPU >80%, Disk <10%, Mem <10% |
| Podman container count panel | `node-exporter.json` | Queries `podman_container_running` but no scrape target |

## Files

```
service-monitoring/
  ansible-role/monitoring_service/
    defaults/main.yml         # 7 vars: images, auto_update, max_conns
    tasks/main.yml            # 8 tasks
  quadlets/
    monitoring.pod            # Pod: 3000(public), 3100+9090(localhost)
    shared-network.network    # Bridge 10.89.0.0/24
    monitoring-*.container.j2 # 4 templated container files
    configs/                  # Loki, Prometheus, Grafana configs
    configs/dashboards/       # Pre-built dashboards
```

## Known Issues

- **Container Count panel shows "No data"** — queries `podman_container_running` but Prometheus doesn't scrape Podman socket. Either add a Podman exporter or remove the panel.
- **Grafana uses `:latest`** — major version bumps could break on auto-update. Pin in `secrets/vars.yml`.
- **`shared-network.network` duplicated across repos** — each service user gets their own copy. Podman deduplicates by name, but differing subnet values would silently break.

## Deployment

```bash
ansible-playbook -i inventory site.yml --tags monitoring_service
```

## License

MIT
