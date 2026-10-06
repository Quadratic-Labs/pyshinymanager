"""Fixtures pytest partagees, dont l'acces au harnais oracle R."""

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


@pytest.fixture
def require_r(oracle):
    """Skip le test si Rscript n'est pas disponible."""
    if not oracle.r_available():
        pytest.skip("Rscript indisponible : test differentiel ignore")
    return oracle
