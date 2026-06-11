# service-monitoring

Monitoring stack for Podman Quadlet -- Loki, Prometheus, Grafana, Node Exporter, and Promtail.

## Services

| Service | Image | Port | Purpose |
|---|---|---|---|
| Loki | grafana/loki:3 | 3100 | Log aggregation |
| Prometheus | prom/prometheus:latest | 9090 | Metrics collection |
| Grafana | grafana/grafana:latest | 3000 | Dashboard UI |
| Node Exporter | prom/node-exporter:latest | 9100 | Host metrics |
| Promtail | grafana/promtail:3 | -- | Log shipping |

## Usage

Deploy via Ansible:

```bash
ansible-playbook -i inventory site.yml --tags monitoring
```

Or manually as the `monitoring` user:

```bash
cp -r quadlets/* ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start monitoring-{loki,prometheus,grafana,node-exporter,promtail}
```

## Network

All services attach to `shared-network` (bridge 10.89.0.0/24).

## Ansible Role

The `ansible-role/monitoring_service/` role copies Quadlet files, reloads systemd, and starts services.

## Configuration

- `quadlets/configs/loki.yaml` -- Loki storage and schema
- `quadlets/configs/prometheus.yaml` -- Scrape targets (node, podman containers)
- `quadlets/configs/promtail.yaml` -- Journal and log scraping
