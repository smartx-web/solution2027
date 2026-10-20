# Render Free with Neon PostgreSQL

The portal runs as one Waitress process on Render; all records live in Neon, independently of Render restarts. Local development still supports SQLite. Login, account roles, ward permissions and historical totals are shared across both backends.

## Database

A dedicated Neon project `solution2027` has been created in Frankfurt (project `aged-king-13372174`, database `solution2027`). Other projects are untouched. Production contains the PU directory and historical baseline; no bootstrap credentials are committed. A separate `verify-postgres` branch is available for validation.

In [Neon Console](https://console.neon.tech), select this project's production branch, database `solution2027`, role `solution_owner`, and Connect. Copy the pooled and direct connection URLs privately. Retain `sslmode=require` or `verify-full`. Never paste URLs into source files, public issues or chat.

## First deployment

1. Install dependencies locally: `pip install -r requirements.txt`.
2. Generate a password hash: `python3 manage.py hash-password`. Save the password privately.
3. Open [Render Blueprint setup](https://dashboard.render.com/blueprint/new?repo=https://github.com/smartx-web/solution2027).
4. Review the free web plan and set these secret environment variables:

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | Neon pooled connection URL |
| `DATABASE_URL_UNPOOLED` | Matching direct connection URL, used for migrations |
| `ADMIN_NAME` | Initial administrator username |
| `ADMIN_PASSWORD_HASH` | Generated hash, including all dollar signs |

`SOLUTION_ENV=production` is supplied by the Blueprint. Render supplies `RENDER_EXTERNAL_URL`; for a custom domain set `APP_ORIGIN` to its canonical HTTPS URL. The app rejects mismatched hosts and cross-origin POSTs. No persistent disk is required. Automatic deployment is off.

Startup applies the versioned PostgreSQL schema, seeds the 236 PUs and 2023 baseline idempotently, and provisions an admin only when none exists. Existing account passwords are preserved. Remove the bootstrap hash from Render after successful first login. Keep a recovery credential securely.

Render and Neon can sleep when idle, so the first visit may be slower. Free quotas and availability are controlled by each provider; review usage in their dashboards. Sessions are in process memory, so Render restarts require another login. Keep one process/instance until shared session storage is implemented. Health checks report process readiness without waking Neon repeatedly.

## Existing SQLite records

The created production database already has directory/baseline records. The transfer command deliberately refuses any populated destination. To transfer an existing portal, use a separate empty Neon database/branch, test there, then point Render to that database. Never delete production records to make room.

Stop writes and make a verified SQLite-native backup using the existing local `manage.py backup --output-dir /secure/path` command, with DATABASE_URL unset and SOLUTION_DB pointing to the original SQLite file. Upgrade the SQLite copy with the latest local application before transfer. Keep the original untouched.

On a trusted machine, privately set DATABASE_URL and DATABASE_URL_UNPOOLED for the empty target:

```bash
python3 manage.py migrate-sqlite --source /secure/path/backup.sqlite3
python3 manage.py migrate-sqlite --source /secure/path/backup.sqlite3 --apply
```

The first command validates and reports counts only. The second creates the schema and copies all tables in one transaction, preserving IDs, password hashes, roles, ward scopes, archival status, audit history and voter totals. It rejects a populated destination and checks row counts before commit. Repeated application is refused. After transfer, startup fills missing bundled directory/baseline entries without replacing manually sourced totals. Check totals and sign in using existing accounts before switching hosting. Keep the old portal read-only until the cutover is verified.

## Backups and verification

SQLite backup scheduling does not apply to Neon. Arrange regular encrypted off-server PostgreSQL backups. On a trusted machine with a compatible PostgreSQL `pg_dump` client installed (version 17 or later for this server):

```bash
python3 manage.py dump-postgres --output /secure/path/solution2027.dump
pg_restore --list /secure/path/solution2027.dump
```

Use the direct URL in DATABASE_URL_UNPOOLED. The output is created exclusively with owner-only permissions. Test restoration into a separate database before relying on backups. Neon recovery windows are plan-dependent and do not replace off-server backups; no recurring backup job is provisioned here.

After deployment check `/healthz`, login, secure cookies, dashboard totals (236 PUs and historical 131,035), ward-scoped access, agent creation/archival, CSV export and logout. Make a test change and verify it survives a Render restart. Check logs without sharing secrets.

## PU Results upgrade

Install the updated `requirements.txt` (Pillow validates result-sheet photos). Restarting applies migration 002 for agent PU assignments, immutable result revisions and verification history. Back up first and validate the migration in the separate test database before switching production. Result-sheet JPEG/PNG uploads are limited to 2 MiB / 20 megapixels and stored as base64 database records; include these growing tables in backup capacity planning. Render’s local filesystem is not used for uploads. Existing PostgreSQL dump and SQLite backup commands include photos and result history. SQLite transfer now includes result revisions, review events and PU account assignments; upgrade a SQLite copy before transfer as described above.

After upgrading, check an authenticated multipart submission, photo retrieval, arithmetic flags, a correction with retained original evidence, and an administrator review. Check that viewers cannot submit, coordinators cannot reach another ward, and PU agents cannot reach another PU’s result or photo. Use sample records in the test database.

### Local PostgreSQL release checks

`test_postgres_integration.py` uses only a local Unix socket and creates/drops its own randomly named disposable databases. It checks the real v1 → v2 migration with existing accounts, repeated seeding, result corrections/flags/permissions, SQLite transfer including photo/history and identity sequences, and the `manage.py dump-postgres` backup command followed by a real restore. It never accepts a remote production URL. With a local PostgreSQL 17+ instance and matching client tools:

```bash
SOLUTION_TEST_PG_SOCKET=/absolute/private/socket \
SOLUTION_TEST_PG_PORT=55432 \
SOLUTION_TEST_PG_BIN=/path/to/postgresql/bin \
.venv/bin/python -m unittest -v
```

Without these variables, the four live PostgreSQL tests are explicitly skipped; the remaining suite still runs. The dump command parses the direct URL into libpq environment variables, retaining TLS and channel-binding requirements while keeping credentials out of command arguments. Local PostgreSQL validation does not establish live Neon or Render connectivity.
