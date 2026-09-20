#!/usr/bin/env python3
"""Generate the Grafana dashboards of service-monitoring.

One place for layout and query conventions; the JSON files are the artifact.
"""
import json
import pathlib
import sys

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}

# nginx JSON access log of the Nextcloud web container, health checks excluded
NGINX = '{user_unit="nextcloud-web.service", syslog_identifier="nginx"} | json | request_uri != "/status.php"'
# Nextcloud application log: one JSON object per line, syslog tag nextcloud
NCLOG = '{syslog_identifier="nextcloud"} | json'
# BunkerWeb access log: <site> <ip> - <reqid> - [time] "<method> <uri> <proto>" <status> <bytes> "<referer>" "<ua>"
BW = ('{container="bunker-nginx"} |~ `^[A-Za-z0-9.-]+ [0-9a-fA-F.:]+ - ` != "bwapi"'
      ' | pattern `<site> <ip> - <_> - [<_>] "<method> <uri> <_>" <status> <bytes> "<_>" "<ua>"`')


class Grid:
    def __init__(self):
        self.y = 0
        self.x = 0
        self.row_h = 0

    def place(self, w, h):
        if self.x + w > 24:
            self.x = 0
            self.y += self.row_h
            self.row_h = 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def newline(self):
        if self.x:
            self.x = 0
            self.y += self.row_h
            self.row_h = 0


def panel(title, ptype, ds, targets, grid, w=12, h=8, unit=None, opts=None, overrides=None,
          decimals=None, thresholds=None, color=None, desc=None, stack=False, minimum=None, maximum=None):
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
    if thresholds:
        d["thresholds"] = {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in thresholds]}
        if ptype == "timeseries":
            d.setdefault("custom", {})["thresholdsStyle"] = {"mode": "line"}
    if color:
        d["color"] = {"mode": color}
    if ptype == "timeseries":
        c = d.setdefault("custom", {})
        c.update({"lineWidth": 1, "fillOpacity": 12, "showPoints": "never", "spanNulls": True})
        if stack:
            c["stacking"] = {"mode": "normal"}
            c["fillOpacity"] = 40
        p["options"].setdefault("legend", {"displayMode": "list", "placement": "bottom", "calcs": []})
        p["options"].setdefault("tooltip", {"mode": "multi", "sort": "desc"})
    if ptype == "stat":
        p["options"].update({"colorMode": "value", "graphMode": "area", "textMode": "value",
                             "reduceOptions": {"calcs": ["lastNotNull"], "fields": ""}})
    if ptype == "logs":
        p["options"].update({"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
                             "dedupStrategy": "none", "enableLogDetails": True})
    if ptype == "table":
        p["options"].update({"showHeader": True, "cellHeight": "sm"})
    if ptype == "piechart":
        p["options"].update({"reduceOptions": {"calcs": ["lastNotNull"], "fields": ""},
                             "legend": {"displayMode": "list", "placement": "right"},
                             "pieType": "donut", "displayLabels": ["percent"]})
    return p


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
             "titleFormat": "Boot", "textFormat": "{{.__line__}}"},
        ]},
        "panels": panels,
    }


LINKS = [("Host", "/d/host"), ("Proxy", "/d/proxy"), ("Nextcloud", "/d/nextcloud"), ("System log", "/d/system")]

# --------------------------------------------------------------------------- host
g = Grid()
P = []
P.append(panel("Uptime", "stat", PROM, [prom("node_time_seconds - node_boot_time_seconds")], g, w=4, h=4, unit="dtdurations", decimals=1, color="fixed"))
P.append(panel("CPU", "stat", PROM, [prom('100 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100')], g, w=4, h=4, unit="percent", decimals=0, thresholds=[(None, "green"), (70, "orange"), (90, "red")]))
P.append(panel("Load (5m)", "stat", PROM, [prom("node_load5")], g, w=4, h=4, decimals=1, thresholds=[(None, "green"), (4, "orange"), (8, "red")], desc="Four cores: above 4 the box queues work."))
P.append(panel("Memory used", "stat", PROM, [prom("100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)")], g, w=4, h=4, unit="percent", decimals=0, thresholds=[(None, "green"), (80, "orange"), (90, "red")]))
P.append(panel("Root disk used", "stat", PROM, [prom('100 * (1 - node_filesystem_avail_bytes{mountpoint="/var"} / node_filesystem_size_bytes{mountpoint="/var"})')], g, w=4, h=4, unit="percent", decimals=0, thresholds=[(None, "green"), (80, "orange"), (90, "red")]))
P.append(panel("Hottest sensor", "stat", PROM, [prom("max(node_hwmon_temp_celsius)")], g, w=4, h=4, unit="celsius", decimals=0, thresholds=[(None, "green"), (75, "orange"), (85, "red")]))

P.append(panel("CPU by mode", "timeseries", PROM, [prom('sum by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[$__rate_interval])) * 100 / scalar(count(node_cpu_seconds_total{mode="idle"}))', "{{mode}}")], g, w=12, unit="percent", stack=True, minimum=0, maximum=100))
P.append(panel("Load average", "timeseries", PROM, [prom("node_load1", "1m"), prom("node_load5", "5m"), prom("node_load15", "15m")], g, w=12, decimals=1, thresholds=[(None, "transparent"), (4, "orange")]))
P.append(panel("Memory", "timeseries", PROM, [
    prom("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", "used"),
    prom("node_memory_Cached_bytes + node_memory_Buffers_bytes", "cache + buffers"),
    prom("node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes", "swap (zram)"),
    prom("node_memory_MemTotal_bytes", "total")], g, w=12, unit="bytes"))
P.append(panel("Pressure stall", "timeseries", PROM, [
    prom("rate(node_pressure_cpu_waiting_seconds_total[$__rate_interval]) * 100", "cpu"),
    prom("rate(node_pressure_memory_waiting_seconds_total[$__rate_interval]) * 100", "memory"),
    prom("rate(node_pressure_io_waiting_seconds_total[$__rate_interval]) * 100", "io")], g, w=12, unit="percent",
    desc="Share of time tasks waited for the resource. Sustained IO pressure on a thin client means the SSD, not the CPU, is the bottleneck."))
P.append(panel("Disk space used", "timeseries", PROM, [prom('100 * (1 - node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs"} / node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs"})', "{{mountpoint}}")], g, w=12, unit="percent", minimum=0, maximum=100, thresholds=[(None, "transparent"), (90, "red")]))
P.append(panel("Disk throughput", "timeseries", PROM, [
    prom('sum by (device) (rate(node_disk_read_bytes_total{device!~"loop.*|dm-.*|sr.*|zram.*"}[$__rate_interval]))', "{{device}} read"),
    prom('-sum by (device) (rate(node_disk_written_bytes_total{device!~"loop.*|dm-.*|sr.*|zram.*"}[$__rate_interval]))', "{{device}} write")], g, w=12, unit="Bps"))
P.append(panel("Disk busy", "timeseries", PROM, [prom('rate(node_disk_io_time_seconds_total{device!~"loop.*|dm-.*|sr.*|zram.*"}[$__rate_interval]) * 100', "{{device}}")], g, w=12, unit="percent", minimum=0, maximum=100))
P.append(panel("Network", "timeseries", PROM, [
    prom('sum by (device) (rate(node_network_receive_bytes_total{device!~"lo|veth.*|podman.*|pasta.*"}[$__rate_interval]) * 8)', "{{device}} in"),
    prom('-sum by (device) (rate(node_network_transmit_bytes_total{device!~"lo|veth.*|podman.*|pasta.*"}[$__rate_interval]) * 8)', "{{device}} out")], g, w=12, unit="bps"))
P.append(panel("Temperatures", "timeseries", PROM, [prom("node_hwmon_temp_celsius", "{{chip}} {{sensor}}")], g, w=12, unit="celsius", thresholds=[(None, "transparent"), (85, "red")]))
P.append(panel("Processes", "timeseries", PROM, [prom("node_procs_running", "running"), prom("node_procs_blocked", "blocked on IO")], g, w=12, decimals=0))
host = dashboard("host", "Host", ["home-server"], P, links=LINKS)

# --------------------------------------------------------------------------- proxy
g = Grid()
P = []
P.append(panel("Requests / min", "stat", LOKI, [loki_instant(f"sum(count_over_time({BW} [1h])) / 60")], g, w=4, h=4, decimals=1, color="fixed", desc="Last hour, without BunkerWeb's own health checks."))
P.append(panel("4xx (1h)", "stat", LOKI, [loki_instant(f'sum(count_over_time({BW} | status =~ "4.." [1h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (50, "orange")]))
P.append(panel("5xx (1h)", "stat", LOKI, [loki_instant(f'sum(count_over_time({BW} | status =~ "5.." [1h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (1, "red")]))
P.append(panel("Denied (24h)", "stat", LOKI, [loki_instant('sum(count_over_time({container="bunker-nginx"} |= "denied access" [24h])) or vector(0)')], g, w=4, h=4, decimals=0, color="fixed", desc="Requests BunkerWeb refused: geo allowlist, method, ModSecurity, rate limit."))
P.append(panel("Bans (24h)", "stat", LOKI, [loki_instant('sum(count_over_time({container="bunker-nginx"} |= "[BADBEHAVIOR]" |= "is banned for" [24h])) or vector(0)')], g, w=4, h=4, decimals=0, color="fixed"))
P.append(panel("Certificate lines (7d)", "stat", LOKI, [loki_instant('sum(count_over_time({container="bunker-scheduler"} |~ "(?i)certbot|certificate" |~ "(?i)error|fail" [7d])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (1, "red")], desc="Certificate errors from the scheduler in the last week. Zero is right."))

P.append(panel("Requests by site", "timeseries", LOKI, [loki(f"sum by (site) (rate({BW} [$__auto]))", "{{site}}")], g, w=12, unit="reqps", stack=True))
P.append(panel("Responses by class", "timeseries", LOKI, [loki(f'sum by (class) (rate({BW} | label_format class="{{{{ .status | substr 0 1 }}}}xx" [$__auto]))', "{{class}}")], g, w=12, unit="reqps", stack=True,
    overrides=[{"matcher": {"id": "byName", "options": n}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]} for n, c in [("2xx", "green"), ("3xx", "blue"), ("4xx", "orange"), ("5xx", "red")]]))
P.append(panel("Denied and ModSecurity", "timeseries", LOKI, [
    loki('sum(count_over_time({container="bunker-nginx"} |= "denied access" [$__auto]))', "denied"),
    loki('sum(count_over_time({container="bunker-nginx"} |= "ModSecurity" [$__auto]))', "modsecurity match"),
    loki('sum(count_over_time({container="bunker-nginx"} |= "[BADBEHAVIOR]" |= "is banned for" [$__auto]))', "ban")], g, w=12, decimals=0))
P.append(panel("ModSecurity rules hit", "table", LOKI, [loki_instant('topk(10, sum by (id) (count_over_time({container="bunker-nginx"} |= "ModSecurity" | regexp `\\[id "(?P<id>\\d+)"\\]` [$__range])))')], g, w=12,
    desc="CRS rule ids over the dashboard range. 930130 is scanners probing /.env and friends; 920440 a blocked file extension.",
    opts={"sortBy": [{"displayName": "Value", "desc": True}]}))
P.append(panel("Top clients", "table", LOKI, [loki_instant(f"topk(10, sum by (ip) (count_over_time({BW} [$__range])))")], g, w=8, opts={"sortBy": [{"displayName": "Value", "desc": True}]}))
P.append(panel("Top paths", "table", LOKI, [loki_instant(f"topk(10, sum by (uri) (count_over_time({BW} [$__range])))")], g, w=8, opts={"sortBy": [{"displayName": "Value", "desc": True}]}))
P.append(panel("Top user agents", "table", LOKI, [loki_instant(f"topk(10, sum by (ua) (count_over_time({BW} [$__range])))")], g, w=8, opts={"sortBy": [{"displayName": "Value", "desc": True}]}))
P.append(panel("Bans and denials", "logs", LOKI, [loki('{container="bunker-nginx"} |~ "denied access|\\\\[BADBEHAVIOR\\\\]" | regexp "client: (?P<client>[0-9.]+)" | line_format "{{.client}} {{.__line__}}" ')], g, w=12, h=10))
P.append(panel("Errors and certificate events", "logs", LOKI, [loki('{container=~"bunker-.*"} |~ "\\\\[(error|crit|alert|emerg)\\\\]|\\\\[ERROR\\\\]|(?i)certbot|certificate" != "denied access" != "ModSecurity"')], g, w=12, h=10))
proxy = dashboard("proxy", "Proxy", ["home-server"], P, links=LINKS)

# --------------------------------------------------------------------------- nextcloud
g = Grid()
P = []
P.append(panel("Requests / min", "stat", LOKI, [loki_instant(f"sum(count_over_time({NGINX} [1h])) / 60")], g, w=4, h=4, decimals=1, color="fixed"))
P.append(panel("p95 response time", "stat", LOKI, [loki_instant(f"quantile_over_time(0.95, {NGINX} | unwrap request_time [1h])")], g, w=4, h=4, unit="s", decimals=2, thresholds=[(None, "green"), (2, "orange"), (5, "red")]))
P.append(panel("5xx (1h)", "stat", LOKI, [loki_instant(f'sum(count_over_time({NGINX} | status =~ "5.." [1h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (1, "red")]))
P.append(panel("Failed logins (24h)", "stat", LOKI, [loki_instant('sum(count_over_time({syslog_identifier="nextcloud"} |= "Login failed: \'" [24h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (5, "orange"), (20, "red")]))
P.append(panel("App warnings+ (24h)", "stat", LOKI, [loki_instant(f"sum(count_over_time({NCLOG} | level >= 2 [24h])) or vector(0)")], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (50, "orange")], desc="Nextcloud log level 2 warning, 3 error, 4 fatal."))
P.append(panel("php-fpm at max children (24h)", "stat", LOKI, [loki_instant('sum(count_over_time({syslog_identifier="php-fpm"} |= "reached pm.max_children" [24h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (1, "orange")], desc="Every hit means requests queued. Raise nextcloud_service_php_max_children if this is not zero at quiet times."))

P.append(panel("Requests by class", "timeseries", LOKI, [loki(f'sum by (class) (rate({NGINX} | label_format class="{{{{ .status | substr 0 1 }}}}xx" [$__auto]))', "{{class}}")], g, w=12, unit="reqps", stack=True,
    overrides=[{"matcher": {"id": "byName", "options": n}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]} for n, c in [("2xx", "green"), ("3xx", "blue"), ("4xx", "orange"), ("5xx", "red")]]))
P.append(panel("Response time", "timeseries", LOKI, [
    loki(f"quantile_over_time(0.5, {NGINX} | unwrap request_time [$__auto])", "p50"),
    loki(f"quantile_over_time(0.95, {NGINX} | unwrap request_time [$__auto])", "p95"),
    loki(f"max_over_time({NGINX} | unwrap request_time [$__auto])", "max")], g, w=12, unit="s"))
P.append(panel("Bytes sent", "timeseries", LOKI, [loki(f"sum(rate({NGINX} | unwrap bytes_sent [$__auto]))", "sent")], g, w=12, unit="Bps"))
P.append(panel("Clients", "timeseries", LOKI, [loki(f'sum by (client) (rate({NGINX} | regexp "(?P<client>mirall|Nextcloud-android|Nextcloud-iOS|DAVx5|PhoneTrack|Mozilla|curl|Wget)" | label_format client="{{{{ if .client }}}}{{{{ .client }}}}{{{{ else }}}}other{{{{ end }}}}" [$__auto]))', "{{client}}")], g, w=12, unit="reqps", stack=True,
    desc="mirall is the desktop client."))
P.append(panel("Top paths", "table", LOKI, [loki_instant(f'topk(10, sum by (path) (count_over_time({NGINX} | regexp "(?P<path>^/[^/?]*(/[^/?]*)?)" [$__range])))')], g, w=8, opts={"sortBy": [{"displayName": "Value", "desc": True}]}))
P.append(panel("Top client addresses", "table", LOKI, [loki_instant(f'topk(10, sum by (addr) (count_over_time({NGINX} | label_format addr="{{{{ if .http_x_forwarded_for }}}}{{{{ .http_x_forwarded_for }}}}{{{{ else }}}}{{{{ .remote_addr }}}}{{{{ end }}}}" [$__range])))')], g, w=8, opts={"sortBy": [{"displayName": "Value", "desc": True}]}))
P.append(panel("App log by level", "table", LOKI, [loki_instant(f"sum by (level, app) (count_over_time({NCLOG} | level >= 2 [$__range]))")], g, w=8, opts={"sortBy": [{"displayName": "Value", "desc": True}]}))
P.append(panel("Failed logins", "logs", LOKI, [loki('{syslog_identifier="nextcloud"} |= "Login failed: \'" | regexp "Login failed: \'(?P<user>[^\']+)\' \\\\(Remote IP: \'(?P<ip>[^\']+)\'\\\\)" | line_format "{{.user}} from {{.ip}}"')], g, w=12, h=9))
P.append(panel("App warnings and errors", "logs", LOKI, [loki(f'{NCLOG} | level >= 2 | line_format "[{{{{.app}}}}] {{{{.message}}}}"')], g, w=12, h=9))
P.append(panel("Background containers (cron, preview, recognize, push)", "logs", LOKI, [loki('{user_unit=~"nextcloud-(cron|preview|recognize|push).service"} != "Scanning folder"')], g, w=12, h=9))
P.append(panel("Database and cache", "logs", LOKI, [loki('{container=~"nextcloud-(db|redis)"} |~ "(?i)error|fatal|warning|panic"')], g, w=12, h=9))
nextcloud = dashboard("nextcloud", "Nextcloud", ["home-server"], P, links=LINKS)

# --------------------------------------------------------------------------- system
g = Grid()
P = []
P.append(panel("Active alerts", "stat", PROM, [prom('sum(alertmanager_alerts{state="active"}) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (1, "orange")]))
P.append(panel("Unit failures (24h)", "stat", LOKI, [loki_instant('sum(count_over_time({syslog_identifier="systemd"} |= "Failed with result" [24h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (1, "orange")]))
P.append(panel("SSH logins (24h)", "stat", LOKI, [loki_instant('sum(count_over_time({unit="sshd.service"} |= "Accepted publickey" [24h])) or vector(0)')], g, w=4, h=4, decimals=0, color="fixed"))
P.append(panel("SSH failures (24h)", "stat", LOKI, [loki_instant('sum(count_over_time({unit="sshd.service"} |~ "Invalid user|Failed (publickey|password)" [24h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (1, "orange")]))
P.append(panel("SELinux denials (24h)", "stat", LOKI, [loki_instant('sum(count_over_time({syslog_identifier="audit"} |= "avc:  denied" != "permissive=1" [24h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (20, "orange")], desc="Enforced denials only."))
P.append(panel("OOM kills (24h)", "stat", LOKI, [loki_instant('sum(count_over_time({syslog_identifier="kernel"} |= "Killed process" [24h])) or vector(0)')], g, w=4, h=4, decimals=0, thresholds=[(None, "green"), (1, "red")]))

P.append(panel("Alerts", "alertlist", {"type": "alertmanager", "uid": "alertmanager"}, [], g, w=12, h=9,
    opts={"datasource": "Alertmanager", "showInstances": True, "sortOrder": 1, "stateFilter": {"firing": True, "pending": True, "normal": False, "error": True, "noData": False},
          "viewMode": "list", "groupMode": "default", "maxItems": 20, "alertName": "", "dashboardAlerts": False, "alertInstanceLabelFilter": "", "folder": None},
    desc="What Alertmanager holds right now, from Prometheus and Loki rules."))
P.append(panel("Journal errors by unit", "timeseries", LOKI, [loki('sum by (unit) (count_over_time({job="systemd-journal", priority=~"[0-3]"} [$__auto]))', "{{unit}}")], g, w=12, h=9, decimals=0, stack=True,
    desc="Priority 0 to 3: emerg, alert, crit, err. Container output carries the unit's priority (info), so this is the host's own noise."))
P.append(panel("Container state changes", "logs", LOKI, [loki('{syslog_identifier="systemd", user_unit=~"(nextcloud|bunker|monitoring|nc|proxy)-.*"} |~ "Started|Stopped|Failed with result|Scheduled restart|Main process exited" | line_format "{{.user_unit}}: {{.__line__}}"')], g, w=12, h=10,
    desc="Manager messages about the service users' containers. A deploy restarts the pods; anything else here deserves a look."))
P.append(panel("Restarts per unit", "timeseries", LOKI, [loki('sum by (user_unit) (count_over_time({syslog_identifier="systemd", user_unit=~".+"} |= "Scheduled restart job" [$__auto]))', "{{user_unit}}")], g, w=12, h=10, decimals=0))
P.append(panel("Logins", "logs", LOKI, [loki('{unit="sshd.service"} |~ "Accepted publickey|Failed (publickey|password)|Invalid user" | regexp "(?P<what>Accepted publickey|Failed publickey|Failed password|Invalid user) (for )?(?P<user>[^ ]+) from (?P<ip>[0-9a-f.:]+)" | line_format "{{.what}}: {{.user}} from {{.ip}}"')], g, w=12, h=9))
P.append(panel("Updates, reboots, boots", "logs", LOKI, [loki('{unit=~"rpm-ostreed.service|auto-reboot-staged.service|init.scope"} |~ "Staged|Deployment|Rebooting|Startup finished|reboot|No staged deployment"')], g, w=12, h=9,
    desc="rpm-ostree stages an update; auto-reboot-staged.timer reboots at night when one is staged."))
P.append(panel("Scheduled jobs", "logs", LOKI, [loki('{unit=~"btrfs-backup@.*|btrfs-snapshot@.*|pg-dumpall.service|unstick-jobs.service|podman-auto-update.service"} |~ "Deactivated successfully|Failed with result|run complete|Starting|error|Error"')], g, w=12, h=9,
    desc="Backups, snapshots, the database dump, job unsticking and image updates."))
P.append(panel("SELinux denials and OOM kills", "logs", LOKI, [loki('{syslog_identifier=~"audit|kernel"} |~ "avc:  denied|Killed process" != "permissive=1"')], g, w=12, h=9))
system = dashboard("system", "System log", ["home-server"], P, links=LINKS)

for d in (host, proxy, nextcloud, system):
    (OUT / f"{d['uid']}.json").write_text(json.dumps(d, indent=2) + "\n")
    print(d["uid"], len(d["panels"]), "panels")
