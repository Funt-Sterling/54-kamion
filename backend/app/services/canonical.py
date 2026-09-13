"""One canonicalization registry, shared by ingestion, resolution and
market retrieval.

The audit found three separate ad-hoc normalizations: `values_agree()`
lowercased for comparison, `appraisals._canonical()` lowercased for lookup,
and the data pipeline lowercased at collection. They agreed by luck. When
they disagree, a truck is compared against the wrong population.

Rules that matter here:

* Aliases are **explicit and per field**. No fuzzy matching, no edit
  distance — `actros` and `atego` must never merge because they look alike.
* An unknown value stays unresolved (`canonical is None`) rather than being
  guessed at. Unresolved identity cannot select a pricing population.
* The raw reading is never destroyed; canonical is an additional key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: field -> {canonical: (accepted aliases, in any case/spacing)}
_REGISTRY: dict[str, dict[str, tuple[str, ...]]] = {
    "model_family": {
        "f-max": ("f-max", "fmax", "f max", "f_max"),
        "actros": ("actros",),
        "tgx": ("tgx",),
        "tgs": ("tgs",),
        "stralis": ("stralis",),
        "xf": ("xf",),
        "r-series": ("r-series", "r series", "rseries"),
    },
    "make": {
        "ford": ("ford", "ford trucks", "fordtrucks"),
        "mercedes-benz": ("mercedes-benz", "mercedes benz", "mercedes", "mercedesbenz"),
        "man": ("man",),
        "iveco": ("iveco",),
        "daf": ("daf",),
        "scania": ("scania",),
        "renault": ("renault", "renault trucks"),
        "volvo": ("volvo",),
    },
    "axle_config": {
        "4x2": ("4x2", "4×2", "4 x 2"),
        "6x2": ("6x2", "6×2", "6 x 2"),
        "6x4": ("6x4", "6×4", "6 x 4"),
        "8x4": ("8x4", "8×4", "8 x 4"),
    },
    "vat_basis": {
        "vat_included": ("vat_included", "vat included", "kdv dahil"),
        "vat_excluded": ("vat_excluded", "vat excluded", "kdv hariç", "kdv haric"),
        "vat_exempt": ("vat_exempt", "vat exempt"),
        "unknown": ("unknown", "not stated"),
    },
}

#: Which make each model family belongs to. Used to reject a badge that
#: names one manufacturer while the model guess belongs to another.
MODEL_FAMILY_MAKE: dict[str, str] = {
    "f-max": "ford",
    "actros": "mercedes-benz",
    "tgx": "man",
    "tgs": "man",
    "stralis": "iveco",
    "xf": "daf",
    "r-series": "scania",
}

def _normalize(text: str) -> str:
    """Case/space/punctuation-insensitive key for alias lookup only."""
    return re.sub(r"[\s_\-–—×]+", "", text.strip().lower())


#: field -> {normalized alias: canonical}
_ALIAS_INDEX: dict[str, dict[str, str]] = {
    field: {_normalize(alias): canonical for canonical, aliases in families.items() for alias in aliases}
    for field, families in _REGISTRY.items()
}


@dataclass(frozen=True)
class CanonicalValue:
    field: str
    raw: str
    canonical: str | None
    display: str

    @property
    def resolved(self) -> bool:
        return self.canonical is not None


def canonicalize(field: str, raw: str) -> CanonicalValue:
    """Map a raw reading onto the registry. Unknown values stay unresolved."""
    text = (raw or "").strip()
    if not text:
        return CanonicalValue(field, raw, None, raw)

    index = _ALIAS_INDEX.get(field)
    if index is None:
        # Field has no registry (year, mileage_km): canonical == raw text.
        return CanonicalValue(field, text, text, text)

    canonical = index.get(_normalize(text))
    return CanonicalValue(field, text, canonical, text)


def known_families() -> frozenset[str]:
    return frozenset(_REGISTRY["model_family"])


def make_for_family(family_canonical: str) -> str | None:
    return MODEL_FAMILY_MAKE.get(family_canonical)


def family_is_consistent_with_make(family_canonical: str, make_canonical: str | None) -> bool:
    """False only when both are known AND they disagree.

    A readable `SCANIA` badge therefore cannot support a model family of
    `f-max`; an unknown make leaves the question open rather than blocking.
    """
    if make_canonical is None:
        return True
    expected = MODEL_FAMILY_MAKE.get(family_canonical)
    if expected is None:
        return True
    return expected == make_canonical
