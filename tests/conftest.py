"""Fixtures pytest partagees, dont l'acces au harnais oracle R."""

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "tools"))

from oracle import oracle as _oracle  # noqa: E402


@pytest.fixture
def oracle():
    """Expose le module oracle (runner R + comparateurs)."""
    return _oracle


def _r_source_dir() -> Path:
    """Source R de reference, resolu comme les scripts tools/oracle/*.R."""
    if os.environ.get("SM_SOURCE_PATH"):
        return Path(os.environ["SM_SOURCE_PATH"])
    return _ROOT.parent / "source" / os.environ.get("SM_SOURCE", "shinymanager")


@pytest.fixture
def require_r(oracle):
    """Skip le test si Rscript ou le source R de reference n'est pas disponible."""
    if not oracle.r_available():
        pytest.skip("Rscript indisponible : test differentiel ignore")
    if not _r_source_dir().is_dir():
        pytest.skip(f"Source R introuvable ({_r_source_dir()}) : definir SM_SOURCE_PATH")
    return oracle
