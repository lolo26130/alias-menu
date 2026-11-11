# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
uv run aliases_menu.py
```

This is a [PEP 723](https://peps.python.org/pep-0723/) self-contained script — `uv` reads the inline `# dependencies` header (textual, json5, pyyaml) and installs them automatically. No virtualenv setup required.

## Architecture

Everything lives in a single file: **`aliases_menu.py`**.

The app is a Textual TUI with six tabs, each backed by a `DataTable`:

- **Alias** — parses `~/.bash_aliases` via `parse_aliases()`
- **Fonctions** — parses `~/.bash_functions` via `parse_functions()`
- **Outils uv** — lazy-loaded on first tab activation via `parse_uv_tools()`
- **Duplicates** — derived from the uv tools scan
- **opencode** — lazy-loaded on first activation via `list_opencode_sessions()`
- **Migration** — Claude Code → OpenCode conversion and project best-practices audit

### Data model

All entries are plain `dict`s with a `"type"` key: `"section"` | `"alias"` | `"func"` | `"tool"` | `"session"` | `"migration"`. Section entries become visual separators in the table rows (rendered by `_sec_row()`). The `_current()` method returns the dict under the cursor, or `None` if the cursor is on a section row. opencode session entries are the API `Session.Info` objects with an added `"type": "session"`.

### Section detection in bash files

Lines matching `#===...===` / `# title text` / `#===...===` in the source files are collapsed into section-header rows. `_classify_comment()` distinguishes `"heavy"` (`===` only), `"light"` (`---`/`***`), and `"text"` comment lines. The parser uses a state machine (`after_heavy`, `sec_buf`) to accumulate section titles between two `===` markers.

### Tool discovery (`parse_uv_tools`)

Combines three sources, in this order:

1. **`uv tool install` globals** — parses `uv tool list --show-paths --show-python` output
2. **uv projects** — walks `UV_PROJECT_DIRS` (hardcoded to `~/Documents/Python` and `~/Documents/Python/aaa_modules`), finds directories with `pyproject.toml` + a uv signal (`uv.lock`, `[tool.uv]`, or a uv-created `.venv`), extracts `[project.scripts]`
3. **pip editable installs** — same walk, but for projects without uv, confirmed via `direct_url.json` editable markers in any discovered venv's `site-packages`

`_PROJECT_SCAN_PRUNE` prunes known noise directories during os.walk. `_THIRDPARTY_ANCESTOR_NAMES` filters out unconfirmed pip projects whose path suggests third-party/example code.

### Architecture analysis (keys `m` / `M`)

`architecture_diagram()` uses `ast` to parse all `.py` files in a project, resolves both absolute and relative internal imports via `_local_imports_detail()`, collapses module paths to 2 levels with `_collapse_node()`, and produces a Mermaid flowchart. The result is cached as `ARCHITECTURE.md` in the project directory and regenerated only when source files are newer than the cache.

- `m` shows the structure as an indented adjacency list in a `SchemaOverlay` (terminal, à la pipdeptree), with up to `_MAX_EDGE_SYMBOLS_SHOWN = 3` imported symbols per edge
- `M` renders an interactive `vis-network` HTML page and opens it in the browser (output to `~/.cache/aliases_menu/`)

### Cross-package dependency graph (keys `g` / `G`)

`build_dependency_graph()` scans all discovered packages for two signals: actual `import` statements referencing another known package's name, and declared dependencies in `pyproject.toml`/`setup.cfg`. Isolated packages (no links) are excluded from the graph.

- `g` shows the result as an indented adjacency list in a `DepGraphOverlay`
- `G` renders an interactive HTML page (blue edges = detected import, orange = declared dependency)

### opencode sessions (`tab-opencode`)

The opencode tab lists every root session on the machine (subagents and archived
sessions excluded) by calling the local OpenCode API through the CLI:

```bash
opencode api GET '/api/session?limit=100&parentID=null&archived=false'
opencode api GET '/api/session/active'
```

- Query parameters must be embedded in the path; `--param` is ignored by this
  OpenCode version.
- `list_opencode_sessions()` follows `cursor.next` until an empty page and
  deduplicates by session ID.
- Sessions are grouped by `location.directory`; groups are sorted by most recent
  activity, and sessions inside a group by `time.updated`.
- Running sessions are marked `⏺` using `/api/session/active`.
- `Espace` resumes a session with `opencode --session <id>` and
  `cwd=location.directory`. `r` reloads the list.
- `s` opens `format_session_details()` (metadata, cost, tokens, last 5 user
  prompts and last assistant text, fetched with `?type=user&limit=5&order=desc`
  and the same for assistant messages).
- `x` deletes a session after a `ConfirmOverlay`; deletion goes through
  `DELETE /api/session/<id>` and also removes subagents.

### opencode documents (`d`, `a`, `c`)

`format_opencode_docs()` reads raw files (no config merging):

- `a` — `AGENTS.md` from `~/.config/opencode/` plus every ancestor from the
  session directory up to `~`; existing `CLAUDE.md` / `.claude/CLAUDE.md` files
  are appended with a "Claude Code — non chargé par OpenCode V2" note.
- `c` — `opencode.json` / `opencode.jsonc` from the global config directory and
  every ancestor, direct files first then `.opencode/` files (load order).
- `d` — both instructions and config.

The overlays reuse `ReadmeOverlay(..., markup=False)` so brackets in Markdown or
JSONC are not interpreted as Textual markup.

### Migration Claude Code → OpenCode (`tab-migration`)

The Migration tab lists the global scope (`~/.claude` → `~/.config/opencode`) plus
every project directory found in OpenCode sessions and the current working
directory. It shows Claude artifacts, OpenCode artifacts, and a state
(`à convertir`, `mixte`, `OpenCode seul`, `vide`).

- `C` builds a `ConversionPlan` without writing anything
  (`build_claude_conversion_plan`), shows a preview in `ConversionOverlay`, and
  applies it only after `y`/`Enter`.
- `A` runs `audit_project()` or `audit_global()` and opens the scored report.
- `s` opens the latest `CONVERSION_REPORT.md` from `.claude-backup/<timestamp>/`.
- `d` / `t` reuse the OpenCode documents overlay and directory tree.
- `r` rescans the selected scope list.

Conversion details:

- Discovered sources: `CLAUDE.md`, `.claude/CLAUDE.md`, `CLAUDE.local.md`,
  `.claude/settings.json`, `.claude/settings.local.json`, `.claude/commands/`,
  `.claude/agents/`, `.claude/skills/`, `.claude/rules/`, `.mcp.json`, global
  `~/.claude/settings.json`, and `mcpServers` from `~/.claude.json`.
- Every source is copied to `.claude-backup/<timestamp>/sources/`; existing
  destination files are copied to `.claude-backup/<timestamp>/existing/`.
  Originals are never modified.
- `CLAUDE.md` is appended to `AGENTS.md` (with duplicate detection), with
  `CLAUDE.md` → `AGENTS.md` and `Claude Code` → `OpenCode` substitutions.
  `@path` imports are added to the `instructions` array.
- `CLAUDE.local.md` goes to `.opencode/instructions/claude-local.md` and is
  loaded through `instructions` (warned as personal/possibly committed).
- Claude `permissions` are translated to OpenCode `permissions`
  (`Bash`→`shell`, `Read`→`read`, `Edit`/`Write`→`edit`, MCP tools→normalized
  server/tool actions, effects `allow`/`ask`/`deny`).
- `.claude/commands/*.md` → `.opencode/commands/`, `.claude/agents/*.md` →
  `.opencode/agents/` (mode subagent, tools→permissions, maxTurns→steps),
  `.claude/skills/*/SKILL.md` → `.opencode/skills/` (supporting files copied),
  `.claude/rules/*.md` → `.opencode/instructions/claude-rules/` plus an
  `instructions` entry.
- `.mcp.json` / `~/.claude.json` `mcpServers` → `mcp.servers` (local/remote).
- Unsupported hooks, env, Claude model aliases, status line, plugins, etc. are
  reported in warnings and preserved in the backup; no false equivalence is
  written.
- Existing `opencode.json(c)` is merged with `json5` (comments in a `.jsonc`
  file are not preserved in the rewritten file, but the original is backed up).
  Existing OpenCode permission rules are kept after imported rules so they keep
  precedence. `json5` and `pyyaml` are declared in the PEP 723 header.

Audit details (`audit_project` / `audit_global`):

- Checks AGENTS.md presence/size, CLAUDE.md divergence, OpenCode config validity
  and `$schema`, README, LICENSE, .gitignore/.env, pyproject metadata, lockfile,
  tests, lint/format, type checking, CI, src layout, py.typed, docs/changelog,
  and potential secrets.
- Weighted score: critical=5, warn=3, info=0; warnings count half. The report
  groups findings into CRITIQUE / IMPORTANT / INFO / POINTS FORTS.

### Key bindings

| Key | Action |
|-----|--------|
| `Space` | Run selected alias/function/tool; on the opencode tab, resume the selected session |
| `h` | Show `man` page (alias/tool), function body, or the equivalent `opencode --session` command |
| `r` | Reload source files and, if already loaded, tools and opencode sessions |
| `t` | Toggle directory tree overlay for the selected tool/session |
| `s` | Toggle detail columns (tools), show session statistics (opencode), or open the latest conversion report (Migration) |
| `d` | Show README overlay (tools), opencode documents (session), or Claude/OpenCode documents (Migration) |
| `a` | Show AGENTS.md / existing CLAUDE.md files for the selection (or cwd) |
| `c` | Show global and project opencode.json(c) files for the selection (or cwd) |
| `x` | Delete the selected opencode session (confirmation required) |
| `C` | Convert the selected Migration scope from Claude Code to OpenCode (preview + confirmation) |
| `A` | Audit the selected Migration scope against best practices |
| `m` / `M` | Package module structure — terminal text / HTML in browser |
| `g` / `G` | Cross-package dependency graph — terminal text / HTML in browser |
| `Ctrl+→` / `Ctrl+←` / `Tab` | Switch tabs |

### Modal overlays

Each overlay (`DirTreeOverlay`, `ReadmeOverlay`, `SchemaOverlay`, `DepGraphOverlay`, `ConfirmOverlay`, `ConversionOverlay`) is a `ModalScreen` with its own dismiss binding matching the key that opened it (e.g., `d` closes `ReadmeOverlay`). `ConfirmOverlay` is keyboard-only: `y`/`Enter` confirms, `n`/`Esc`/`q` cancels. `ConversionOverlay` previews a Claude → OpenCode plan with `y`/`Enter` to apply.

### Running alias/function entries

Functions that take arguments (`$1`, `$@`, etc., detected by `_HAS_ARGS_RE`) get an interactive prompt pre-filled with the function name via `readline.set_startup_hook`. Tools whose entry is `is_root=True` (no declared scripts, so the project directory itself is the "tool") get pre-filled with `cd '<tool_dir>' && `.
