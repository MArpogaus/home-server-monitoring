# home-server-monitoring

The monitoring stack in one rootless Podman pod, with an Ansible role that
deploys it. Alloy ships the host journal to Loki, Prometheus and Loki raise
alerts, and Alertmanager sends them through ntfy to the phone.

| Container | Job | Memory ceiling |
|---|---|---|
| monitoring-prometheus | Metrics, metric alert rules | 512M |
| monitoring-alertmanager | Alert routing to ntfy | 128M |
| monitoring-grafana | Dashboards | 384M |
| monitoring-loki | Log store, log alert rules | 512M |
| monitoring-alloy | Host journal to Loki | 256M |
| monitoring-node-exporter | Host metrics, hwmon, textfile metrics | 64M |
| monitoring-blackbox | Probes the public URLs from the host | 64M |
| monitoring-ntfy | Push notifications | 128M |

## Configuration

| Variable | Default | Controls |
|---|---|---|
| `monitoring_service_*_image` | see `defaults/main.yml` | The images |
| `monitoring_service_probe_urls` | `[]` | URLs that blackbox probes |
| `monitoring_service_grafana_admin_password` | required | Grafana `admin` login; Grafana takes it on its first start only |
| `monitoring_service_ntfy_password` | required | ntfy login of the phone, user `ntfy`, topic `alerts` |
| `monitoring_service_ntfy_token` | required | ntfy token of Alertmanager |
| `monitoring_service_ntfy_base_url` | `http://127.0.0.1:8081` | The address that links in a notification use |

## Specifics

- The containers talk over `127.0.0.1`, because a rootless pod binds IPv4 only.
- The proxy pod maps only the host's `127.0.0.1`, so it reaches ntfy and
  nothing else of this stack. Grafana is reached through
  `ssh -L 3000:127.0.0.2:3000`.
- Loki is not published, because every local user could push forged lines.
  Grafana's datasource proxy (`/api/datasources/proxy/uid/loki/`) reads it.
  Loki refuses delete requests.
- Alloy reads `/var/log/journal` as `container_logreader_t`, with the
  `systemd-journal` group. The mount has no `:z`, because a relabel breaks
  journald for the host.
- `container_logreader_t` has no `watch` on the journal directory, and without
  it Alloy reads the journal once and then stays silent. The role loads
  `files/alloy_journal_watch.cil`, its one change outside the service user.
  Alloy sets the watch once at start, so the role restarts the pod in the run
  that loads the module.
- node-exporter mounts `/` with `rslave`, so a backup target that an automount
  mounts later also appears.
- Unit state comes from the journal, because SELinux denies `container_t` the
  system D-Bus that the systemd collector needs.
- Loki has no health check, because its image has neither `wget` nor a shell.
- `alertmanager.yaml` holds the ntfy token and stays `0644`, because
  Alertmanager runs as `nobody`, outside the subuid range. The env files with
  credentials are `0600`.
- Alertmanager reaches ntfy inside the pod, so alerts do not depend on the
  proxy. Every ntfy start syncs the phone's user and Alertmanager's token into
  `user.db`.
- Alertmanager groups by every label, so each alert keeps all its annotations.
  The title is the alert name, the message the summary and the description.
- While `LogShippingStopped` fires, Alertmanager holds back every alert without
  the label `source: prometheus`.
- A notifier on this machine cannot report that the machine is down.

## Monitoring files of a repository

`home-server-template/README.md`, "Monitoring", lists the files that a
repository carries in `monitoring/` and the rules for them. At every deploy the
role collects them from `home-server` and from each `home-server/services/<name>`.

- The rule file, the Loki rule namespace and the Grafana folder carry the
  service name (`home-server` for the host repository). Only this role writes
  rules, so a service cannot change the alerts of another.
- The files go into the service's Quadlet archive. A changed archive replaces
  the Quadlet directory on the host, so a removed file leaves the host at the
  next deploy.
- A drop pattern applies only to lines of its own `service` label
  (`home-server`'s: lines without one), so another sender cannot drop them.
  `loki_process_dropped_lines_total` counts the drops per service.
- A redact pattern applies to every line, because another sender can log the
  same value. Its capture group matches only the value, never quotes or
  whitespace.
- A scheduled job reports success as a textfile metric: it writes
  `/var/lib/node-textfile/<name>.prom` through a temporary file and a rename.
  A rule compares its timestamp with `time()`.

### Dashboards

- A dashboard is JSON maintained by hand: edit it in Grafana, export it,
  delete its `links`, and commit it.
- The role adds one link bar to every dashboard: this repository's first, then
  `home-server`'s, then each service's in the order of `base_setup_services`.
- A metric query over `| json` aggregates `by (...)`, because every JSON field
  becomes a label.
- An `unwrap` carries `| __error__=""`, so a line without the field drops out.
- Label-less series of several queries share the first legend. Name each one
  with `label_replace`.

## Labels

Alloy sets these labels on every journal line. Rules and dashboards select on
no other label.

| Label | From | Sender can write it |
|---|---|---|
| `job` | always `systemd-journal` | no |
| `unit`, `user_unit` | `UNIT`/`USER_UNIT`, else `_SYSTEMD_UNIT`/`_SYSTEMD_USER_UNIT` | yes |
| `syslog_identifier` | `SYSLOG_IDENTIFIER` if a rule or dashboard selects it, else `other` | yes |
| `container` | `CONTAINER_NAME` | yes |
| `priority` | `PRIORITY` | yes |
| `transport` | `_TRANSPORT` | no |
| `emitter_uid` | `_UID` | no |
| `service` | the `base_setup_services` name whose uid is `_SYSTEMD_OWNER_UID`, else `_UID` | no |

`service` covers every line of a service user's processes: its user manager,
its container output and its containers' `/dev/log`. journald takes
`_SYSTEMD_OWNER_UID` from the cgroup, so a container process under a subuid
still carries its service.

## Writing a log rule

- A rule that must not be forgeable pins labels that journald sets:
  `emitter_uid="0"` for PID 1 and root daemons, `transport` for kernel and
  audit lines, and `service` plus `transport="journal"` for a service user's
  manager. A container writes as `syslog` or `stdout`, so it cannot pass for
  its manager. A pod with the journald log driver also writes as `journal`, with
  `container` set, so a rule on its lines pins `container`.
- A rule on `syslog_identifier` trusts every container of that service, because
  the sender chooses the identifier.
- The window of a count rule must be longer than its `for:`. One line keeps
  `count_over_time(...[w]) > 0` true for exactly `w`, so it never reaches
  `for: w`.
- Loki's ruler logs the match string of every rule, so `loki.yaml` runs Loki at
  `warn`. Otherwise a rule matches Loki's own log.
- Check a rule file with `promtool check rules` or `lokitool rules lint --dry-run`.

## Loki limits

- Loki keeps at most 5000 streams, one per combination of label values. Above
  the limit it refuses pushes and `LokiDiscardingLogs` fires.
- Alloy therefore rewrites unbounded unit names (`run-*`, `session-*`,
  `libpod-*` and Podman's health check units) to `transient`.
- `syslog_identifier` keeps only the identifiers that a collected rule or
  dashboard selects, as a literal or an `a|b` alternation.
- A query returns at most 5000 series (`max_query_series`).

## Alerts

| Severity | Meaning | ntfy priority | Repeats | Resolved message |
|---|---|---|---|---|
| `critical` | Data at risk, or a service is down | 5 | every 4 h | yes |
| `warning` | Needs attention within a day | 3 | every 24 h | yes |
| `info` | An event | 2 | every 1 h while it fires | no |

| Alert | Severity | Fires when |
|---|---|---|
| `ContainerRestartLoop` | critical | A service's unit restarts more than 5 times in 15 minutes |
| `UserUnitFailed` | warning | A service's unit fails, `podman-auto-update.service` included |
| `TargetDown` | critical | A scrape target is down for 5 minutes |
| `HostMemoryPressure` | critical | All tasks stall on memory more than 10 % of the time for 10 minutes |
| `OomKill` | warning | The kernel OOM killer ends a process |
| `ZramFull` | warning | zram swap is more than 80 % full for 15 minutes |
| `HostLowDiskSpace` | critical | `/var` has less than 10 % free for 5 minutes |
| `HostDiskFillsSoon` | warning | The 6 h trend fills `/var` within 3 days, for 6 hours |
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

## Role contract

The contract is in `home-server-template/README.md`. The role also needs the
`dir` of every `base_setup_services` entry, which a pre-task in `site.yml` adds.

## LLM coding tools

This project is developed with LLM-based coding tools. They write most of the
code and documentation. The maintainer sets the goals and the design, reviews
every change and is responsible for it. Changes are tested on a VM before they
reach a host.

## License

MIT
