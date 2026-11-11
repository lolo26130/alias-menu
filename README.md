# Alias Menu

Menu TUI façon DietPi pour parcourir et lancer les alias bash (`~/.bash_aliases`)
et les fonctions (`~/.bash_functions`), avec accès rapide au `man` / `--help`
de chaque commande.

![Textual](https://img.shields.io/badge/built%20with-Textual-blueviolet)

## Fonctionnalités

- Onglets **Alias**, **Fonctions**, **packages pip/uv**, **Duplicates** et **opencode**
- Sections visuelles regroupant les commandes (détectées automatiquement à partir
  des blocs `#===...===` dans les fichiers source)
- `Space` : lance l'alias / la fonction sélectionnée
  - pour une fonction qui attend des arguments, le prompt est pré-rempli
    avec son nom : tu n'as qu'à taper les arguments et valider
- `h` : affiche le `man` de la commande (saute automatiquement `sudo` en tête de
  commande) ou, pour une fonction, son corps source
- `r` : recharge les fichiers sans relancer l'appli

### Onglet opencode

- Liste toutes les sessions OpenCode du PC, regroupées par projet, avec agent,
  modèle, date de modification et coût ; les sessions en cours sont marquées `⏺`.
- `Space` : reprend la session sélectionnée dans son répertoire de travail.
- `s` : statistiques de la session (coût, tokens, derniers prompts, dernière réponse).
- `d` : affiche les documents de la session (`AGENTS.md`, `CLAUDE.md` existants,
  `opencode.json` / `opencode.jsonc` global et projet).
- `a` : uniquement `AGENTS.md` / `CLAUDE.md` ; `c` : uniquement la configuration.
- `t` : arbre du répertoire de la session.
- `x` : supprime la session après confirmation.
- `Ctrl+→` / `Ctrl+←` : change d'onglet (clic souris aussi possible)

## Installation / usage

Aucune installation nécessaire : c'est un script autonome
([PEP 723](https://peps.python.org/pep-0723/)) avec ses dépendances déclarées en
en-tête. [`uv`](https://docs.astral.sh/uv/) les installe à la volée.

```bash
uv run aliases_menu.py
```

Ou directement depuis GitHub, sans cloner :

```bash
uv run https://raw.githubusercontent.com/<user>/alias-menu/main/aliases_menu.py
```

### Lancer depuis n'importe où

Ajoute un alias dans `~/.bash_aliases` :

```bash
alias am='uv run /chemin/vers/aliases_menu.py'
```

## Prérequis

- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)

## Licence

MIT — voir [LICENSE](LICENSE).
