# service-monitoring

Prometheus, Alertmanager, Grafana, Loki, Alloy and Node Exporter in one rootless
Podman pod under the `monitoring` user.

## Architecture

```
  node-exporter (9100) ─┐
                         ├─ prometheus (9090) ─ alertmanager (9093) ─ ntfy
  host journal ─ alloy ──┤                                  │
                         └─ loki (3100) ──────── grafana (3000)
```

Everything shares the pod's network namespace and talks over `localhost`.
Alloy reads `/var/log/journal` directly: every service user's containers log to
journald (Podman's default log driver), so one shipper covers the whole host.
The `monitoring` user is in `systemd-journal` and `GroupAdd=keep-groups` carries
that into the container.

| Container | Image | Purpose |
|---|---|---|
| monitoring-prometheus | prom/prometheus:v3.5.0 | Metrics + alert rules |
| monitoring-alertmanager | prom/alertmanager:v0.28.1 | Alert routing to ntfy |
| monitoring-grafana | grafana/grafana:11.5.2 | Dashboards |
| monitoring-loki | grafana/loki:3.5.0 | Log store |
| monitoring-alloy | grafana/alloy:v1.10.0 | Journal → Loki |
| monitoring-node-exporter | prom/node-exporter:v1.9.1 | Host metrics incl. hwmon |

All ports are published on `127.0.0.1` only. Reach Grafana with
`ssh -L 3000:localhost:3000 core@host`.

## Alerts

`quadlets/configs/prometheus-rules.yaml`: target down, CPU > 80 %, disk < 10 %,
memory < 10 %, temperature > 85 °C. Delivery via Alertmanager webhook to
`monitoring_service_ntfy_url` (empty = evaluated, not delivered).

## Configuration

| Variable | Default | Controls |
|---|---|---|
| `monitoring_service_*_image` | see `defaults/main.yml` | Pinned image tags |
| `monitoring_service_*_extra_args` | `--memory=...` | Per-container ceilings (8 GB host) |
| `monitoring_service_ntfy_url` | `""` | ntfy topic URL for alerts |
| `monitoring_service_auto_update` | `registry` | Podman auto-update |
| `monitoring_service_grafana_max_conns` | `2` | Grafana datasource proxy conns |

## Role Contract

Inherited from `site.yml`: `service_name`, `service_user`, `service_uid`,
`service_home`, `service_repo`. File tasks notify `monitoring quadlets changed`
(daemon-reload + pod restart), so a deploy without changes touches nothing.

## Development

```bash
pre-commit install --install-hooks -t pre-commit -t commit-msg -t pre-push
```

Plain `pre-commit install` wires up only the pre-commit stage, so the
commitizen message and branch checks stay dormant. Hooks: shellcheck,
ansible-lint (which owns YAML style here), commitizen for conventional commits.
CI runs the same set on push and pull request. Actions are pinned to SHAs, and
dependabot updates actions and hook revisions weekly against `dev`.

## License

MIT
