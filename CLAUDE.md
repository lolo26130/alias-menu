# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
uv run aliases_menu.py
```

This is a [PEP 723](https://peps.python.org/pep-0723/) self-contained script — `uv` reads the inline `# dependencies` header and installs `textual` automatically. No virtualenv setup required.

## Architecture

Everything lives in a single file: **`aliases_menu.py`**.

The app is a Textual TUI with three tabs, each backed by a `DataTable`:

- **Alias** — parses `~/.bash_aliases` via `parse_aliases()`
- **Fonctions** — parses `~/.bash_functions` via `parse_functions()`
- **Outils uv** — lazy-loaded on first tab activation via `parse_uv_tools()`

### Data model

All entries are plain `dict`s with a `"type"` key: `"section"` | `"alias"` | `"func"` | `"tool"`. Section entries become visual separators in the table rows (rendered by `_sec_row()`). The `_current()` method returns the dict under the cursor, or `None` if the cursor is on a section row.

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

### Key bindings

| Key | Action |
|-----|--------|
| `Space` | Run selected alias/function/tool (suspends TUI, runs in interactive bash, waits for Enter) |
| `H` | Show `man` page (alias/tool) or function body (piped through `less`) |
| `R` | Reload source files (and tools if already loaded) |
| `t` | Toggle directory tree overlay for selected tool/project |
| `s` | Toggle detail columns (python+path vs. install state+site-packages) |
| `d` | Show README overlay for selected tool/project |
| `m` / `M` | Package module structure — terminal text / HTML in browser |
| `g` / `G` | Cross-package dependency graph — terminal text / HTML in browser |
| `Ctrl+→` / `Ctrl+←` / `Tab` | Switch tabs |

### Modal overlays

Each overlay (`DirTreeOverlay`, `ReadmeOverlay`, `SchemaOverlay`, `DepGraphOverlay`) is a `ModalScreen` with its own dismiss binding matching the key that opened it (e.g., `d` closes `ReadmeOverlay`).

### Running alias/function entries

Functions that take arguments (`$1`, `$@`, etc., detected by `_HAS_ARGS_RE`) get an interactive prompt pre-filled with the function name via `readline.set_startup_hook`. Tools whose entry is `is_root=True` (no declared scripts, so the project directory itself is the "tool") get pre-filled with `cd '<tool_dir>' && `.
