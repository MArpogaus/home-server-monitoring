#!/usr/bin/env python3
"""Generate the Grafana dashboards of service-monitoring.

One place for layout and query conventions; the JSON files are the artifact.
Every dashboard has the same shape: key figures on top, trends in the middle,
tables and logs at the bottom.
"""
import json
import pathlib
import sys

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).parent / "quadlets/configs/dashboards")
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}

# nginx JSON access log of the Nextcloud web container, health checks excluded.
# json makes every field a label, so unwrap aggregations need a by (job).
NGINX = '{user_unit="nextcloud-web.service", syslog_identifier="nginx"} | json | request_uri != "/status.php"'
# Nextcloud application log: one JSON object per line, syslog tag nextcloud
NCLOG = '{syslog_identifier="nextcloud"} | json'
# BunkerWeb access log: <site> <ip> - <reqid> - [time] "<method> <uri> <proto>" <status> <bytes> "<referer>" "<ua>"
BW = ('{container="bunker-nginx"} |~ `^[A-Za-z0-9.-]+ [0-9a-fA-F.:]+ - ` != "bwapi"'
      ' | pattern `<site> <ip> - <_> - [<_>] "<method> <uri> <_>" <status> <bytes> "<_>" "<ua>"` | status =~ "[0-9]+"')
# Enforced SELinux denials and OOM kills, without pasta's harmless capability
# probes at every pod start. loki-rules.yaml carries the same filter.
AVC = ('{syslog_identifier=~"audit|kernel"} |~ "avc:  denied|Killed process"'
       ' != "permissive=1" != "comm=\\"pasta"')
# One value per container from podman's event log: 0 stopped, 1 running without
# a health check, 2 health check starting, 3 healthy, 4 unhealthy. last_over_time
# plus spanNulls turns it into a state that holds until the next event.
CSTATE = ('{syslog_identifier="podman"} |~ `container (start|died|health_status) `'
          ' | regexp `container (?P<ev>start|died|health_status) `'
          ' | regexp `name=(?P<c>[a-zA-Z0-9_.-]+)` | c =~ `(nc|proxy|monitoring|nextcloud|bunker)-[a-z-]+`'
          ' | label_format hs=`{{ regexReplaceAll "^.*health_status=([a-z]+).*$" __line__ "${1}" }}`'
          ' | label_format v=`{{ if eq .ev "died" }}0{{ else if eq .ev "start" }}1'
          '{{ else if eq .hs "healthy" }}3{{ else if eq .hs "unhealthy" }}4{{ else }}2{{ end }}`')
REBOOT_STATE = ('{unit="auto-reboot-staged.service"} |~ "No staged deployment|Staged deployment found|Blocked by"'
                ' | label_format v=`{{ if contains "No staged" __line__ }}0'
                '{{ else if contains "Blocked" __line__ }}2{{ else }}1{{ end }}`')
# sshd, straight from its unit; README, "Dashboards".
# The first two words of an admin_audit message are the action.
AUDIT = ('{syslog_identifier="nextcloud"} | json | app="admin_audit"'
         ' | label_format action=`{{ regexReplaceAll "^([A-Za-z]+ [a-z]+).*$" .message "${1}" }}`')

# BunkerWeb refuses a request with "[ACCESS] denied access from <reason> : ...,
# client: <ip>" and bans a client with "[BADBEHAVIOR] IP <ip> is banned for <n>s".
BW_DENY = ('{container="bunker-nginx"} |= "denied access"'
           ' | regexp `denied access from (?P<reason>[a-z]+)`'
           ' | regexp `client: (?P<ip>[0-9a-fA-F.:]+)`')
BW_BAN = ('{container="bunker-nginx"} |= "is banned for"'
          ' | regexp `IP (?P<ip>[0-9a-fA-F.:]+) is banned for`')
# systemd says "Starting x.service", then "Finished" or "Deactivated
# successfully" or "Failed with result". Turned into a number and carried
# forward, that is a band per job: when it ran, how long, how it ended.
JOBS = ('{syslog_identifier="systemd", unit=~"btrfs-backup@.+\\\\.service|btrfs-snapshot@.+\\\\.service'
        '|pg-dumpall.service|podman-auto-update.service|auto-reboot-staged.service"}'
        ' |~ "Starting |Deactivated successfully|Failed with result|Finished "'
        ' | regexp `(?P<ev>Starting|Deactivated successfully|Failed with result|Finished)`'
        ' | label_format v=`{{ if eq .ev "Starting" }}1{{ else if eq .ev "Failed with result" }}2{{ else }}0{{ end }}`')
JOBS_MAP = [(0, "finished", "green"), (1, "running", "blue"), (2, "failed", "red")]
SSHD = '{job="systemd-journal", unit="sshd.service"}'
SSH_OK = SSHD + ' |= "Accepted" | pattern `Accepted <method> for <user> from <ip> port <_>`'
SSH_BAD = (SSHD + ' |~ "Failed (password|publickey)|Invalid user|Connection closed by authenticating"'
           ' | regexp `(?P<user>[^ ]+) (from )?(?P<ip>[0-9a-fA-F.:]+) port`')
CSTATE_MAP = [(0, "stopped", "dark-red"), (1, "running", "blue"), (2, "starting", "yellow"),
              (3, "healthy", "green"), (4, "unhealthy", "red")]
CLASS_COLORS = [("2xx", "green"), ("3xx", "blue"), ("4xx", "orange"), ("5xx", "red")]
DISK = 'device!~"loop.*|dm-.*|sr.*|zram.*"'
NIC = 'device!~"lo|veth.*|podman.*|pasta.*|tap.*"'


class Grid:
    def __init__(self):
        self.y = 0
        self.x = 0
        self.row_h = 0

    def place(self, w, h):
        if self.x + w > 24:
            self.newline()
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def newline(self):
        if self.x:
            self.x = 0
            self.y += self.row_h
            self.row_h = 0


def by_name(name, **props):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": k, "value": v} for k, v in props.items()]}


def color(name, c):
    return by_name(name, color={"mode": "fixed", "fixedColor": c})


def panel(title, ptype, ds, targets, grid, w=12, h=8, unit=None, opts=None, overrides=None,
          decimals=None, thresholds=None, color=None, desc=None, stack=False, minimum=None, maximum=None,
          legend=("mean", "max", "lastNotNull"), bars=False, mappings=None, transformations=None, sparkline=True,
          no_value=None):
    p = {
        "title": title,
        "type": ptype,
        "datasource": ds,
        "gridPos": grid.place(w, h),
        "targets": [],
        "fieldConfig": {"defaults": {}, "overrides": overrides or []},
        "options": opts or {},
    }
    if desc:
        p["description"] = desc
    if transformations:
        p["transformations"] = transformations
    for i, t in enumerate(targets):
        t = dict(t)
        t.setdefault("refId", chr(65 + i))
        t["datasource"] = ds
        p["targets"].append(t)
    d = p["fieldConfig"]["defaults"]
    if unit:
        d["unit"] = unit
    if decimals is not None:
        d["decimals"] = decimals
    if minimum is not None:
        d["min"] = minimum
    if maximum is not None:
        d["max"] = maximum
    if no_value:
        d["noValue"] = no_value
    if mappings:
        d["mappings"] = [{"type": "value", "options": {str(k): {"text": t, "color": c, "index": i}
                                                        for i, (k, t, c) in enumerate(mappings)}}]
    if thresholds:
        d["thresholds"] = {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in thresholds]}
        if ptype == "timeseries":
            d.setdefault("custom", {})["thresholdsStyle"] = {"mode": "line"}
    else:
        d["thresholds"] = {"mode": "absolute", "steps": [{"color": "green", "value": None}]}
    if color:
        d["color"] = {"mode": color}
    if ptype == "timeseries":
        c = d.setdefault("custom", {})
        # spanNulls would draw a straight ramp across hours without log lines.
        c.update({"lineWidth": 1, "fillOpacity": 12, "showPoints": "auto", "spanNulls": False})
        if stack:
            c["stacking"] = {"mode": "normal"}
            c["fillOpacity"] = 40
        if bars:
            c.update({"drawStyle": "bars", "fillOpacity": 80, "lineWidth": 0, "spanNulls": False})
        # A table legend with many rows hides the plot; lists stay compact.
        table = bool(legend) and len(targets) <= 4 and not any("{{" in t["legendFormat"] for t in p["targets"])
        p["options"].setdefault("legend", {"displayMode": "table" if table else "list", "placement": "bottom",
                                           "calcs": list(legend or [])})
        # Loki names every label-less series after the first target's legend, so a
        # static legend becomes a label on the result instead.
        if ds is LOKI:
            for t in p["targets"]:
                if t["legendFormat"] and "{{" not in t["legendFormat"]:
                    t["expr"] = f'label_replace({t["expr"]}, "series", "{t["legendFormat"]}", "", "")'
                    t["legendFormat"] = "{{series}}"
        p["options"].setdefault("tooltip", {"mode": "multi", "sort": "desc"})
    if ptype == "stat":
        for k, v in {"colorMode": "value", "graphMode": "area" if sparkline else "none", "textMode": "value",
                     "reduceOptions": {"calcs": ["lastNotNull"], "fields": ""}}.items():
            p["options"].setdefault(k, v)
    if ptype == "gauge":
        p["options"].update({"showThresholdMarkers": True, "showThresholdLabels": False,
                             "reduceOptions": {"calcs": ["lastNotNull"], "fields": ""}})
    if ptype == "logs":
        p["options"].update({"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
                             "dedupStrategy": "none", "enableLogDetails": True, "prettifyLogMessage": False})
    if ptype == "table":
        p["options"].update({"showHeader": True, "cellHeight": "sm", "footer": {"show": False}})
    if ptype == "state-timeline":
        d.setdefault("custom", {}).update({"fillOpacity": 80, "lineWidth": 0, "spanNulls": True})
        p["options"].update({"mergeValues": True, "showValue": "never", "rowHeight": 0.9,
                             "alignValue": "left",
                             "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                             "tooltip": {"mode": "single", "sort": "none"}})
    if ptype == "bargauge":
        p["options"].update({"displayMode": "gradient", "orientation": "horizontal",
                             "valueMode": "text", "showUnfilled": True, "minVizWidth": 8,
                             "minVizHeight": 12, "namePlacement": "left", "sizing": "auto",
                             "reduceOptions": {"calcs": ["lastNotNull"], "fields": ""}})
    return p


def top_table(title, ds, target, grid, value="requests", w=8, h=9, desc=None, overrides=None,
              mappings=None):
    """Instant top-k query as a table: the useless Time column hidden, the value column named."""
    t = [{"id": "organize", "options": {"excludeByName": {"Time": True},
                                        "renameByName": {"Value": value, "Value #A": value}}}]
    return panel(title, "table", ds, [target], grid, w=w, h=h, desc=desc, transformations=t,
                 opts={"sortBy": [{"displayName": value, "desc": True}]}, decimals=0,
                 overrides=list(overrides or []), mappings=mappings)


def top_bars(title, expr, label, grid, w=8, h=9, desc=None):
    """Top-k as a sorted table. A bargauge draws one bar for a Loki instant
    vector, because it arrives as a single frame, and a gauge table cell needs
    a field config the Loki frame does not carry."""
    return top_table(title, LOKI, loki_instant(expr), grid, w=w, h=h, desc=desc,
                     overrides=[by_name(label, custom={"width": 260})])


def prom(expr, legend=""):
    return {"expr": expr, "legendFormat": legend, "range": True}


def prom_instant(expr, legend=""):
    return {"expr": expr, "legendFormat": legend, "instant": True, "range": False, "format": "table"}


def loki(expr, legend=""):
    return {"expr": expr, "legendFormat": legend, "queryType": "range"}


def loki_instant(expr, legend=""):
    return {"expr": expr, "legendFormat": legend, "queryType": "instant"}


def row(title, grid):
    grid.newline()
    return {"type": "row", "title": title, "collapsed": False, "gridPos": grid.place(24, 1), "panels": []}


def dashboard(uid, title, tags, panels, refresh="1m", time_from="now-24h", links=()):
    for i, p in enumerate(panels):
        p["id"] = i + 1
    return {
        "uid": uid,
        "title": title,
        "tags": tags,
        "schemaVersion": 39,
        "version": 1,
        "editable": True,
        "graphTooltip": 1,
        "timezone": "browser",
        "refresh": refresh,
        "time": {"from": time_from, "to": "now"},
        "links": [{"title": t, "type": "link", "url": u, "icon": "dashboard", "keepTime": True} for t, u in links],
        "templating": {"list": []},
        "annotations": {"list": [
            {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True,
             "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
            {"datasource": LOKI, "enable": True, "iconColor": "red", "name": "Boots",
             "expr": '{job="systemd-journal", unit="init.scope"} |= "Startup finished in"',
             "titleFormat": "Boot", "textFormat": "Startup finished"},
        ]},
        "panels": panels,
    }


LINKS = [("Host", "/d/host"), ("Proxy", "/d/proxy"), ("Nextcloud", "/d/nextcloud"), ("System log", "/d/system")]
PCT = dict(unit="percent", decimals=0, minimum=0, maximum=100)
PCT300 = dict(unit="percent", decimals=0, minimum=0, maximum=300)
OK_WARN_CRIT = [(None, "green"), (80, "orange"), (90, "red")]
ZERO_IS_GOOD = [(None, "green"), (1, "red")]
CLASS_OVERRIDES = [color(n, c) for n, c in CLASS_COLORS]
STATUS_OVERRIDES = [by_name("status", custom={"width": 80})]

# --------------------------------------------------------------------------- host
g = Grid()
P = [row("Now", g)]
P.append(panel("Uptime", "stat", PROM, [prom("node_time_seconds - node_boot_time_seconds")], g, w=4, h=4, unit="dtdurations", decimals=1, color="fixed", sparkline=False,
    desc="Time since the last boot. auto-reboot-staged reboots at night when rpm-ostree has staged an update."))
P.append(panel("CPU busy", "gauge", PROM, [prom('100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])))')], g, w=4, h=4, **PCT, thresholds=[(None, "green"), (70, "orange"), (90, "red")],
    desc="Share of the four cores not idle, averaged over the last minutes."))
P.append(panel("Load per core", "gauge", PROM, [prom('100 * node_load5 / scalar(count(node_cpu_seconds_total{mode="idle"}))')], g, w=4, h=4, **PCT300, thresholds=[(None, "green"), (100, "orange"), (200, "red")],
    desc="5-minute load average divided by the core count. 100 % means one runnable task per core; above that work queues."))
P.append(panel("Memory used", "gauge", PROM, [prom("100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)")], g, w=4, h=4, **PCT, thresholds=OK_WARN_CRIT,
    desc="Used = total minus available (MemAvailable already counts reclaimable cache as free). 8 GB host."))
P.append(panel("Disk /var used", "gauge", PROM, [prom('100 * (1 - node_filesystem_avail_bytes{mountpoint="/var"} / node_filesystem_size_bytes{mountpoint="/var"})')], g, w=4, h=4, **PCT, thresholds=OK_WARN_CRIT,
    desc="/var holds containers, service data and snapshots; on ostree the rest of the root is read-only and small. HostLowDiskSpace alerts below 10 % free."))
P.append(panel("Hottest sensor", "stat", PROM, [prom("max(node_hwmon_temp_celsius)")], g, w=4, h=4, unit="celsius", decimals=0, thresholds=[(None, "green"), (75, "orange"), (85, "red")],
    desc="Maximum over all hwmon sensors. The thin client throttles around 85 °C (HostHighTemperature)."))

P.append(row("Trends", g))
P.append(panel("CPU by mode", "timeseries", PROM, [prom('sum by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[$__rate_interval])) * 100 / scalar(count(node_cpu_seconds_total{mode="idle"}))', "{{mode}}")], g, w=12, **PCT, stack=True, legend=("mean", "max"),
    overrides=[by_name(m, displayName=n) for m, n in [("user", "user – processes"), ("system", "system – kernel"), ("iowait", "iowait – waiting for disk"), ("nice", "nice – low-priority processes"), ("irq", "irq – hardware interrupts"), ("softirq", "softirq – network, timers"), ("steal", "steal – hypervisor took the core")]],
    h=9, desc="Percent of all cores, stacked. iowait rising with the disk-busy panel means storage, not CPU, is the limit."))
P.append(panel("Load average", "timeseries", PROM, [prom("node_load1", "1 min"), prom("node_load5", "5 min"), prom("node_load15", "15 min")], g, w=12, decimals=1, thresholds=[(None, "transparent"), (4, "orange")],
    desc="Runnable plus uninterruptible tasks. The orange line is the core count: above it, work queues."))
P.append(panel("Memory", "timeseries", PROM, [
    prom("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", "used – total minus available"),
    prom("node_memory_Cached_bytes + node_memory_Buffers_bytes + node_memory_SReclaimable_bytes", "cache – page cache, buffers, reclaimable slab"),
    prom("node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes", "swap used – zram, compressed in RAM"),
    prom("node_memory_MemTotal_bytes", "total")], g, w=12, unit="bytes", legend=("mean", "max", "lastNotNull"),
    overrides=[color("total", "#8e8e8e")],
    desc="Cache shrinks under pressure before anything is killed; swap here is zram, so swapping costs CPU, not disk."))
P.append(panel("Pressure stall", "timeseries", PROM, [
    prom("rate(node_pressure_cpu_waiting_seconds_total[$__rate_interval]) * 100", "cpu – some task waited for a core"),
    prom("rate(node_pressure_memory_waiting_seconds_total[$__rate_interval]) * 100", "memory – some task waited for reclaim"),
    prom("rate(node_pressure_io_waiting_seconds_total[$__rate_interval]) * 100", "io – some task waited for disk")], g, w=12, unit="percent", decimals=1,
    desc="Share of wall time in which at least one task stalled on the resource (PSI 'some'). Sustained io pressure on this thin client means the SSD is the bottleneck."))
P.append(panel("Disk space used", "timeseries", PROM, [prom('100 * (1 - node_filesystem_avail_bytes{mountpoint=~"/var|/boot|/var/backup/.*"} / node_filesystem_size_bytes{mountpoint=~"/var|/boot|/var/backup/.*"})', "{{mountpoint}}")], g, w=12, **PCT, thresholds=[(None, "transparent"), (90, "red")], legend=("lastNotNull",),
    desc="/var (everything lives there on ostree; /, /etc and /sysroot are the same disk), /boot (small, 90 % is normal) and the backup target while mounted. Btrfs counts snapshots."))
P.append(panel("Disk throughput", "timeseries", PROM, [
    prom(f'sum by (device) (rate(node_disk_read_bytes_total{{{DISK}}}[$__rate_interval]))', "{{device}} read"),
    prom(f'-sum by (device) (rate(node_disk_written_bytes_total{{{DISK}}}[$__rate_interval]))', "{{device}} write (drawn below zero)")], g, w=12, unit="Bps",
    desc="Physical disks only. Reads go up, writes go down, so the two directions never hide each other."))
P.append(panel("Disk busy", "timeseries", PROM, [prom(f'rate(node_disk_io_time_seconds_total{{{DISK}}}[$__rate_interval]) * 100', "{{device}}")], g, w=12, **PCT,
    desc="Share of time the device had a request in flight. Near 100 % for minutes means the disk is saturated (backups, previews, Recognize)."))
P.append(panel("Network", "timeseries", PROM, [
    prom(f'sum by (device) (rate(node_network_receive_bytes_total{{{NIC}}}[$__rate_interval]) * 8)', "{{device}} in"),
    prom(f'-sum by (device) (rate(node_network_transmit_bytes_total{{{NIC}}}[$__rate_interval]) * 8)', "{{device}} out (drawn below zero)")], g, w=12, unit="bps",
    desc="Physical interfaces only; the pods' virtual interfaces are excluded. Backups to the NAS show as out on the LAN interface."))
P.append(panel("Temperatures", "timeseries", PROM, [prom("node_hwmon_temp_celsius", "{{chip}} {{sensor}}")], g, w=12, unit="celsius", decimals=0, thresholds=[(None, "transparent"), (85, "red")], legend=("mean", "max"),
    desc="Every hwmon sensor. The red line is the alert threshold."))
P.append(panel("Processes", "timeseries", PROM, [prom("node_procs_running", "running"), prom("node_procs_blocked", "blocked on IO")], g, w=12, decimals=0, legend=("mean", "max"),
    desc="Blocked processes are waiting for disk; a steady count above zero matches io pressure above."))
P.append(row("SSH", g))
P.append(panel("Accepted and refused connections", "timeseries", LOKI, [
    loki(f'sum(count_over_time({SSHD} |= "Accepted" [$__auto]))', "accepted"),
    loki(f'sum(count_over_time({SSHD} |~ "Failed (password|publickey)|Invalid user|Connection closed by authenticating" [$__auto]))', "refused")], g, w=24, h=7,
    decimals=0, bars=True, legend=("sum",),
    overrides=[color("accepted", "green"), color("refused", "orange")],
    desc="sshd only, from its own unit. SSH is LAN-only and accepts one hardware key, so 'refused' is usually a client that opened a connection and gave up (Connection closed by authenticating user), not an attack. Every accepted login also reaches the phone (SshLogin)."))
P.append(top_bars("Accepted: who", f"topk(10, sum by (user) (count_over_time({SSH_OK} [$__range])))", "user", g,
    desc="Users that logged in successfully over the dashboard range."))
P.append(top_bars("Accepted: from where", f"topk(10, sum by (ip) (count_over_time({SSH_OK} [$__range])))", "ip", g,
    desc="Source addresses of successful logins. Only LAN addresses should appear."))
P.append(top_bars("Refused: from where", f"topk(10, sum by (ip) (count_over_time({SSH_BAD} [$__range])))", "ip", g,
    desc="Source addresses of refused or abandoned connections. A stranger here means something on the LAN is probing, because the firewall does not publish port 22."))
P.append(panel("sshd", "logs", LOKI, [loki(SSHD)], g, w=24, h=10,
    desc="Everything sshd logged, unfiltered."))
host = dashboard("host", "Host", ["home-server"], P, links=LINKS)

# --------------------------------------------------------------------------- proxy
g = Grid()
P = [row("Now", g)]
P.append(panel("Public URLs", "stat", PROM, [prom("probe_success", "{{instance}}")], g, w=12, h=4, decimals=0, sparkline=False,
    mappings=[(1, "UP", "green"), (0, "DOWN", "red")], thresholds=[(None, "red"), (1, "green")],
    opts={"colorMode": "background", "textMode": "value_and_name", "graphMode": "none", "reduceOptions": {"calcs": ["lastNotNull"], "fields": ""}},
    desc="Blackbox probe from this host through the public path: DNS, TLS (validated), BunkerWeb and the backend. PublicUrlDown fires after 5 min."))
P.append(panel("Certificate expires in", "stat", PROM, [prom("min((probe_ssl_earliest_cert_expiry - time()) / 86400)")], g, w=6, h=4, unit="suffix: days", decimals=0, sparkline=False,
    thresholds=[(None, "red"), (14, "orange"), (30, "green")],
    desc="Shortest remaining validity over all probed sites. Let's Encrypt renews at 30 days; CertificateExpiresSoon fires below 14."))
P.append(panel("Requests / min", "stat", LOKI, [loki_instant(f"sum(count_over_time({BW} [1h])) / 60")], g, w=6, h=4, decimals=1, color="fixed", sparkline=False,
    desc="Average over the last hour, both sites, without BunkerWeb's own health checks."))
P.append(panel("Visitors (1h)", "stat", LOKI, [loki_instant(f"count(sum by (ip) (count_over_time({BW} [1h]))) or vector(0)")], g, w=8, h=4, decimals=0, color="fixed", sparkline=False,
    desc="Distinct client addresses in the last hour. Includes scanners."))
P.append(panel("5xx (1h)", "stat", LOKI, [loki_instant(f'sum(count_over_time({BW} | status =~ "5.." [1h])) or vector(0)')], g, w=8, h=4, decimals=0, thresholds=ZERO_IS_GOOD, sparkline=False,
    desc="Server errors answered to clients in the last hour. Zero is right; 502 means the backend pod was down."))
P.append(panel("Bans (24h)", "stat", LOKI, [loki_instant('sum(count_over_time({container="bunker-nginx"} |= "[BADBEHAVIOR]" |= "is banned for" [24h])) or vector(0)')], g, w=8, h=4, decimals=0, color="fixed", sparkline=False,
    desc="Clients BunkerWeb banned for 24 h after too many 4xx in a minute (threshold in bunker_service_bad_behavior_threshold). See the log panel for the addresses."))

P.append(row("Trends", g))
P.append(panel("Public URLs over time", "state-timeline", PROM, [prom("probe_success", "{{instance}}")], g, w=24, h=5,
    mappings=[(0, "DOWN", "red"), (1, "UP", "green")], minimum=0, maximum=1,
    desc="One band per probed URL, once a minute. A gap means the probe itself did not run; red means DNS, TLS, the WAF or the backend failed."))
P.append(panel("Requests by site", "timeseries", LOKI, [loki(f"sum by (site) (rate({BW} [$__auto]))", "{{site}}")], g, w=12, unit="reqps", stack=True, legend=("mean", "max"),
    desc="Access-log lines per second per server name. The ntfy site is the phone polling; everything else is Nextcloud."))
P.append(panel("Responses by class", "timeseries", LOKI, [loki(f'sum by (class) (rate({BW} | label_format class="{{{{ .status | substr 0 1 }}}}xx" [$__auto]))', "{{class}}")], g, w=12, unit="reqps", stack=True, overrides=CLASS_OVERRIDES, legend=("mean", "max"),
    desc="2xx ok, 3xx redirects (HTTP to HTTPS, login), 4xx client errors and WAF denials, 5xx backend errors."))
P.append(panel("Why requests were refused", "timeseries", LOKI, [
    loki(f"sum by (reason) (count_over_time({BW_DENY} [$__auto]))", "{{reason}}"),
    loki('sum(count_over_time({container="bunker-nginx"} |= "ModSecurity" [$__auto]))', "modsecurity"),
    loki(f"sum(count_over_time({BW_BAN} [$__auto]))", "ban")], g, w=12, decimals=0, bars=True, stack=True, legend=("sum",),
    overrides=[color("modsecurity", "yellow"), color("ban", "red"), color("country", "orange"),
               color("dnsbl", "purple"), color("blacklist", "semi-dark-orange")],
    desc="Every refusal by the check that made it: country (the geo allowlist), dnsbl (a public blocklist), blacklist (BunkerWeb's own), modsecurity (a CRS rule), ban (the client was already blocked). A burst from one address ends in a ban."))
P.append(panel("Probe duration", "timeseries", PROM, [prom("probe_duration_seconds", "{{instance}}")], g, w=12, unit="s", decimals=2, legend=("mean", "max"),
    desc="Full request time of the blackbox probe including DNS and TLS handshake, once a minute. A step up means the backend or the WAF got slower."))

P.append(row("Details", g))
P.append(top_bars("Top clients", f"topk(10, sum by (ip) (count_over_time({BW} [$__range])))", "ip", g,
    desc="Addresses with the most requests in the dashboard range. Own devices sit on top; a stranger with thousands of hits is a scanner."))
P.append(top_bars("Top paths", f"topk(10, sum by (uri) (count_over_time({BW} [$__range])))", "uri", g,
    desc="Most requested paths across both sites."))
P.append(top_bars("Top user agents", f"topk(10, sum by (ua) (count_over_time({BW} [$__range])))", "ua", g,
    desc="mirall is the Nextcloud desktop client; DAVx5 and Thunderbird sync calendars and contacts."))
P.append(top_table("ModSecurity rules hit", LOKI, loki_instant('topk(10, sum by (id) (count_over_time({container="bunker-nginx"} |= "ModSecurity" | regexp `\\[id "(?P<id>\\d+)"\\]` | id != "" [$__range])))'), g, value="matches", w=8,
    desc="OWASP CRS rule ids over the dashboard range. 930130 is scanners probing /.env and friends; 920440 a blocked file extension; 949110 the anomaly-score block itself."))
P.append(top_table("Responses by status", LOKI, loki_instant(f"sum by (status) (count_over_time({BW} [$__range]))"), g, w=8,
    desc="Exact status codes over the dashboard range."))
P.append(top_table("Banned clients", LOKI, loki_instant(f"topk(10, sum by (ip) (count_over_time({BW_BAN} [$__range])))"), g, value="bans", w=8,
    desc="Addresses BunkerWeb banned in the dashboard range, and how often. A ban lasts 24 h; `bwcli unban <ip>` in the scheduler container lifts one early."))
P.append(top_table("Refused clients", LOKI, loki_instant(f"topk(10, sum by (ip, reason) (count_over_time({BW_DENY} [$__range])))"), g, value="refusals", w=8,
    desc="Addresses that were refused, with the check that refused them."))
P.append(top_table("Requests by site and method", LOKI, loki_instant(f"sum by (site, method) (count_over_time({BW} [$__range]))"), g, w=8,
    desc="PROPFIND and REPORT are WebDAV/CalDAV clients syncing."))

P.append(row("Logs", g))
P.append(panel("Bans and denials", "logs", LOKI, [loki('{container="bunker-nginx"} |~ "denied access|is banned for"')], g, w=12, h=10,
    desc="Every refused request and every ban, newest first. If your own address appears, `bwcli unban <ip>` in the bunker-scheduler container lifts it."))
P.append(panel("Errors and certificate events", "logs", LOKI, [loki('{container=~"bunker-.*"} |~ "\\\\[(error|crit|alert|emerg)\\\\]|\\\\[ERROR\\\\]|(?i)certbot|certificate" != "denied access" != "ModSecurity"')], g, w=12, h=10,
    desc="nginx errors and the scheduler's certificate work. A renewal appears here once a month; an error line raises CertificateRenewalFailed."))
proxy = dashboard("proxy", "Proxy", ["home-server"], P, links=LINKS)

# --------------------------------------------------------------------------- nextcloud
g = Grid()
P = [row("Now", g)]
P.append(panel("Requests/min", "stat", LOKI, [loki_instant(f"sum(count_over_time({NGINX} [1h])) / 60 or vector(0)")], g, w=4, h=4, decimals=1, color="fixed", sparkline=False,
    desc="Requests that reached Nextcloud's nginx in the last hour, health checks excluded."))
P.append(panel("p95 latency", "stat", LOKI, [loki_instant(f"quantile_over_time(0.95, {NGINX} | unwrap request_time [1h]) by (job)")], g, w=4, h=4, unit="s", decimals=2, thresholds=[(None, "green"), (2, "orange"), (5, "red")], sparkline=False, no_value="no requests",
    desc="95 % of requests in the last hour were faster than this. Sync clients poll cheaply; the web UI and previews are the slow part."))
P.append(panel("5xx (1h)", "stat", LOKI, [loki_instant(f'sum(count_over_time({NGINX} | status =~ "5.." [1h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=ZERO_IS_GOOD, sparkline=False,
    desc="Errors nginx answered, mostly php-fpm timeouts (504) or a full worker pool (502)."))
P.append(panel("Failed logins", "stat", LOKI, [loki_instant('sum(count_over_time({syslog_identifier="nextcloud"} |= "Login failed: \'" [24h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (5, "orange"), (20, "red")], sparkline=False,
    desc="Wrong password or unknown user. Each one also arrives on the phone (NextcloudLoginFailed)."))
P.append(panel("App warnings", "stat", LOKI, [loki_instant(f"sum(count_over_time({NCLOG} | level >= 2 [24h])) or vector(0)")], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (50, "orange")], sparkline=False,
    desc="Nextcloud log level 2 warning, 3 error, 4 fatal. The table below splits them by app."))
P.append(panel("php-fpm maxed", "stat", LOKI, [loki_instant('sum(count_over_time({syslog_identifier="php-fpm"} |= "reached pm.max_children" [24h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (1, "orange")], sparkline=False,
    desc="Every hit means requests queued. Raise nextcloud_service_php_max_children if this is not zero at quiet times."))

P.append(row("Trends", g))
P.append(panel("Requests by class", "timeseries", LOKI, [loki(f'sum by (class) (rate({NGINX} | label_format class="{{{{ .status | substr 0 1 }}}}xx" [$__auto]))', "{{class}}")], g, w=12, unit="reqps", stack=True, overrides=CLASS_OVERRIDES, legend=("mean", "max"),
    desc="2xx ok, 3xx redirects and 304 not-modified, 4xx includes 401 from clients renewing their session, 5xx errors."))
P.append(panel("Response time", "timeseries", LOKI, [
    loki(f"quantile_over_time(0.5, {NGINX} | unwrap request_time [$__auto]) by (job)", "p50 – median"),
    loki(f"quantile_over_time(0.95, {NGINX} | unwrap request_time [$__auto]) by (job)", "p95"),
    loki(f"max_over_time({NGINX} | unwrap request_time [$__auto]) by (job)", "max – slowest request")], g, w=12, unit="s", decimals=2,
    desc="nginx request_time per interval. Long maxima are uploads and preview generation; a rising median means the box is busy."))
P.append(panel("Clients", "timeseries", LOKI, [loki(f'sum by (client) (rate({NGINX} | regexp "(?P<client>mirall|Nextcloud-android|Nextcloud-iOS|DAVx5|Thunderbird|PhoneTrack|Mozilla|curl|Wget)" | label_format client="{{{{ if .client }}}}{{{{ .client }}}}{{{{ else }}}}other{{{{ end }}}}" [$__auto]))', "{{client}}")], g, w=12, unit="reqps", stack=True, legend=("mean", "max"),
    overrides=[by_name("mirall", displayName="mirall – desktop client"), by_name("Mozilla", displayName="Mozilla – browsers")],
    desc="Requests per second by user agent family. The desktop client polls every 30 s, so it dominates when nobody is active."))
P.append(panel("Bytes sent", "timeseries", LOKI, [loki(f"sum(rate({NGINX} | unwrap bytes_sent [$__auto]))", "sent to clients")], g, w=12, unit="Bps",
    desc="Payload nginx sent, from the access log. Downloads and photo previews make the peaks."))

P.append(row("Details", g))
P.append(top_bars("Top paths", f'topk(10, sum by (path) (count_over_time({NGINX} | label_format path="{{{{ regexReplaceAll \\"^(/[^/?]*(/[^/?]*)?).*\\" .request_uri \\"${{1}}\\" }}}}" [$__range])))', "path", g,
    desc="First two path segments. /remote.php/dav is sync and CalDAV, /ocs is the client API, /apps/... the web UI."))
P.append(top_bars("Top client addresses", f'topk(10, sum by (addr) (count_over_time({NGINX} | label_format addr="{{{{ if .http_x_forwarded_for }}}}{{{{ .http_x_forwarded_for }}}}{{{{ else }}}}{{{{ .remote_addr }}}}{{{{ end }}}}" [$__range])))', "addr", g,
    desc="Real client address (X-Forwarded-For from BunkerWeb)."))
P.append(top_table("App log by level", LOKI, loki_instant(f"sum by (level, app) (count_over_time({NCLOG} | level >= 2 [$__range]))"), g, value="lines",
    mappings=[(2, "warning", "orange"), (3, "error", "red"), (4, "fatal", "dark-red")],
    overrides=[by_name("level", custom={"cellOptions": {"type": "color-text"}, "width": 90})],
    desc="Warnings and errors per Nextcloud app over the dashboard range. The logs panel below has the messages."))

P.append(row("Audit", g))
P.append(panel("What happened to files", "timeseries", LOKI, [loki(f"sum by (action) (count_over_time({AUDIT} [$__auto]))", "{{action}}")], g, w=12, h=9,
    decimals=0, bars=True, stack=True, legend=("sum",),
    desc="Every action the audit app recorded, by kind: File written, File deleted, File renamed, Login successful and so on. Needs nextcloud_service_apps to include admin_audit."))
P.append(top_table("Busiest users", LOKI, loki_instant(f"topk(10, sum by (user, action) (count_over_time({AUDIT} [$__range])))"), g, value="actions", w=12, h=9,
    desc="Who did how much of what over the dashboard range. `--` is a background job or the command line, not a person."))
P.append(panel("File activity", "logs", LOKI, [loki(f'{AUDIT} | action !~ "(Preview|File) accessed" | line_format "{{{{.user}}}} {{{{.message}}}}"')], g, w=24, h=10,
    desc="Writes, deletes, renames and shares with the user that caused them. Reads are left out: preview generation and sync clients read constantly."))

P.append(row("Logs", g))
P.append(panel("Failed logins", "logs", LOKI, [loki('{syslog_identifier="nextcloud"} |= "Login failed: \'" | regexp "Login failed: \'(?P<user>[^\']+)\' \\\\(Remote IP: \'(?P<ip>[^\']+)\'\\\\)" | line_format "{{.user}} from {{.ip}}"')], g, w=12, h=9,
    desc="User name and source address of every failed login."))
P.append(panel("App warnings and errors", "logs", LOKI, [loki(f'{NCLOG} | level >= 2 | line_format "[{{{{.app}}}}] {{{{.message}}}}"')], g, w=12, h=9,
    desc="Nextcloud's own log at level warning and above, one line per entry. Expand a line for the full JSON (user, URL, exception)."))
P.append(panel("Background containers (cron, preview, recognize, push)", "logs", LOKI, [loki('{user_unit=~"nextcloud-(cron|preview|recognize|push).service"} != "Scanning folder" != "Waiting for new jobs"')], g, w=12, h=9,
    desc="Output of the four helper containers. Recognize logs each classified batch; the preview generator each folder it finished."))
P.append(panel("Database and cache", "logs", LOKI, [loki('{container=~"nextcloud-(db|redis)"} |~ "(?i)error|fatal|warning|panic"')], g, w=12, h=9,
    desc="PostgreSQL and redis, warnings and up. Empty is normal."))
nextcloud = dashboard("nextcloud", "Nextcloud", ["home-server"], P, links=LINKS)

# --------------------------------------------------------------------------- system
g = Grid()
P = [row("Now", g)]
P.append(panel("Active alerts", "stat", PROM, [prom('sum(alertmanager_alerts{state="active"}) or vector(0)')], g, w=6, h=4, decimals=0, thresholds=[(None, "green"), (1, "orange")],
    desc="Alerts Alertmanager holds right now, from Prometheus and Loki rules. The timeline below names them."))
P.append(panel("OS update", "stat", LOKI, [loki_instant(f"last_over_time({REBOOT_STATE} | unwrap v [$__range])")], g, w=6, h=4, sparkline=False,
    mappings=[(0, "up to date", "green"), (1, "reboot pending", "orange"), (2, "waiting for backup", "blue")],
    thresholds=[(None, "text")], no_value="no run yet",
    opts={"colorMode": "background", "textMode": "value"},
    desc="What auto-reboot-staged.service found on its last nightly run: nothing staged, an update waiting for the next reboot, or a backup holding a shutdown inhibitor."))
P.append(panel("Unhealthy", "stat", LOKI, [loki_instant('sum(count_over_time({syslog_identifier="podman"} |= "health_status=unhealthy" [1h])) or vector(0)')], g, w=6, h=4, decimals=0, thresholds=ZERO_IS_GOOD, sparkline=False,
    desc="Failed container health checks in the last hour. A container restarting after a deploy produces a few; a steady count is a sick container."))
P.append(panel("Unit failures", "stat", LOKI, [loki_instant('sum(count_over_time({syslog_identifier="systemd", user_unit!~"[0-9a-f]{64}-.*"} |~ "(?i)failed with result" [24h])) or vector(0)')], g, w=6, h=4, decimals=0, thresholds=[(None, "green"), (1, "orange")], sparkline=False,
    desc="systemd units that ended in failure in the last 24 h, system and service users. Podman's transient health-check units are not counted."))
P.append(panel("SSH logins", "stat", LOKI, [loki_instant('sum(count_over_time({unit="sshd.service"} |= "Accepted publickey" [24h])) or vector(0)')], g, w=6, h=4, decimals=0, color="fixed", sparkline=False,
    desc="Accepted key logins in the last 24 h. Each one also reaches the phone (SshLogin)."))
P.append(panel("SSH failures", "stat", LOKI, [loki_instant('sum(count_over_time({unit="sshd.service"} |~ "Invalid user|Failed (publickey|password)" [24h])) or vector(0)')], g, w=6, h=4, decimals=0, thresholds=[(None, "green"), (1, "orange")], sparkline=False,
    desc="Refused logins in the last 24 h. SSH is LAN-only, so anything here is a device on the LAN or a typo."))
P.append(panel("SELinux", "stat", LOKI, [loki_instant(f'sum(count_over_time({AVC} |= "avc:  denied" [24h])) or vector(0)')], g, w=6, h=4, decimals=0, thresholds=[(None, "green"), (20, "orange")], sparkline=False,
    desc="Enforced denials in the last 24 h, without pasta's capability probes at every pod start (harmless, excluded from the alert too)."))
P.append(panel("OOM kills", "stat", LOKI, [loki_instant('sum(count_over_time({syslog_identifier="kernel"} |= "Killed process" [24h])) or vector(0)')], g, w=6, h=4, decimals=0, thresholds=ZERO_IS_GOOD, sparkline=False,
    desc="Processes the kernel killed in the last 24 h for hitting a container's memory ceiling. The OomKill alert names the process."))

P.append(row("Trends", g))
P.append(panel("Container health: green healthy, blue running, yellow starting, red stopped or unhealthy", "state-timeline", LOKI, [loki(f"last_over_time({CSTATE} | unwrap v [$__auto]) by (c)", "{{c}}")], g, w=24, h=14,
    mappings=CSTATE_MAP,
    desc="Every container podman reported on, from its own event log. A deploy shows as a short stopped-starting-healthy sequence on the whole pod; a single red band is one container in trouble. Containers without a health check stay blue while they run."))
P.append(panel("Alerts firing", "state-timeline", PROM, [prom('ALERTS{alertstate="firing"}', "{{alertname}} {{severity}}")], g, w=12, h=9,
    mappings=[(1, "firing", "red")], no_value="none fired in this range",
    desc="Prometheus rules only: host, target and probe alerts. Loki's rules have no queryable state, so its alerts appear as events in the log panels and on the phone."))
P.append(panel("Journal error lines by unit", "timeseries", LOKI, [loki('sum by (unit) (count_over_time({job="systemd-journal", priority=~"[0-3]", unit!~"user@.*|session-.*|run-.*", unit!=""} [$__auto]))', "{{unit}}")], g, w=12, h=9, decimals=0, stack=True, bars=True, legend=("sum",),
    desc="Lines the host's own units logged at priority emerg, alert, crit or err, counted per bar. The legend column is the total over the selected range. Login session scopes and container output are excluded: the proxy pod's journald driver files every stderr line as err, and the other pods log at info."))
P.append(panel("Scheduled jobs: green finished, blue running, red failed", "state-timeline", LOKI,
    [loki(f"last_over_time({JOBS} | unwrap v [$__auto]) by (unit)", "{{unit}}")], g, w=24, h=9,
    mappings=JOBS_MAP,
    desc="One band per job: where it turns blue the job was running, so the width is how long it took. Snapshots run daily, the database dump before the Nextcloud snapshot, the backup to the NAS nightly. A job that never runs raises BackupMissing, SnapshotMissing or DumpMissing after 30 h; a red band raises ScheduledJobFailed."))

P.append(top_table("Failed units", LOKI, loki_instant('topk(10, sum by (unit, user_unit) (count_over_time({syslog_identifier="systemd", user_unit!~"[0-9a-f]{64}-.*"} |~ "(?i)failed with result" [$__range])))'), g, value="failures", w=24, h=8,
    desc="Which units produced the failure count above, system units by `unit` and container units by `user_unit`. A deploy restarting a pod is the usual source; anything else deserves the log below."))

P.append(row("Logs", g))
P.append(panel("Logins", "logs", LOKI, [loki('{unit="sshd.service"} |~ "Accepted publickey|Failed (publickey|password)|Invalid user" | regexp "(?P<what>Accepted publickey|Failed publickey|Failed password|Invalid user) (for )?(?P<user>[^ ]+) from (?P<ip>[0-9a-f.:]+)" | line_format "{{.what}}: {{.user}} from {{.ip}}"')], g, w=12, h=10,
    desc="Accepted and refused SSH logins with user and address."))
P.append(panel("Updates, reboots, boots", "logs", LOKI, [loki('{unit=~"rpm-ostreed.service|auto-reboot-staged.service|init.scope"} |~ "Staged|Deployment|Rebooting|Startup finished|reboot|No staged deployment"')], g, w=12, h=10,
    desc="rpm-ostree stages an OS update (UpdateStaged); auto-reboot-staged.timer reboots at night when one is staged and no backup holds an inhibitor (AutoReboot); 'Startup finished' is the boot (HostBooted)."))
P.append(panel("Scheduled jobs", "logs", LOKI, [loki('{unit=~"btrfs-backup@.*|btrfs-snapshot@.*|pg-dumpall.service|podman-auto-update.service"} |~ "Deactivated successfully|Failed with result|run complete|Starting|error|Error"')], g, w=12, h=9,
    desc="Backups, snapshots, the database dump and image updates: start, end and errors."))
P.append(panel("Image pulls", "logs", LOKI, [loki('{syslog_identifier="podman"} |= "Trying to pull"')], g, w=12, h=9,
    desc="Every image podman fetched: a deploy with a new tag, or podman-auto-update following a tag's digest (ImagePulled)."))
P.append(panel("SELinux denials and OOM kills", "logs", LOKI, [loki(AVC)], g, w=24, h=9,
    desc="Raw audit and kernel lines. `scontext` names the confined domain; `comm` the program."))
system = dashboard("system", "System log", ["home-server"], P, links=LINKS)

for d in (host, proxy, nextcloud, system):
    (OUT / f"{d['uid']}.json").write_text(json.dumps(d, indent=2) + "\n")
    print(d["uid"], len(d["panels"]), "panels")
