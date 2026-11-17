#!/usr/bin/env python3
"""session-vault: turn Claude Code session transcripts into an Obsidian-style vault.

Every session is a note. Notes connect the way Obsidian notes do:

  link      a session names another one as [[Title]] (in a prompt or a reply),
            reads it through the vault's read tool, or was linked by hand
  fork      a session was forked from, or started by, another one
  files     two sessions edited the same file (a weak, dashed connection)
  mention   a session says another one's title in plain text without linking
            it: an "unlinked mention", listed but not drawn

The transcripts are the source of truth and are never written. Everything this
script makes lives in the vault folder (default ~/.claude/session-vault):

  sessions/*.md      one Markdown note per session, with [[wikilinks]]
  Session Vault.md   the home note
  graph.json         nodes and edges, read by the pane
  .vault/            parse caches, hand-made links, the inbox, a lock

A session can also leave a task for another one. A running session is messaged
by the mod itself; for one that is not running the task waits in the inbox
(.vault/inbox.json) and is handed over when that session next starts.

Subcommands print one JSON object on stdout (ASCII only).
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CACHE_VERSION = 3
MAX_MESSAGE_CHARS = 6000
STALE_LOCK_SECONDS = 120

HOME = Path.home()
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
PROJECTS = CLAUDE_DIR / "projects"
VAULT = Path(os.environ.get("SESSION_VAULT_DIR") or CLAUDE_DIR / "session-vault")
META = VAULT / ".vault"
CACHE = META / "cache"
NOTES = VAULT / "sessions"
GRAPH = VAULT / "graph.json"
LINKS = META / "links.json"
INBOX = META / "inbox.json"
LIVE = CLAUDE_DIR / "sessions"
MAX_TASK_CHARS = 8000
TASK_DAYS = 30
LOCK = META / "index.lock"
HOME_NOTE = VAULT / "Session Vault.md"
NOTE_MARK = "vault: session-vault"

EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
SKIP_TYPES = (
    b'{"type":"api-request',
    b'{"type":"queue-operation"',
    b'{"type":"atis-latch"',
    b'{"type":"bridge-session"',
    b'{"type":"file-history-snapshot"',
    b'{"type":"tools-used"',
    b'{"type":"mode"',
    b'{"type":"last-prompt"',
    b'{"type":"agent-name"',
)
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.S)
COMMAND = re.compile(
    r"<command-name>\s*(/?[^<\s]+)\s*</command-name>.*?(?:<command-args>(.*?)</command-args>)?\s*$",
    re.S,
)
MACHINE_PREFIX = re.compile(
    r"<(local-command|task-notification|command-message|bash-|user-prompt-submit-hook|side-sessions-event|ci-monitor-event)"
)
WIKILINK = re.compile(r"\[\[([^\[\]\n]{2,160})\]\]")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


# ---------------------------------------------------------------- utilities


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=True))
    sys.stdout.write("\n")


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_text(path, text):
    """Atomic write; skipped when the file already holds the text."""
    path = Path(path)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except (OSError, ValueError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp%d" % os.getpid())
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temp, path)
    return True


def write_json(path, value):
    return write_text(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def parse_time(stamp):
    """ISO timestamp -> epoch milliseconds, or 0."""
    if not stamp or not isinstance(stamp, str):
        return 0
    try:
        return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def day(ms):
    if not ms:
        return "?"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def clip(text, limit):
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def one_line(text, limit):
    return clip(re.sub(r"\s+", " ", text), limit)


def short_id(node_id):
    found = UUID.search(node_id)
    return (found.group(0) if found else node_id)[:8]


def project_name(cwd):
    if not cwd:
        return "unknown"
    norm = cwd.replace("\\", "/").rstrip("/")
    if "/scratch-workspaces/" in norm:
        return "No folder"
    name = norm.rsplit("/", 1)[-1]
    return name or norm or "unknown"


def tag_of(project):
    return re.sub(r"[^A-Za-z0-9_\-/]+", "-", project).strip("-") or "unknown"


def file_safe(title):
    safe = re.sub(r'[\\/:*?"<>|#^\[\]\r\n\t]+', " ", title)
    safe = re.sub(r"\s+", " ", safe).strip(" .")
    return safe[:80].rstrip(" .") or "Session"


# ------------------------------------------------------ parsing transcripts


def clean_prompt(text):
    """What the person typed, without the harness's wrapping; '' for machine rows."""
    text = SYSTEM_REMINDER.sub("", text).strip()
    if not text:
        return ""
    if text.startswith("<command-name>") or "<command-name>" in text[:200]:
        found = COMMAND.search(text)
        if found:
            name = found.group(1)
            name = name if name.startswith("/") else "/" + name
            return (name + " " + (found.group(2) or "").strip()).strip()
        return ""
    if text.startswith("<") and MACHINE_PREFIX.match(text):
        return ""
    if text.startswith("[Request interrupted"):
        return ""
    return text


def new_record(cli):
    return {
        "v": CACHE_VERSION,
        "cli": cli,
        "size": 0,
        "mtime": 0,
        "offset": 0,
        "cwd": "",
        "entry": "",
        "title": "",
        "parents": [],
        "turns": [],
        "files": {},
        "reads": [],
    }


def user_text(row):
    if row.get("isMeta") or row.get("isSidechain"):
        return ""
    origin = row.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return ""
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return clean_prompt(content)
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            return ""
        if block.get("type") == "text":
            parts.append(block.get("text") or "")
    return clean_prompt("\n".join(parts))


def take_row(record, row):
    kind = row.get("type")
    cli = record["cli"]
    if kind == "custom-title":
        if row.get("sessionId") == cli and row.get("customTitle"):
            record["title"] = str(row["customTitle"])
        return
    if kind in ("ai-title", "summary"):
        title = row.get("aiTitle") or row.get("title") or row.get("summary")
        if title and not record["title"] and row.get("sessionId", cli) == cli:
            record["title"] = str(title)
        return
    if kind == "relocated":
        if row.get("sessionId") == cli and row.get("relocatedCwd"):
            record["cwd"] = row["relocatedCwd"]
        return
    if kind not in ("user", "assistant"):
        return
    session = row.get("sessionId")
    if session and session != cli:
        if session not in record["parents"]:
            record["parents"].append(session)
        return
    if row.get("isSidechain"):
        return
    if row.get("cwd"):
        record["cwd"] = row["cwd"]
    if row.get("entrypoint"):
        record["entry"] = row["entrypoint"]
    stamp = parse_time(row.get("timestamp"))
    uid = str(row.get("uuid") or "")[:8]
    turns = record["turns"]
    if kind == "user":
        text = user_text(row)
        if text:
            turns.append({"r": "u", "t": stamp, "u": uid, "x": clip(text, MAX_MESSAGE_CHARS)})
        return
    content = (row.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = (block.get("text") or "").strip()
            if not text:
                continue
            last = turns[-1] if turns else None
            if last and last["r"] == "a":
                if len(last["x"]) < MAX_MESSAGE_CHARS:
                    last["x"] = clip(last["x"] + "\n\n" + text, MAX_MESSAGE_CHARS)
                last["t"] = stamp or last["t"]
            else:
                turns.append({"r": "a", "t": stamp, "u": uid, "x": clip(text, MAX_MESSAGE_CHARS)})
        elif block.get("type") == "tool_use":
            name = str(block.get("name") or "")
            data = block.get("input") or {}
            if not isinstance(data, dict):
                continue
            if name in EDIT_TOOLS:
                path = data.get("file_path") or data.get("notebook_path")
                if isinstance(path, str) and path:
                    record["files"][path] = record["files"].get(path, 0) + 1
            elif name.endswith("session-vault__read") or name.endswith("session-vault__link"):
                ref = data.get("session") or data.get("to")
                if isinstance(ref, str) and ref and ref not in record["reads"]:
                    record["reads"].append(ref)


def parse_into(record, path, size):
    """Read complete lines from record['offset'] on; cheap prefix tests skip the bulk."""
    with open(path, "rb") as handle:
        handle.seek(record["offset"])
        position = record["offset"]
        for raw in handle:
            if not raw.endswith(b"\n"):
                break  # a line still being written: next time
            position += len(raw)
            if raw.startswith(SKIP_TYPES):
                continue
            head = raw[:700]
            if b'"attachment":{' in head:
                continue
            if b'"tool_result"' in head and b'"toolUseResult"' in raw[-4000:]:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict):
                take_row(record, row)
        record["offset"] = position
    record["size"] = size


def load_record(path, stat):
    """The cached parse of one transcript, brought up to date."""
    cli = path.stem
    cache_path = CACHE / (cli + ".json")
    record = read_json(cache_path)
    fresh = (
        not isinstance(record, dict)
        or record.get("v") != CACHE_VERSION
        or record.get("cli") != cli
        or stat.st_size < record.get("offset", 0)
    )
    if fresh:
        record = new_record(cli)
    elif record.get("size") == stat.st_size and record.get("mtime") == int(stat.st_mtime):
        return record, False
    try:
        parse_into(record, path, stat.st_size)
    except OSError:
        return record, False
    record["mtime"] = int(stat.st_mtime)
    write_json(cache_path, record)
    return record, True


# ------------------------------------------------------ the desktop's sessions


def desktop_roots():
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Claude" / "claude-code-sessions")
    roots.append(HOME / "AppData" / "Roaming" / "Claude" / "claude-code-sessions")
    roots.append(HOME / "Library" / "Application Support" / "Claude" / "claude-code-sessions")
    roots.append(HOME / ".config" / "Claude" / "claude-code-sessions")
    seen = []
    for root in roots:
        if root not in seen and root.is_dir():
            seen.append(root)
    return seen


def desktop_sessions():
    """The desktop app's own record of each session: title, folder, transcripts, parent."""
    found = {}
    for root in desktop_roots():
        for path in root.glob("*/*/local_*.json"):
            meta = read_json(path)
            if not isinstance(meta, dict) or not meta.get("sessionId"):
                continue
            spawned = meta.get("spawnedFrom")
            parent = meta.get("forkedFromSessionId")
            parent_kind = "fork"
            if not parent and isinstance(spawned, dict):
                parent = spawned.get("sessionId") or spawned.get("parentSessionId")
                parent_kind = "spawn"
            ids = [meta.get("cliSessionId")] + list(meta.get("priorCliSessionIds") or [])
            found[meta["sessionId"]] = {
                "id": meta["sessionId"],
                "title": meta.get("title") or "",
                "cwd": meta.get("cwd") or meta.get("originCwd") or "",
                "created": int(meta.get("createdAt") or 0),
                "updated": int(meta.get("lastActivityAt") or 0),
                "archived": bool(meta.get("isArchived")),
                "cli": [one for one in ids if isinstance(one, str) and one],
                "parent": parent if isinstance(parent, str) else None,
                "parentKind": parent_kind,
            }
    return found


# ------------------------------------------------------------- the index


def scan_transcripts():
    records = {}
    parsed = 0
    if not PROJECTS.is_dir():
        return records, parsed
    for folder in PROJECTS.iterdir():
        if not folder.is_dir():
            continue
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if not entry.name.endswith(".jsonl") or not entry.is_file():
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            record, changed = load_record(Path(entry.path), stat)
            parsed += 1 if changed else 0
            records[record["cli"]] = record
    return records, parsed


def build_nodes(records, desktop):
    """Sessions as the person knows them: a desktop session joins its transcripts."""
    nodes = {}
    owner = {}
    for meta in desktop.values():
        for cli in meta["cli"]:
            owner.setdefault(cli, meta["id"])
    for meta in desktop.values():
        mine = [records[cli] for cli in meta["cli"] if cli in records]
        node = join_records(meta["id"], mine)
        if not node["turns"]:
            continue
        node["title"] = meta["title"] or node["title"]
        node["cwd"] = meta["cwd"] or node["cwd"]
        node["created"] = node["created"] or meta["created"]
        node["updated"] = max(node["updated"], 0) or meta["updated"]
        node["archived"] = meta["archived"]
        node["cli"] = meta["cli"]
        if meta["parent"]:
            node["parentRefs"].insert(0, (meta["parent"], meta["parentKind"]))
        nodes[node["id"]] = node
    has_desktop = bool(desktop)
    for cli, record in records.items():
        if cli in owner:
            continue
        if has_desktop and record.get("entry") == "claude-desktop":
            continue  # a desktop session the person deleted there
        node = join_records(cli, [record])
        if not any(turn["r"] == "u" for turn in node["turns"]):
            continue
        node["cli"] = [cli]
        nodes[cli] = node
        owner[cli] = cli
    return nodes, owner


def join_records(node_id, records):
    turns = []
    seen = set()
    files = {}
    reads = []
    parents = []
    title = ""
    cwd = ""
    for record in sorted(records, key=lambda r: (r["turns"][0]["t"] if r["turns"] else 0)):
        for turn in record["turns"]:
            key = (turn["r"], turn.get("u"), turn["t"])
            if turn.get("u") and key in seen:
                continue
            seen.add(key)
            turns.append(turn)
        for path, count in record["files"].items():
            files[path] = files.get(path, 0) + count
        for ref in record["reads"]:
            if ref not in reads:
                reads.append(ref)
        for parent in record["parents"]:
            if parent not in parents:
                parents.append(parent)
        title = record["title"] or title
        cwd = record["cwd"] or cwd
    turns.sort(key=lambda turn: turn["t"])
    prompts = [turn for turn in turns if turn["r"] == "u"]
    stamps = [turn["t"] for turn in turns if turn["t"]]
    return {
        "id": node_id,
        "title": title,
        "cwd": cwd,
        "turns": turns,
        "prompts": len(prompts),
        "first": prompts[0]["x"] if prompts else "",
        "last": prompts[-1]["x"] if prompts else "",
        "reply": next((turn["x"] for turn in reversed(turns) if turn["r"] == "a"), ""),
        "lastRole": turns[-1]["r"] if turns else "",
        "files": files,
        "reads": reads,
        "parentRefs": [(parent, "fork") for parent in parents],
        "created": min(stamps) if stamps else 0,
        "updated": max(stamps) if stamps else 0,
        "archived": False,
        "cli": [],
    }


def make_resolver(nodes, owner):
    """A session by what a person would call it: its id, a prefix of it, its title."""
    by_title = {}
    for node in nodes.values():
        by_title.setdefault(node["title"].strip().lower(), []).append(node)
    for same in by_title.values():
        same.sort(key=lambda node: (node["title"].startswith("btw:"), -node["updated"]))

    def resolve(ref, loose=True):
        ref = (ref or "").strip()
        if not ref:
            return None
        ref = ref.split("|", 1)[0].split("#", 1)[0].strip()
        if ref in nodes:
            return nodes[ref]
        if ref in owner and owner[ref] in nodes:
            return nodes[owner[ref]]
        tail = re.search(r"\(([0-9a-f]{6,8})\)\s*$", ref)
        if tail:
            for node in nodes.values():
                if short_id(node["id"]) .startswith(tail.group(1)):
                    return node
        low = ref.lower()
        if low in by_title:
            return by_title[low][0]
        bare = low[6:] if low.startswith("local_") else low
        if re.fullmatch(r"[0-9a-f\-]{6,36}", bare):
            hits = [n for n in nodes.values() if short_id(n["id"]).startswith(bare[:8]) or n["id"].endswith(bare) or bare in n["id"]]
            hits += [nodes[owner[c]] for c in owner if c.startswith(bare) and owner[c] in nodes]
            if hits:
                return hits[0]
        if not loose or len(low) < 4:
            return None
        hits = [node for title, same in by_title.items() if low in title for node in same[:1]]
        if len(hits) == 1:
            return hits[0]
        starts = [node for node in hits if node["title"].lower().startswith(low)]
        if len(starts) == 1:
            return starts[0]
        return None

    return resolve


def norm_path(path):
    return path.replace("\\", "/").lower()


def build_edges(nodes, owner, resolve):
    """Directed edges a -> b: a links to b. One edge per pair and kind."""
    edges = {}

    def add(a, b, kind, weight=1):
        if a == b or a not in nodes or b not in nodes:
            return
        key = (a, b, kind)
        edges[key] = edges.get(key, 0) + weight

    hand = read_json(LINKS, {}) or {}
    for a, targets in hand.items():
        source = resolve(a, loose=False)
        if not source or not isinstance(targets, dict):
            continue
        for b in targets:
            target = resolve(b, loose=False)
            if target:
                add(source["id"], target["id"], "link")

    for node in nodes.values():
        for ref, kind in node["parentRefs"]:
            parent = owner.get(ref, ref)
            add(node["id"], parent, kind)
            if parent in nodes and parent != node["id"]:
                break  # the nearest parent that still exists
        for ref in node["reads"]:
            target = resolve(ref)
            if target:
                add(node["id"], target["id"], "link")
        for turn in node["turns"]:
            if "[[" not in turn["x"]:
                continue
            for found in WIKILINK.findall(turn["x"]):
                target = resolve(found, loose=False)
                if target:
                    add(node["id"], target["id"], "link")

    linked = {(a, b) for (a, b, _kind) in edges} | {(b, a) for (a, b, _kind) in edges}

    by_file = {}
    for node in nodes.values():
        for path in node["files"]:
            by_file.setdefault(norm_path(path), set()).add(node["id"])
    shared = {}
    for path, ids in by_file.items():
        if len(ids) < 2 or len(ids) > 6:
            continue
        ordered = sorted(ids)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                if (a, b) not in linked:
                    shared[(a, b)] = shared.get((a, b), 0) + 1
    kept = {}
    for (a, b), weight in sorted(shared.items(), key=lambda item: -item[1]):
        if kept.get(a, 0) >= 3 or kept.get(b, 0) >= 3:
            continue
        kept[a] = kept.get(a, 0) + 1
        kept[b] = kept.get(b, 0) + 1
        newer, older = (a, b) if nodes[a]["updated"] >= nodes[b]["updated"] else (b, a)
        edges[(newer, older, "files")] = weight

    mentions = []
    texts = {node["id"]: "\n".join(turn["x"] for turn in node["turns"]).lower() for node in nodes.values()}
    for target in nodes.values():
        title = target["title"].strip().lower()
        if len(title) < 12 or " " not in title or title.startswith("btw:") or title == "new session":
            continue
        for node in nodes.values():
            if node["id"] == target["id"] or (node["id"], target["id"]) in linked:
                continue
            if title in texts[node["id"]]:
                mentions.append([node["id"], target["id"]])
    return edges, mentions


def note_name(node):
    return "%s (%s)" % (file_safe(node["title"]), short_id(node["id"]))


def wikilink(node):
    return "[[%s|%s]]" % (note_name(node), node["title"].replace("|", "/").replace("]", ")"))


def render_note(node, nodes, outgoing, incoming, mentioned_by):
    project = project_name(node["cwd"])
    lines = [
        "---",
        NOTE_MARK,
        "id: %s" % node["id"],
        "title: %s" % json.dumps(node["title"], ensure_ascii=False),
        "project: %s" % json.dumps(project, ensure_ascii=False),
        "cwd: %s" % json.dumps(node["cwd"], ensure_ascii=False),
        "created: %s" % day(node["created"]),
        "updated: %s" % day(node["updated"]),
        "prompts: %d" % node["prompts"],
        "tags: [session, project/%s]" % tag_of(project),
        "aliases: [%s]" % json.dumps(node["title"], ensure_ascii=False),
        "---",
        "",
        "# %s" % node["title"],
        "",
        "Project **%s** · %s → %s · %d prompts" % (project, day(node["created"]), day(node["updated"]), node["prompts"]),
        "",
    ]
    if node["first"]:
        lines += ["> " + one_line(node["first"], 400), ""]
    words = {"link": "links to", "fork": "forked from", "spawn": "started by", "files": "edited the same files as"}
    if outgoing:
        lines.append("## Links")
        for (target, kind) in outgoing:
            lines.append("- %s — %s" % (wikilink(nodes[target]), words[kind]))
        lines.append("")
    if incoming:
        lines.append("## Backlinks")
        back = {"link": "links here", "fork": "forked from this", "spawn": "started from this", "files": "edited the same files"}
        for (source, kind) in incoming:
            lines.append("- %s — %s" % (wikilink(nodes[source]), back[kind]))
        lines.append("")
    if mentioned_by:
        lines.append("## Unlinked mentions")
        for source in mentioned_by:
            lines.append("- %s mentions this session by name" % nodes[source]["title"])
        lines.append("")
    prompts = [turn for turn in node["turns"] if turn["r"] == "u"]
    if prompts:
        lines.append("## Prompts")
        for index, turn in enumerate(prompts[:60], 1):
            lines.append("%d. `%s` %s" % (index, day(turn["t"]), one_line(turn["x"], 300)))
        if len(prompts) > 60:
            lines.append("… and %d more" % (len(prompts) - 60))
        lines.append("")
    replies = [turn for turn in node["turns"] if turn["r"] == "a"]
    if replies:
        lines += ["## Last reply", "", clip(replies[-1]["x"], 1500), ""]
    if node["files"]:
        lines.append("## Files edited")
        ranked = sorted(node["files"].items(), key=lambda item: -item[1])
        for path, count in ranked[:25]:
            lines.append("- `%s` ×%d" % (path, count))
        lines.append("")
    return "\n".join(lines)


def render_home(nodes, edge_count):
    lines = [
        "---",
        NOTE_MARK,
        "tags: [session-vault]",
        "---",
        "",
        "# Session Vault",
        "",
        "%d sessions · %d connections. Written by the session-vault mod; open this folder in Obsidian to browse it there." % (len(nodes), edge_count),
        "",
    ]
    groups = {}
    for node in nodes.values():
        groups.setdefault(project_name(node["cwd"]), []).append(node)
    for project in sorted(groups, key=lambda name: -max(n["updated"] for n in groups[name])):
        lines.append("## %s" % project)
        for node in sorted(groups[project], key=lambda n: -n["updated"]):
            lines.append("- %s — %s" % (wikilink(node), day(node["updated"])))
        lines.append("")
    return "\n".join(lines)


def acquire_lock():
    META.mkdir(parents=True, exist_ok=True)
    try:
        age = time.time() - LOCK.stat().st_mtime
        if age < STALE_LOCK_SECONDS:
            return False
        LOCK.unlink()
    except OSError:
        pass
    try:
        handle = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    os.write(handle, str(os.getpid()).encode())
    os.close(handle)
    return True


def live_sessions():
    """Sessions with a process now, as Claude Code records them: {cli id: host id}."""
    live = {}
    if not LIVE.is_dir():
        return live
    for path in LIVE.glob("*.json"):
        record = read_json(path)
        if isinstance(record, dict) and isinstance(record.get("sessionId"), str):
            live[record["sessionId"]] = str(record.get("hostSessionId") or "")
    return live


def live_cli(node, live):
    """The transcript id of the node's running process, or ''."""
    for cli, host in live.items():
        if cli in node.get("cli", []) or cli == node["id"] or (host and host == node["id"]):
            return cli
    return ""


def gather():
    """Transcripts -> nodes, edges, mentions. Writes only the parse caches."""
    records, parsed = scan_transcripts()
    nodes, owner = build_nodes(records, desktop_sessions())
    for node in nodes.values():
        if not node["title"].strip():
            node["title"] = one_line(node["first"], 60) or "Untitled session"
    resolve = make_resolver(nodes, owner)
    edges, mentions = build_edges(nodes, owner, resolve)
    return nodes, owner, resolve, edges, mentions, parsed


def command_index(args):
    started = time.time()
    if not acquire_lock():
        emit({"ok": True, "busy": True, "vault": str(VAULT)})
        return
    try:
        nodes, _owner, _resolve, edges, mentions, parsed = gather()
        outgoing = {}
        incoming = {}
        for (a, b, kind) in sorted(edges, key=lambda key: ("link", "fork", "spawn", "files").index(key[2])):
            outgoing.setdefault(a, []).append((b, kind))
            incoming.setdefault(b, []).append((a, kind))
        mentioned_by = {}
        for a, b in mentions:
            mentioned_by.setdefault(b, []).append(a)

        NOTES.mkdir(parents=True, exist_ok=True)
        wanted = set()
        for node in nodes.values():
            name = note_name(node) + ".md"
            wanted.add(name)
            write_text(
                NOTES / name,
                render_note(node, nodes, outgoing.get(node["id"], []), incoming.get(node["id"], []), mentioned_by.get(node["id"], [])),
            )
        for path in NOTES.glob("*.md"):
            if path.name in wanted:
                continue
            try:
                head = path.read_text(encoding="utf-8")[:200]
            except (OSError, ValueError):
                continue
            if NOTE_MARK in head:
                path.unlink()
        write_text(HOME_NOTE, render_home(nodes, len(edges)))

        live = live_sessions()
        graph = {
            "v": 1,
            "generatedAt": int(time.time() * 1000),
            "vault": str(VAULT),
            "nodes": [
                {
                    "id": node["id"],
                    "short": short_id(node["id"]),
                    "title": node["title"],
                    "project": project_name(node["cwd"]),
                    "cwd": node["cwd"],
                    "created": node["created"],
                    "updated": node["updated"],
                    "prompts": node["prompts"],
                    "first": one_line(node["first"], 220),
                    "last": one_line(node["last"], 220),
                    "reply": one_line(node["reply"], 420),
                    "lastRole": node["lastRole"],
                    "note": note_name(node),
                    "cli": node["cli"],
                    "archived": node["archived"],
                    "live": live_cli(node, live) != "",
                }
                for node in sorted(nodes.values(), key=lambda n: -n["updated"])
            ],
            "edges": [{"a": a, "b": b, "k": kind, "w": weight} for (a, b, kind), weight in edges.items()],
            "mentions": mentions,
        }
        write_json(GRAPH, graph)
        emit(
            {
                "ok": True,
                "vault": str(VAULT),
                "sessions": len(nodes),
                "edges": len(edges),
                "parsed": parsed,
                "ms": int((time.time() - started) * 1000),
            }
        )
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


# ----------------------------------------------------------- asking the vault


def load_vault():
    """The graph as last indexed, with each session's turns from the parse caches."""
    graph = read_json(GRAPH)
    if not isinstance(graph, dict):
        return None
    nodes = {}
    owner = {}
    for meta in graph.get("nodes", []):
        node = dict(meta)
        node["turns"] = None
        nodes[node["id"]] = node
        for cli in node.get("cli", []):
            owner[cli] = node["id"]
    return graph, nodes, owner


def turns_of(node):
    if node.get("turns") is None:
        records = [read_json(CACHE / (cli + ".json")) for cli in node.get("cli", [])]
        records = [r for r in records if isinstance(r, dict) and r.get("v") == CACHE_VERSION]
        node["turns"] = join_records(node["id"], records)["turns"] if records else []
    return node["turns"]


def neighbours(graph, node_id):
    out = [(e["b"], e["k"]) for e in graph.get("edges", []) if e["a"] == node_id]
    back = [(e["a"], e["k"]) for e in graph.get("edges", []) if e["b"] == node_id]
    return out, back


def describe(node):
    return '"%s" [%s] project %s, %s -> %s, %d prompts' % (
        node["title"],
        node["id"],
        node["project"],
        day(node["created"]),
        day(node["updated"]),
        node["prompts"],
    )


def no_vault():
    emit({"ok": False, "text": "The vault has not been indexed yet. Run /vault reindex and try again."})


def command_list(args):
    loaded = load_vault()
    if not loaded:
        return no_vault()
    graph, nodes, _owner = loaded
    rows = list(nodes.values())
    if args.project:
        want = args.project.lower()
        rows = [node for node in rows if want in node["project"].lower() or want in node["cwd"].lower()]
    rows.sort(key=lambda node: -node["updated"])
    total = len(rows)
    rows = rows[: args.limit]
    lines = ["%d sessions in the vault%s; newest first." % (total, (" matching project '%s'" % args.project) if args.project else "")]
    for node in rows:
        out, back = neighbours(graph, node["id"])
        lines.append("- %s; %d links out, %d in" % (describe(node), len(out), len(back)))
        if node["first"]:
            lines.append("    first prompt: %s" % one_line(node["first"], 160))
    if total > len(rows):
        lines.append("... %d more; raise limit or narrow by project." % (total - len(rows)))
    emit({"ok": True, "text": "\n".join(lines)})


def snippet(text, low_text, term, width=160):
    at = low_text.find(term)
    if at < 0:
        return ""
    start = max(0, at - width // 2)
    end = min(len(text), at + len(term) + width)
    return ("…" if start else "") + re.sub(r"\s+", " ", text[start:end]).strip() + ("…" if end < len(text) else "")


def command_search(args):
    loaded = load_vault()
    if not loaded:
        return no_vault()
    graph, nodes, _owner = loaded
    query = args.query.strip()
    phrase = query.lower()
    terms = [term for term in re.findall(r"[\w\-\.]{2,}", phrase) if term not in ("the", "and", "for", "what", "did", "how", "was", "with", "about", "my", "in", "of", "to", "is", "session", "sessions")]
    if not terms:
        terms = [phrase] if phrase else []
    if not terms:
        return emit({"ok": False, "text": "Give a query: words to look for in my sessions."})
    results = []
    for node in nodes.values():
        if args.project and args.project.lower() not in (node["project"] + " " + node["cwd"]).lower():
            continue
        if args.exclude and node["id"] == args.exclude:
            continue
        score = 0.0
        title = node["title"].lower()
        for term in terms:
            score += 8 * title.count(term) + 4 * node["project"].lower().count(term)
        hits = []
        matched = set(term for term in terms if term in title or term in node["project"].lower())
        for index, turn in enumerate(turns_of(node)):
            low = turn["x"].lower()
            weight = 2.0 if turn["r"] == "u" else 1.0
            local = 0.0
            if len(terms) > 1 and phrase in low:
                local += 10 * weight
            for term in terms:
                count = low.count(term)
                if count:
                    matched.add(term)
                    local += weight * min(count, 5)
            if local:
                score += local
                hits.append((local, index, turn, low))
        if not score:
            continue
        score *= (len(matched) / float(len(terms))) ** 2
        if not score:
            continue
        hits.sort(key=lambda hit: -hit[0])
        results.append((score, node, hits[:3]))
    results.sort(key=lambda item: (-item[0], -item[1]["updated"]))
    total = len(results)
    lines = ['%d sessions match "%s".' % (total, query)]
    for score, node, hits in results[: args.limit]:
        lines.append("")
        lines.append("## %s" % describe(node))
        for _local, index, turn, low in hits:
            term = phrase if phrase in low else next((t for t in terms if t in low), terms[0])
            who = "user" if turn["r"] == "u" else "claude"
            lines.append("  turn %d, %s, %s: %s" % (index + 1, who, day(turn["t"]), snippet(turn["x"], low, term)))
    if total > args.limit:
        lines.append("")
        lines.append("... %d more sessions matched; narrow the query or raise limit." % (total - args.limit))
    if total:
        lines.append("")
        lines.append("Read one with the read tool (session = its id or title; start_turn to jump to a turn).")
    emit({"ok": True, "text": "\n".join(lines)})


def resolver_for(nodes, owner):
    shaped = {}
    for node in nodes.values():
        copy = dict(node)
        shaped[node["id"]] = copy
    return make_resolver(shaped, owner)


def command_read(args):
    loaded = load_vault()
    if not loaded:
        return no_vault()
    graph, nodes, owner = loaded
    resolve = resolver_for(nodes, owner)
    found = resolve(args.session)
    if not found:
        close = [n for n in nodes.values() if any(w in n["title"].lower() for w in args.session.lower().split() if len(w) > 3)]
        close.sort(key=lambda n: -n["updated"])
        lines = ['No session matches "%s".' % args.session]
        if close:
            lines.append("Closest titles:")
            lines += ["- %s" % describe(n) for n in close[:8]]
        return emit({"ok": False, "text": "\n".join(lines)})
    node = nodes[found["id"]]
    turns = turns_of(node)
    out, back = neighbours(graph, node["id"])
    lines = ["# %s" % describe(node), "folder: %s" % node["cwd"], "note: %s" % str(NOTES / (node["note"] + ".md"))]
    words = {"link": "links to", "fork": "forked from", "spawn": "started by", "files": "shares edited files with"}
    for target, kind in out:
        if target in nodes:
            lines.append("- %s %s" % (words[kind], describe(nodes[target])))
    for source, kind in back:
        if source in nodes:
            lines.append("- linked from (%s) %s" % (kind, describe(nodes[source])))
    lines.append("")
    total = len(turns)
    if args.find:
        want = args.find.lower()
        picked = [(i, t) for i, t in enumerate(turns) if want in t["x"].lower()]
        lines.append('%d of %d turns contain "%s".' % (len(picked), total, args.find))
        picked = picked[: args.count]
    else:
        if args.start is None:
            start = max(0, total - args.count)
        elif args.start < 0:
            start = max(0, total + args.start)
        else:
            start = max(0, min(total, args.start - 1))
        picked = list(enumerate(turns))[start : start + args.count]
        lines.append("Turns %d-%d of %d (a turn is one user prompt or one reply; start_turn pages)." % (start + 1 if picked else 0, start + len(picked), total))
    budget = args.budget
    for index, turn in picked:
        who = "USER" if turn["r"] == "u" else "CLAUDE"
        body = clip(turn["x"], args.chars)
        entry = "\n[%d] %s, %s\n%s" % (index + 1, who, day(turn["t"]), body)
        if budget - len(entry) < 0:
            lines.append("\n... output limit reached at turn %d; continue with start_turn=%d." % (index + 1, index + 1))
            break
        budget -= len(entry)
        lines.append(entry)
    emit({"ok": True, "id": node["id"], "title": node["title"], "text": "\n".join(lines)})


def command_link(args):
    loaded = load_vault()
    if not loaded:
        return no_vault()
    _graph, nodes, owner = loaded
    resolve = resolver_for(nodes, owner)
    source = resolve(args.source, loose=False)
    target = resolve(args.to)
    source_id = source["id"] if source else args.source
    if not target:
        return emit({"ok": False, "text": 'No session matches "%s".' % args.to})
    if target["id"] == source_id:
        return emit({"ok": False, "text": "A session cannot link to itself."})
    hand = read_json(LINKS, {}) or {}
    mine = hand.setdefault(source_id, {})
    if args.remove:
        existed = mine.pop(target["id"], None) is not None
        if not mine:
            hand.pop(source_id, None)
        write_json(LINKS, hand)
        text = ('Unlinked from "%s".' if existed else 'There was no hand-made link to "%s".') % target["title"]
        return emit({"ok": True, "text": text, "id": target["id"], "title": target["title"]})
    mine[target["id"]] = {"at": int(time.time() * 1000), "note": args.note or ""}
    write_json(LINKS, hand)
    emit({"ok": True, "text": 'Linked to "%s".' % target["title"], "id": target["id"], "title": target["title"]})


def read_payload():
    """The JSON object on stdin: text too long or too odd for an argument."""
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def command_target(args):
    """Who a send would go to, and whether that session is running now."""
    loaded = load_vault()
    if not loaded:
        return no_vault()
    _graph, nodes, owner = loaded
    resolve = resolver_for(nodes, owner)
    found = resolve(args.to)
    if not found:
        return emit({"ok": False, "text": 'No session matches "%s".' % args.to})
    source = resolve(args.source, loose=False) if args.source else None
    emit(
        {
            "ok": True,
            "id": found["id"],
            "title": found["title"],
            "project": found["project"],
            "live": live_cli(found, live_sessions()),
            "sourceId": source["id"] if source else "",
            "sourceTitle": source["title"] if source else "",
        }
    )


def fresh_tasks(tasks):
    limit = int(time.time() * 1000) - TASK_DAYS * 86400 * 1000
    return [task for task in tasks if isinstance(task, dict) and task.get("at", 0) >= limit]


def command_queue(args):
    """Leaves a task (JSON on stdin: to, source, sourceTitle, text) in a session's inbox."""
    loaded = load_vault()
    if not loaded:
        return no_vault()
    _graph, nodes, owner = loaded
    payload = read_payload()
    resolve = resolver_for(nodes, owner)
    target = resolve(str(payload.get("to") or ""), loose=False)
    text = str(payload.get("text") or "").strip()
    if not target or not text:
        return emit({"ok": False, "text": "Nothing was queued: the session or the task is missing."})
    inbox = read_json(INBOX, {}) or {}
    waiting = fresh_tasks(inbox.get(target["id"], []))
    task = {
        "id": "t%x" % int(time.time() * 1000),
        "at": int(time.time() * 1000),
        "from": str(payload.get("source") or ""),
        "fromTitle": str(payload.get("sourceTitle") or ""),
        "text": clip(text, MAX_TASK_CHARS),
    }
    waiting.append(task)
    inbox[target["id"]] = waiting[-20:]
    write_json(INBOX, inbox)
    emit({"ok": True, "id": task["id"], "waiting": len(inbox[target["id"]]), "title": target["title"]})


def command_inbox(args):
    """The tasks waiting for a session; --take hands them over and empties its inbox."""
    loaded = load_vault()
    nodes, owner = (loaded[1], loaded[2]) if loaded else ({}, {})
    inbox = read_json(INBOX, {}) or {}
    mine = owner.get(args.session, args.session)
    for host in (live_sessions().get(args.session, ""),):
        if host and host in inbox and mine not in inbox:
            mine = host
    tasks = fresh_tasks(inbox.get(mine, []))
    if args.take and mine in inbox:
        inbox.pop(mine, None)
        write_json(INBOX, inbox)
    waiting = {}
    for target, queued in inbox.items():
        if fresh_tasks(queued):
            waiting[nodes[target]["title"] if target in nodes else target] = len(fresh_tasks(queued))
    emit({"ok": True, "tasks": tasks, "waiting": waiting})


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("index").set_defaults(run=command_index)

    one = commands.add_parser("list")
    one.add_argument("--project", default="")
    one.add_argument("--limit", type=int, default=40)
    one.set_defaults(run=command_list)

    one = commands.add_parser("search")
    one.add_argument("--query", required=True)
    one.add_argument("--project", default="")
    one.add_argument("--exclude", default="")
    one.add_argument("--limit", type=int, default=8)
    one.set_defaults(run=command_search)

    one = commands.add_parser("read")
    one.add_argument("--session", required=True)
    one.add_argument("--start", type=int, default=None)
    one.add_argument("--count", type=int, default=24)
    one.add_argument("--chars", type=int, default=1800)
    one.add_argument("--budget", type=int, default=45000)
    one.add_argument("--find", default="")
    one.set_defaults(run=command_read)

    one = commands.add_parser("link")
    one.add_argument("--source", required=True)
    one.add_argument("--to", required=True)
    one.add_argument("--note", default="")
    one.add_argument("--remove", action="store_true")
    one.set_defaults(run=command_link)

    one = commands.add_parser("target")
    one.add_argument("--to", required=True)
    one.add_argument("--source", default="")
    one.set_defaults(run=command_target)

    commands.add_parser("queue").set_defaults(run=command_queue)

    one = commands.add_parser("inbox")
    one.add_argument("--session", required=True)
    one.add_argument("--take", action="store_true")
    one.set_defaults(run=command_inbox)

    args = parser.parse_args()
    try:
        args.run(args)
    except Exception as error:  # one JSON object on stdout, whatever happened
        emit({"ok": False, "text": "session-vault: %s: %s" % (type(error).__name__, error)})
        sys.exit(1)


if __name__ == "__main__":
    main()
