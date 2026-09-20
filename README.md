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
| monitoring-prometheus | prom/prometheus:v3.14.0 | Metrics + alert rules |
| monitoring-alertmanager | prom/alertmanager:v0.34.1 | Alert routing to ntfy |
| monitoring-grafana | grafana/grafana:13.2.2 | Dashboards |
| monitoring-loki | grafana/loki:3.7.8 | Log store |
| monitoring-alloy | grafana/alloy:v1.19.2 | Journal → Loki |
| monitoring-node-exporter | prom/node-exporter:v1.12.1 | Host metrics incl. hwmon |
| monitoring-ntfy | binwiederhier/ntfy:v2.28.0 | Push notifications for alerts |

All ports are published on `127.0.0.1` only. Reach Grafana with
`ssh -L 3000:localhost:3000 core@host`.

## Alerts

Two sources, both delivered through Alertmanager to ntfy, which runs in this
pod. Alerts therefore reach no third party. Set `monitoring_service_ntfy_url`
to a hosted topic to change that, or empty to evaluate and discard.

ntfy keeps no message history: a notification is pushed when it happens, and
that is all an alert needs.

Two kinds of alert share the topic. States (a failing container, a failed
backup, low disk) fire, repeat every 12 h and send a resolved message. Events
(an SSH login with user and IP, a host boot, a staged update, a BunkerWeb ban,
a BunkerWeb critical error) carry `severity: info`, are sent once and never
resolve. The event rules read the journal through Loki, so they depend on
Alloy labelling the stream `job="systemd-journal"`; Alloy 1.19 stopped doing
that by itself, and `config.alloy` pins the label.

ntfy listens on loopback, so the phone reaches it in one of two ways. Over an
SSH tunnel, `ssh -L 8081:localhost:8081 core@host`, and subscribe to the
`alerts` topic. Or through BunkerWeb, which serves ntfy on a name of its own
with TLS and basic auth. See `bunker_service_ntfy_server_name` in
`service-bunker`. Set `monitoring_service_ntfy_base_url` to that same address.

Alerts must not depend on the services they watch, so ntfy runs in the
monitoring pod and not behind Nextcloud. A Nextcloud outage still reaches the
phone.

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
`service_repo`. The role imports `quadlet_service` from `ansible-base`, which
deploys everything under `quadlets/`: `.j2` files are templated, all other
files are copied, and the pod restarts only when one of them changed.
Nothing beyond the shared role: every file under `quadlets/configs/` deploys as it is, `alertmanager.yaml.j2` is the one template.

## Development

Work on `dev`. Conventional commits.

```bash
pre-commit install --install-hooks -t pre-commit -t commit-msg -t pre-push
```

Plain `pre-commit install` wires up the pre-commit stage only, which leaves the
commit-message and branch hooks dormant.

## License

MIT
