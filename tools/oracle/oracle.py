"""Cote Python du harnais oracle : execute le runner R et compare aux sorties Python.

Le runner R (`run_r.R`) charge le package SOURCE (clone 1.1.0) et evalue des cas ; ce module
les rejoue et fournit des helpers de comparaison tolerante (NA/None, flottants).
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_RUN_R = _HERE / "run_r.R"


def r_available() -> bool:
    """Indique si Rscript est disponible sur la machine."""
    return shutil.which("Rscript") is not None


def run_r_cases(cases: list[dict[str, Any]], tmp_path: Path) -> dict[str, Any]:
    """Execute une liste de cas via le runner R et retourne le JSON parse.

    Args:
        cases: liste d'objets {"id", "fn", "args"}.
        tmp_path: repertoire temporaire pour les fichiers d'echange.

    Returns:
        Le dict {"loaded_from", "results"} produit par run_r.R.
    """
    cases_file = tmp_path / "cases.json"
    out_file = tmp_path / "out.json"
    cases_file.write_text(json.dumps(cases), encoding="utf-8")
    subprocess.run(
        ["Rscript", str(_RUN_R), str(cases_file), str(out_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out_file.read_text(encoding="utf-8"))


def run_r_script(
    script_name: str, tmp_path: Path, extra_args: list[str] | None = None
) -> dict[str, Any]:
    """Execute un script oracle dedie (ex. oracle_tokens.R) ecrivant un JSON, et le retourne.

    Args:
        script_name: nom du script dans tools/oracle/ (prend <out.json> en premier argument).
        tmp_path: repertoire temporaire pour le fichier de sortie.
        extra_args: arguments supplementaires passes au script apres <out.json>.

    Returns:
        Le JSON parse produit par le script.
    """
    out_file = tmp_path / "out.json"
    cmd = ["Rscript", str(_HERE / script_name), str(out_file), *(extra_args or [])]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(out_file.read_text(encoding="utf-8"))


def approx_equal(a: Any, b: Any, rel: float = 1e-9) -> bool:
    """Compare deux valeurs avec tolerance flottante et semantique NA/None alignee."""
    if a is None and b is None:
        return True
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(a, b, rel_tol=rel)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(approx_equal(x, y, rel) for x, y in zip(a, b, strict=False))
    return a == b
