#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=0.40.0"]
# ///
"""
Alias & Functions Browser — menu TUI style DietPi
Lancement : uv run ~/scripts/aliases_menu.py   ou   am
"""

import ast
import configparser
import hashlib
import json
import os
import re
import readline
import subprocess
import textwrap
import tomllib
import webbrowser
from datetime import datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Label, Static, TabbedContent, TabPane

ALIASES_FILE   = Path.home() / ".bash_aliases"
FUNCTIONS_FILE = Path.home() / ".bash_functions"
UV_TOOLS_DIR   = Path.home() / ".local" / "share" / "uv" / "tools"
# Arborescences scannées pour trouver des projets gérés par uv (pyproject.toml + uv.lock).
# Ajoute d'autres répertoires ici si besoin.
UV_PROJECT_DIRS = [Path.home() / "Documents" / "Python", 
                    Path.home() / "Documents" / "Python" / "aaa_modules"]
_PROJECT_SCAN_PRUNE = {
    ".venv", "venv", "__pycache__", ".git", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages",
    "build", "dist", ".tox", ".idea", ".ipynb_checkpoints", ".cache",
    ".buildozer",
}
# Noms de dossiers indiquant un dépôt tiers téléchargé / un exemple / un
# exercice de formation plutôt qu'un projet personnel — utilisé uniquement
# pour filtrer les projets pip *non confirmés* (cf. find_pip_editable_projects) ;
# un projet dont l'install --editable est réellement retrouvée reste listé
# même si son chemin matche un de ces motifs.
_THIRDPARTY_ANCESTOR_NAMES = {"old", "libraries", "exemple_python_repo", "tests_exemples", "doc_python", "examples"}


def _looks_like_thirdparty(d: Path, roots: list[Path]) -> bool:
    name_lower = d.name.lower()
    if name_lower.endswith("master") or name_lower.endswith("-main") or name_lower == "main":
        return True
    for root in roots:
        try:
            parts = d.relative_to(root).parts
        except ValueError:
            continue
        if any(p.lower() in _THIRDPARTY_ANCESTOR_NAMES for p in parts):
            return True
    return False

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

TAB_IDS = ["tab-alias", "tab-func", "tab-tools", "tab-dups"]


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
    roots_set = {str(r) for r in roots}
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            depth = len(d.relative_to(root).parts)
            # Ignore les noms connus + tout répertoire qui est lui-même un venv
            # (pyvenv.cfg), peu importe son nom, pour ne pas remonter de "projets"
            # qui sont en fait des paquets installés dans un environnement virtuel.
            dirnames[:] = [
                n for n in dirnames
                if n not in _PROJECT_SCAN_PRUNE and not (d / n / "pyvenv.cfg").exists()
            ]
            if depth >= max_depth:
                dirnames[:] = []
            if "pyproject.toml" not in filenames or str(d) in roots_set or str(d) in seen:
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
            seen.add(str(d))
            dirnames[:] = []

    return entries


def _is_real_setup_py(path: Path) -> bool:
    """Un setup.py de packaging appelle réellement setup() via setuptools/distutils
    — ça écarte les modules qui s'appellent juste 'setup.py' par coïncidence
    (ex: un sous-module 'configuration initiale' sans aucun rapport au packaging)."""
    try:
        text = path.read_text()
    except OSError:
        return False
    return "setup(" in text and ("setuptools" in text or "distutils" in text)


def _pip_project_health(d: Path) -> bool:
    """Critère minimal de 'bonne santé' : un descripteur de build exploitable
    (pyproject.toml avec [project]/[build-system], setup.cfg avec [metadata],
    ou un setup.py qui appelle vraiment setup())."""
    pyproject = d / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text()
        except OSError:
            text = ""
        if "[build-system]" in text or "[project]" in text:
            return True

    setup_cfg = d / "setup.cfg"
    if setup_cfg.exists():
        cp = configparser.ConfigParser()
        try:
            cp.read(setup_cfg)
            if cp.has_section("metadata"):
                return True
        except (OSError, configparser.Error):
            pass

    setup_py = d / "setup.py"
    return setup_py.exists() and _is_real_setup_py(setup_py)


def _find_all_venvs(roots: list[Path], max_depth: int = 8) -> list[Path]:
    """Liste tous les venvs (n'importe quel nom, détecté via pyvenv.cfg) sous
    les arborescences données — un package éditable peut être installé dans
    le venv d'un *autre* projet (dépendance locale partagée), pas seulement
    dans son propre venv."""
    venvs: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            if "pyvenv.cfg" in filenames:
                venvs.append(d)
                dirnames[:] = []
                continue
            depth = len(d.relative_to(root).parts)
            # Repère les sous-dossiers qui sont eux-mêmes des venvs (pyvenv.cfg)
            # *avant* de filtrer via _PROJECT_SCAN_PRUNE (qui exclut ".venv"/"venv"
            # du parcours) — sinon on ne les visiterait jamais.
            kept = []
            for n in dirnames:
                if (d / n / "pyvenv.cfg").exists():
                    venvs.append(d / n)
                elif n not in _PROJECT_SCAN_PRUNE:
                    kept.append(n)
            dirnames[:] = kept
            if depth >= max_depth:
                dirnames[:] = []
    return venvs


def _site_packages_dir(venv_dir: Path) -> Path | None:
    lib = venv_dir / "lib"
    if not lib.is_dir():
        return None
    for py_dir in sorted(lib.glob("python3.*")):
        cand = py_dir / "site-packages"
        if cand.is_dir():
            return cand
    return None


def _editable_markers_in(site_packages: Path) -> list[dict]:
    """Recense, dans site_packages, tous les marqueurs d'install --editable
    (direct_url.json moderne ou .egg-link historique) et le répertoire cible
    réel de chacun — c'est le signe qu'un package est *vraiment* installé,
    pas juste un dossier qui ressemble à un projet."""
    found: list[dict] = []

    for dist_info in site_packages.glob("*.dist-info"):
        direct_url = dist_info / "direct_url.json"
        if not direct_url.exists():
            continue
        try:
            data = json.loads(direct_url.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not data.get("dir_info", {}).get("editable"):
            continue
        url = data.get("url", "")
        if not url.startswith("file://"):
            continue
        try:
            target = Path(url[len("file://"):]).resolve()
        except OSError:
            continue
        stem = dist_info.name[: -len(".dist-info")]
        name, _, version = stem.rpartition("-") if "-" in stem else (stem, "", "")
        found.append({"target": target, "name": name or stem, "version": version, "dist_info": dist_info})

    for egg_link in site_packages.glob("*.egg-link"):
        try:
            first_line = egg_link.read_text().splitlines()[0].strip()
        except (OSError, IndexError):
            continue
        if not first_line:
            continue
        try:
            target = Path(first_line).resolve()
        except OSError:
            continue
        found.append({"target": target, "name": egg_link.stem, "version": "", "dist_info": None})

    return found


def _entry_points_from_dist_info(dist_info: Path | None) -> dict:
    if dist_info is None:
        return {}
    ep_file = dist_info / "entry_points.txt"
    if not ep_file.exists():
        return {}
    cp = configparser.ConfigParser()
    try:
        cp.read_string(ep_file.read_text())
    except (OSError, configparser.Error):
        return {}
    if not cp.has_section("console_scripts"):
        return {}
    return dict(cp.items("console_scripts"))


def _setup_cfg_metadata(cfg_path: Path) -> tuple[str, str]:
    cp = configparser.ConfigParser()
    try:
        cp.read(cfg_path)
    except (OSError, configparser.Error):
        return "", ""
    if not cp.has_section("metadata"):
        return "", ""
    return cp.get("metadata", "name", fallback=""), cp.get("metadata", "version", fallback="")


def _setup_cfg_console_scripts(cfg_path: Path) -> dict:
    cp = configparser.ConfigParser()
    try:
        cp.read(cfg_path)
    except (OSError, configparser.Error):
        return {}
    if not cp.has_section("options.entry_points"):
        return {}
    scripts: dict = {}
    for line in cp.get("options.entry_points", "console_scripts", fallback="").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, val = line.partition("=")
        scripts[key.strip()] = val.strip()
    return scripts


def _local_egg_info(d: Path) -> Path | None:
    matches = sorted(d.glob("*.egg-info"))
    return matches[0] if matches else None


def _read_pkg_info_name_version(pkg_info: Path) -> tuple[str, str]:
    name = version = ""
    try:
        for line in pkg_info.read_text(errors="replace").splitlines():
            if line.startswith("Name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Version:"):
                version = line.split(":", 1)[1].strip()
            if name and version:
                break
    except OSError:
        pass
    return name, version


def _pip_project_identity(d: Path) -> tuple[str, str, dict]:
    """Détermine (nom, version, points d'entrée déclarés) d'un projet pip
    'classique' à partir des descripteurs disponibles, par ordre de
    préférence : pyproject.toml [project], setup.cfg [metadata] /
    [options.entry_points], puis egg-info/PKG-INFO si le projet a déjà été
    construit/installé localement (`setup.py develop` / `pip install -e .`)."""
    name = version = ""
    scripts: dict = {}

    pyproject = d / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        project = data.get("project", {})
        name = project.get("name", "")
        version = project.get("version", "")
        scripts.update(project.get("scripts", {}) or {})
        scripts.update(project.get("gui-scripts", {}) or {})

    setup_cfg = d / "setup.cfg"
    if setup_cfg.exists():
        cfg_name, cfg_version = _setup_cfg_metadata(setup_cfg)
        name = name or cfg_name
        version = version or cfg_version
        if not scripts:
            scripts.update(_setup_cfg_console_scripts(setup_cfg))

    egg_info = _local_egg_info(d)
    if egg_info is not None:
        pkg_info = egg_info / "PKG-INFO"
        if pkg_info.exists():
            egg_name, egg_version = _read_pkg_info_name_version(pkg_info)
            name = name or egg_name
            version = version or egg_version
        if not scripts:
            scripts.update(_entry_points_from_dist_info(egg_info))

    return name or d.name, version, scripts


def find_pip_editable_projects(roots: list[Path], skip_dirs: set[str], max_depth: int = 6) -> list[dict]:
    """Cherche, sous les mêmes arborescences que les projets uv, les projets
    pip 'classiques' (setup.py/setup.cfg/pyproject.toml, sans gestion uv).
    Quand un venv — le leur ou celui d'un *autre* projet (dépendance locale
    partagée, p.ex. une bibliothèque maison réutilisée par plusieurs scripts)
    — contient réellement leur marqueur --editable, on récupère les infos
    précises (nom/version/points d'entrée) depuis cette install confirmée.
    Sinon, le projet est quand même listé tant qu'il présente un critère de
    bonne santé minimal : un descripteur de build exploitable et un nom
    résoluble (pyproject.toml [project], setup.cfg [metadata], ou egg-info
    s'il a déjà été construit) — c'est le signe qu'il est réellement
    installable, même si l'install actuelle a été faite ailleurs ou plus."""
    installed: dict[str, dict] = {}
    for venv_dir in _find_all_venvs(roots):
        site_packages = _site_packages_dir(venv_dir)
        if site_packages is None:
            continue
        for marker in _editable_markers_in(site_packages):
            target = marker["target"]
            if not target.is_dir():
                continue
            key = str(target)
            prev = installed.get(key)
            # Préfère le venv local du projet (si on en retrouve un), sinon le
            # premier venv (tiers) où l'éditable a été repéré.
            is_local = venv_dir.parent == target
            if prev is None or (is_local and not prev["_local"]):
                installed[key] = {
                    "venv_dir": venv_dir, "name": marker["name"],
                    "version": marker["version"], "dist_info": marker["dist_info"],
                    "_local": is_local,
                }

    entries: list[dict] = []
    roots_set = {str(r) for r in roots}
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, _filenames in os.walk(root):
            d = Path(dirpath)
            depth = len(d.relative_to(root).parts)
            dirnames[:] = [
                n for n in dirnames
                if n not in _PROJECT_SCAN_PRUNE and not (d / n / "pyvenv.cfg").exists()
            ]
            if depth >= max_depth:
                dirnames[:] = []
            if str(d) in roots_set or str(d) in skip_dirs or str(d) in seen or not _pip_project_health(d):
                continue

            confirmed = installed.get(str(d))
            if confirmed is None and _looks_like_thirdparty(d, roots):
                continue
            decl_name, decl_version, decl_scripts = _pip_project_identity(d)

            if confirmed:
                venv_dir = confirmed["venv_dir"]
                name = confirmed["name"] or decl_name
                version = confirmed["version"] or decl_version
                scripts = decl_scripts or _entry_points_from_dist_info(confirmed["dist_info"])
            else:
                local_venv = d / ".venv"
                venv_dir = local_venv if local_venv.exists() else None
                name, version, scripts = decl_name, decl_version, decl_scripts

            python = _venv_python_version(venv_dir) if venv_dir else ""

            if scripts:
                for script_name in scripts:
                    exe = (venv_dir / "bin" / script_name) if venv_dir else None
                    entries.append({
                        "type":     "tool",
                        "source":   "pip-editable",
                        "tool":     name,
                        "version":  version,
                        "python":   python,
                        "tool_dir": str(d),
                        "venv_dir": str(venv_dir) if venv_dir else str(d),
                        "name":     script_name,
                        "path":     str(exe) if exe and exe.exists() else "(non installé — pip install -e . requis)",
                        "is_root":  False,
                    })
            else:
                entries.append({
                    "type":     "tool",
                    "source":   "pip-editable",
                    "tool":     name,
                    "version":  version,
                    "python":   python,
                    "tool_dir": str(d),
                    "venv_dir": str(venv_dir) if venv_dir else str(d),
                    "name":     name,
                    "path":     str(d),
                    "is_root":  True,
                })

            seen.add(str(d))
            dirnames[:] = []

    # Dédupliquer les packages non-confirmés (venv_dir == tool_dir) de même
    # (nom, version) : une copie de travail dans un sous-dossier temporaire
    # (ex: Qt6/test_conversion/…) peut avoir un descripteur identique.
    # On garde l'occurrence la moins profonde par rapport à la racine de scan
    # la plus proche — c'est le plus souvent la version canonique dans aaa_modules.
    def _depth_from_roots(tool_dir: str) -> int:
        p = Path(tool_dir)
        best = 9999
        for r in roots:
            try:
                best = min(best, len(p.relative_to(r).parts))
            except ValueError:
                pass
        return best

    confirmed_dirs = {e["tool_dir"] for e in entries if e["venv_dir"] != e["tool_dir"]}
    seen_uc: dict[tuple[str, str], int] = {}   # (nom_lower, version) → index retenu
    to_keep: list[bool] = []
    for i, e in enumerate(entries):
        if e["tool_dir"] in confirmed_dirs:
            to_keep.append(True)
            continue
        key = (e["name"].lower(), e["version"])
        prev = seen_uc.get(key)
        if prev is None:
            seen_uc[key] = i
            to_keep.append(True)
        elif _depth_from_roots(e["tool_dir"]) < _depth_from_roots(entries[prev]["tool_dir"]):
            entries[prev]["_dup_reason"] = "copie"
            entries[prev]["_dup_canonical"] = e["tool_dir"]
            to_keep[prev] = False
            seen_uc[key] = i
            to_keep.append(True)
        else:
            e["_dup_reason"] = "copie"
            e["_dup_canonical"] = entries[seen_uc[key]]["tool_dir"]
            to_keep.append(False)
    kept = [e for e, k in zip(entries, to_keep) if k]
    dups = [e for e, k in zip(entries, to_keep) if not k]
    return kept, dups


def parse_uv_tools() -> tuple[list[dict], list[dict]]:
    """Combine les outils installés globalement (`uv tool install`), les
    projets uv trouvés sous UV_PROJECT_DIRS, et les projets pip --editable
    (même arborescence, hors dossiers déjà classés comme uv).
    Retourne (entrées_retenues, doublons_filtrés)."""
    uv_tool_entries = _parse_uv_tool_installs()
    project_entries = find_uv_projects(UV_PROJECT_DIRS)
    uv_project_dirs = {e["tool_dir"] for e in project_entries}
    pip_entries, pip_dups = find_pip_editable_projects(UV_PROJECT_DIRS, uv_project_dirs)

    # Déduplication cross-sources : un pip-editable non confirmé (pas de venv réel)
    # dont le nom de package coïncide avec un projet uv est redondant — la version
    # uv est la référence canonique.
    uv_names = {e["tool"].lower() for e in uv_tool_entries + project_entries}
    filtered_pip: list[dict] = []
    cross_dups: list[dict] = []
    for e in pip_entries:
        if e["venv_dir"] != e["tool_dir"] or e["tool"].lower() not in uv_names:
            filtered_pip.append(e)
        else:
            canon = next((x for x in uv_tool_entries + project_entries
                          if x["tool"].lower() == e["tool"].lower()), None)
            e["_dup_reason"] = "pip/uv"
            e["_dup_canonical"] = canon["tool_dir"] if canon else ""
            cross_dups.append(e)

    return uv_tool_entries + project_entries + filtered_pip, pip_dups + cross_dups


def tool_install_state(e: dict) -> str:
    """'uv' si le point d'entrée est bien dans le venv du projet/outil, sinon 'pip'."""
    if e.get("is_root"):
        return "pip" if e.get("source") == "pip-editable" else "uv"
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
        return "(pas de venv — install à faire)"

    names = sorted(p.name for p in site_packages.iterdir() if p.name not in _SITE_PKG_NOISE)
    pretty = [n + "/" if (site_packages / n).is_dir() else n for n in names]
    text = ", ".join(pretty) if pretty else "(vide)"
    if len(text) > limit:
        text = text[: limit - 1].rstrip(", ") + "…"
    return text


_TREE_NO_DESCEND = {"site-packages", "__pycache__", ".git", "node_modules",
                    ".mypy_cache", ".pytest_cache", ".ruff_cache"}


def render_dir_tree(root: Path, max_depth: int = 3, max_per_dir: int = 40) -> str:
    """Arbre ASCII du répertoire d'installation, bridé pour rester lisible
    (ne descend pas dans site-packages/__pycache__ etc., ni au-delà de max_depth)."""
    lines: list[str] = [str(root)]

    def walk(dir_: Path, prefix: str, depth: int) -> None:
        try:
            children = sorted(dir_.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        children = [c for c in children if c.name not in _SITE_PKG_NOISE]
        shown = children[:max_per_dir]
        hidden = len(children) - len(shown)
        for i, child in enumerate(shown):
            last = (i == len(shown) - 1) and hidden == 0
            branch = "└─ " if last else "├─ "
            label = child.name + ("/" if child.is_dir() else "")
            lines.append(f"{prefix}{branch}{label}")
            if child.is_dir() and depth < max_depth and child.name not in _TREE_NO_DESCEND:
                walk(child, prefix + ("   " if last else "│  "), depth + 1)
        if hidden > 0:
            lines.append(f"{prefix}└─ … ({hidden} de plus)")

    if root.is_dir():
        walk(root, "", 1)
    else:
        lines.append("(répertoire introuvable)")
    return "\n".join(lines)


# ─── README & schéma Mermaid ───────────────────────────────────────────────────

_README_NAMES = ("README.md", "README.rst", "README.txt", "README")
_DIAGRAM_PRUNE = _PROJECT_SCAN_PRUNE | {"tests", "test", "docs"}
_DIAGRAM_MAX_NODES = 35


def find_readme(d: Path) -> Path | None:
    """Cherche un fichier README (insensible à la casse) à la racine du projet."""
    if not d.is_dir():
        return None
    for name in _README_NAMES:
        p = d / name
        if p.exists():
            return p
    for child in d.iterdir():
        if child.is_file() and child.name.lower() in {n.lower() for n in _README_NAMES}:
            return child
    return None


def _find_package_root(tool_dir: Path, pkg_name: str) -> Path | None:
    """Localise le dossier représentant le package Python (layout src/ ou plat)."""
    safe = pkg_name.replace("-", "_")
    for cand in (tool_dir / "src" / safe, tool_dir / safe):
        if cand.is_dir() and any(cand.glob("*.py")):
            return cand
    if any(tool_dir.glob("*.py")):
        return tool_dir
    for child in sorted(tool_dir.iterdir()) if tool_dir.is_dir() else []:
        if child.is_dir() and (child / "__init__.py").exists():
            return child
    return None


def _iter_py_files(src_root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(src_root):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if n not in _DIAGRAM_PRUNE and not (d / n / "pyvenv.cfg").exists()]
        files.extend(d / f for f in filenames if f.endswith(".py"))
    return files


def _module_parts(path: Path, src_root: Path) -> tuple[str, ...]:
    """Chemin du module relatif à src_root, sous forme de tuple ('sous', 'module')."""
    parts = path.relative_to(src_root).with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return parts


def _collapse_node(parts: tuple[str, ...], pkg_root_name: str, depth: int = 2) -> str:
    """Résume un module à 'package' ou 'package.sous_module' (profondeur bridée)
    pour garder un schéma lisible plutôt qu'un graphe fichier par fichier."""
    full = (pkg_root_name,) + parts
    return ".".join(full[:depth])


def _local_imports_detail(path: Path, src_root: Path, pkg_root_name: str) -> dict[tuple[str, ...], set[str]]:
    """Modules internes au package importés par ce fichier (imports absolus
    préfixés par pkg_root_name, et imports relatifs résolus), avec pour
    chacun l'ensemble des symboles précis importés (vide si import du module
    entier, ex: 'import pkg.sous_module')."""
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (OSError, SyntaxError):
        return {}

    current_pkg = _module_parts(path, src_root)[:-1]
    results: dict[tuple[str, ...], set[str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                dotted = alias.name.split(".")
                if dotted[0] == pkg_root_name:
                    results.setdefault(tuple(dotted[1:]), set())
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                up = node.level - 1
                base = current_pkg[:-up] if up and up <= len(current_pkg) else current_pkg
                mod_parts = list(base)
                if node.module:
                    mod_parts += node.module.split(".")
                bucket = results.setdefault(tuple(mod_parts), set())
                bucket.update(alias.name for alias in node.names)
            elif node.module:
                dotted = node.module.split(".")
                if dotted[0] == pkg_root_name:
                    bucket = results.setdefault(tuple(dotted[1:]), set())
                    bucket.update(alias.name for alias in node.names)

    return results


def _diagram_graph(
    py_files: list[Path], src_root: Path, pkg_root_name: str
) -> tuple[set[str], set[tuple[str, str]], int, dict[tuple[str, str], set[str]]]:
    """Calcule le graphe (nœuds, arêtes, nb omis, symboles par arête) des
    dépendances internes du package : modules collapsés à 2 niveaux, et bridé
    à un nombre de nœuds raisonnable (les plus connectés, en priorité) pour
    rester lisible."""
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()
    edge_symbols: dict[tuple[str, str], set[str]] = {}

    for f in py_files:
        cur = _collapse_node(_module_parts(f, src_root), pkg_root_name)
        nodes.add(cur)
        for imp_parts, symbols in _local_imports_detail(f, src_root, pkg_root_name).items():
            imp = _collapse_node(imp_parts, pkg_root_name)
            nodes.add(imp)
            if imp != cur:
                edges.add((cur, imp))
                if symbols:
                    edge_symbols.setdefault((cur, imp), set()).update(symbols)

    omitted = 0
    if len(nodes) > _DIAGRAM_MAX_NODES:
        degree: dict[str, int] = {}
        for a, b in edges:
            degree[a] = degree.get(a, 0) + 1
            degree[b] = degree.get(b, 0) + 1
        keep = {n for n, _ in sorted(degree.items(), key=lambda kv: -kv[1])[:_DIAGRAM_MAX_NODES]}
        keep.add(pkg_root_name)
        omitted = len(nodes) - len(keep)
        nodes = keep
        edges = {(a, b) for a, b in edges if a in keep and b in keep}
        edge_symbols = {(a, b): s for (a, b), s in edge_symbols.items() if a in keep and b in keep}

    return nodes, edges, omitted, edge_symbols


def _mermaid_safe_id(n: str) -> str:
    return "n_" + re.sub(r"[^0-9A-Za-z_]", "_", n)


def _build_mermaid(py_files: list[Path], src_root: Path, pkg_root_name: str) -> str:
    """Construit un flowchart Mermaid résumé des dépendances internes du package."""
    nodes, edges, omitted, _edge_symbols = _diagram_graph(py_files, src_root, pkg_root_name)

    lines = ["flowchart TD"]
    for n in sorted(nodes):
        lines.append(f'    {_mermaid_safe_id(n)}["{n}"]')
    for a, b in sorted(edges):
        lines.append(f"    {_mermaid_safe_id(a)} --> {_mermaid_safe_id(b)}")
    if omitted > 0:
        lines.append(f'    note_more["… {omitted} module(s) supplémentaire(s) non affiché(s)"]')

    return "\n".join(lines)


def architecture_diagram(tool_dir: Path, pkg_name: str) -> str:
    """Construit (ou réutilise depuis le cache ARCHITECTURE.md à côté du
    README) un schéma Mermaid résumé de la structure interne du package —
    régénéré seulement si le code source est plus récent que le cache."""
    cache_file = tool_dir / "ARCHITECTURE.md"
    src_root = _find_package_root(tool_dir, pkg_name)
    if src_root is None:
        return "(aucun code source Python trouvé pour générer un schéma)"

    py_files = _iter_py_files(src_root)
    if not py_files:
        return "(aucun fichier .py trouvé pour générer un schéma)"

    newest = max((p.stat().st_mtime for p in py_files), default=0)
    if cache_file.exists() and cache_file.stat().st_mtime >= newest:
        try:
            return cache_file.read_text()
        except OSError:
            pass

    mermaid = _build_mermaid(py_files, src_root, src_root.name)
    content = (
        f"# Architecture — {pkg_name}\n\n"
        f"_Généré automatiquement depuis le code source ({datetime.now():%Y-%m-%d}) — "
        f"repasse par la touche **m** pour régénérer après modification._\n\n"
        f"```mermaid\n{mermaid}\n```\n"
    )
    try:
        cache_file.write_text(content)
    except OSError:
        pass
    return content


# ─── Rendu graphique (terminal & HTML) ─────────────────────────────────────────

def _format_module_structure(nodes: set[str], edges: set[tuple[str, str]], omitted: int,
                              edge_symbols: dict[tuple[str, str], set[str]] | None = None) -> str:
    """Représentation textuelle (liste d'adjacence indentée, façon `pipdeptree`)
    de la structure interne d'un package — bien plus lisible dans un terminal
    qu'un dessin ASCII/braille où les étiquettes finissent par se chevaucher."""
    if not nodes:
        return "(aucun module trouvé)"
    edge_symbols = edge_symbols or {}
    by_node: dict[str, list[str]] = {n: [] for n in nodes}
    for a, b in edges:
        by_node.setdefault(a, []).append(b)

    lines: list[str] = []
    for n in sorted(nodes):
        lines.append(n)
        for target in sorted(set(by_node.get(n, []))):
            symbols = sorted(edge_symbols.get((n, target), ()))
            detail = ""
            if symbols:
                shown = ", ".join(symbols[:_MAX_EDGE_SYMBOLS_SHOWN])
                extra = len(symbols) - _MAX_EDGE_SYMBOLS_SHOWN
                if extra > 0:
                    shown += f", … (+{extra})"
                detail = f"  ({shown})"
            lines.append(f"  └─ {target}{detail}")
    if omitted:
        lines.append(f"\n… {omitted} module(s) supplémentaire(s) non affiché(s) (les plus connectés sont prioritaires)")
    return "\n".join(lines)


_MAX_EDGE_SYMBOLS_SHOWN = 3


def _format_dependency_text(nodes: set[str], edges: set[tuple[str, str, str]],
                             labels: dict[str, str], omitted: int,
                             edge_symbols: dict[tuple[str, str], set[str]] | None = None) -> str:
    """Représentation textuelle (liste d'adjacence indentée) des dépendances
    entre packages — chaque package, suivi de ce dont il dépend, avec le
    signal détecté (import réel vs dépendance déclarée) et, pour les imports,
    les symboles/sous-modules précis importés."""
    if not nodes:
        return "(aucune dépendance détectée entre les packages)"
    edge_symbols = edge_symbols or {}
    by_node: dict[str, list[tuple[str, str]]] = {n: [] for n in nodes}
    for a, b, kind in edges:
        by_node.setdefault(a, []).append((b, kind))

    lines: list[str] = []
    for n in sorted(nodes, key=lambda x: labels.get(x, x).lower()):
        lines.append(labels.get(n, n))
        targets = sorted(set(by_node.get(n, [])), key=lambda tk: labels.get(tk[0], tk[0]).lower())
        for target, kind in targets:
            tag = "déclarée" if kind == "declared" else "import"
            detail = ""
            if kind == "import":
                symbols = sorted(edge_symbols.get((n, target), ()))
                if symbols:
                    shown = ", ".join(symbols[:_MAX_EDGE_SYMBOLS_SHOWN])
                    extra = len(symbols) - _MAX_EDGE_SYMBOLS_SHOWN
                    if extra > 0:
                        shown += f", … (+{extra})"
                    detail = f" : {shown}"
            lines.append(f"  → {labels.get(target, target)}  ({tag}{detail})")
    if omitted:
        lines.append(f"\n… {omitted} package(s) non affiché(s) (isolés ou moins connectés)")
    return "\n".join(lines)


def _render_graph_html(nodes: set[str] | list[str], edges: set[tuple[str, str, str]] | list[tuple[str, str, str]],
                        title: str, out_path: Path, labels: dict[str, str] | None = None,
                        edge_symbols: dict[tuple[str, str], set[str]] | None = None) -> Path:
    """Génère une page HTML autonome (vis-network via CDN) avec une mise en
    page physique réellement élastique/interactive (zoom, glisser-déposer) —
    le rendu 'comme en analyse web' demandé, ouvert dans le navigateur."""
    labels = labels or {}
    edge_symbols = edge_symbols or {}
    vis_nodes = [{"id": n, "label": labels.get(n, n)} for n in nodes]
    vis_edges = []
    for a, b, kind in edges:
        tooltip = "déclarée" if kind == "declared" else "import"
        if kind == "import":
            symbols = sorted(edge_symbols.get((a, b), ()))
            if symbols:
                tooltip = "import : " + ", ".join(symbols)
        tooltip = textwrap.fill(tooltip, width=50)
        vis_edges.append({
            "from": a, "to": b,
            "color": {"color": "#e67e22" if kind == "declared" else "#3498db"},
            "arrows": "to", "title": tooltip,
        })
    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  html, body {{ margin: 0; height: 100%; background: #1e1e1e; font-family: sans-serif; }}
  #title {{ color: #eee; padding: 8px 14px; font-size: 16px; }}
  #graph {{ width: 100%; height: calc(100% - 40px); }}
  div.vis-tooltip {{ white-space: pre-line !important; max-width: 480px; }}
</style>
</head>
<body>
<div id="title">{title} — bleu : import détecté, orange : dépendance déclarée</div>
<div id="graph"></div>
<script>
  const nodes = new vis.DataSet({json.dumps(vis_nodes)});
  const edges = new vis.DataSet({json.dumps(vis_edges)});
  const container = document.getElementById('graph');
  const data = {{ nodes, edges }};
  const options = {{
    nodes: {{ shape: 'dot', size: 12, font: {{ color: '#eee' }}, color: {{ background: '#2ecc71', border: '#27ae60' }} }},
    edges: {{ smooth: {{ type: 'dynamic' }} }},
    physics: {{ solver: 'forceAtlas2Based', forceAtlas2Based: {{ gravitationalConstant: -60, springLength: 120 }}, stabilization: {{ iterations: 200 }} }},
    interaction: {{ hover: true, tooltipDelay: 100 }}
  }};
  new vis.Network(container, data, options);
</script>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
    return out_path


# ─── Graphe global des dépendances entre packages ──────────────────────────────

def _norm_pkg_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _declared_dependency_names(tool_dir: Path) -> set[str]:
    """Noms de dépendances déclarées (pyproject.toml [project.dependencies] /
    [tool.uv.sources], ou setup.cfg [options] install_requires), normalisés."""
    names: set[str] = set()

    pyproject = tool_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        deps = (data.get("project", {}) or {}).get("dependencies", []) or []
        for dep in deps:
            m = re.match(r"[A-Za-z0-9_.\-]+", dep)
            if m:
                names.add(_norm_pkg_name(m.group(0)))
        uv_sources = ((data.get("tool", {}) or {}).get("uv", {}) or {}).get("sources", {}) or {}
        names.update(_norm_pkg_name(k) for k in uv_sources)

    setup_cfg = tool_dir / "setup.cfg"
    if setup_cfg.exists():
        cp = configparser.ConfigParser()
        try:
            cp.read(setup_cfg)
            raw = cp.get("options", "install_requires", fallback="")
            for line in raw.splitlines():
                m = re.match(r"\s*([A-Za-z0-9_.\-]+)", line)
                if m:
                    names.add(_norm_pkg_name(m.group(1)))
        except (OSError, configparser.Error):
            pass

    return names


def _external_imports_detail(tool_dir: Path) -> dict[str, set[str]]:
    """Premiers segments de tous les imports (hors stdlib filtré implicitement
    par l'absence de correspondance) trouvés dans les .py du projet, normalisés,
    avec pour chacun l'ensemble des symboles/sous-modules précis importés
    (ex: 'gen_exclude_libraries_to_compile' pour 'from Outils import ...')."""
    detail: dict[str, set[str]] = {}
    for f in _iter_py_files(tool_dir):
        try:
            tree = ast.parse(f.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    key = _norm_pkg_name(parts[0])
                    bucket = detail.setdefault(key, set())
                    if len(parts) > 1:
                        bucket.add(".".join(parts[1:]))
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mod_parts = node.module.split(".")
                key = _norm_pkg_name(mod_parts[0])
                rest = ".".join(mod_parts[1:])
                bucket = detail.setdefault(key, set())
                for alias in node.names:
                    bucket.add(f"{rest}.{alias.name}" if rest else alias.name)
    return detail


_DEP_GRAPH_MAX_NODES = 60


def build_dependency_graph(
    entries: list[dict],
) -> tuple[set[str], set[tuple[str, str, str]], dict[str, str], int, dict[tuple[str, str], set[str]]]:
    """Construit le graphe des dépendances entre TOUS les packages trouvés
    (uv-tool / projets uv / pip --editable), à partir de deux signaux :
    - imports détectés dans le code source référençant le nom (module ou
      distribution) d'un autre package du catalogue ;
    - dépendances déclarées (pyproject.toml / setup.cfg) qui correspondent au
      nom d'un autre package du catalogue.
    Retourne (nœuds, arêtes(src,dst,kind), labels{id: nom affiché}, nb omis,
    symboles{(src,dst): symboles/sous-modules importés précisément})."""
    packages: dict[str, dict] = {}
    for e in entries:
        tool_dir = e["tool_dir"]
        if tool_dir in packages:
            continue
        packages[tool_dir] = {"label": e["tool"], "source": e["source"]}

    # un même nom (python import vs nom de distribution) peut désigner le même package
    norm_to_id: dict[str, str] = {}
    for tool_dir, info in packages.items():
        d = Path(tool_dir)
        pkg_root = _find_package_root(d, info["label"])
        candidates = {_norm_pkg_name(info["label"])}
        if pkg_root is not None:
            candidates.add(_norm_pkg_name(pkg_root.name))
        for c in candidates:
            norm_to_id.setdefault(c, tool_dir)

    edges: set[tuple[str, str, str]] = set()
    edge_symbols: dict[tuple[str, str], set[str]] = {}

    for tool_dir, info in packages.items():
        d = Path(tool_dir)
        for imp, symbols in _external_imports_detail(d).items():
            target = norm_to_id.get(imp)
            if target and target != tool_dir:
                edges.add((tool_dir, target, "import"))
                edge_symbols.setdefault((tool_dir, target), set()).update(symbols)
        for dep in _declared_dependency_names(d):
            target = norm_to_id.get(dep)
            if target and target != tool_dir:
                edges.add((tool_dir, target, "declared"))

    # Un graphe de *dépendances* n'a d'intérêt que pour les packages reliés à
    # au moins un autre — les packages isolés (aucune dépendance détectée)
    # sont donc exclus par défaut plutôt que dispersés sans connexion.
    degree: dict[str, int] = {}
    for a, b, _ in edges:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    nodes: set[str] = set(degree)
    total_isolated = len(packages) - len(nodes)

    omitted = total_isolated
    if len(nodes) > _DEP_GRAPH_MAX_NODES:
        connected = sorted(degree.items(), key=lambda kv: -kv[1])
        keep = {n for n, _ in connected[:_DEP_GRAPH_MAX_NODES]}
        omitted += len(nodes) - len(keep)
        nodes = keep
        edges = {(a, b, k) for a, b, k in edges if a in keep and b in keep}
        edge_symbols = {(a, b): s for (a, b), s in edge_symbols.items() if a in keep and b in keep}

    labels = {tool_dir: packages[tool_dir]["label"] for tool_dir in nodes}
    return nodes, edges, labels, omitted, edge_symbols


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


# ─── Overlay : arbre du répertoire d'installation ──────────────────────────────

class DirTreeOverlay(ModalScreen):
    """Affiche l'arbre du répertoire d'un outil/projet par-dessus la liste."""

    BINDINGS = [
        Binding("t",      "dismiss_overlay", "Fermer", show=True),
        Binding("escape", "dismiss_overlay", "Fermer", show=True),
        Binding("q",      "dismiss_overlay", "Fermer", show=False),
    ]

    CSS = """
    DirTreeOverlay { align: center middle; }
    #tree-panel {
        width: 92%;
        height: 85%;
        border: thick $accent;
        background: $panel;
        padding: 1 2;
    }
    #tree-title { text-style: bold; color: $accent; padding-bottom: 1; }
    """

    def __init__(self, title: str, tree_text: str) -> None:
        super().__init__()
        self._title = title
        self._tree_text = tree_text

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="tree-panel"):
            yield Static(f"[bold bright_cyan]{self._title}[/bold bright_cyan]  "
                         f"[dim]( t / Échap pour fermer )[/dim]", id="tree-title")
            yield Static(self._tree_text)

    def action_dismiss_overlay(self) -> None:
        self.dismiss()


class ReadmeOverlay(ModalScreen):
    """Affiche le README d'un outil/projet par-dessus la liste."""

    BINDINGS = [
        Binding("d",      "dismiss_overlay", "Fermer", show=True),
        Binding("escape", "dismiss_overlay", "Fermer", show=True),
        Binding("q",      "dismiss_overlay", "Fermer", show=False),
    ]

    CSS = """
    ReadmeOverlay { align: center middle; }
    #readme-panel {
        width: 92%;
        height: 85%;
        border: thick $accent;
        background: $panel;
        padding: 1 2;
    }
    #readme-title { text-style: bold; color: $accent; padding-bottom: 1; }
    """

    def __init__(self, title: str, readme_text: str) -> None:
        super().__init__()
        self._title = title
        self._readme_text = readme_text

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="readme-panel"):
            yield Static(f"[bold bright_cyan]{self._title}[/bold bright_cyan]  "
                         f"[dim]( d / Échap pour fermer )[/dim]", id="readme-title")
            yield Static(self._readme_text)

    def action_dismiss_overlay(self) -> None:
        self.dismiss()


class SchemaOverlay(ModalScreen):
    """Affiche la structure interne d'un package par-dessus la liste."""

    BINDINGS = [
        Binding("m",      "dismiss_overlay", "Fermer", show=True),
        Binding("escape", "dismiss_overlay", "Fermer", show=True),
        Binding("q",      "dismiss_overlay", "Fermer", show=False),
    ]

    CSS = """
    SchemaOverlay { align: center middle; }
    #schema-panel {
        width: 92%;
        height: 85%;
        border: thick $accent;
        background: $panel;
        padding: 1 2;
    }
    #schema-title { text-style: bold; color: $accent; padding-bottom: 1; }
    """

    def __init__(self, title: str, body_text: str) -> None:
        super().__init__()
        self._title = title
        self._body_text = body_text

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="schema-panel"):
            yield Static(f"[bold bright_cyan]{self._title}[/bold bright_cyan]  "
                         f"[dim]( m / Échap pour fermer )[/dim]", id="schema-title")
            yield Static(self._body_text)

    def action_dismiss_overlay(self) -> None:
        self.dismiss()


class DepGraphOverlay(ModalScreen):
    """Affiche le graphe des dépendances entre packages par-dessus la liste."""

    BINDINGS = [
        Binding("g",      "dismiss_overlay", "Fermer", show=True),
        Binding("escape", "dismiss_overlay", "Fermer", show=True),
        Binding("q",      "dismiss_overlay", "Fermer", show=False),
    ]

    CSS = """
    DepGraphOverlay { align: center middle; }
    #depgraph-panel {
        width: 92%;
        height: 85%;
        border: thick $accent;
        background: $panel;
        padding: 1 2;
    }
    #depgraph-title { text-style: bold; color: $accent; padding-bottom: 1; }
    """

    def __init__(self, title: str, body_text: str) -> None:
        super().__init__()
        self._title = title
        self._body_text = body_text

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="depgraph-panel"):
            yield Static(f"[bold bright_cyan]{self._title}[/bold bright_cyan]  "
                         f"[dim]( g / Échap pour fermer )[/dim]", id="depgraph-title")
            yield Static(self._body_text)

    def action_dismiss_overlay(self) -> None:
        self.dismiss()


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
        Binding("t",          "toggle_view",   "Arbre",   show=True),
        Binding("s",          "toggle_detail", "Détails", show=True),
        Binding("d",          "show_doc",      "README", show=True),
        Binding("m",          "show_mermaid",      "Pkg (M:HTML)", show=True),
        Binding("M",          "show_mermaid_html", "Structure interne (HTML)", show=False),
        Binding("g",          "show_dep_graph_terminal", "Deps (G:HTML)", show=True),
        Binding("G",          "show_dep_graph_html",     "Deps entre packages (HTML)", show=False),
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
            with TabPane("  packages pip/uv  ", id="tab-tools"):
                yield DataTable(id="tools-table", zebra_stripes=True, cursor_type="row")
            with TabPane("  Duplicates  ", id="tab-dups"):
                yield DataTable(id="dups-table", zebra_stripes=True, cursor_type="row")
        yield Label("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._load_aliases()
        self._load_functions()
        self.tools_entries: list[dict] = []
        self.dups_entries:  list[dict] = []
        self._tool_rows: list[dict | None] = []
        self._dup_rows:  list[dict | None] = []
        self._tools_loaded = False
        self._tools_detail = False
        self._init_tools_placeholder()
        self._init_dups_placeholder()
        self.query_one("#alias-table", DataTable).focus()

    def _init_tools_placeholder(self) -> None:
        t = self.query_one("#tools-table", DataTable)
        t.clear(columns=True)
        t.add_columns("  ▶ Run  ", "  ? Man  ", "Outil", "Point d'entrée", "Python", "Chemin")
        t.add_row("", "", "[dim]Active cet onglet pour lancer la recherche (uv tool list)…[/dim]", "", "", "")

    def _init_dups_placeholder(self) -> None:
        t = self.query_one("#dups-table", DataTable)
        t.clear(columns=True)
        t.add_columns("Raison", "Outil", "Version", "Canonique", "Arborescence")
        t.add_row("[dim]Active l'onglet packages pip/uv pour charger…[/dim]", "", "", "", "")

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
        elif active == "tab-tools":
            self.query_one("#tools-table", DataTable).focus()
            if not self._tools_loaded:
                self._status("Recherche des outils installés via uv…")
                self._load_tools()
                self._tools_loaded = True
            n = len(self.tools_entries)
            self._status(
                f"{n} point(s) d'entrée  ·  [t] arbre  [s] détails  [d] README  ·  "
                f"[m]/[M] structure interne du package sélectionné (texte/HTML)  ·  "
                f"[g]/[G] dépendances ENTRE tous les packages (texte/HTML)"
            )
        elif active == "tab-dups":
            self.query_one("#dups-table", DataTable).focus()
            if not self._tools_loaded:
                self._status("Recherche des outils installés via uv…")
                self._load_tools()
                self._tools_loaded = True
            n = len(self.dups_entries)
            self._status(f"{n} doublon(s) filtré(s)  ·  [t] arbre du répertoire sélectionné")

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
        self.tools_entries, self.dups_entries = parse_uv_tools()
        self._render_tools_table()
        self._load_duplicates()

    _SOURCE_LABELS = {
        "uv-tool":      "uv tool install",
        "project":      "projets uv",
        "pip-editable": "pip --editable",
    }
    _SOURCE_TAGS = {"uv-tool": "uv tool", "project": "projet uv", "pip-editable": "pip -e"}

    def _render_tools_table(self) -> None:
        t = self.query_one("#tools-table", DataTable)
        t.clear(columns=True)
        detail = self._tools_detail
        self._tool_rows = []

        info1_h, info2_h = ("État", "Structure (site-packages)") if detail else ("Python", "Chemin")
        t.add_columns("  ▶ Run  ", "  ? Man  ", "Source", "Outil", "Point d'entrée", info1_h, info2_h)

        current_source = None
        for e in self.tools_entries:
            if e["source"] != current_source:
                current_source = e["source"]
                label = self._SOURCE_LABELS.get(current_source) or current_source or ""
                t.add_row(
                    "", "",
                    "[dim cyan]" + "─" * 8 + "[/dim cyan]",
                    f"[bold bright_cyan]  ◆  {label}[/bold bright_cyan]",
                    "", "", "",
                    key=f"tsh{len(self._tool_rows)}",
                )
                self._tool_rows.append(None)

            if detail:
                info1, info2 = tool_install_state(e), tool_structure_summary(e["venv_dir"])
            else:
                info1, info2 = e["python"], e["path"]
            source_cell = f"[dim]{self._SOURCE_TAGS.get(e['source'], e['source'])}[/dim]"
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

    # ── duplicates loader ────────────────────────────────────────────────────

    _DUP_REASON_LABELS = {
        "pip/uv": "pip non confirmé — version uv existe",
        "copie":  "Copie (chemin plus profond)",
    }

    def _load_duplicates(self) -> None:
        t = self.query_one("#dups-table", DataTable)
        t.clear(columns=True)
        t.add_columns("Raison", "Outil", "Version", "Canonique", "Arborescence")
        self._dup_rows = []

        by_reason: dict[str, list[dict]] = {}
        for e in self.dups_entries:
            by_reason.setdefault(e.get("_dup_reason", "?"), []).append(e)

        for reason_key in ("pip/uv", "copie"):
            group = by_reason.get(reason_key, [])
            if not group:
                continue
            sec_label = self._DUP_REASON_LABELS.get(reason_key, reason_key)
            t.add_row(
                f"[bold bright_cyan]  ◆  {sec_label}[/bold bright_cyan]",
                "[dim cyan]" + "─" * 38 + "[/dim cyan]",
                "", "", "",
                key=f"dsh{len(self._dup_rows)}",
            )
            self._dup_rows.append(None)
            for e in group:
                canon_name = os.path.basename(e.get("_dup_canonical", "")) or "—"
                root = Path(e["tool_dir"])
                tree_lines = render_dir_tree(root, max_depth=1, max_per_dir=10).splitlines()
                tree_lines[0] = root.name + "/"
                tree = "\n".join(tree_lines)
                t.add_row(
                    f"[dim]{sec_label}[/dim]",
                    f"[bold yellow]{e['tool']}[/bold yellow]",
                    f"[dim]{e.get('version') or '—'}[/dim]",
                    f"[dim cyan]{canon_name}[/dim cyan]",
                    f"[dim]{tree}[/dim]",
                    key=f"d{len(self._dup_rows)}",
                )
                self._dup_rows.append(e)

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

        if active == "tab-dups":
            t = self.query_one("#dups-table", DataTable)
            row = t.cursor_row
            if 0 <= row < len(self._dup_rows):
                return self._dup_rows[row]
            return None

        t = self.query_one("#tools-table", DataTable)
        row = t.cursor_row
        if 0 <= row < len(self._tool_rows):
            return self._tool_rows[row]
        return None

    # ── action : run ────────────────────────────────────────────────────────

    def action_run_entry(self) -> None:
        active = self._active_tab()
        if active in ("tab-tools", "tab-dups"):
            entry = self._current()
            if entry:
                self._open_file_manager(entry["tool_dir"])
            return
        entry = self._current()
        if not entry:
            return
        if entry["type"] == "alias":
            self._run_alias(entry)
        elif entry["type"] == "func":
            self._run_function(entry)
        else:
            self._run_tool(entry)

    def _open_file_manager(self, path: str) -> None:
        # dolphin appelé directement (pas via xdg-open) : il détecte sa propre
        # instance via D-Bus et ouvre dans la fenêtre existante plutôt qu'en créer
        # une nouvelle. stdout/stderr → DEVNULL pour éviter les messages sur le TUI.
        subprocess.Popen(
            ["dolphin", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._status(f"Explorateur ouvert : {path}")

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
        if self._active_tab() not in ("tab-tools", "tab-dups") or not self._tools_loaded:
            return
        entry = self._current()
        if not entry:
            self._status("Sélectionne un outil/projet pour voir son arbre d'installation")
            return
        target_dir = Path(entry["tool_dir"])
        title = f"◆ {entry['tool']}  —  {target_dir}"
        self.push_screen(DirTreeOverlay(title, render_dir_tree(target_dir)))

    def action_toggle_detail(self) -> None:
        if self._active_tab() != "tab-tools" or not self._tools_loaded:
            return
        self._tools_detail = not self._tools_detail
        self._render_tools_table()
        self._status("Détails : " + ("état + structure" if self._tools_detail else "python + chemin"))

    def action_show_doc(self) -> None:
        if self._active_tab() != "tab-tools" or not self._tools_loaded:
            return
        entry = self._current()
        if not entry:
            self._status("Sélectionne un outil/projet pour afficher son README")
            return
        target_dir = Path(entry["tool_dir"])
        readme = find_readme(target_dir)
        title = f"◆ {entry['tool']}  —  README"
        if readme is None:
            text = f"(aucun README trouvé dans {target_dir})"
        else:
            try:
                text = readme.read_text(errors="replace")
            except OSError:
                text = "(impossible de lire le fichier)"
        self.push_screen(ReadmeOverlay(title, text))

    def action_show_mermaid(self) -> None:
        if self._active_tab() != "tab-tools" or not self._tools_loaded:
            return
        entry = self._current()
        if not entry:
            self._status("Sélectionne un outil/projet pour voir sa structure interne")
            return
        target_dir = Path(entry["tool_dir"])
        # Tient à jour le cache ARCHITECTURE.md (texte Mermaid, pour GitHub/lecture
        # ultérieure) tout en affichant un résumé texte par-dessus la liste.
        architecture_diagram(target_dir, entry["tool"])
        src_root = _find_package_root(target_dir, entry["tool"])
        if src_root is None:
            self._status("Aucun code source Python trouvé pour générer un schéma")
            return
        py_files = _iter_py_files(src_root)
        nodes, edges, omitted, edge_symbols = _diagram_graph(py_files, src_root, src_root.name)
        body = _format_module_structure(nodes, edges, omitted, edge_symbols)
        title = f"◆ {entry['tool']}  —  structure interne ({len(nodes)} modules)"
        self.push_screen(SchemaOverlay(title, body))

    def action_show_mermaid_html(self) -> None:
        if self._active_tab() != "tab-tools" or not self._tools_loaded:
            return
        entry = self._current()
        if not entry:
            self._status("Sélectionne un outil/projet pour voir sa structure interne")
            return
        target_dir = Path(entry["tool_dir"])
        architecture_diagram(target_dir, entry["tool"])
        src_root = _find_package_root(target_dir, entry["tool"])
        if src_root is None:
            self._status("Aucun code source Python trouvé pour générer un schéma")
            return
        py_files = _iter_py_files(src_root)
        nodes, edges, omitted, edge_symbols = _diagram_graph(py_files, src_root, src_root.name)
        if not nodes:
            self._status("Aucun module trouvé pour générer un schéma")
            return
        slug = re.sub(r"[^0-9A-Za-z_-]+", "_", entry["tool"])[:40]
        digest = hashlib.sha1(str(target_dir).encode()).hexdigest()[:8]
        out_path = Path.home() / ".cache" / "aliases_menu" / f"architecture_{slug}_{digest}.html"
        title = f"Structure interne — {entry['tool']}" + (f"  ({omitted} non affiché(s))" if omitted else "")
        _render_graph_html(nodes, {(a, b, "import") for a, b in edges}, title, out_path,
                            edge_symbols={(a, b): s for (a, b), s in edge_symbols.items()})
        webbrowser.open(f"file://{out_path}")
        self._status(f"Structure HTML ouverte dans le navigateur · {out_path}")

    def action_show_dep_graph_terminal(self) -> None:
        if self._active_tab() != "tab-tools" or not self._tools_loaded:
            return
        self._status("Analyse des dépendances entre packages…")
        nodes, edges, labels, omitted, edge_symbols = build_dependency_graph(self.tools_entries)
        body = _format_dependency_text(nodes, edges, labels, omitted, edge_symbols)
        title = f"◆ Dépendances entre packages ({len(nodes)} packages, {len(edges)} liens)"
        self.push_screen(DepGraphOverlay(title, body))
        self._status(f"{len(nodes)} package(s), {len(edges)} dépendance(s) détectée(s)")

    def action_show_dep_graph_html(self) -> None:
        if self._active_tab() != "tab-tools" or not self._tools_loaded:
            return
        self._status("Analyse des dépendances entre packages…")
        nodes, edges, labels, omitted, edge_symbols = build_dependency_graph(self.tools_entries)
        if not nodes:
            self._status("Aucune dépendance détectée entre les packages")
            return
        out_path = Path.home() / ".cache" / "aliases_menu" / "dep_graph.html"
        title = f"Dépendances entre packages ({len(nodes)} packages, {omitted} non affiché(s))"
        _render_graph_html(nodes, edges, title, out_path, labels, edge_symbols)
        webbrowser.open(f"file://{out_path}")
        self._status(f"Graphe HTML ouvert dans le navigateur · {out_path}")

    # ── action : reload ─────────────────────────────────────────────────────

    def action_reload(self) -> None:
        self._load_aliases()
        self._load_functions()
        if self._tools_loaded:
            self._load_tools()


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    AliasMenu().run()
