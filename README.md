# Solution 2027

Python portal for Engr. Solomon Eigege Oloche’woche’s polling-unit agent coordination in Apa and Agatu.

## Run locally

Requires Python 3.10 or newer. No packages to install.

```bash
python3 app.py
```

Open http://127.0.0.1:8000. Stop with Ctrl+C.

```bash
python3 -m unittest -v
```

## First version

- Add polling units with LGA, ward, unique official PU code and name.
- Record aggregate registered-voter totals with source and source date.
- Assign consenting primary and backup agents; record Pending, Confirmed or Trained status.
- Track confirmed PU coverage (Confirmed or Trained assignments), voter totals and agent assignments.
- Add operational field reports for logistics, incidents and community concerns.
- Persist data in SQLite across restarts. User input is escaped and writes require a CSRF token.

No official PU or voter figures are preloaded. Start with sample data. Dashboard totals cover entered records only; missing units do not count toward coverage.

## Development boundary and next steps

Login and roles are deferred. The server binds only to localhost. Do not expose this version publicly or enter real agent data before authentication and access controls are implemented. This is a development server, not a production deployment.

Next: authenticated administrator/coordinator roles, edit/archive workflows, sourced PU CSV import, exports, audit history and production database/backups.

The portal does not store individual voter records, PVC/NIN details, or political-affiliation profiles. Operational reports should not include those details.

Database: `solution2027.sqlite3`, excluded from git. Override its path with `SOLUTION_DB`. Keep database backups outside the public repository. No secrets or personal records belong in git.
