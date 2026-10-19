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
- Require an enabled account before viewing portal records; permissions control changes. Passwords use salted PBKDF2-SHA256 (600,000 iterations); sessions expire after one hour. Login rotates the session, logout revokes it, and login attempts are limited to five per five minutes per account and 50 per five minutes per address.

Startup loads the bundled Apa/Agatu directory snapshot (236 PUs in 21 wards). It contains PU names/codes only, taken from the user-supplied Nigeria_polling_units.csv. It is not a live or independently verified current INEC register. The bundled 2023 historical baseline is loaded separately; other voter totals remain unknown until entered with a source/date. Dashboard totals cover recorded values only. Start with sample agent data.

## Development boundary and next steps

Multiple administrator, viewer and coordinator accounts are supported. The server binds only to localhost. Continue using sample data: production hosting, HTTPS, backup procedures and deployment review are still needed before real use. This is a development server, not a production deployment. Sessions and login throttles are held in memory for this single-process local server; restarting signs everyone out. The local HTTP cookie uses HttpOnly and SameSite=Strict. Production deployment must use HTTPS with Secure cookies.

Next: production hosting, database and backups.

The portal does not store individual voter records, PVC/NIN details, or political-affiliation profiles. Operational reports should not include those details.

Database: `solution2027.sqlite3`, excluded from git. Override its path with `SOLUTION_DB`. Keep database backups outside the public repository. No secrets or personal records belong in git.

## Edit and import

After signing in, open **Edit records** and expand a PU or agent to change its details. PU edits keep existing agent/report links. Invalid edits leave the existing record unchanged.

Open **Import PUs** and paste CSV with this exact header:

```csv
lga,ward,code,name,voters,source,source_date
```

Each row needs Apa or Agatu, a ward, a unique official PU code, a PU name, a nonnegative integer voter total, a source and an ISO date (`YYYY-MM-DD`). Keep codes as text so leading zeros survive. Quote CSV values containing commas.

Preview validates all rows and shows the first 100 for review. Confirm to save. Imports accept at most 5,000 rows / 1 MB and never overwrite existing PU codes. A conflict or invalid row rejects the entire batch. Validation does not independently verify the figures: use an authoritative dataset and retain its source/date. The bundled historical baseline is independent of this manual CSV import workflow.


## Linked location dropdowns

As in the volunteer portal, choose **LGA → Ward → Polling unit**. Agent assignment, agent editing, field reporting and the voter-total form use the same directory. Changing a parent clears its dependent choices. Directory endpoints require login. No external service is needed for the dropdowns.

Restart with `python3 app.py` after updating to load the directory. Existing PU codes, voter figures, agent assignments and reports are preserved. The database migrates unknown voter totals to NULL instead of treating them as zero. A known zero remains zero. Back up your SQLite database while the server is stopped before updating.

The bundled snapshot contains 11 Apa wards / 124 PUs and 10 Agatu wards / 112 PUs. These are file counts, not a claim of current official completeness. Additional PUs can be added or imported. Source: user-provided `Nigeria_polling_units.csv`, extracted 2026-10-19; original publication date is unknown. No voter identities are included.

## Search, filters and CSV downloads

Use **Find records** to filter by LGA, ward, PU/agent search or confirmed-agent coverage. Search matches PU name/code and agent name/phone; results include all assignments and reports belonging to matching PUs. Coverage includes Confirmed and Trained agents. Pending assignments do not count as confirmed coverage. Dashboard counts and CSV downloads follow the same filters. Clear filters to restore the full view.

Authenticated accounts can download polling units, agent assignments or field reports as CSV within their permitted area. Unknown voter totals export as blank. Spreadsheet formula prefixes are escaped and phone numbers receive a leading apostrophe to retain leading zeros. Keep agent exports restricted to authorised coordinators. Creating/editing records remains available across the permitted directory even when the displayed records are filtered.


## Archive and activity history

Under **Edit records**, expand an active agent and confirm **Archive assignment**. This preserves the record while excluding it from active assignment lists, confirmed coverage and agent CSV exports. Use **Archived agents** to restore an assignment after confirming ongoing consent. Archived assignments must be restored before editing. Archive and activity sections show portal-wide records independently of location filters.

The **Activity log** shows the latest 50 successful record creates, edits, PU imports, archives and restorations, with the acting account username, record ID and UTC time. Edit entries list changed field names; they do not copy contact details or report contents. No-op edits and rejected actions do not create change entries. Changes and their log entries commit in the same database transaction. Historical edits made before this feature, direct SQL changes, directory seeding and login events are not logged. This local application log is not tamper-proof against someone who has database file access.

Existing databases gain the archive column and activity table automatically on startup. Existing agents remain active. Keep a database backup before updates.


## Accounts and area permissions

Existing administrator credentials migrate automatically. Sign in with your current account and open **Accounts**. No default credentials or extra users are created. Create a separate account for each person:

| Role | Access |
| --- | --- |
| Administrator | Full portal; create/reset/disable accounts; manage directory and imports; view activity log |
| Viewer | View and export all portal records; cannot change records or manage accounts |
| Coordinator | View/export assigned LGA or selected wards; manage agents, reports, archive/restore and sourced voter totals there |

For Solomon, create a **Viewer** account if he needs full oversight without editing. Create a separate **Administrator** account only if he should manage records and accounts too.

For coordinators, choose one LGA. Leave ward scope blank for the entire LGA, or enter exact directory ward names one per line. Coordinators cannot add/rename/move directory PUs, import CSVs, manage users or see the portal-wide activity log. Areas are checked on the server for pages, location APIs, downloads and submitted record IDs, including moving agent assignments. Dashboard filters do not widen permissions.

To reset a password, expand the account under **Accounts** and enter a new password. Leave it blank when only changing permissions. Uncheck **Account enabled** to disable access. Updating any account invalidates its existing sessions on the next request, including changes to its role, area or password. You cannot disable/demote your own administrator account or remove the last active administrator. Existing usernames cannot be renamed.

Stop the server and use `python3 app.py --setup-admin` for local administrator recovery, then restart. This local setup command requires access to the server/database. Keep database files, password hashes and exports private. No real credentials should be committed to GitHub or shared in chat.


## 2023 registered-voter baseline

Startup loads `data/registered_voters_2023.json` into a separate, year-keyed `voter_baselines` table. It contains 236 unique PU totals, exactly matching the bundled directory:

| LGA | Polling units | 2023 registered voters |
| --- | ---: | ---: |
| Apa | 124 | 66,720 |
| Agatu | 112 | 64,315 |
| Total | 236 | 131,035 |

Source: CCIJ public repository `anndawn/Nigeria-2023-election`, `voter_info.csv`, field `registered_use`, pinned commit `e38e3bc9a22ac005564a9e95b4e846208a594522`. The source file's SHA-256, retrieval date (2026-10-19), source URL and methodology URL are retained in the bundled JSON. CCIJ's methodology attributes the registered-voter dataset to an INEC early-2023 publication; its exact publication date is unknown. These are historical aggregate figures, not individual voter records, 2027 totals or a newly certified INEC register.

The dashboard, PU table, location-selection messages and PU CSV export identify the 2023 baseline explicitly. Baseline totals follow account scope and active dashboard filters. A PU outside the historical file displays `Not recorded`, never an estimated value.

Other manually entered voter totals and their sources/dates stay in `pus` and are shown separately. Startup does not overwrite them, reset known zeros, combine the two totals or change agent/report links. Subsequent changes to those sourced totals do not modify 2023 history. Repeated startups do not duplicate historical rows or import-log entries. A conflicting historical row stops startup for review rather than overwriting it. Baselines match exact PU codes; renamed codes must be reconciled deliberately.

Update instructions: stop the server, back up the SQLite database, run `git pull origin main`, then `python3 app.py`. Baseline integration happens automatically during startup; do not paste the earlier review CSV into the directory-import form.

## Hosting preparation

Render configuration and a Waitress production entry point are included. See [DEPLOYMENT.md](DEPLOYMENT.md) for costs, initial administrator hash generation, persistent storage, hosted checks and backup/recovery instructions. Run `python3 app.py` for local development; Render runs `python3 deploy.py`. The deployment dependency is `requirements.txt`. This repository change does not create or publish a hosted service.
