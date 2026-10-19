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

Startup loads the bundled Apa/Agatu directory snapshot (236 PUs in 21 wards). It contains PU names/codes only, taken from the user-supplied Nigeria_polling_units.csv. It is not a live or independently verified current INEC register. Registered-voter totals remain unknown until entered with a source/date. Dashboard totals cover recorded values only. Start with sample agent data.

## Development boundary and next steps

One administrator account is supported. Coordinator roles are deferred. The server binds only to localhost. Continue using sample data: production hosting, HTTPS, access roles, backup procedures and deployment review are still needed before real use. This is a development server, not a production deployment. Sessions and login throttles are held in memory for this single-process local server; restarting signs everyone out. The local HTTP cookie uses HttpOnly and SameSite=Strict. Production deployment must use HTTPS with Secure cookies.

Next: coordinator roles, archive workflows, exports, audit history and production database/backups.

The portal does not store individual voter records, PVC/NIN details, or political-affiliation profiles. Operational reports should not include those details.

Database: `solution2027.sqlite3`, excluded from git. Override its path with `SOLUTION_DB`. Keep database backups outside the public repository. No secrets or personal records belong in git.

## Edit and import

After signing in, open **Edit records** and expand a PU or agent to change its details. PU edits keep existing agent/report links. Invalid edits leave the existing record unchanged.

Open **Import PUs** and paste CSV with this exact header:

```csv
lga,ward,code,name,voters,source,source_date
```

Each row needs Apa or Agatu, a ward, a unique official PU code, a PU name, a nonnegative integer voter total, a source and an ISO date (`YYYY-MM-DD`). Keep codes as text so leading zeros survive. Quote CSV values containing commas.

Preview validates all rows and shows the first 100 for review. Confirm to save. Imports accept at most 5,000 rows / 1 MB and never overwrite existing PU codes. A conflict or invalid row rejects the entire batch. Validation does not independently verify the figures: use an authoritative dataset and retain its source/date. No registered-voter figures have been imported.


## Linked location dropdowns

As in the volunteer portal, choose **LGA → Ward → Polling unit**. Agent assignment, agent editing, field reporting and the voter-total form use the same directory. Changing a parent clears its dependent choices. Directory endpoints require login. No external service is needed for the dropdowns.

Restart with `python3 app.py` after updating to load the directory. Existing PU codes, voter figures, agent assignments and reports are preserved. The database migrates unknown voter totals to NULL instead of treating them as zero. A known zero remains zero. Back up your SQLite database while the server is stopped before updating.

The bundled snapshot contains 11 Apa wards / 124 PUs and 10 Agatu wards / 112 PUs. These are file counts, not a claim of current official completeness. Additional PUs can be added or imported. Source: user-provided `Nigeria_polling_units.csv`, extracted 2026-10-19; original publication date is unknown. No voter identities are included.
