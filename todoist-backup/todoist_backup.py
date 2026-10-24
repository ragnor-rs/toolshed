#!/usr/bin/env python3
"""Full backup of Todoist account via API. Outputs a ZIP with all data, restore script, and README."""

import argparse
import glob
import io
import json
import os
import sys
import time
import zipfile
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
console = Console()

BASE_URL = "https://api.todoist.com/api/v1"


class TodoistBackup:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}
        self.client = httpx.Client(headers=self.headers, timeout=30.0)

    def close(self):
        self.client.close()

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        """GET with retry on 429."""
        url = f"{BASE_URL}{path}"
        delay = 1.0
        for _ in range(5):
            resp = self.client.get(url, params=params)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", delay))
                console.print(f"  [yellow]Rate limited, waiting {retry_after}s...[/]")
                time.sleep(retry_after)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Rate limited too many times: {url}")

    def _get_paginated(self, path: str, params: dict | None = None) -> list:
        """Fetch all pages from a paginated endpoint (cursor-based)."""
        params = dict(params or {})
        all_results = []
        while True:
            data = self._get(path, params)
            if isinstance(data, list):
                all_results.extend(data)
                break
            results = data.get("results", [])
            all_results.extend(results)
            cursor = data.get("next_cursor")
            if not cursor:
                break
            params["cursor"] = cursor
        return all_results

    def _sync(self, resource_types: list[str]) -> dict:
        """Fetch via Sync endpoint."""
        delay = 1.0
        for _ in range(5):
            resp = self.client.post(
                f"{BASE_URL}/sync",
                json={"sync_token": "*", "resource_types": resource_types},
            )
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", delay))
                time.sleep(retry_after)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("Rate limited too many times on sync")

    def fetch_all_sync(self) -> dict:
        """Single sync call to get all resource types."""
        console.print("  Syncing all data...")
        return self._sync(["all"])

    def fetch_completed_activities(self) -> list:
        """Fetch completed task events from activity log (7 days on free plan)."""
        console.print("  Fetching completed task history...")
        return self._get_paginated("/activities", {"event_type": "completed"})

    def fetch_comments_for_tasks(self, items: list) -> dict:
        """Fetch comments for tasks that have note_count > 0."""
        tasks_with_notes = [t for t in items if t.get("note_count", 0) > 0]
        if not tasks_with_notes:
            console.print("  No task comments to fetch")
            return {}
        console.print(f"  Fetching comments for {len(tasks_with_notes)} tasks...")
        result = {}
        for i, task in enumerate(tasks_with_notes):
            task_id = task["id"]
            try:
                comments = self._get_paginated("/comments", {"task_id": task_id})
                if comments:
                    result[task_id] = comments
            except httpx.HTTPStatusError:
                pass
            if (i + 1) % 30 == 0:
                console.print(f"    ...{i + 1}/{len(tasks_with_notes)}")
        return result

    def fetch_comments_for_projects(self, projects: list) -> dict:
        """Fetch comments for each project."""
        console.print(f"  Fetching comments for {len(projects)} projects...")
        result = {}
        for project in projects:
            pid = project["id"]
            try:
                comments = self._get_paginated("/comments", {"project_id": pid})
                if comments:
                    result[pid] = comments
            except httpx.HTTPStatusError:
                pass
        return result

    def fetch_collaborators(self, projects: list) -> dict:
        """Fetch collaborators per shared project."""
        shared = [p for p in projects if p.get("is_shared")]
        if not shared:
            console.print("  No shared projects")
            return {}
        console.print(f"  Fetching collaborators for {len(shared)} shared projects...")
        result = {}
        for project in shared:
            pid = project["id"]
            try:
                collabs = self._get_paginated(f"/projects/{pid}/collaborators")
                if collabs:
                    result[pid] = collabs
            except httpx.HTTPStatusError:
                pass
        return result

    def fetch_archived_projects(self) -> list:
        """Fetch archived projects via REST."""
        console.print("  Fetching archived projects...")
        try:
            return self._get_paginated("/projects/archived")
        except httpx.HTTPStatusError:
            console.print("  [yellow]Archived projects not available[/]")
            return []


def load_previous_completed(output_dir: str) -> list:
    """Load completed task activities from the most recent backup ZIP."""
    pattern = os.path.join(output_dir, "todoist_backup_*.zip")
    zips = sorted(glob.glob(pattern))
    if not zips:
        return []
    latest = zips[-1]
    try:
        with zipfile.ZipFile(latest, "r") as zf:
            for name in zf.namelist():
                if name.endswith("completed_activities.json"):
                    return json.loads(zf.read(name))
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError):
        pass
    return []


def merge_completed(previous: list, new: list) -> list:
    """Merge completed activity entries, deduplicating by ID."""
    seen = {}
    for entry in previous:
        eid = entry.get("id")
        if eid is not None:
            seen[eid] = entry
    for entry in new:
        eid = entry.get("id")
        if eid is not None:
            seen[eid] = entry
    return sorted(seen.values(), key=lambda e: e.get("event_date", ""), reverse=True)


def generate_restore_script() -> str:
    return '''\
#!/usr/bin/env python3
"""Restore Todoist backup. Run from the extracted ZIP directory."""

import argparse
import json
import os
import sys
import time
from collections import defaultdict

try:
    import requests
except ImportError:
    print("Error: requests library required. Install with: pip install requests")
    sys.exit(1)

BASE_URL = "https://api.todoist.com/api/v1"


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else []
    with open(path) as f:
        return json.load(f)


def api_post(token, path, data, retries=5):
    for attempt in range(retries):
        resp = requests.post(
            f"{BASE_URL}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=data,
        )
        if resp.status_code == 429:
            retry = float(resp.headers.get("Retry-After", 2 ** attempt))
            print(f"  Rate limited, waiting {retry}s...")
            time.sleep(retry)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Rate limited too many times: {path}")


def topo_sort(items, id_field="id", parent_field="parent_id"):
    """Sort items so parents come before children at any depth."""
    by_id = {item[id_field]: item for item in items}
    children = defaultdict(list)
    roots = []
    for item in items:
        pid = item.get(parent_field)
        if pid and pid in by_id:
            children[pid].append(item)
        else:
            roots.append(item)
    result = []
    stack = list(roots)
    while stack:
        node = stack.pop(0)
        result.append(node)
        stack = children.get(node[id_field], []) + stack
    return result


def main():
    parser = argparse.ArgumentParser(description="Restore Todoist backup")
    parser.add_argument("--token", required=True, help="Todoist API token")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    args = parser.parse_args()

    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if not os.path.isdir(data_dir):
        print(f"Error: data/ directory not found at {data_dir}")
        sys.exit(1)

    id_map = {}  # old_id -> new_id

    # Labels
    labels = load_json(os.path.join(data_dir, "labels.json"))
    print(f"\\nRestoring {len(labels)} labels...")
    for label in labels:
        payload = {"name": label["name"]}
        if label.get("color"):
            payload["color"] = label["color"]
        if label.get("item_order") is not None:
            payload["order"] = label["item_order"]
        if args.dry_run:
            print(f"  Would create label: {label['name']}")
        else:
            result = api_post(args.token, "/labels", payload)
            id_map[label["id"]] = result["id"]
            print(f"  Created label: {label['name']}")
            time.sleep(0.3)

    # Projects — topologically sorted so parents come before children, skip Inbox
    projects = load_json(os.path.join(data_dir, "projects.json"))
    projects = topo_sort(projects)
    print(f"\\nRestoring {len(projects)} projects...")
    for project in projects:
        if project.get("inbox_project"):
            id_map[project["id"]] = project["id"]
            continue
        payload = {"name": project["name"]}
        if project.get("color"):
            payload["color"] = project["color"]
        if project.get("view_style"):
            payload["view_style"] = project["view_style"]
        if project.get("is_favorite"):
            payload["is_favorite"] = project["is_favorite"]
        parent_id = project.get("parent_id")
        if parent_id and parent_id in id_map:
            payload["parent_id"] = id_map[parent_id]
        if args.dry_run:
            print(f"  Would create project: {project['name']}")
        else:
            result = api_post(args.token, "/projects", payload)
            id_map[project["id"]] = result["id"]
            print(f"  Created project: {project['name']}")
            time.sleep(0.3)

    # Sections
    sections = load_json(os.path.join(data_dir, "sections.json"))
    print(f"\\nRestoring {len(sections)} sections...")
    for section in sections:
        project_id = id_map.get(section.get("project_id"), section.get("project_id"))
        payload = {"name": section["name"], "project_id": project_id}
        if section.get("section_order") is not None:
            payload["order"] = section["section_order"]
        if args.dry_run:
            print(f"  Would create section: {section['name']}")
        else:
            result = api_post(args.token, "/sections", payload)
            id_map[section["id"]] = result["id"]
            print(f"  Created section: {section['name']}")
            time.sleep(0.3)

    # Active tasks — topologically sorted for arbitrary nesting depth
    items = load_json(os.path.join(data_dir, "items.json"))
    active = [i for i in items if not i.get("checked") and not i.get("is_deleted")]
    active = topo_sort(active)
    print(f"\\nRestoring {len(active)} active tasks...")
    for task in active:
        project_id = id_map.get(task.get("project_id"), task.get("project_id"))
        payload = {"content": task["content"], "project_id": project_id}
        if task.get("description"):
            payload["description"] = task["description"]
        if task.get("priority"):
            payload["priority"] = task["priority"]
        if task.get("due"):
            due = task["due"]
            if due.get("string"):
                payload["due_string"] = due["string"]
            elif due.get("date"):
                payload["due_date"] = due["date"]
        if task.get("labels"):
            payload["labels"] = task["labels"]
        if task.get("child_order") is not None:
            payload["order"] = task["child_order"]
        section_id = task.get("section_id")
        if section_id and section_id in id_map:
            payload["section_id"] = id_map[section_id]
        parent_id = task.get("parent_id")
        if parent_id and parent_id in id_map:
            payload["parent_id"] = id_map[parent_id]
        if args.dry_run:
            print(f"  Would create task: {task['content'][:60]}")
        else:
            result = api_post(args.token, "/tasks", payload)
            id_map[task["id"]] = result["id"]
            print(f"  Created task: {task['content'][:60]}")
            time.sleep(0.3)

    # Task comments
    task_comments = load_json(os.path.join(data_dir, "comments_by_task.json"), default={})
    total = sum(len(v) for v in task_comments.values())
    print(f"\\nRestoring {total} task comments...")
    for old_task_id, comments in task_comments.items():
        new_task_id = id_map.get(old_task_id, old_task_id)
        for comment in comments:
            payload = {"task_id": new_task_id, "content": comment.get("content", "")}
            if args.dry_run:
                print(f"  Would create comment on task {old_task_id}")
            else:
                try:
                    api_post(args.token, "/comments", payload)
                    time.sleep(0.3)
                except Exception as e:
                    print(f"  Warning: failed to create comment: {e}")

    # Project comments
    proj_comments = load_json(os.path.join(data_dir, "comments_by_project.json"), default={})
    total = sum(len(v) for v in proj_comments.values())
    print(f"\\nRestoring {total} project comments...")
    for old_proj_id, comments in proj_comments.items():
        new_proj_id = id_map.get(old_proj_id, old_proj_id)
        for comment in comments:
            payload = {"project_id": new_proj_id, "content": comment.get("content", "")}
            if args.dry_run:
                print(f"  Would create comment on project {old_proj_id}")
            else:
                try:
                    api_post(args.token, "/comments", payload)
                    time.sleep(0.3)
                except Exception as e:
                    print(f"  Warning: failed to create comment: {e}")

    print("\\nDone!" if not args.dry_run else "\\nDry run complete. No changes made.")


if __name__ == "__main__":
    main()
'''


def generate_readme(backup_date: str, counts: dict) -> str:
    lines = [
        "# Todoist Backup",
        "",
        f"**Date:** {backup_date}",
        "",
        "## Contents",
        "",
        "| Data | Count |",
        "|------|-------|",
    ]
    for key, count in counts.items():
        lines.append(f"| {key} | {count} |")
    lines.extend([
        "",
        "## Files",
        "",
        "- `data/` — JSON files with all backed-up data",
        "- `restore.py` — Script to restore this backup to a Todoist account",
        "- `README.md` — This file",
        "",
        "## What Gets Restored",
        "",
        "- Labels (with colors and order)",
        "- Projects (with hierarchy, colors, favorites)",
        "- Sections (with order)",
        "- Active tasks (with full hierarchy, due dates, priorities, labels, descriptions, order)",
        "- Task and project comments",
        "",
        "## What Is Backed Up But NOT Restored",
        "",
        "- Completed tasks / activity history (archived for reference only)",
        "- Filters, view options, user settings",
        "- Notifications, day orders, productivity stats",
        "- Reminders (plan-dependent)",
        "",
        "## Restore Instructions",
        "",
        "1. Unzip this archive",
        "2. Install requests: `pip install requests`",
        "3. Get your API token from Todoist Settings > Integrations > Developer",
        "4. **Always dry run first:** `python restore.py --token YOUR_TOKEN --dry-run`",
        "5. Restore: `python restore.py --token YOUR_TOKEN`",
        "",
        "**Warning:** The restore script creates new items — running it on an account that",
        "already has data will produce duplicates. Use on a clean/new account.",
        "",
        "## Incremental Completed Tasks",
        "",
        "The free plan only exposes 7 days of completed task history.",
        "`completed_activities.json` accumulates history across backups —",
        "each run merges new completions with the previous backup's archive.",
        "Run weekly to avoid gaps.",
    ])
    return "\n".join(lines) + "\n"


def run_backup(output_dir: str):
    token = os.getenv("TODOIST_API_TOKEN")
    if not token:
        console.print("[red]Error: TODOIST_API_TOKEN not set in .env[/]")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    # Inside output_dir, not beside it — `--output-dir /backups` would otherwise try to
    # create /logs at the filesystem root, after the whole account has been read.
    log_dir = os.path.join(output_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")
    backup_date = now.isoformat()

    console.print(f"\n[bold]Todoist Backup[/] — {backup_date}\n")

    api = TodoistBackup(token)
    try:
        # 1. Single sync call gets bulk data
        sync_data = api.fetch_all_sync()

        items = sync_data.get("items", [])
        projects = sync_data.get("projects", [])
        sections = sync_data.get("sections", [])
        labels = sync_data.get("labels", [])
        filters = sync_data.get("filters", [])
        reminders = sync_data.get("reminders", [])
        notes = sync_data.get("notes", [])
        project_notes = sync_data.get("project_notes", [])
        completed_info = sync_data.get("completed_info", [])
        collaborators = sync_data.get("collaborators", [])
        user = sync_data.get("user", {})
        user_settings = sync_data.get("user_settings", {})
        user_plan_limits = sync_data.get("user_plan_limits", {})
        stats = sync_data.get("stats", {})
        live_notifications = sync_data.get("live_notifications", [])
        day_orders = sync_data.get("day_orders", {})
        view_options = sync_data.get("view_options", [])

        # 2. Fetch additional data not in sync
        archived_projects = api.fetch_archived_projects()
        completed_activities = api.fetch_completed_activities()

        # 3. Fetch comments via REST (sync notes may be empty for API-created comments)
        comments_by_task = api.fetch_comments_for_tasks(items)
        comments_by_project = api.fetch_comments_for_projects(projects)

        # 4. Fetch collaborators for shared projects
        rest_collaborators = api.fetch_collaborators(projects)
    finally:
        api.close()

    # Merge completed activities with previous backup
    previous_completed = load_previous_completed(output_dir)
    all_completed = merge_completed(previous_completed, completed_activities)

    console.print(f"\n  [dim]Completed activities: {len(previous_completed)} previous + "
                  f"{len(completed_activities)} new = {len(all_completed)} total[/]")

    # Build ZIP
    zip_name = f"todoist_backup_{timestamp}.zip"
    zip_path = os.path.join(output_dir, zip_name)

    active_tasks = [i for i in items if not i.get("checked") and not i.get("is_deleted")]

    counts = {
        "User": 1,
        "Projects": len(projects),
        "Projects (archived)": len(archived_projects),
        "Sections": len(sections),
        "Tasks (active)": len(active_tasks),
        "Tasks (total)": len(items),
        "Completed activities": len(all_completed),
        "Labels": len(labels),
        "Filters": len(filters),
        "Reminders": len(reminders),
        "Task comments": sum(len(v) for v in comments_by_task.values()),
        "Project comments": sum(len(v) for v in comments_by_project.values()),
        "Collaborators": len(collaborators) or sum(len(v) for v in rest_collaborators.values()),
    }

    data_files = {
        "data/user.json": user,
        "data/user_settings.json": user_settings,
        "data/user_plan_limits.json": user_plan_limits,
        "data/stats.json": stats,
        "data/projects.json": projects,
        "data/projects_archived.json": archived_projects,
        "data/sections.json": sections,
        "data/items.json": items,
        "data/completed_activities.json": all_completed,
        "data/completed_info.json": completed_info,
        "data/labels.json": labels,
        "data/filters.json": filters,
        "data/reminders.json": reminders,
        "data/notes.json": notes,
        "data/project_notes.json": project_notes,
        "data/comments_by_task.json": comments_by_task,
        "data/comments_by_project.json": comments_by_project,
        "data/collaborators.json": collaborators or rest_collaborators,
        "data/live_notifications.json": live_notifications,
        "data/day_orders.json": day_orders,
        "data/view_options.json": view_options,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in data_files.items():
            zf.writestr(name, json.dumps(data, indent=2, ensure_ascii=False))
        zf.writestr("restore.py", generate_restore_script())
        zf.writestr("README.md", generate_readme(backup_date, counts))

    with open(zip_path, "wb") as f:
        f.write(buf.getvalue())

    # Summary table
    table = Table(title="Backup Summary")
    table.add_column("Data", style="cyan")
    table.add_column("Count", style="green", justify="right")
    for key, count in counts.items():
        table.add_row(key, str(count))
    console.print()
    console.print(table)

    zip_size = os.path.getsize(zip_path)
    console.print(f"\n[bold green]Saved:[/] {zip_path} ({zip_size:,} bytes)\n")

    # Log
    log_entry = {
        "timestamp": backup_date,
        "output": zip_path,
        "size_bytes": zip_size,
        "counts": counts,
    }
    log_file = os.path.join(log_dir, "todoist_backup.log")
    with open(log_file, "a") as f:
        f.write(json.dumps(log_entry) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Full Todoist account backup")
    parser.add_argument(
        "--output-dir",
        default=os.path.join("data", "todoist_backups"),
        help="Output directory (default: data/todoist_backups/)",
    )
    args = parser.parse_args()
    run_backup(args.output_dir)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
        sys.exit(130)
    except httpx.HTTPStatusError as e:
        # A traceback tells the user nothing they can act on; the status does.
        if e.response.status_code == 401:
            console.print("[red]Error: Todoist rejected the token (401).[/] "
                          "Check TODOIST_API_TOKEN — Settings > Integrations > Developer.")
        else:
            console.print(f"[red]Error: Todoist API returned "
                          f"{e.response.status_code} for {e.request.url.path}[/]")
        sys.exit(1)
    except httpx.HTTPError as e:
        console.print(f"[red]Error: could not reach the Todoist API:[/] {e}")
        sys.exit(1)
