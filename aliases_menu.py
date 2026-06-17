#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=0.40.0"]
# ///
"""
Alias & Functions Browser — menu TUI style DietPi
Lancement : uv run ~/scripts/aliases_menu.py   ou   am
"""

import re
import readline
import subprocess
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Label, TabbedContent, TabPane

ALIASES_FILE   = Path.home() / ".bash_aliases"
FUNCTIONS_FILE = Path.home() / ".bash_functions"

# Séparateurs lourds (===) → titres de section ; légers (---) → ignorés
_HEAVY_SEP_RE = re.compile(r"^=+$")
_LIGHT_SEP_RE = re.compile(r"^[-*]+$")
_ALIAS_RE     = re.compile(r"^alias\s+(\S+?)=(['\"])(.*)\2\s*(?:#.*)?$")
_ALIAS_NQ_RE  = re.compile(r"^alias\s+(\S+?)=(.*)")
_FUNC_DEF_RE  = re.compile(r"^(?:function\s+)?([a-zA-Z_]\w*)\s*\(\s*\)\s*\{?\s*$")
_HAS_ARGS_RE  = re.compile(r'\$[1-9@*]|\$\{[1-9]\}')

TAB_IDS = ["tab-alias", "tab-func"]


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
        yield Label("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._load_aliases()
        self._load_functions()
        self.query_one("#alias-table", DataTable).focus()

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
        if self._active_tab() == "tab-alias":
            self.query_one("#alias-table", DataTable).focus()
            n = sum(1 for e in getattr(self, "alias_entries", []) if e["type"] != "section")
            self._status(f"{n} alias  ·  {ALIASES_FILE}")
        else:
            self.query_one("#func-table", DataTable).focus()
            n = sum(1 for e in getattr(self, "func_entries", []) if e["type"] != "section")
            self._status(f"{n} fonctions  ·  {FUNCTIONS_FILE}")

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

    # ── helpers ─────────────────────────────────────────────────────────────

    def _status(self, msg: str) -> None:
        self.query_one("#status", Label).update(msg)

    def _current(self) -> dict | None:
        """Retourne l'entrée sous le curseur, ou None si c'est une section."""
        if self._active_tab() == "tab-alias":
            t, entries = self.query_one("#alias-table", DataTable), self.alias_entries
        else:
            t, entries = self.query_one("#func-table", DataTable), self.func_entries
        row = t.cursor_row
        if 0 <= row < len(entries):
            e = entries[row]
            return None if e["type"] == "section" else e
        return None

    # ── action : run ────────────────────────────────────────────────────────

    def action_run_entry(self) -> None:
        entry = self._current()
        if not entry:
            return
        if entry["type"] == "alias":
            self._run_alias(entry)
        else:
            self._run_function(entry)

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
        else:
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

    # ── action : reload ─────────────────────────────────────────────────────

    def action_reload(self) -> None:
        self._load_aliases()
        self._load_functions()


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    AliasMenu().run()
