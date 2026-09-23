# home-server-monitoring

Prometheus, Alertmanager, Grafana, Loki, Alloy, node-exporter, blackbox-exporter
and ntfy in one rootless Podman pod, with an Ansible role that deploys them.

Alloy reads the host journal and sends it to Loki. Prometheus and Loki both
raise alerts. Alertmanager sends each one to ntfy, which the phone reads.
`home-server` prepares the host. Every repository of the deployment brings
its own rules and dashboards: see "Monitoring files of a repository".

## Architecture

```
  node-exporter (9100) ──┐
  blackbox (9115) ───────┤
                         ├─ prometheus (9090) ─ alertmanager (9093) ─ ntfy (8081)
  host journal ─ alloy ──┤                                  │
                         └─ loki (3100) ──────── grafana (3000)
```

Everything shares the pod's network namespace and talks over `127.0.0.1`. A
rootless pod binds IPv4 only, and `localhost` resolves to `::1` first.

Alloy reads `/var/log/journal` directly. The containers of every service user
log to the host journal, so one shipper covers the whole host. The `monitoring`
user is in `systemd-journal`, and `GroupAdd=keep-groups` carries that into the
container. Alloy runs as uid 473, which owns `/var/lib/alloy` in the image. The
journal mount carries no `:z`: relabelling `/var/log/journal` breaks journald
for the whole host.

node-exporter mounts `/` at `/rootfs` with `rslave`, so a backup target that
its automount mounts later still appears. It reads the container's own `/proc`,
which carries the host mounts under `/rootfs`: a rootless container cannot read
the host's `/proc/1/mountinfo`.

| Container | Purpose |
|---|---|
| monitoring-prometheus | Metrics + alert rules |
| monitoring-alertmanager | Alert routing to ntfy |
| monitoring-grafana | Dashboards |
| monitoring-loki | Log store and log alert rules |
| monitoring-alloy | Journal → Loki |
| monitoring-node-exporter | Host metrics, hwmon, textfile metrics |
| monitoring-blackbox | Probes the public URLs from the host |
| monitoring-ntfy | Push notifications |

The images and their pinned tags are in `defaults/main.yml`.

### Access

Grafana and Prometheus are published on the host's `127.0.0.2`, ntfy on
`127.0.0.1`. pasta maps only `127.0.0.1` into the proxy pod, so the proxy can
reach ntfy and nothing else of this stack. Loki is not published: every local
user could push forged lines into it. Outside the pod, read Loki through
Grafana's datasource proxy with the admin login:
`http://127.0.0.2:3000/api/datasources/proxy/uid/loki/loki/api/v1/...`. Loki
refuses delete requests (`deletion_mode: disabled`).

Reach Grafana with `ssh -L 3000:127.0.0.2:3000 core@host`, user `admin`,
password `monitoring_service_grafana_admin_password`. The image's sshd forbids
port forwarding, and the host's own task file gives the admin user the local
kind back.

The pod mounts every config file read-only, and `quadlet_service` repairs drift
on the next deploy. Loki has no health check: its image carries neither `wget`
nor a shell, so every `HealthCmd` fails and `HealthOnFailure=kill` restarts it.
After a pod restart Loki recovers from its WAL and needs some minutes before
`/ready` answers.

### Loki stream limit

Loki keeps one stream per combination of label values and stops at 5000 per
tenant. Above that limit Loki refuses the pushes, the log alerts go blind, and
`LokiDiscardingLogs` fires. Unbounded unit names reach the limit: every `run0`
call makes a `run-<pid>-<id>.service` and a `session-<n>.scope`, every
container start a `libpod-<id>.scope`, and every health check a
`<container id>-<n>.timer` and `.service`. `loki.relabel "journal"` rewrites
all four to `transient`.

A compromised container can still reach the limit on purpose, because
`syslog_identifier` has no bound. This project accepts that risk:
sender-controlled labels are what make the log useful per container, and
`LokiDiscardingLogs` fires.

Loki answers a stream above the limit with 429, and Alloy backs off up to five
minutes per attempt. When the stream count is below the limit again, restart
`monitoring-alloy.service`.

### Alloy watch on the journal

container-selinux gives `container_logreader_t` read access to the host's logs
and no `watch`. sd-journal follows a local journal only by an inotify watch on
`/var/log/journal`, and libsystemd ignores a refused watch. Without the watch
Alloy drains the journal once at start and then waits for ever, healthy and
silent.

The role therefore loads the policy module
`files/alloy_journal_watch.cil`, which grants `watch` on `var_log_t`
directories. Alloy installs its watch once, at the first read, so the role
restarts the pod in the run that loads the module. The denial without it reads:

```
avc: denied { watch } for comm="alloy" path="/var/log/journal"
  scontext=system_u:system_r:container_logreader_t:s0:c167,c828
  tcontext=system_u:object_r:var_log_t:s0 tclass=dir
```

## Monitoring files of a repository

A repository of the deployment may carry a `monitoring/` directory:

```
monitoring/
  prometheus-rules.yaml   Prometheus rule groups
  loki-rules.yaml         Loki ruler groups
  dashboards/*.json       Grafana dashboards
  alloy-drop.txt          journal lines Alloy drops
  alloy-redact.txt        values Alloy redacts
```

The role collects the directory from `home-server` and from the repository
of each entry in `base_setup_services`, on the controller, at every deploy. The
rule files go out verbatim: they are not templates. Each dashboard goes out with
the link bar that the role adds (see below). Each repository becomes one
Prometheus rule file, one Loki rule namespace and one Grafana folder, all named
after the repository. A file that leaves a repository leaves the host on the
next deploy. Only this role writes the rules, as the `monitoring` user, so no
service can change the alerts of another.

The two Alloy files hold one RE2 regex per line; `#` lines and blank lines do
not count. Alloy applies them to every journal line, whoever wrote it, before
the line reaches Loki. A line that matches a pattern in `alloy-drop.txt` is
dropped, counted under the repository's name in
`loki_process_dropped_lines_total`. In a line that matches a pattern in
`alloy-redact.txt`, the pattern's first capture group becomes `<redacted>`, so a
repository keeps its own credentials out of Loki. The owning repository's README
says what each pattern is for.

Check a rule file before you commit it:

```bash
promtool check rules monitoring/prometheus-rules.yaml
lokitool rules lint --dry-run monitoring/loki-rules.yaml
```

A rule obeys these conventions:

- `labels.severity` is `critical`, `warning` or `info` ("Alerts").
- `annotations.summary` is one line and names what is wrong. It is the whole
  message on the phone.
- The alert name starts with the service's name.
- A Loki rule selects only on the labels in "Labels", and pins the labels that
  the sender cannot write ("Writing a log rule").
- A dashboard carries the tag `home-server` and no `links`. The role writes one
  link bar into every dashboard it deploys: this repository's dashboards first,
  then `home-server`'s, then each service's in the order of
  `base_setup_services`. Every page shows the same buttons in the same place,
  the current one included. Dashboards use the datasource uids `prometheus` and
  `loki`.

A job that must run on a schedule reports success as a textfile metric. The
host keeps `/var/lib/node-textfile/`, root-owned, and node-exporter reads every
`*.prom` file in it. The job writes a temporary file and renames it to
`<name>.prom`, so node-exporter never reads half a file. A rule then compares
the timestamp with `time()`. A log line cannot fake that metric.

## Labels

Alloy sets these labels on every journal line. Rules and dashboards use no
other label.

| Label | From | Sender can write it |
|---|---|---|
| `job` | always `systemd-journal` | no |
| `unit`, `user_unit` | `UNIT`/`USER_UNIT`, else `_SYSTEMD_UNIT`/`_SYSTEMD_USER_UNIT` | yes |
| `syslog_identifier` | `SYSLOG_IDENTIFIER` | yes |
| `container` | `CONTAINER_NAME` | yes |
| `priority` | `PRIORITY` | yes |
| `transport` | `_TRANSPORT` | no |
| `emitter_uid` | `_UID` | no |
| `service` | `_UID` equal to the uid of an entry in `base_setup_services` | no |

`service` covers what a service user's own processes write: its user manager,
and the output of its containers (`LogDriver=passthrough`). A process inside a
container that writes to `/dev/log` runs under a subuid and gets no `service`.

A manager's message about a unit carries the subject in `UNIT` or `USER_UNIT`,
so the labels name the unit and not the manager.

## Writing a log rule

The window of a count rule must outlast its `for:`. One log line makes
`count_over_time(...[w]) > 0` true for exactly `w`, so a single occurrence
never reaches `for: w`.

A rule that must not be forgeable pins a label that journald sets:
`emitter_uid="0"` for PID 1 and root daemons, `service` for a service user's
manager and container output, `transport` for kernel and audit records. A rule
on a line written through `/dev/log` can only select on `syslog_identifier`,
which any container can forge; keep such rules to the service's own
application log.

Loki's ruler quotes every rule's match string in its own log, so `loki.yaml`
runs the server at `warn`.

## Alerts

| Severity | Meaning | ntfy priority | Repeats | Resolved message |
|---|---|---|---|---|
| `critical` | Data at risk, or a service is down | 5 | every 4 h | yes |
| `warning` | Needs attention within a day | 3 | every 24 h | yes |
| `info` | An event | 2 | every 1 h while it fires | no |

The rules are in `*/monitoring/*-rules.yaml` of every repository: this
repository's cover the host and the monitoring itself, `home-server`'s the
host jobs, and each service its own. A rule's `summary` is the notification
text.

This repository's rules:

| Alert | Severity | Fires when |
|---|---|---|
| `ContainerRestartLoop` | critical | A service's unit restarts more than 5 times in 15 minutes |
| `UserUnitFailed` | warning | A service's unit fails, `podman-auto-update.service` included |
| `TargetDown` | critical | A scrape target is down for 5 minutes |
| `HostMemoryPressure` | critical | Tasks stall on memory for more than 10 % of the time for 10 minutes |
| `OomKill` | warning | The kernel OOM killer ends a process |
| `ZramFull` | warning | zram swap is more than 80 % full for 15 minutes |
| `HostLowDiskSpace` | critical | `/var` has less than 10 % free for 5 minutes |
| `HostDiskFillsSoon` | warning | At the trend of the last 6 h, `/var` fills within 3 days, for 6 hours |
| `BtrfsDeviceErrors` | critical | A Btrfs device counts a new error |
| `HostHighTemperature` | warning | A sensor reads above 85 °C for 10 minutes |
| `ClockNotSynchronised` | warning | The clock is not synchronised for 30 minutes |
| `HostBooted` | info | The host booted in the last 15 minutes |
| `PublicUrlDown` | critical | A probe URL fails for 5 minutes |
| `CertificateExpiresSoon` | warning | A probed certificate expires within 14 days |
| `CertificateExpiring` | critical | A probed certificate expires within 3 days |
| `LogShippingStopped` | critical | Alloy sends no line to Loki for 30 minutes |
| `LokiDiscardingLogs` | critical | Loki discards lines at a stream or rate limit |
| `RuleEvaluationFailing` | critical | A Prometheus or Loki rule fails to evaluate |
| `TextfileScrapeError` | critical | node-exporter cannot read a textfile metric |
| `AlertDeliveryFailing` | critical | Alertmanager cannot deliver a notification |

Alertmanager groups by every label (`group_by: ['...']`), so each alert is a
group of its own. A group shares only the annotations common to all its alerts,
and ntfy renders a missing one as `<no value>`. Prometheus labels its alerts
`source: prometheus`. While `LogShippingStopped` fires, Alertmanager holds back
every alert without that label, so a blind Loki does not page its log rules as
failures.

A notifier on this machine cannot report that the machine is down. This
project has no heartbeat to a system off the box.

## Dashboards

**Host** and **System log** live in `monitoring/dashboards/`; a service's
dashboards live in its repository. Every dashboard is JSON maintained by hand:
edit it in Grafana, export it, delete its `links`, and commit it. Three query
conventions keep a panel correct:

- A metric query over `| json` aggregates `by (...)`, because every JSON field
  becomes a label and each log line otherwise becomes a series.
- An `unwrap` carries `| __error__=""`, so a line without the field drops out.
- Label-less series from several queries take the first query's legend; give
  each a name with `label_replace`.

Unit and container state come from the journal: SELinux denies `container_t`
the system D-Bus socket that node-exporter's systemd collector needs, and an
allow rule would open every D-Bus service to a container.

## Reaching ntfy

ntfy listens on the pod loopback. The pod publishes it on the host's
`127.0.0.1:8081`. The phone reaches it through an SSH tunnel
(`ssh -L 8081:localhost:8081 core@host`) or through the reverse proxy, which
serves ntfy on a name of its own with TLS. Set
`monitoring_service_ntfy_base_url` to that address, so that links in a
notification point at it. Subscribe to the topic `alerts` with the user `ntfy`
and `monitoring_service_ntfy_password`.

ntfy does its own authentication (`NTFY_AUTH_*`, default `deny-all`). One user
is for the phone and one token is for Alertmanager. Every start syncs both into
`user.db`. The phone and the machines therefore have separate credentials, a
token can be revoked on its own, and the proxy does not count the phone's `401`
round trips as bad behaviour. The bcrypt salt derives from the token, so the
unit is stable across runs and the salt reveals nothing about the password.

On the host, with the header `Authorization: Bearer <token>` in a curl config
on stdin (`curl -K -`, as in "Operations"),
`curl -K - -d "text" http://127.0.0.1:8081/alerts` posts a message and
`curl -K - 'http://127.0.0.1:8081/alerts/json?poll=1&since=<epoch>'` reads the
cached messages.

## Operations

### When alerts do not arrive

Loki answers only inside the pod. Query it through Grafana's datasource proxy
with the admin credential. `curl -K -` reads the credential from stdin, so it
is on no command line.

```bash
L=http://127.0.0.2:3000/api/datasources/proxy/uid/loki
read -rs PW; cfg="user = \"admin:$PW\""
curl -s -K - "$L/loki/api/v1/rules" <<<"$cfg" | grep -c 'alert:'
curl -s -K - -G "$L/loki/api/v1/label/job/values" <<<"$cfg"      # systemd-journal
curl -s http://127.0.0.2:9090/api/v1/rules | head
run0 --user=monitoring -- bash -c 'podman exec monitoring-alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 alert query'
```

A test alert through the whole path:

```bash
run0 --user=monitoring -- bash -c 'podman exec monitoring-alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 alert add PathTest severity=info instance=host --end="$(date -u -d "+3 min" +%FT%TZ)"'
```

## Configuration

| Variable | Default | Controls |
|---|---|---|
| `monitoring_service_*_image` | see `defaults/main.yml` | Pinned image tags; `AutoUpdate=registry` follows the digest of the tag |
| `monitoring_service_ntfy_base_url` | loopback | The address the phone uses |
| `monitoring_service_grafana_admin_password` | none, required | Grafana's admin password. A deploy tries it and resets the live one when the login returns 401 |
| `monitoring_service_probe_urls` | `[]` | Public URLs the blackbox exporter probes every minute; `https://` only, 2xx passes |
| `monitoring_service_ntfy_password` | required | The phone's login, user `ntfy` |
| `monitoring_service_ntfy_token` | required | Alertmanager's and deploy.sh's bearer token (`tk_` + 29 lowercase alphanumerics) |

### File modes

`ntfy-auth.env` and `grafana.env` carry credentials, and the role templates them
with mode `0600` (`vars/main.yml`). Quadlet turns `EnvironmentFile=` into
`podman run --env-file`, so the values still reach the container's environment
and `podman inspect`. The mode only keeps them out of a world-readable file.
`alertmanager.yaml` carries the ntfy token and stays `0644`: Alertmanager runs
as `nobody`, which maps outside the service user's subuid range and cannot read
a `0600` file. The service home is `0750`, so only this user and root reach
any of them.

## Role contract

Inherited from `site.yml`: `service_name`, `service_user`, `service_home`,
`service_repo`, and `base_setup_services`. The role hands the collected
monitoring files to `quadlet_service` from `home-server` as
`quadlet_service_extra_files`. That role stages them with `quadlets/`, templates
the `.j2` files and restarts the pod when a file changed.

The role also loads a system-wide SELinux module, `alloy_journal_watch`. That
is the one thing it changes outside its own service user.

## License

MIT
