# Render deployment

Prepared for one Python web service with login-gated portal access and a 1 GB persistent disk. The login screen and minimal health check are reachable online; this is application-level privacy, not a network-private Render service. No resources are created by committing these files.

## Cost and capacity

The Blueprint selects the smallest paid web compute plan that supports a disk (`0.5c-512mb`) and 1 GB storage. Review the price in Render before applying. Do not switch this SQLite setup to the free plan or remove its disk: runtime records would otherwise be lost after redeploys. No separate database service is provisioned.

Use one instance and one Python process. In-memory sessions and login throttles are shared between Waitress threads, not processes. Restarts sign users out. Disk-backed deployments briefly interrupt service. If you later need horizontal scaling, migrate to a managed database and shared session/rate-limit storage.

## Before deployment

1. Pull the latest `main` and back up your local database. A new hosted service starts with the PU directory, 2023 baseline and a new administrator; your local agents/accounts are not copied automatically.
2. Install the hosting dependency in a virtual environment: `python3 -m pip install -r requirements.txt`.
3. Generate your password hash locally:

   ```bash
   python3 manage.py hash-password
   ```

   Enter a password of 12 or more characters twice. Only the encoded hash prints. Save the actual password in your password manager; do not put it or the hash in GitHub or chat.
4. Review `render.yaml`. If Render CLI v2.7+ is installed, run `render blueprints validate render.yaml`. The CLI was unavailable in the preparation environment; YAML parsing, validation against Render’s public JSON Schema, and application tests passed. Render's platform-aware validation must still pass in the Dashboard/CLI.

## Create the service when ready

Open https://dashboard.render.com/blueprint/new?repo=https://github.com/smartx-web/solution2027 and connect the GitHub repository if prompted. Review the plan, disk and estimated charges before applying.

At the initial Blueprint prompts, set:

| Variable | Value |
| --- | --- |
| `ADMIN_NAME` | Your chosen initial administrator username |
| `ADMIN_PASSWORD_HASH` | Full output from `python3 manage.py hash-password` |

Other settings are already declared:

| Variable | Value |
| --- | --- |
| `SOLUTION_ENV` | `production` |
| `SOLUTION_DB` | `/var/data/solution2027.sqlite3` |
| `DATA_DIR` | `/var/data` |
| `BACKUP_DIR` | `/var/data/backups` |

Render injects the HTTPS URL as `RENDER_EXTERNAL_URL`; the application uses it as the allowed origin/host. For a custom domain, set `APP_ORIGIN` to its exact HTTPS origin and redeploy. Do not include a path or trailing query. The application rejects unexpected hosts and cross-origin POST requests. The canonical public endpoint is HTTPS; the internal HTTP listener must stay behind Render's TLS edge. Secure cookies are enabled in production.

Apply the Blueprint only when ready to incur its charges. Builds install dependencies. Startup, not build/pre-deploy, backs up and migrates the disk database, loads the baseline and provisions the initial administrator if none exists. Later restarts do not reset users or passwords from bootstrap variables. After successful initial login, remove `ADMIN_PASSWORD_HASH` from the service environment and keep its local recovery process available; existing accounts keep working.

Automatic deploys are disabled. Trigger a manual deploy after reviewing future changes. Do not add another process or instance.

## Hosted checks before entering real agent data

- `/healthz` responds `ok` and exposes no records.
- Opening the portal while signed out redirects to login; direct API/export access is blocked.
- The historical baseline shows 236 PUs and 131,035 voters when signed in as administrator.
- Create viewer and coordinator test accounts. Confirm the viewer cannot save changes and the coordinator sees only the assigned area, including exports.
- Save a clearly marked sample agent, restart the service, sign in again and verify it remains. Archive the sample after testing.
- In the service shell, verify a readable backup exists under `/var/data/backups` and test it as described below. Keep production exports and database files restricted.

## Backups and recovery

SQLite-native backups run before startup migrations when a database exists, after startup and once every 24 hours while the process is running. The latest 14 backup copies are retained; frequent deployments shorten the time span covered. Files have owner-only permissions and are not exposed by any portal route. A backup failure before migrations stops startup. Scheduled backup failures are logged for the operator to investigate.

Run a manual backup from the Render service shell:

```bash
python3 manage.py backup --output-dir /var/data/backups
```

A same-disk backup does not protect against deletion of the service/disk or account loss. Download regular copies to secure off-server storage using Render's documented SSH/SCP transfer flow. Off-server backup automation is not configured by this Blueprint.

To test a downloaded backup locally, leave the original unchanged:

```bash
python3 -c "import sqlite3; db=sqlite3.connect('downloaded-backup.sqlite3'); print(db.execute('PRAGMA quick_check').fetchone()[0]); print(db.execute('PRAGMA foreign_key_check').fetchall())"
```

Expect `ok` and an empty foreign-key-error list. Then start a separate local copy of the portal with `SOLUTION_DB` pointing to that backup and check account access, baseline figures and sample records. Keep it off public networks.

For production recovery, stop the service, preserve the current database, verify the chosen backup and replace the database while the server is stopped. Restart and repeat the hosted checks. Do not overwrite a live database, and do not use raw filesystem snapshots as a substitute for tested SQLite-native backups. Recovery to an older copy loses later changes.

If locked out, use the Render service shell with the configured `SOLUTION_DB` and run `python3 app.py --setup-admin`, then restart. This requires server-level access.

## Existing local records

No automatic transfer is performed. If you need existing local accounts and records online, create a SQLite-native backup, arrange a maintenance window and securely transfer it to the mounted disk before starting the hosted app. Do not upload databases to the public GitHub repository. Test migration on a separate copy first.
