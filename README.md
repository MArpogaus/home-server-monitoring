# service-monitoring

Monitoring stack — Loki, Prometheus, Grafana, Node Exporter — deployed via Podman Quadlet. Runs rootless under the `monitoring` user.

## Structure

```
service-monitoring/
├── ansible-role/monitoring_service/
│   ├── defaults/main.yml            Role default variables (image tags, auto_update)
│   └── tasks/main.yml               Deployment tasks
├── quadlets/
│   ├── monitoring.pod               Pod definition (publishes 3000, 3100, 9090)
│   ├── *.container.j2               Container Quadlet templates (4, templated)
│   ├── shared-network.network       Bridge network (10.89.0.0/24)
│   └── configs/
│       ├── loki.yaml                Loki storage and schema
│       ├── prometheus.yaml          Scrape targets (node, podman containers)
│       ├── prometheus-rules.yaml    Alerting rules
│       ├── grafana-datasources.yaml  Provisioned datasources
│       ├── grafana-dashboards.yaml   Dashboard provisioning
│       └── dashboards/              Pre-built dashboards
└── .github/workflows/               CI/CD
```

## Architecture

All services attach to `shared-network` (10.89.0.0/24).

| Service | Image | Port | Purpose |
|---|---|---|---|
| Loki | grafana/loki:3 | 3100 | Log aggregation |
| Prometheus | prom/prometheus:latest | 9090 | Metrics collection |
| Grafana | grafana/grafana:latest | 3000 | Dashboard UI |
| Node Exporter | prom/node-exporter:latest | 9100 | Host metrics |

Prometheus scrapes Node Exporter (host metrics) and Podman containers (via `io.containers.autoupdate=registry` label).

## Role Contract

Inherited variables from `site.yml`:

| Var | Description |
|-----|-------------|
| `service_name` | Service name (`monitoring`) |
| `service_user` | System user (`monitoring`) |
| `service_uid` | User UID (default: 1002) |
| `service_home` | Home dir (`/var/services/monitoring`) |
| `service_repo` | Repo path (`../service-monitoring`) |

Role tasks:
1. Copy static Quadlet files (pod, network)
2. Template `.container.j2` files (4 containers)
3. Copy config files to `configs/` and `configs/dashboards/`

## Configuration

Image tags and container options are configurable via `defaults/main.yml`:

| Variable | Default |
|---|---|
| `monitoring_service_auto_update` | `registry` |
| `monitoring_service_grafana_image` | `docker.io/grafana/grafana:latest` |
| `monitoring_service_loki_image` | `docker.io/grafana/loki:3` |
| `monitoring_service_prometheus_image` | `docker.io/prom/prometheus:latest` |
| `monitoring_service_node_exporter_image` | `docker.io/prom/node-exporter:latest` |
| `monitoring_service_grafana_max_conns` | `2` |

Override in `secrets/vars.yml` to pin versions or use custom registries.

## Deployment

```bash
ansible-playbook -i inventory site.yml --tags monitoring_service
```

## Requirements

- Podman 4.0+ (Quadlet)
- systemd user instances
- Ansible

## License

MIT
