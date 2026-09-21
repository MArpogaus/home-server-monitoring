# service-monitoring

Prometheus, Alertmanager, Grafana, Loki, Alloy, Node Exporter and ntfy in one
rootless Podman pod under the `monitoring` user. The host is set up by
`ansible-base`, whose README is the entry point for the project.

## Architecture

```
  node-exporter (9100) ─┐
                         ├─ prometheus (9090) ─ alertmanager (9093) ─ ntfy (8081)
  host journal ─ alloy ──┤                                  │
                         └─ loki (3100) ──────── grafana (3000)
```

Everything shares the pod's network namespace and talks over `127.0.0.1`. A
rootless pod binds IPv4 only, and `localhost` resolves to `::1` first.

Alloy reads `/var/log/journal` directly: every service user's containers log
to the host journal, so one shipper covers the whole host. The `monitoring`
user is in `systemd-journal` and `GroupAdd=keep-groups` carries that into the
container.

| Container | Image | Purpose |
|---|---|---|
| monitoring-prometheus | prom/prometheus:v3.14.0 | Metrics + alert rules |
| monitoring-alertmanager | prom/alertmanager:v0.34.1 | Alert routing to ntfy |
| monitoring-grafana | grafana/grafana:13.2.2 | Dashboards |
| monitoring-loki | grafana/loki:3.7.8 | Log store and log alert rules |
| monitoring-alloy | grafana/alloy:v1.19.2 | Journal → Loki |
| monitoring-node-exporter | prom/node-exporter:v1.12.1 | Host metrics incl. hwmon |
| monitoring-ntfy | binwiederhier/ntfy:v2.28.0 | Push notifications |

Grafana, Loki and Prometheus are published on the host's `127.0.0.2`, ntfy on
`127.0.0.1`. pasta maps only `127.0.0.1` into the proxy pod, so the proxy can
reach ntfy and nothing else of this stack. Reach Grafana with
`ssh -L 3000:127.0.0.2:3000 core@host`, user `admin`, password
`monitoring_service_grafana_admin_password`. securecore's sshd forbids
forwarding; `base_setup` allows local forwarding for the admin user alone.

## Dashboards

Four provisioned dashboards under `quadlets/configs/dashboards/`, linked to
each other in the top bar. They are generated: `gen_dashboards.py` holds the
queries and the layout, its output is committed, and a change is made in the
generator.

| Dashboard | Source | Shows |
|---|---|---|
| Host | Prometheus | CPU, load, memory, pressure stall, disks, network, temperatures |
| Proxy | Loki | BunkerWeb access log by site and status, denials, ModSecurity rule hits, bans, certificate events |
| Nextcloud | Loki | nginx access log (latency, status, clients, paths), the Nextcloud log by level, failed logins, background containers, database |
| System log | Loki, Prometheus | Active alert count, firing Prometheus alerts, unit failures, container state changes, restarts, SSH logins, updates and boots, scheduled jobs, SELinux and OOM, image pulls |

Every dashboard has the same rows: **Now** (stat tiles and gauges for the
last hour or day), **Trends** (time series, table legends with mean, max and
last), **Details** (top-10 tables over the dashboard range) and **Logs**.
Every panel carries a description (the `i` icon) that says what the numbers
mean and which alert belongs to them. Counts per interval are bars, rates
are stacked areas, and gaps stay gaps: `spanNulls` is off because it drew
straight ramps across hours without log lines.

Two Loki traps the generator handles: `| json` turns every field into a
label, so an unwrap aggregation without `by (job)` returns one series per
log line; and label-less series from several queries all take the first
query's legend, so a static legend becomes a `series` label via
`label_replace`. Loki alerts have no queryable state for a table: the
ruler's `remote_write` into Prometheus stalled every evaluation
(`appender not ready`) and was reverted, so only Prometheus alerts are
listed and Loki's show up as events in the log panels.

Unit and container state are derived from the journal, not from metrics.
node-exporter's systemd collector would need this container to run as root
with the host's `/run/systemd/private` socket mounted, which is more privilege
than a state band is worth, and podman publishes container start, death and
health events to the journal anyway. The SSH panels read `sshd.service`
directly; the community SSH dashboards on grafana.com expect promtail reading
`/var/log/auth.log` and match on `sshd[`, a prefix journald strips, so their
queries find nothing here without rewriting.

The Loki panels parse the nginx JSON access log and BunkerWeb's access line
with `pattern`; a change to either format is a change to the generator. The
datasources carry fixed uids (`prometheus`, `loki`, `alertmanager`) so the
dashboards work on every host.

The pod runs with `LogDriver=passthrough` (`quadlets/container.d/log.conf`).
Loki has no health check: its image carries neither `wget` nor a shell, so
every `HealthCmd` exits 1, and with `HealthOnFailure=kill` that was a restart
every eight minutes. `Restart=on-failure` covers a real crash. After a pod
restart Loki takes a SIGKILL (Quadlet's `pod stop --time=10`), recovers from
its WAL and needs some minutes before `/ready` answers.

## Alerts

Two sources, one delivery path. Prometheus rules cover the host. Loki rules
read the journal Alloy already ships, so there is no podman exporter. Both go
to Alertmanager, which delivers to ntfy in this pod. Alerts reach no third party.
Set `monitoring_service_ntfy_url` to a hosted topic to change that. ntfy caches the topic in a volume for 72 h, so a
phone that was offline catches up and a reboot keeps the history.

Two kinds share the topic. **States** fire, repeat every 12 h and send a
resolved message. **Events** carry `severity: info`, are sent once and never
resolve (route `ntfy-events`, `send_resolved: false`).

ntfy renders each notification from the Alertmanager payload (`template=yes`
in the webhook URL, the template URL-encoded in `alertmanager.yaml.j2`):

```
title:   {{.commonLabels.alertname}}{{if eq .status "resolved"}} resolved{{end}}
message: {{.commonAnnotations.summary}}
         {{.commonAnnotations.description}}     (when the rule has one)
```

So a rule's `summary` is the whole message on the phone; keep it one line and
put the labels that matter into it. Alertmanager groups by nothing
(`group_by: ['...']`): a group of two alerts shares only the annotations both
carry, and ntfy renders a missing `summary` as `<no value>`.

| Alert | Source | Kind |
|---|---|---|
| `TargetDown`, `HostHighCpuLoad`, `HostLowDiskSpace` (`/var`, where everything lives on ostree), `HostMemoryLow`, `HostHighTemperature` | Prometheus | state |
| `PublicUrlDown` (a probed URL fails: DNS, TLS, WAF or backend), `CertificateExpiresSoon` (< 14 days) | Prometheus, blackbox exporter | state |
| `ContainerRestartLoop` (>5 restarts in 15 min), `ContainerFailed` | Loki, by `user_unit` | state |
| `ScheduledJobFailed` (backup, snapshot, dump; 6 h window), `BackupMissing`, `SnapshotMissing`, `DumpMissing` (nothing finished in 30 h) | Loki, by `unit` | state |
| `OomKill`, `SelinuxDenials` (>20 enforced in 15 min, pasta excluded), `BunkerWebError`, `CertificateRenewalFailed` | Loki | state |
| `SshLogin` (user, IP), `SshLoginFailed`, `NextcloudLoginFailed` (user, IP), `HostBooted`, `UpdateStaged`, `AutoReboot`, `BunkerWebBan` (IP), `BackupDone`, `ImagePulled` (image) | Loki | event |

A container restart loop is the single highest-value alert here: it is how
every container-level bug in this project first showed itself. A deploy that
restarts a pod produces restart lines too, so both can fire once after a
deploy and resolve within fifteen minutes.

The Loki rules select on `job="systemd-journal"` and on `unit` / `user_unit`.
`config.alloy` sets both and says why. The systemd rules also select
`syslog_identifier="systemd"`: Loki's ruler quotes every rule's match string
in its own log, and at log level info those lines tripped the rules they came
from (`loki.yaml` runs it at `warn` for the same reason).

The blackbox exporter probes `monitoring_service_probe_urls` from the host
through the public path (hairpin NAT), so a broken DNS record, an expired
certificate or a WAF that bans everyone shows up before a user reports it.

A notifier on this machine cannot report that this machine is down. A
heartbeat to something off the box would cover that; it is not done.

### Reaching ntfy

ntfy listens on the pod loopback and is published on the host's `127.0.0.1:8081`.
The phone reaches it in one of two ways: an SSH tunnel
(`ssh -L 8081:localhost:8081 core@host`) or BunkerWeb, which serves ntfy on a
name of its own with TLS (`bunker_service_ntfy_server_name` in
`service-bunker`). Set `monitoring_service_ntfy_base_url` to that address so
links in a notification point at it. Subscribe to the topic `alerts` with
`monitoring_service_ntfy_user` and its password.

Authentication is ntfy's own (`NTFY_AUTH_*`, default `deny-all`): one user
for the phone, one token for Alertmanager, both declared in the container
unit and synced into `user.db` at every start. ntfy authenticates rather than
the proxy, so the phone and the machines have separate credentials, a token
can be revoked on its own, and the proxy does not count the phone's `401`
round trips as bad behaviour. Anything on the host that posts to the topic
sends the token too.

The topic is also a channel:
`curl -H "Authorization: Bearer $token" -d "text" http://127.0.0.1:8081/alerts`
on the host posts a message, and
`curl -H "Authorization: Bearer $token" 'http://127.0.0.1:8081/alerts/json?poll=1&since=<epoch>'`
reads what was posted, 72 hours back.

### When alerts do not arrive

Confirm the rules loaded and the route exists:

```bash
curl -s http://127.0.0.2:3100/loki/api/v1/rules | grep -c 'alert:'
curl -s http://127.0.0.2:9090/api/v1/rules | head
curl -s -G http://127.0.0.2:3100/loki/api/v1/label/job/values          # systemd-journal
podman exec monitoring-alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 alert query
```

A test alert through the whole path:

```bash
podman exec monitoring-alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 \
  alert add PathTest severity=info instance=host --end="$(date -u -d '+3 min' +%FT%TZ)"
```

## Configuration

| Variable | Default | Controls |
|---|---|---|
| `monitoring_service_*_image` | see `defaults/main.yml` | Pinned image tags |
| `monitoring_service_*_extra_args` | `--memory=...` | Per-container ceilings (8 GB host) |
| `monitoring_service_ntfy_url` | ntfy in the pod | Alertmanager's target; loopback on purpose, so alerts arrive when the proxy is down |
| `monitoring_service_ntfy_base_url` | loopback | The address the phone uses |
| `monitoring_service_grafana_admin_password` | `""` | Set it: the proxy pod can reach Grafana, and empty leaves `admin/admin` |
| `monitoring_service_probe_urls` | `[]` | Public URLs the blackbox exporter probes every minute; 2xx passes |
| `monitoring_service_ntfy_user` / `_password` | `ntfy` / required | The phone's login |
| `monitoring_service_ntfy_token` | required | Alertmanager's and deploy.sh's bearer token (`tk_` + 29 lowercase alphanumerics) |

### File modes

`nextcloud` and `bunker` template their environment files `0600`; this repo
does not, and that is deliberate. An `EnvironmentFile` is read by the user's
systemd manager, so `0600` costs nothing. `alertmanager.yaml` carries the ntfy
token but is bind-mounted into a container that runs as `nobody`, which maps
to a subuid outside the service user: `0600` makes it unreadable and
Alertmanager exits. The service home is `0750`, so only this user and root
reach either file.

## Role contract

Inherited from `site.yml`: `service_name`, `service_user`, `service_home`,
`service_repo`. The role imports `quadlet_service` from `ansible-base`, which
deploys everything under `quadlets/`: `.j2` files are templated, all other
files are copied, and the pod restarts only when one of them changed.
`alertmanager.yaml.j2` is the one templated config file.

## Development

Work on `dev`. Conventional commits. Hook setup: `ansible-base/README.md`.

Dashboards are checked visually before a commit: `grafana-image-renderer`
joined to the monitoring pod on the test VM (`SERVER_ADDR=:8082`, a shared
`AUTH_TOKEN`, Grafana gets `GF_RENDERING_SERVER_URL`, `_CALLBACK_URL` and
`_RENDERER_TOKEN` through a `monitoring-grafana.container.d` drop-in), then
`GET /render/d/<uid>/<uid>?kiosk&width=1600&height=-1` per dashboard. Copying
`quadlets/configs/dashboards/` into the VM's configs directory reloads them
within ten seconds, so an iteration costs under a minute. The next deploy
removes the drop-in again.

## License

MIT
