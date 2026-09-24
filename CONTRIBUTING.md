# Contributing

Work on `dev`. `main` takes a merge from `dev` with `--no-ff`.

Write conventional commits. The commitizen hook rejects a message that does not
follow the format.

Install the hooks in this repository:

```bash
pre-commit install --install-hooks -t pre-commit -t commit-msg -t pre-push
```

Plain `pre-commit install` installs the pre-commit stage alone, and the commit
message and branch hooks then do not run. CI runs the pre-commit stage hooks on
a push and on a pull request.

Every GitHub action is pinned to a commit SHA. Dependabot updates the actions
and the hook revisions weekly against `dev`. `pinact run -u` updates and
re-pins the actions by hand.

## Releases

A release is a merge of `dev` into `main`, then an annotated tag on the merge
and `git push --follow-tags`. The `release` workflow turns every pushed tag into
a GitHub release. GitHub writes its notes: the pull requests merged since the
previous release and a link that compares the two tags.

Tags are `vX.Y`, and a fix release adds `.Z`, such as `v1.0` and `v1.0.1`.

## House style

A comment says why, never what. Longer reasoning belongs in the README of the
repository that owns the code. Write the prose in Simplified Technical English:
short sentences, one meaning per word, and the condition before the command.

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
