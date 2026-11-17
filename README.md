# session-vault

Every Claude Code session on this machine becomes a note in one vault, and the notes connect the way Obsidian notes do. Any session can then look into the others.

## Install

At the prompt of a Claude Code terminal session:

```
/plugin install session-vault --marketplace josemacontrerasp-arch/session-vault
```

From a shell:

```
claude plugin install session-vault --marketplace josemacontrerasp-arch/session-vault
```

In the desktop app, where `/plugin` does not run: click + next to the prompt box, then Plugins, Add marketplace with `josemacontrerasp-arch/session-vault`, then Add plugin.

Answer `y` to add the marketplace, then pick a scope (user makes it load in every session). It needs Python 3.8 or later on the PATH.

## What it does

- **A vault on disk.** `scripts/vault.py` reads the session transcripts (it never writes them) and keeps `~/.claude/session-vault`: one Markdown note per session under `sessions/`, with `[[wikilinks]]`, a home note, and `graph.json`. The folder opens in Obsidian as it is.
- **Connections.**
  - *Link*: a session names another as `[[Title]]` in a prompt or a reply, reads it through the vault, or was linked by hand.
  - *Fork*: a session was forked from, or started by, another.
  - *Shared files*: two sessions edited the same file (dashed).
  - *Unlinked mention*: a session says another's title in plain text. Listed with a Link button, not drawn, as in Obsidian.
- **A pane with three things.** `/vault` opens it: the graph, a session picker, and one message box.
  - *The graph* shows every session grouped by project; pick a session and it shows that session's neighbourhood. A node grows with its connections, and hovering one shows its name and lights up what it connects to.
  - *The picker* lists your other sessions, the open ones first. Picking one shows what it is doing: whether it is working, idle or closed, your last prompt there and Claude's last reply. The pane re-indexes every 30 seconds while it is open.
  - *The message box* sends what you type to the picked session. With no session picked, the message goes to the session you are in, whose Claude finds the right session in the vault and answers.
  - In the desktop app the graph also sits as a small widget when a session starts (`/vault auto off` stops that).
- **Asking about other sessions.** The model gets tools over the vault, `search`, `read`, `list` and `link` (and `send`, below), and is told to use them when you ask about another session. Typing `[[Session title]]` in a prompt points it at that session.
- **Giving another session a task.** Ask for it in any session ("have the journal paper session update the abstract"), pick the session in the pane and type in the message box, or run `/vault send <session> :: <task>`. A session that is running gets it at once, as a message from the sending session. For one that is not running, the task waits in the vault's inbox and starts as a turn of its own when that session next starts with this mod loaded, marked as coming from the other session. Waiting tasks older than 30 days are dropped.

## Commands

| Command | Does |
| --- | --- |
| `/vault` | Opens the pane |
| `/vault <title or id>` | Opens it on that session's local graph |
| `/vault reindex` | Indexes now (it also runs at session start and after each turn) |
| `/vault link <session>` / `/vault unlink <session>` | Links this session to another by hand, or removes that link |
| `/vault send <session> :: <task>` | Hands a task to another session |
| `/vault inbox` | Takes the tasks waiting for this session now |
| `/vault auto on` / `off` | Whether the widget opens when a session starts |
| `/vault path` | Prints the vault's folder |

## Needs

Python 3.8 or later on the PATH (`python`, `py -3` or `python3`); standard library only. Set `SESSION_VAULT_DIR` to keep the vault somewhere else.

## Limits

- A node in the drawing cannot be clicked, and the pane cannot open another session in the app: hover shows a node's name, and the picker under the graph is how you choose a session.
- The graph draws in the desktop app, VS Code and mobile; a terminal gets the lists.
- A task reaches a session that is not running only once that session starts with this mod loaded, so the mod has to be installed for all sessions, not one.
- The session that receives a task works with its own permissions; it reports back only if its Claude sends the report.
- The vault holds what you and Claude wrote, cut to 6,000 characters a message; tool output is not kept.
