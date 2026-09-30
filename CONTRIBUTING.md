# Contributing

The branch flow, the hooks, the releases and the house style are in
`home-server/CONTRIBUTING.md`.

## Checking a dashboard before you commit

Join `grafana-image-renderer` to the monitoring pod on the test VM. Set
`SERVER_ADDR=:8082` and a shared `AUTH_TOKEN`. Give Grafana
`GF_RENDERING_SERVER_URL`, `GF_RENDERING_CALLBACK_URL` and
`GF_RENDERING_RENDERER_TOKEN` through a `monitoring-grafana.container.d`
drop-in.

Then request `GET /render/d/<uid>/<uid>?kiosk&width=1600&height=-1` for each
dashboard.

Copy `monitoring/dashboards/` into the VM's `configs/dashboards/monitoring/`.
Grafana reloads them within ten seconds, so one iteration costs less than a
minute. A copied dashboard has no link bar until a deploy that changes this service's
files adds it.

A deploy that changes this service's files removes the drop-in. Otherwise
delete it by hand.

## Checks in this repository

- Hooks: the basics, ansible-lint and commitizen.
- Ansible variables are `<role>_*`.
- Renovate updates the container image tags in the role defaults, through the
  preset that `.github/renovate.json` extends.
- `home-server` checks this repository out at `services/monitoring`.
  `home-server/CONTRIBUTING.md` says how a change here reaches a deploy.
