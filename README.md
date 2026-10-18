# Solution 2027

Python portal for Engr. Solomon Eigege Oloche’woche’s polling-unit agent coordination in Apa and Agatu.

## Run locally

Requires Python 3.10 or newer. No packages to install.

```bash
python3 app.py --setup-admin
python3 app.py
```

Open http://127.0.0.1:8000 and sign in. Stop with Ctrl+C. Setup prompts for a username and a password of at least 12 characters; password entry is hidden. To reset credentials, stop the server, rerun `python3 app.py --setup-admin`, then restart. There is no default password.

```bash
python3 -m unittest -v
```

## First version

- Add polling units with LGA, ward, unique official PU code and name.
- Record aggregate registered-voter totals with source and source date.
- Assign consenting primary and backup agents; record Pending, Confirmed or Trained status.
- Track confirmed PU coverage (Confirmed or Trained assignments), voter totals and agent assignments.
- Add operational field reports for logistics, incidents and community concerns.
- Persist data in SQLite across restarts. User input is escaped and writes require a session-specific CSRF token.
- Require administrator login before viewing or changing portal records. Passwords use salted PBKDF2-SHA256 (600,000 iterations); sessions expire after one hour. Login rotates the session, logout revokes it, and login attempts are limited to five per five minutes per address.

No official PU or voter figures are preloaded. Start with sample data. Dashboard totals cover entered records only; missing units do not count toward coverage.

## Development boundary and next steps

One administrator account is supported. Coordinator roles are deferred. The server binds only to localhost. Continue using sample data: production hosting, HTTPS, access roles, backup procedures and deployment review are still needed before real use. This is a development server, not a production deployment. Sessions and login throttles are held in memory for this single-process local server; restarting signs everyone out. The local HTTP cookie uses HttpOnly and SameSite=Strict. Production deployment must use HTTPS with Secure cookies.

Next: coordinator roles, edit/archive workflows, sourced PU CSV import, exports, audit history and production database/backups.

The portal does not store individual voter records, PVC/NIN details, or political-affiliation profiles. Operational reports should not include those details.

Database: `solution2027.sqlite3`, excluded from git. Override its path with `SOLUTION_DB`. Keep database backups outside the public repository. No secrets or personal records belong in git.
