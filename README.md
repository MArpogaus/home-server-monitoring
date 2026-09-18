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

Everything shares the pod's network namespace and talks over `127.0.0.1`. A
rootless pod binds IPv4 only, and `localhost` resolves to `::1` first.

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
| monitoring-ntfy | binwiederhier/ntfy:v2.11.0 | Push notifications for alerts |

All ports are published on `127.0.0.1` only. Reach Grafana with
`ssh -L 3000:localhost:3000 core@host`.

## Alerts

Two sources, both delivered through Alertmanager to ntfy, which runs in this
pod. Alerts therefore reach no third party. Set `monitoring_service_ntfy_url`
to a hosted topic to change that, or empty to evaluate and discard.

ntfy keeps no message history: a notification is pushed when it happens, and
that is all an alert needs. Reach it the same way as Grafana,
`ssh -L 8081:localhost:8081 core@host`, and subscribe to the `alerts` topic
from the phone app.

CAUTION: ntfy listens on loopback only, so the phone gets nothing while it is
away from the host. Publishing it needs a second BunkerWeb site and an ntfy
access token. That is a deliberate exposure, so it is not done here.

A notifier on this machine cannot report that this machine is down. `TargetDown`
is the one alert it cannot deliver. A heartbeat to something off the box is
what covers that.

`quadlets/configs/prometheus-rules.yaml` covers the host: target down,
CPU > 80 %, disk < 10 %, memory < 10 %, temperature > 85 °C.

`quadlets/configs/loki-rules.yaml` covers the containers, using the journal
Loki already ingests rather than another exporter. `ContainerRestartLoop` fires
when a unit restarts more than five times in fifteen minutes, and
`ContainerFailed` catches a unit that gave up and therefore stopped to produce
restart messages.

Those two rules group by `user_unit`, which only a rootless container has. The
backup, the snapshot and the database dump are system units and log under
`unit`. `ScheduledJobFailed` covers these three, over a six-hour window,
because they run once a day.

## Configuration

| Variable | Default | Controls |
|---|---|---|
| `monitoring_service_*_image` | see `defaults/main.yml` | Pinned image tags |
| `monitoring_service_*_extra_args` | `--memory=...` | Per-container ceilings (8 GB host) |
| `monitoring_service_ntfy_url` | `""` | ntfy topic URL for alerts |
| `monitoring_service_auto_update` | `registry` | Podman auto-update |
| `monitoring_service_grafana_max_conns` | `2` | Grafana datasource proxy conns |

## Role Contract

Inherited from `site.yml`: `service_name`, `service_user`, `service_home`,
`service_repo`. File tasks notify `monitoring quadlets changed`
(daemon-reload + pod restart), so a deploy without changes touches nothing.

## Development

Work on `dev`. Conventional commits.

```bash
pre-commit install --install-hooks -t pre-commit -t commit-msg -t pre-push
```

Plain `pre-commit install` wires up the pre-commit stage only, which leaves the
commit-message and branch hooks dormant.

## License

MIT
