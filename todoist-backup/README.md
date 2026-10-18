# todoist-backup

Full backup of a Todoist account to a self-contained ZIP: every project, section, task, comment, label, filter and reminder, plus archived projects and completed activity, plus a restore script and a README describing what's inside.

Todoist's own export covers one project at a time as CSV, and the CSV drops anything that isn't a task row. This reads the API and keeps the whole account.

## Install and run

```bash
pip3 install -r requirements.txt
export TODOIST_API_TOKEN=...            # or put it in a .env file
python3 todoist_backup.py               # writes data/todoist_backups/<timestamp>.zip
python3 todoist_backup.py --output-dir /path/to/backups
```

Get the token from Todoist → Settings → Integrations → Developer.

## Restoring

The ZIP contains a `restore.py` generated at backup time, so a backup never depends on this repository still being around.

```bash
python3 restore.py --token YOUR_TOKEN --dry-run    # always do this first
python3 restore.py --token YOUR_TOKEN
```

Restore creates new items. It does not reconcile against what's already in the account, so running it against a live account duplicates everything — restore into a fresh account, or an empty one.

The one non-obvious part: projects, sections and tasks are topologically sorted before creation, so a parent always exists before its children, at any nesting depth. Creating them in API order fails as soon as a subtask precedes its parent.

## Known limits

- **Read-side rate limiting is handled** with `Retry-After` and exponential backoff over five attempts; past that it raises. The restore script retries too, but a large restore can still hit limits.
- **Completed tasks** come from the activity endpoint, which Todoist caps by history window and plan. Free accounts get a short window, so old completions are not in the backup because they are not in the API.
- **No incremental mode.** Every run is a full account read.
- **No encryption.** The ZIP is plaintext JSON of everything in your account. Treat it accordingly.
- **Attachments are not downloaded**, only the comment metadata that references them.
