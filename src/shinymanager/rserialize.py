"""Lecture / ecriture d'un data.frame au format binaire de `serialize()` de R (XDR).

Sert au stockage chiffre des bases SQLite, compatible avec le package R : `write_db_encrypt`
chiffre `serialize(table, NULL)` ; il faut donc savoir relire ET produire ce binaire.

Perimetre volontairement limite a ce que contiennent les tables shinymanager : un data.frame de
colonnes texte, logiques, entieres ou numeriques (+ facteurs, Date, POSIXct en lecture), avec
valeurs manquantes. Conversion vers Python, alignee sur le reste du paquet :
  - texte -> str ; logique -> "TRUE" / "FALSE" (convention des chaines de shinymanager) ;
  - entier -> int ; numerique -> int si entier, sinon float ; Date -> "YYYY-MM-DD" ;
    POSIXct -> "YYYY-MM-DD HH:MM:SS" (UTC) ; facteur -> son libelle ; NA -> None.
En ecriture : colonne de bool -> logique, de nombres -> numerique (double), sinon texte.

Reference du format : R Internals, section "Serialization Formats" (src/main/serialize.c).
"""

from __future__ import annotations

import math
import struct
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Types SEXP utilises.
_SYMSXP, _LISTSXP, _CHARSXP, _LGLSXP, _INTSXP, _REALSXP = 1, 2, 9, 10, 13, 14
_STRSXP, _VECSXP, _RAWSXP = 16, 19, 24
_ALTREP_SXP, _REFSXP, _NILVALUE_SXP = 238, 255, 254
# Pseudo-types sans contenu (environnements speciaux, valeurs marqueurs).
_MARKERS = {241, 242, 246, 247, 251, 252, 253}
_IS_OBJECT, _HAS_ATTR, _HAS_TAG = 1 << 8, 1 << 9, 1 << 10
_UTF8_MASK, _LATIN1_MASK, _ASCII_MASK = 1 << 3, 1 << 2, 1 << 6
_NA_INT = -(2**31)
_NA_REAL_BITS = 0x7FF00000000007A2
# Version d'ecriture : format 3 (defaut de R >= 3.5), lisible a partir de R 3.5.0.
_WRITER_R_VERSION = (4 << 16) | (3 << 8)
_MIN_READER_VERSION = (3 << 16) | (5 << 8)


class RSerializeError(ValueError):
    """Binaire R illisible ou hors du perimetre supporte."""


class _Sym:
    def __init__(self, name: str) -> None:
        self.name = name


class _Vec:
    """Vecteur R decode : type SEXP, valeurs Python, attributs {nom: valeur}."""

    def __init__(self, sexp_type: int, values: list[Any], attrs: dict[str, Any]) -> None:
        self.type = sexp_type
        self.values = values
        self.attrs = attrs


# --------------------------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------------------------


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.refs: list[Any] = []
        # Encodage natif de l'ecrivain (en-tete v3) : celui des chaines sans marque d'encodage.
        self.native = "utf-8"

    def _take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise RSerializeError("Unexpected end of R serialized data")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def int(self) -> int:
        return struct.unpack(">i", self._take(4))[0]

    def length(self) -> int:
        n = self.int()
        if n == -1:  # vecteur long : 2 entiers
            return (self.int() << 32) + self.int()
        return n

    def double(self) -> float | None:
        raw = self._take(8)
        value = struct.unpack(">d", raw)[0]
        return None if math.isnan(value) else value

    def item(self) -> Any:
        flags = self.int()
        sexp_type = flags & 0xFF
        levels = flags >> 12
        has_attr = bool(flags & _HAS_ATTR)
        has_tag = bool(flags & _HAS_TAG)

        if sexp_type == _NILVALUE_SXP or sexp_type in _MARKERS:
            return None
        if sexp_type == _REFSXP:
            index = flags >> 8
            if index == 0:
                index = self.int()
            return self.refs[index - 1]
        if sexp_type == _SYMSXP:
            sym = _Sym(self.item())
            self.refs.append(sym)
            return sym
        if sexp_type == _LISTSXP:
            attrs = self.item() if has_attr else None
            tag = self.item() if has_tag else None
            car = self.item()
            cdr = self.item()
            del attrs
            pairs = [(tag.name if isinstance(tag, _Sym) else None, car)]
            return pairs + (cdr if isinstance(cdr, list) else [])
        if sexp_type == _CHARSXP:
            n = self.int()
            if n == -1:
                return None
            raw = self._take(n)
            if levels & _LATIN1_MASK:
                return raw.decode("latin-1")
            if levels & (_UTF8_MASK | _ASCII_MASK):
                return raw.decode("utf-8", errors="replace")
            return _decode_native(raw, self.native)
        if sexp_type == _ALTREP_SXP:
            info = self.item()
            state = self.item()
            attrs = self.item()
            return self._altrep(info, state, attrs)

        if sexp_type in (_LGLSXP, _INTSXP):
            n = self.length()
            values: list[Any] = []
            for _ in range(n):
                v = self.int()
                values.append(None if v == _NA_INT else v)
        elif sexp_type == _REALSXP:
            values = [self.double() for _ in range(self.length())]
        elif sexp_type in (_STRSXP, _VECSXP):
            values = [self.item() for _ in range(self.length())]
        elif sexp_type == _RAWSXP:
            values = list(self._take(self.length()))
        else:
            raise RSerializeError(f"Unsupported R object type in serialized data: {sexp_type}")
        attrs = self._attrs(self.item()) if has_attr else {}
        return _Vec(sexp_type, values, attrs)

    @staticmethod
    def _attrs(pairlist: Any) -> dict[str, Any]:
        return dict(pairlist) if isinstance(pairlist, list) else {}

    def _altrep(self, info: Any, state: Any, attrs: Any) -> _Vec:
        """Reconstruit les representations ALTREP que R peut ecrire (sequences, wrappers...)."""
        cls = info[0][1].name if isinstance(info, list) and isinstance(info[0][1], _Sym) else ""
        attr_dict = self._attrs(attrs)
        if cls in ("compact_intseq", "compact_realseq"):
            n, start, step = (int(v) for v in state.values)
            seq = [start + i * step for i in range(n)]
            if cls == "compact_intseq":
                return _Vec(_INTSXP, seq, attr_dict)
            return _Vec(_REALSXP, [float(v) for v in seq], attr_dict)
        if cls.startswith("wrapper_"):
            inner = state[0][1] if isinstance(state, list) else state
            return _Vec(inner.type, inner.values, attr_dict or inner.attrs)
        if cls == "deferred_string":
            arg = state[0][1] if isinstance(state, list) else state
            values = [None if v is None else _r_number_to_string(v) for v in arg.values]
            return _Vec(_STRSXP, values, attr_dict)
        raise RSerializeError(f"Unsupported ALTREP class in serialized data: {cls!r}")


def _decode_native(raw: bytes, encoding: str) -> str:
    """Decode une chaine sans marque d'encodage (ex. base creee par un R Windows en CP1252)."""
    codec = {"latin1": "latin-1", "": "utf-8"}.get(encoding.lower(), encoding)
    try:
        return raw.decode(codec)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def _r_number_to_string(value: float | int) -> str:
    """Approximation de `as.character()` de R pour un nombre (15 chiffres significatifs)."""
    if isinstance(value, int) or (float(value).is_integer() and abs(value) < 1e15):
        return str(int(value))
    return f"{value:.15g}"


def _column_to_python(vec: _Vec) -> list[Any]:
    classes = _classes(vec)
    if vec.type == _STRSXP:
        return list(vec.values)
    if vec.type == _LGLSXP:
        return [None if v is None else ("TRUE" if v else "FALSE") for v in vec.values]
    if vec.type == _INTSXP:
        levels = vec.attrs.get("levels")
        if "factor" in classes and isinstance(levels, _Vec):
            return [None if v is None else levels.values[v - 1] for v in vec.values]
        return list(vec.values)
    if vec.type == _REALSXP:
        if "Date" in classes:
            epoch = date(1970, 1, 1)
            return [
                None if v is None else (epoch + timedelta(days=v)).isoformat() for v in vec.values
            ]
        if "POSIXct" in classes:
            return [
                None
                if v is None
                else datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                for v in vec.values
            ]
        return [None if v is None else (int(v) if float(v).is_integer() else v) for v in vec.values]
    raise RSerializeError(f"Unsupported column type in R data.frame: {vec.type}")


def _classes(vec: _Vec) -> list[str]:
    cls = vec.attrs.get("class")
    return list(cls.values) if isinstance(cls, _Vec) else []


def unserialize_data_frame(data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    """Decode le binaire `serialize()` d'un data.frame R.

    Returns:
        (noms des colonnes, lignes sous forme de dicts).

    Raises:
        RSerializeError: format non XDR, objet qui n'est pas un data.frame, type non supporte.
    """
    if data[:2] != b"X\n":
        raise RSerializeError("Only the R XDR serialization format is supported")
    reader = _Reader(data)
    reader.pos = 2
    version = reader.int()
    reader.int()  # version de R qui a ecrit
    reader.int()  # version minimale de lecture
    if version == 3:
        reader.native = reader._take(reader.int()).decode("ascii", errors="replace")
    elif version != 2:
        raise RSerializeError(f"Unsupported R serialization version: {version}")

    top = reader.item()
    if not isinstance(top, _Vec) or top.type != _VECSXP or "data.frame" not in _classes(top):
        raise RSerializeError("The serialized R object is not a data.frame")
    names_vec = top.attrs.get("names")
    names = list(names_vec.values) if isinstance(names_vec, _Vec) else []
    columns = [_column_to_python(col) for col in top.values]
    n_rows = len(columns[0]) if columns else 0
    rows = [{name: col[i] for name, col in zip(names, columns, strict=True)} for i in range(n_rows)]
    return names, rows


# --------------------------------------------------------------------------------------------
# Ecriture
# --------------------------------------------------------------------------------------------


class _Writer:
    def __init__(self) -> None:
        self.out = bytearray()

    def int(self, value: int) -> None:
        self.out += struct.pack(">i", value)

    def charsxp(self, value: str | None) -> None:
        if value is None:
            self.int(_CHARSXP)
            self.int(-1)
            return
        raw = value.encode("utf-8")
        levels = _ASCII_MASK if value.isascii() else _UTF8_MASK
        self.int(_CHARSXP | (levels << 12))
        self.int(len(raw))
        self.out += raw

    def strsxp(self, values: list[str | None]) -> None:
        self.int(_STRSXP)
        self.int(len(values))
        for v in values:
            self.charsxp(v)

    def column(self, values: list[Any]) -> None:
        present = [v for v in values if v is not None]
        if present and all(isinstance(v, bool) for v in present):
            self.int(_LGLSXP)
            self.int(len(values))
            for v in values:
                self.int(_NA_INT if v is None else int(v))
        elif present and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in present
        ):
            self.int(_REALSXP)
            self.int(len(values))
            for v in values:
                if v is None:
                    self.out += struct.pack(">Q", _NA_REAL_BITS)
                else:
                    self.out += struct.pack(">d", float(v))
        else:
            self.strsxp([None if v is None else _to_r_string(v) for v in values])

    def tagged(self, tag: str) -> None:
        """Debut d'un element de pairlist d'attributs avec son tag (symbole)."""
        self.int(_LISTSXP | _HAS_TAG)
        self.int(_SYMSXP)
        self.charsxp(tag)


def _to_r_string(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def serialize_data_frame(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    """Produit le binaire `serialize(x, NULL)` (XDR, version 3) d'un data.frame R."""
    w = _Writer()
    w.out += b"X\n"
    w.int(3)
    w.int(_WRITER_R_VERSION)
    w.int(_MIN_READER_VERSION)
    encoding = b"UTF-8"
    w.int(len(encoding))
    w.out += encoding

    w.int(_VECSXP | _IS_OBJECT | _HAS_ATTR)
    w.int(len(columns))
    for col in columns:
        w.column([row.get(col) for row in rows])
    # Attributs, dans l'ordre de R : names, class, row.names (forme compacte c(NA, -n)).
    w.tagged("names")
    w.strsxp(list(columns))
    w.tagged("class")
    w.strsxp(["data.frame"])
    w.tagged("row.names")
    w.int(_INTSXP)
    if rows:
        w.int(2)
        w.int(_NA_INT)
        w.int(-len(rows))
    else:
        w.int(0)  # data.frame vide : integer(0), comme R
    w.int(_NILVALUE_SXP)
    return bytes(w.out)
