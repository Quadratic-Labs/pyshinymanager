"""Internationalisation : dictionnaires de labels et selection de langue (source R/language.R).

Module 1 du plan. Fidelite PORTAGE_1_1 (donnees quasi-statiques). Les dictionnaires sont extraits
du package R source (1.1.1.1) via `tools/oracle/export_i18n.R` et embarques dans
`_data/labels.json`, garantissant une parite exacte. Les quirks du source sont REPRODUITS et non
corriges (decision gate G1/G2 "comportement source fait foi") :
  - les jeux de cles divergents entre langues sont conserves tels quels (ZF-2) ;
  - set_labels mute un etat global de processus (ZF-3), comme le pkgEnv de R.

ECART ASSUME (decision gate module 1) : le source ne fournissait pas de traduction DataTables
pour "zh-CN" (cle stockee sous "cn", jamais atteinte). On la restaure ici (voir export_i18n.R) :
get_DT("zh-CN") renvoie desormais la traduction chinoise au lieu de None.
"""

from __future__ import annotations

import json
import warnings
from importlib.resources import files

_DATA = json.loads(files("shinymanager").joinpath("_data/labels.json").read_text(encoding="utf-8"))

#: Codes de langue supportes, dans l'ordre du source.
_CODES: list[str] = _DATA["codes"]
#: Labels par langue. Etat global mutable de processus (reproduit pkgEnv, cf. ZF-3).
_LABELS: dict[str, dict[str, str]] = _DATA["labels"]
#: Labels DataTables par langue (12 langues ; zh-CN restaure, cf. ecart assume ci-dessus).
_DT: dict[str, dict[str, str]] = _DATA["dt"]
#: Code de langue pour shiny dateInput (identite pour les 12 langues).
_DATE_INPUT: dict[str, str] = _DATA["dateInput"]
#: Langues enregistrees : code -> nom d'affichage.
_REGISTERED: dict[str, str] = _DATA["registered"]


class Language:
    """Jeu de labels pour une langue donnee (equivalent de la classe R6 `language`)."""

    def __init__(self) -> None:
        """Initialise en anglais (comme le champ prive par defaut de la classe R6)."""
        self._language: str = "en"
        self._labels: dict[str, str] = _LABELS["en"]

    def set_language(self, lan: str) -> None:
        """Positionne la langue courante.

        Args:
            lan: code de langue (parmi les 11 supportes).

        Raises:
            ValueError: si `lan` n'est pas un code supporte (equivalent du stop R).
        """
        if lan not in _REGISTERED:
            raise ValueError("Unsupported language !")
        self._language = lan
        self._labels = _LABELS[lan]

    def get(self, label: str) -> str:
        """Retourne la traduction de `label`, ou `label` lui-meme si la cle est inconnue."""
        return self._labels.get(label, label)

    def get_all(self) -> dict[str, str]:
        """Retourne tous les labels de la langue courante."""
        return self._labels

    def get_DT(self) -> dict[str, str] | None:  # noqa: N802 (nom du source R conserve)
        """Retourne les labels DataTables de la langue courante, ou None si absents."""
        return _DT.get(self._language)

    def get_dateInput(self) -> str | None:  # noqa: N802 (nom du source R conserve)
        """Retourne le code de langue pour shiny dateInput."""
        return _DATE_INPUT.get(self._language)

    def get_language_registered(self) -> dict[str, str]:
        """Retourne les langues enregistrees (code -> nom d'affichage)."""
        return _REGISTERED

    def get_language(self) -> str:
        """Retourne le code de la langue courante."""
        return self._language


def use_language(lan: str = "en") -> Language:
    """Charge et retourne les labels de la langue `lan`.

    Args:
        lan: code de langue (defaut "en").

    Returns:
        Une instance `Language` positionnee sur `lan`.

    Raises:
        ValueError: si `lan` n'est pas un code supporte.
    """
    language = Language()
    language.set_language(lan)
    return language


def set_labels(language: str, *args: dict[str, str], **labels: str) -> bool:
    """Surcharge des labels pour une langue (etat global, equivalent de set_labels R).

    Accepte soit des arguments nommes, soit un unique dict en premier argument positionnel
    (equivalent de la forme "liste nommee" du source).

    Args:
        language: code de langue a modifier.
        *args: optionnellement un unique dict {cle: traduction}.
        **labels: paires cle=traduction.

    Returns:
        True (comme le `invisible(TRUE)` du source).

    Raises:
        ValueError: si `language` est inconnu, ou si un dict positionnel et des kwargs sont
            fournis en meme temps.
    """
    if language not in _REGISTERED:
        raise ValueError("Unsupported language !")
    if args:
        if len(args) > 1 or labels:
            raise ValueError("Provide either a single dict or named labels, not both.")
        mapping = args[0]
    else:
        mapping = labels
    _LABELS[language].update(mapping)
    return True


def get_labels(language: str = "en") -> dict[str, str]:
    """Retourne les labels d'une langue.

    Args:
        language: code de langue (defaut "en").

    Returns:
        Le dictionnaire de labels. Si `language` est inconnu, emet un warning et retombe sur
        l'anglais (comportement du source : warning, pas erreur).
    """
    if language not in _REGISTERED:
        warnings.warn(f"Unsupported language '{language}', falling back to 'en'.", stacklevel=2)
        return _LABELS["en"]
    return _LABELS[language]
