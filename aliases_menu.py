#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=0.40.0"]
# ///
"""
Alias & Functions Browser — menu TUI style DietPi
Lancement : uv run ~/scripts/aliases_menu.py   ou   am
"""

import os
import re
import readline
import subprocess
import tomllib
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Label, TabbedContent, TabPane

ALIASES_FILE   = Path.home() / ".bash_aliases"
FUNCTIONS_FILE = Path.home() / ".bash_functions"
UV_TOOLS_DIR   = Path.home() / ".local" / "share" / "uv" / "tools"
# Arborescences scannées pour trouver des projets gérés par uv (pyproject.toml + uv.lock).
# Ajoute d'autres répertoires ici si besoin.
UV_PROJECT_DIRS = [Path.home() / "Documents" / "Python"]
_PROJECT_SCAN_PRUNE = {
    ".venv", "venv", "__pycache__", ".git", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages",
    "build", "dist", ".tox", ".idea", ".ipynb_checkpoints", ".cache",
}

# Séparateurs lourds (===) → titres de section ; légers (---) → ignorés
_HEAVY_SEP_RE = re.compile(r"^=+$")
_LIGHT_SEP_RE = re.compile(r"^[-*]+$")
_ALIAS_RE     = re.compile(r"^alias\s+(\S+?)=(['\"])(.*)\2\s*(?:#.*)?$")
_ALIAS_NQ_RE  = re.compile(r"^alias\s+(\S+?)=(.*)")
_FUNC_DEF_RE  = re.compile(r"^(?:function\s+)?([a-zA-Z_]\w*)\s*\(\s*\)\s*\{?\s*$")
_HAS_ARGS_RE  = re.compile(r'\$[1-9@*]|\$\{[1-9]\}')
_TOOL_HEADER_RE = re.compile(r"^(\S+)\s+v(\S+)\s+\[(.*?)\]\s+\((.*?)\)$")
_TOOL_ENTRY_RE  = re.compile(r"^-\s+(\S+)\s+\((.*?)\)$")
_SITE_PKG_NOISE = {"__pycache__", "_virtualenv.py", "_virtualenv.pth"}

TAB_IDS = ["tab-alias", "tab-func", "tab-tools"]


# ─── Parsers ──────────────────────────────────────────────────────────────────

def _classify_comment(text: str) -> str:
    """Classifie le contenu d'une ligne de commentaire (après # et strip)."""
    if _HEAVY_SEP_RE.match(text):
        return "heavy"
    if not text or _LIGHT_SEP_RE.match(text):
        return "light"
    return "text"


def parse_aliases(path: Path) -> list[dict]:
    """Extrait alias + sections depuis ~/.bash_aliases."""
    entries: list[dict] = []
    pending: list[str] = []
    sec_buf: list[str] = []   # titres accumulés entre deux ===
    after_heavy = False

    if not path.exists():
        return entries

    for line in path.read_text().splitlines():
        s = line.strip()

        if not s:
            after_heavy = False
            sec_buf = []
            pending = []
            continue

        if s.startswith("#"):
            text = s[1:].strip()
            kind = _classify_comment(text)

            if kind == "heavy":
                if after_heavy and sec_buf:
                    # === titre === → section confirmée
                    title = " · ".join(t for t in sec_buf if t).strip()
                    if title:
                        entries.append({"type": "section", "title": title})
                    sec_buf = []
                    after_heavy = False
                    pending = []
                else:
                    after_heavy = True
                    sec_buf = []
            elif kind == "light":
                after_heavy = False
                sec_buf = []
            else:
                if after_heavy:
                    sec_buf.append(text)
                else:
                    pending.append(text)
            continue

        # Ligne non-commentaire
        if after_heavy and sec_buf:
            pending = sec_buf[:]      # titre orphelin → commentaire d'alias
        after_heavy = False
        sec_buf = []

        m = _ALIAS_RE.match(s)
        if m:
            name, cmd = m.group(1), m.group(3)
        else:
            m2 = _ALIAS_NQ_RE.match(s)
            if m2:
                name = m2.group(1)
                raw  = re.sub(r"\s+#.*$", "", m2.group(2).strip())
                cmd  = raw.strip("\"' ")
            else:
                pending = []
                continue

        entries.append({"type": "alias", "name": name, "cmd": cmd,
                        "comment": " · ".join(pending)})
        pending = []

    return entries


def parse_functions(path: Path) -> list[dict]:
    """Extrait fonctions + sections depuis ~/.bash_functions."""
    entries: list[dict] = []
    pending: list[str] = []
    sec_buf: list[str] = []
    after_heavy = False

    if not path.exists():
        return entries

    lines = path.read_text().splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()

        if not s:
            after_heavy = False
            sec_buf = []
            pending = []
            i += 1
            continue

        if s.startswith("#"):
            text = s[1:].strip()
            kind = _classify_comment(text)

            if kind == "heavy":
                if after_heavy and sec_buf:
                    title = " · ".join(t for t in sec_buf if t).strip()
                    if title:
                        entries.append({"type": "section", "title": title})
                    sec_buf = []
                    after_heavy = False
                    pending = []
                else:
                    after_heavy = True
                    sec_buf = []
            elif kind == "light":
                after_heavy = False
                sec_buf = []
            else:
                if after_heavy:
                    sec_buf.append(text)
                else:
                    pending.append(text)
            i += 1
            continue

        if after_heavy and sec_buf:
            pending = sec_buf[:]
        after_heavy = False
        sec_buf = []

        m = _FUNC_DEF_RE.match(s)
        if m:
            name = m.group(1)
            i += 1
            if "{" not in s:
                while i < len(lines) and "{" not in lines[i]:
                    i += 1
                i += 1
            body: list[str] = []
            depth = 1
            while i < len(lines) and depth > 0:
                raw_line = lines[i]
                stripped = raw_line.strip()
                depth += stripped.count("{") - stripped.count("}")
                if depth > 0:
                    body.append(raw_line.rstrip())
                i += 1
            body_text = "\n".join(body)
            entries.append({
                "type":     "func",
                "name":     name,
                "has_args": bool(_HAS_ARGS_RE.search(body_text)),
                "comment":  " · ".join(pending),
                "body":     body_text,
            })
            pending = []
        else:
            pending = []
            i += 1

    return entries


def _parse_uv_tool_installs() -> list[dict]:
    """Liste les points d'entrée des outils installés globalement via `uv tool install`."""
    entries: list[dict] = []
    try:
        result = subprocess.run(
            ["uv", "tool", "list", "--show-paths", "--show-python"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return entries
    if result.returncode != 0:
        return entries

    current: dict | None = None
    for raw in result.stdout.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        m_header = _TOOL_HEADER_RE.match(line)
        if m_header:
            current = {
                "tool":     m_header.group(1),
                "version":  m_header.group(2),
                "python":   m_header.group(3),
                "tool_dir": m_header.group(4),
            }
            continue
        m_entry = _TOOL_ENTRY_RE.match(line)
        if m_entry and current:
            entries.append({
                "type":     "tool",
                "source":   "uv-tool",
                "tool":     current["tool"],
                "version":  current["version"],
                "python":   current["python"],
                "tool_dir": current["tool_dir"],
                "venv_dir": current["tool_dir"],
                "name":     m_entry.group(1),
                "path":     m_entry.group(2),
                "is_root":  False,
            })

    return entries


def _is_uv_project(dir_: Path, pyproject_text: str) -> bool:
    """Un projet est considéré 'uv' s'il a un uv.lock, une section [tool.uv], ou un venv créé par uv."""
    if (dir_ / "uv.lock").exists():
        return True
    if "[tool.uv]" in pyproject_text:
        return True
    cfg = dir_ / ".venv" / "pyvenv.cfg"
    if cfg.exists():
        try:
            return "uv" in cfg.read_text().lower()
        except OSError:
            return False
    return False


def _venv_python_version(venv_dir: Path) -> str:
    cfg = venv_dir / "pyvenv.cfg"
    if not cfg.exists():
        return ""
    for line in cfg.read_text().splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip()
    return ""


def find_uv_projects(roots: list[Path], max_depth: int = 6) -> list[dict]:
    """Scanne des arborescences (par défaut ~/Documents/Python) à la recherche de
    projets gérés par uv (pyproject.toml + uv.lock / [tool.uv] / venv créé par uv),
    et en extrait les points d'entrée déclarés dans [project.scripts]."""
    entries: list[dict] = []
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            depth = len(d.relative_to(root).parts)
            dirnames[:] = [n for n in dirnames if n not in _PROJECT_SCAN_PRUNE]
            if depth >= max_depth:
                dirnames[:] = []
            if "pyproject.toml" not in filenames:
                continue

            try:
                text = (d / "pyproject.toml").read_text()
            except OSError:
                continue
            if not _is_uv_project(d, text):
                continue

            try:
                data = tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                data = {}
            project = data.get("project", {})
            name = project.get("name", d.name)
            version = project.get("version", "")
            venv_dir = d / ".venv"
            python = _venv_python_version(venv_dir) if venv_dir.exists() else ""

            scripts: dict = {}
            scripts.update(project.get("scripts", {}) or {})
            scripts.update(project.get("gui-scripts", {}) or {})

            if scripts:
                for script_name in scripts:
                    exe = venv_dir / "bin" / script_name
                    entries.append({
                        "type":     "tool",
                        "source":   "project",
                        "tool":     name,
                        "version":  version,
                        "python":   python,
                        "tool_dir": str(d),
                        "venv_dir": str(venv_dir),
                        "name":     script_name,
                        "path":     str(exe) if exe.exists() else "(non installé — uv sync requis)",
                        "is_root":  False,
                    })
            else:
                entries.append({
                    "type":     "tool",
                    "source":   "project",
                    "tool":     name,
                    "version":  version,
                    "python":   python,
                    "tool_dir": str(d),
                    "venv_dir": str(venv_dir),
                    "name":     name,
                    "path":     str(d),
                    "is_root":  True,
                })

            # ne pas redescendre dans un projet déjà identifié
            dirnames[:] = []

    return entries


def parse_uv_tools() -> list[dict]:
    """Combine les outils installés globalement (`uv tool install`) et les
    projets uv trouvés sous UV_PROJECT_DIRS (par défaut ~/Documents/Python)."""
    return _parse_uv_tool_installs() + find_uv_projects(UV_PROJECT_DIRS)


def tool_install_state(e: dict) -> str:
    """'uv' si le point d'entrée est bien dans le venv du projet/outil, sinon 'pip'."""
    if e.get("is_root"):
        return "uv"
    try:
        resolved = Path(e["path"]).resolve()
        base = Path(e["venv_dir"]).resolve()
        resolved.relative_to(base)
        return "uv"
    except (OSError, ValueError):
        return "pip"


def tool_structure_summary(venv_dir: str, limit: int = 60) -> str:
    """Résumé du contenu de site-packages du venv (outil uv ou projet)."""
    lib = Path(venv_dir) / "lib"
    site_packages = None
    if lib.is_dir():
        for py_dir in sorted(lib.glob("python3.*")):
            candidate = py_dir / "site-packages"
            if candidate.is_dir():
                site_packages = candidate
                break
    if site_packages is None:
        return "(venv non créé — uv sync requis)"

    names = sorted(p.name for p in site_packages.iterdir() if p.name not in _SITE_PKG_NOISE)
    pretty = [n + "/" if (site_packages / n).is_dir() else n for n in names]
    text = ", ".join(pretty) if pretty else "(vide)"
    if len(text) > limit:
        text = text[: limit - 1].rstrip(", ") + "…"
    return text


# ─── Helpers ──────────────────────────────────────────────────────────────────

def man_target(cmd: str) -> str:
    """Commande pour man : saute 'sudo' si premier mot."""
    words = cmd.strip().split()
    if not words:
        return ""
    return words[1] if words[0] == "sudo" and len(words) > 1 else words[0]


def show_man_or_help(target: str) -> None:
    print(f"\n\033[1;36m?  man {target}\033[0m\n{'─'*60}")
    if subprocess.run(["man", target]).returncode != 0:
        print(f"\033[33mPas de page man pour '{target}' — essai --help...\033[0m\n")
        r2 = subprocess.run(["bash", "-c", f"{target} --help 2>&1 | less -R"])
        if r2.returncode != 0:
            print(f"\033[31mAucune aide disponible pour '{target}'.\033[0m")
            input("\n\033[2m[ Appuyez sur Entrée pour revenir au menu ]\033[0m")


def _sec_row(title: str) -> tuple:
    """Cellules d'une ligne-séparateur de section."""
    if len(title) > 70:
        title = title[:69].rstrip() + "…"
    bar = "[dim cyan]" + "─" * 38 + "[/dim cyan]"
    return (
        "",
        "",
        f"[bold bright_cyan]  ◆  {title}[/bold bright_cyan]",
        bar,
        "",
    )


# ─── CSS ──────────────────────────────────────────────────────────────────────

CSS = """
Screen { background: $surface; }

TabbedContent { height: 1fr; }
TabPane { padding: 0; }

DataTable {
    height: 1fr;
    border: tall $accent;
    margin: 0 1;
}
DataTable > .datatable--header {
    background: $primary;
    color: $text;
    text-style: bold;
}
DataTable > .datatable--cursor    { background: $accent;          color: $text; }
DataTable > .datatable--row-hover { background: $accent-darken-1;              }

#status {
    height: 1;
    padding: 0 2;
    background: $panel;
    color: $text-muted;
    border-top: solid $accent;
}
"""


# ─── App ──────────────────────────────────────────────────────────────────────

class AliasMenu(App):
    TITLE    = "  Alias & Functions Browser"
    CSS      = CSS
    BINDINGS = [
        Binding("q",          "quit",       "Quitter",      show=True),
        Binding("space",      "run_entry",  "▶ Lancer",     show=True),
        Binding("h",          "show_help",  "? Man/Help",   show=True),
        Binding("r",          "reload",     "Recharger",    show=True),
        Binding("t",          "toggle_view",   "Liste/Arbre",   show=True),
        Binding("s",          "toggle_detail", "État/Structure", show=True),
        # Changement d'onglet — priority=True pour passer avant le DataTable
        Binding("ctrl+right", "next_tab",   "Onglet →",     show=True,  priority=True),
        Binding("ctrl+left",  "prev_tab",   "← Onglet",     show=True,  priority=True),
        Binding("tab",        "next_tab",   "Onglet →",     show=False, priority=True),
    ]

    # ── compose & mount ─────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("  Alias  ", id="tab-alias"):
                yield DataTable(id="alias-table", zebra_stripes=True, cursor_type="row")
            with TabPane("  Fonctions  ", id="tab-func"):
                yield DataTable(id="func-table", zebra_stripes=True, cursor_type="row")
            with TabPane("  Outils uv  ", id="tab-tools"):
                yield DataTable(id="tools-table", zebra_stripes=True, cursor_type="row")
        yield Label("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._load_aliases()
        self._load_functions()
        self.tools_entries: list[dict] = []
        self._tool_rows: list[dict | None] = []
        self._tools_loaded = False
        self._tools_view_mode = "list"
        self._tools_detail = False
        self._init_tools_placeholder()
        self.query_one("#alias-table", DataTable).focus()

    def _init_tools_placeholder(self) -> None:
        t = self.query_one("#tools-table", DataTable)
        t.clear(columns=True)
        t.add_columns("  ▶ Run  ", "  ? Man  ", "Outil", "Point d'entrée", "Python", "Chemin")
        t.add_row("", "", "[dim]Active cet onglet pour lancer la recherche (uv tool list)…[/dim]", "", "", "")

    # ── tab switching ────────────────────────────────────────────────────────

    def _active_tab(self) -> str:
        return self.query_one(TabbedContent).active or TAB_IDS[0]

    def _switch_tab(self, delta: int) -> None:
        tc = self.query_one(TabbedContent)
        try:
            idx = TAB_IDS.index(tc.active)
        except ValueError:
            idx = 0
        tc.active = TAB_IDS[(idx + delta) % len(TAB_IDS)]

    def action_next_tab(self) -> None:
        self._switch_tab(+1)

    def action_prev_tab(self) -> None:
        self._switch_tab(-1)

    # TabbedContent.active est un reactive → cet event se déclenche sur changement
    def on_tabbed_content_tab_activated(self, _: TabbedContent.TabActivated) -> None:
        # Utilise tc.active (fiable) plutôt que event.tab.id (préfixé en interne)
        active = self._active_tab()
        if active == "tab-alias":
            self.query_one("#alias-table", DataTable).focus()
            n = sum(1 for e in getattr(self, "alias_entries", []) if e["type"] != "section")
            self._status(f"{n} alias  ·  {ALIASES_FILE}")
        elif active == "tab-func":
            self.query_one("#func-table", DataTable).focus()
            n = sum(1 for e in getattr(self, "func_entries", []) if e["type"] != "section")
            self._status(f"{n} fonctions  ·  {FUNCTIONS_FILE}")
        else:
            self.query_one("#tools-table", DataTable).focus()
            if not self._tools_loaded:
                self._status("Recherche des outils installés via uv…")
                self._load_tools()
                self._tools_loaded = True
            n = len(self.tools_entries)
            self._status(f"{n} point(s) d'entrée  ·  uv tool install + {UV_PROJECT_DIRS[0]}  ·  [t] liste/arbre  [s] détails")

    # ── loaders ─────────────────────────────────────────────────────────────

    def _load_aliases(self) -> None:
        t = self.query_one("#alias-table", DataTable)
        t.clear(columns=True)
        t.add_columns("  ▶ Run  ", "  ? Man  ", "Alias", "Description / Commentaire", "Commande")
        self.alias_entries = parse_aliases(ALIASES_FILE)
        for i, e in enumerate(self.alias_entries):
            if e["type"] == "section":
                t.add_row(*_sec_row(e["title"]), key=f"as{i}")
            else:
                desc_cell = e["comment"] or f"[dim italic]{e['cmd']}[/dim italic]"
                t.add_row(
                    "[bold green] [SPC] [/bold green]",
                    "[bold cyan]  [H]  [/bold cyan]",
                    f"[bold yellow]{e['name']}[/bold yellow]",
                    desc_cell,
                    f"[dim]{e['cmd']}[/dim]",
                    key=f"a{i}",
                )
        n = sum(1 for e in self.alias_entries if e["type"] != "section")
        self._status(f"{n} alias  ·  {ALIASES_FILE}")

    def _load_functions(self) -> None:
        t = self.query_one("#func-table", DataTable)
        t.clear(columns=True)
        t.add_columns("  ▶ Run  ", "  ? Man  ", "Fonction", "Description / Commentaire", "Args")
        self.func_entries = parse_functions(FUNCTIONS_FILE)
        for i, e in enumerate(self.func_entries):
            if e["type"] == "section":
                t.add_row(*_sec_row(e["title"]), key=f"fs{i}")
            else:
                args_cell = "[bold magenta] $1… [/bold magenta]" if e["has_args"] else "[dim]  —  [/dim]"
                t.add_row(
                    "[bold green] [SPC] [/bold green]",
                    "[bold cyan]  [H]  [/bold cyan]",
                    f"[bold yellow]{e['name']}[/bold yellow]",
                    e["comment"] or "[dim]—[/dim]",
                    args_cell,
                    key=f"f{i}",
                )

    def _load_tools(self) -> None:
        self.tools_entries = parse_uv_tools()
        self._render_tools_table()

    _SOURCE_LABELS = {
        "uv-tool": "Outils uv (uv tool install)",
        "project": f"Projets uv ({', '.join(str(p) for p in UV_PROJECT_DIRS)})",
    }

    def _render_tools_table(self) -> None:
        t = self.query_one("#tools-table", DataTable)
        t.clear(columns=True)
        detail = self._tools_detail
        self._tool_rows = []

        if self._tools_view_mode == "tree":
            info1_h, info2_h = ("État", "Structure (site-packages)") if detail else ("Python", "Répertoire d'installation")
            t.add_columns("  ▶ Run  ", "  ? Man  ", "Arborescence d'installation", info1_h, info2_h)
            current_source = None
            current_tool = None
            for e in self.tools_entries:
                if e["source"] != current_source:
                    current_source = e["source"]
                    current_tool = None
                    label = self._SOURCE_LABELS.get(current_source) or current_source or ""
                    t.add_row(*_sec_row(label), key=f"tsh{len(self._tool_rows)}")
                    self._tool_rows.append(None)
                if e["tool"] != current_tool:
                    current_tool = e["tool"]
                    if detail:
                        info1, info2 = tool_install_state(e), tool_structure_summary(e["venv_dir"])
                    else:
                        info1, info2 = e["python"], e["tool_dir"]
                    t.add_row(
                        "", "",
                        f"[bold bright_cyan]◆ {e['tool']}[/bold bright_cyan] [dim]v{e['version']}[/dim]",
                        f"[dim]{info1}[/dim]",
                        f"[dim]{info2}[/dim]",
                        key=f"th{len(self._tool_rows)}",
                    )
                    self._tool_rows.append(None)
                entry_label = f"{e['name']} [dim](dossier projet)[/dim]" if e.get("is_root") else e["name"]
                t.add_row(
                    "[bold green] [SPC] [/bold green]",
                    "[bold cyan]  [H]  [/bold cyan]",
                    f"   [yellow]├─ {entry_label}[/yellow]",
                    "",
                    f"[dim]{e['path']}[/dim]",
                    key=f"te{len(self._tool_rows)}",
                )
                self._tool_rows.append(e)
        else:
            info1_h, info2_h = ("État", "Structure (site-packages)") if detail else ("Python", "Chemin")
            t.add_columns("  ▶ Run  ", "  ? Man  ", "Source", "Outil", "Point d'entrée", info1_h, info2_h)
            for e in self.tools_entries:
                if detail:
                    info1, info2 = tool_install_state(e), tool_structure_summary(e["venv_dir"])
                else:
                    info1, info2 = e["python"], e["path"]
                source_cell = "[dim]uv tool[/dim]" if e["source"] == "uv-tool" else "[dim]projet[/dim]"
                entry_label = f"{e['name']} [dim](dossier projet)[/dim]" if e.get("is_root") else e["name"]
                t.add_row(
                    "[bold green] [SPC] [/bold green]",
                    "[bold cyan]  [H]  [/bold cyan]",
                    source_cell,
                    f"[bold yellow]{e['tool']}[/bold yellow]",
                    entry_label,
                    f"[dim]{info1}[/dim]",
                    f"[dim]{info2}[/dim]",
                    key=f"t{len(self._tool_rows)}",
                )
                self._tool_rows.append(e)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _status(self, msg: str) -> None:
        self.query_one("#status", Label).update(msg)

    def _current(self) -> dict | None:
        """Retourne l'entrée sous le curseur, ou None si c'est une section/en-tête."""
        active = self._active_tab()
        if active == "tab-alias":
            t, entries = self.query_one("#alias-table", DataTable), self.alias_entries
            row = t.cursor_row
            if 0 <= row < len(entries):
                e = entries[row]
                return None if e["type"] == "section" else e
            return None
        if active == "tab-func":
            t, entries = self.query_one("#func-table", DataTable), self.func_entries
            row = t.cursor_row
            if 0 <= row < len(entries):
                e = entries[row]
                return None if e["type"] == "section" else e
            return None

        t = self.query_one("#tools-table", DataTable)
        row = t.cursor_row
        if 0 <= row < len(self._tool_rows):
            return self._tool_rows[row]
        return None

    # ── action : run ────────────────────────────────────────────────────────

    def action_run_entry(self) -> None:
        entry = self._current()
        if not entry:
            return
        if entry["type"] == "alias":
            self._run_alias(entry)
        elif entry["type"] == "func":
            self._run_function(entry)
        else:
            self._run_tool(entry)

    def _run_alias(self, e: dict) -> None:
        self._status(f"Lancement : {e['name']}  →  {e['cmd']}")
        with self.suspend():
            print(f"\n\033[1;32m▶  {e['name']}\033[0m  :  {e['cmd']}\n{'─'*60}")
            subprocess.run(["bash", "-i", "-c", e["cmd"]])
            input("\n\033[2m[ Appuyez sur Entrée pour revenir au menu ]\033[0m")

    def _run_function(self, e: dict) -> None:
        name = e["name"]
        if not e["has_args"]:
            self._status(f"Lancement : {name}()")
            with self.suspend():
                print(f"\n\033[1;32m▶  {name}\033[0m\n{'─'*60}")
                subprocess.run(["bash", "-i", "-c", name])
                input("\n\033[2m[ Appuyez sur Entrée pour revenir au menu ]\033[0m")
        else:
            self._status(f"{name}() — tapez les arguments puis Entrée")
            with self.suspend():
                print(f"\n\033[1;33m▶  {name}\033[0m  \033[33m[arguments requis]\033[0m")
                if e["comment"]:
                    print(f"\033[2m   {e['comment']}\033[0m")
                print(f"{'─'*60}")
                readline.set_startup_hook(lambda: readline.insert_text(f"{name} "))
                try:
                    cmd = input("\033[1;33m$ \033[0m")
                except (EOFError, KeyboardInterrupt):
                    cmd = ""
                finally:
                    readline.set_startup_hook(None)
                if cmd.strip():
                    print()
                    subprocess.run(["bash", "-i", "-c", cmd])
                    input("\n\033[2m[ Appuyez sur Entrée pour revenir au menu ]\033[0m")

    def _run_tool(self, e: dict) -> None:
        name = e["tool"] if e.get("is_root") else e["name"]
        prefill = f"cd '{e['tool_dir']}' && " if e.get("is_root") else f"{e['name']} "
        self._status(f"{name} — tapez des arguments si besoin, puis Entrée")
        with self.suspend():
            print(f"\n\033[1;32m▶  {name}\033[0m  \033[2m({e['tool']} v{e['version']})\033[0m\n{'─'*60}")
            readline.set_startup_hook(lambda: readline.insert_text(prefill))
            try:
                cmd = input("\033[1;33m$ \033[0m")
            except (EOFError, KeyboardInterrupt):
                cmd = ""
            finally:
                readline.set_startup_hook(None)
            if cmd.strip():
                print()
                subprocess.run(["bash", "-i", "-c", cmd])
                input("\n\033[2m[ Appuyez sur Entrée pour revenir au menu ]\033[0m")

    # ── action : man / help ─────────────────────────────────────────────────

    def action_show_help(self) -> None:
        entry = self._current()
        if not entry:
            return

        if entry["type"] == "alias":
            target = man_target(entry["cmd"])
            if not target:
                return
            self._status(f"Manuel : {target}")
            with self.suspend():
                show_man_or_help(target)
        elif entry["type"] == "func":
            name = entry["name"]
            self._status(f"Corps de la fonction : {name}")
            with self.suspend():
                header = (
                    f"\033[1;36m# {name}\033[0m"
                    + (f"\n\033[2m# {entry['comment']}\033[0m" if entry["comment"] else "")
                    + f"\n{'─'*60}\n"
                )
                body = entry["body"] or "(corps vide)"
                subprocess.run(["less", "-R"], input=(header + body + "\n").encode())
        elif entry.get("is_root"):
            self._status(f"Résumé du projet : {entry['tool']}")
            with self.suspend():
                structure = tool_structure_summary(entry["venv_dir"], limit=1000)
                state = tool_install_state(entry)
                summary = (
                    f"\033[1;36m# {entry['tool']}\033[0m  v{entry['version'] or '?'}\n"
                    f"\033[2mRépertoire : {entry['tool_dir']}\033[0m\n"
                    f"\033[2mPython venv : {entry['python'] or '(non créé)'}\033[0m\n"
                    f"\033[2mÉtat : {state}\033[0m\n"
                    f"{'─'*60}\n"
                    f"Contenu de site-packages :\n{structure}\n"
                )
                subprocess.run(["less", "-R"], input=summary.encode())
        else:
            name = entry["name"]
            self._status(f"Manuel : {name}")
            with self.suspend():
                show_man_or_help(name)

    # ── action : vues de l'onglet Outils uv ─────────────────────────────────

    def action_toggle_view(self) -> None:
        if self._active_tab() != "tab-tools" or not self._tools_loaded:
            return
        self._tools_view_mode = "tree" if self._tools_view_mode == "list" else "list"
        self._render_tools_table()
        self._status(f"Vue : {self._tools_view_mode}")

    def action_toggle_detail(self) -> None:
        if self._active_tab() != "tab-tools" or not self._tools_loaded:
            return
        self._tools_detail = not self._tools_detail
        self._render_tools_table()
        self._status("Détails : " + ("état + structure" if self._tools_detail else "python + chemin"))

    # ── action : reload ─────────────────────────────────────────────────────

    def action_reload(self) -> None:
        self._load_aliases()
        self._load_functions()
        if self._tools_loaded:
            self._load_tools()


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    AliasMenu().run()
