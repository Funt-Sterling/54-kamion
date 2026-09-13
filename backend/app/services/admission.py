"""Semantic admission: which claims in a well-formed proposal are supported.

Runs *after* `proposal.parse_proposal`. The split matters: a malformed
envelope admits nothing, but one unsupported claim inside a valid envelope
rejects only itself and leaves independently valid observations standing.

Every rejection carries a reason, and those reasons are persisted on the
InferenceRun. "The model said so" is never a reason for admission — each
rule below requires the proposal to have shown its work.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from ..contract import (
    CATEGORY_TRACTOR_UNIT,
    EXTENT_PARTIAL,
    EXTENT_WHOLE,
    PROV_CANDIDATE,
    PROV_OBSERVED,
    READABILITY_READABLE,
    READING_TOTAL_ODOMETER,
    READING_YEAR_PLATE,
    VIEW_BADGE,
    VIEW_CAB,
    VIEW_CHASSIS,
    VIEW_DASHBOARD,
    VIEW_FRONT,
    VIEW_ODOMETER,
    VIEW_REAR,
    VIEW_SIDE,
    VIEW_TIRE,
    VISIBILITY_VISIBLE,
)
from .canonical import canonicalize, family_is_consistent_with_make
from .proposal import VisionProposal

MIN_PLAUSIBLE_YEAR = 1990
MAX_PLAUSIBLE_YEAR = 2030
MAX_PLAUSIBLE_MILEAGE_KM = 3_000_000


@dataclass(frozen=True)
class AdmittedView:
    view: str
    usable: bool
    limitation: str | None


@dataclass(frozen=True)
class AdmittedClaim:
    field: str
    raw_value: str
    canonical_value: str | None
    display_value: str
    provenance: str
    basis: dict


@dataclass(frozen=True)
class RejectedClaim:
    claim: str
    reason: str


@dataclass
class AdmissionResult:
    views: list[AdmittedView] = dc_field(default_factory=list)
    claims: list[AdmittedClaim] = dc_field(default_factory=list)
    rejected: list[RejectedClaim] = dc_field(default_factory=list)
    #: Localized visible condition only.
    findings: list[dict] = dc_field(default_factory=list)
    concerns: list[dict] = dc_field(default_factory=list)
    limitations: list[str] = dc_field(default_factory=list)

    def claim_for(self, field: str) -> AdmittedClaim | None:
        return next((claim for claim in self.claims if claim.field == field), None)


def _parse_strict_int(text: str) -> int | None:
    """Digits only, with spaces and thousands separators removed — but a
    decimal point is a hard reject, not something to strip.

    `365000.0` previously became 3,650,000 because `.` was deleted along
    with `.`-as-separator. Ambiguity here is a ten-fold error in a number
    that selects the price population, so it abstains instead.
    """
    cleaned = text.strip().replace(" ", "").replace(" ", "")
    if not cleaned:
        return None
    if "." in cleaned or "," in cleaned:
        # Could be a decimal point or a thousands separator; refuse to guess.
        stripped = cleaned.replace(".", "").replace(",", "")
        if not stripped.isdigit():
            return None
        # Only accept when every group is separator-consistent and 3-digit,
        # e.g. "365.000" — anything else (365000.0) is ambiguous.
        for sep in (".", ","):
            if sep in cleaned:
                head, *rest = cleaned.split(sep)
                if rest and all(len(part) == 3 for part in rest) and head.isdigit() and 1 <= len(head) <= 3:
                    continue
                return None
        return int(stripped)
    return int(cleaned) if cleaned.isdigit() else None


def admit(proposal: VisionProposal) -> AdmissionResult:
    result = AdmissionResult()
    result.limitations = list(proposal.image_limitations)

    _admit_views(proposal, result)
    _admit_category(proposal, result)
    _admit_identity(proposal, result)
    _admit_mileage(proposal, result)
    _admit_year(proposal, result)
    _reject_same_image_disagreement(result, ("mileage_km", "year"))
    _admit_axle_geometry(proposal, result)
    _admit_findings(proposal, result)
    return result


def _reject_same_image_disagreement(result: AdmissionResult, fields: tuple[str, ...]) -> None:
    """One image cannot establish two different values for one field.

    Two differing "total" odometer readings in a single frame mean at least
    one is wrong, and nothing here can tell which. Admitting both would let
    the later one silently retire the earlier as a "re-analysis"; instead
    both are rejected. Identical repeats collapse to a single claim.
    """
    for field in fields:
        claims = [claim for claim in result.claims if claim.field == field]
        values = {claim.canonical_value for claim in claims}
        if len(values) > 1:
            result.claims = [claim for claim in result.claims if claim.field != field]
            result.rejected.append(
                RejectedClaim(field, f"one image shows different readings ({', '.join(sorted(values))}); none admitted")
            )
        elif len(claims) > 1:
            first = claims[0]
            result.claims = [claim for claim in result.claims if claim.field != field or claim is first]


def _admit_views(proposal: VisionProposal, result: AdmissionResult) -> None:
    """A view is credited only when it is both visible and usable.

    Note what is deliberately absent: no inference between views. A tight
    odometer crop credits `odometer` and does NOT credit `dashboard`; the
    proposal must claim each one on its own.
    """
    for view, observation in proposal.views.items():
        if observation.visibility == VISIBILITY_VISIBLE and observation.usable:
            result.views.append(AdmittedView(view=view, usable=True, limitation=observation.limitation))
        else:
            result.rejected.append(
                RejectedClaim(
                    claim=f"view:{view}",
                    reason=f"not credited (visibility={observation.visibility}, usable={observation.usable})",
                )
            )


def _admit_category(proposal: VisionProposal, result: AdmissionResult) -> None:
    """Only a whole-vehicle view can establish that this is a tractor unit.

    A tire close-up may be perfectly good tire evidence while proving
    nothing about what the tire is attached to.
    """
    if proposal.subject_category != CATEGORY_TRACTOR_UNIT:
        result.rejected.append(
            RejectedClaim("vehicle_category", f"subject category is {proposal.subject_category!r}")
        )
        return
    if proposal.subject_extent != EXTENT_WHOLE:
        result.rejected.append(
            RejectedClaim(
                "vehicle_category",
                f"tractor category needs a whole-vehicle view; extent is {proposal.subject_extent!r}",
            )
        )
        return
    result.claims.append(
        AdmittedClaim(
            field="vehicle_category",
            raw_value=CATEGORY_TRACTOR_UNIT,
            canonical_value=CATEGORY_TRACTOR_UNIT,
            display_value="Tractor unit",
            provenance=PROV_OBSERVED,
            basis={"extent": proposal.subject_extent},
        )
    )


def _view_is_visible(proposal: VisionProposal, view: str) -> bool:
    observation = proposal.views.get(view)
    return observation is not None and observation.visibility == VISIBILITY_VISIBLE


def _badge_terms(raw: str) -> tuple[set[str], set[str]]:
    """Makes and families named by one badge reading.

    Exact registry aliases only. The whole text is tried first; if it names
    nothing, each word and each adjacent word pair is tried, so a grille
    reading "FORD TRUCKS F-MAX" yields make `ford` and family `f-max`.
    Nothing here is approximate: "F-MAXX" or "Actross" resolve to nothing.
    """
    makes: set[str] = set()
    families: set[str] = set()
    spans = [raw]
    tokens = raw.split()
    if len(tokens) > 1:
        spans += tokens + [" ".join(tokens[i : i + 2]) for i in range(len(tokens) - 1)]
    for index, span in enumerate(spans):
        make = canonicalize("make", span)
        family = canonicalize("model_family", span)
        if make.resolved:
            makes.add(make.canonical)
        if family.resolved:
            families.add(family.canonical)
        if index == 0 and (makes or families):
            break
    return makes, families


def _admit_identity(proposal: VisionProposal, result: AdmissionResult) -> None:
    """Readable model text mapped through the registry becomes an
    observation; appearance stays a candidate.

    The reproduced failure this blocks: badge text `SCANIA` with a model
    guess of `F-MAX` used to admit observed F-MAX. Here the badge supports
    only what it actually says, and a family whose make contradicts a
    readable make badge is rejected outright.
    """
    readable_badges = []
    for reading in proposal.readings:
        if reading.kind != "badge" or reading.readability != READABILITY_READABLE:
            continue
        supporting = reading.supporting_view or VIEW_BADGE
        if not _view_is_visible(proposal, supporting):
            # Text "read" off a view the proposal itself says is not visible
            # is not grounded in anything.
            result.rejected.append(
                RejectedClaim(f"badge:{reading.raw_text}", f"supporting view {supporting!r} is not visible")
            )
            continue
        readable_badges.append(reading)

    badge_makes: dict[str, str] = {}
    badge_families: dict[str, str] = {}
    for reading in readable_badges:
        makes, families = _badge_terms(reading.raw_text)
        for make in makes:
            badge_makes.setdefault(make, reading.raw_text)
        for family in families:
            badge_families.setdefault(family, reading.raw_text)

    if len(badge_makes) > 1:
        # Two manufacturers legible in one image: a mixed or composite scene.
        result.rejected.append(
            RejectedClaim("make", f"several makes are readable in one image ({', '.join(sorted(badge_makes))})")
        )
        observed_make = None
    else:
        observed_make = next(iter(badge_makes), None)

    if observed_make:
        result.claims.append(
            AdmittedClaim(
                field="make",
                raw_value=badge_makes[observed_make],
                canonical_value=observed_make,
                display_value=badge_makes[observed_make],
                provenance=PROV_OBSERVED,
                basis={"reading": "badge", "readability": READABILITY_READABLE},
            )
        )

    observed_families: set[str] = set()
    for family, raw in badge_families.items():
        if not family_is_consistent_with_make(family, observed_make):
            result.rejected.append(
                RejectedClaim(f"model_family:{raw}", f"family {family!r} contradicts readable make badge {observed_make!r}")
            )
            continue
        observed_families.add(family)
        result.claims.append(
            AdmittedClaim(
                field="model_family",
                raw_value=raw,
                canonical_value=family,
                display_value=raw,
                provenance=PROV_OBSERVED,
                basis={"reading": "badge", "readability": READABILITY_READABLE},
            )
        )

    for candidate in proposal.candidates:
        if candidate.field == "make":
            _admit_make_candidate(candidate, observed_make, result)
            continue
        if candidate.field != "model_family":
            continue
        label = f"model_family:{candidate.value}"
        resolved = canonicalize("model_family", candidate.value)

        if not resolved.resolved:
            result.rejected.append(RejectedClaim(label, "not a known model family in the registry"))
            continue
        if not family_is_consistent_with_make(resolved.canonical, observed_make):
            result.rejected.append(
                RejectedClaim(label, f"family {resolved.canonical!r} contradicts readable make badge {observed_make!r}")
            )
            continue
        if resolved.canonical in observed_families:
            continue  # already admitted from the readable badge itself
        if observed_families:
            result.rejected.append(
                RejectedClaim(label, "a different family is readable on the badge in this same image")
            )
            continue

        # Not supported by readable text in this image: appearance, or text
        # the proposal claims but did not report as a readable badge reading.
        result.claims.append(
            AdmittedClaim(
                field="model_family",
                raw_value=candidate.value,
                canonical_value=resolved.canonical,
                display_value=candidate.value,
                provenance=PROV_CANDIDATE,
                basis={"basis": candidate.basis, "badge_supported": False},
            )
        )


def _admit_make_candidate(candidate, observed_make: str | None, result: AdmissionResult) -> None:
    """A make not supported by a readable badge in this image stays a candidate."""
    label = f"make:{candidate.value}"
    resolved = canonicalize("make", candidate.value)
    if not resolved.resolved:
        result.rejected.append(RejectedClaim(label, "not a known make in the registry"))
        return
    if observed_make is not None:
        if resolved.canonical != observed_make:
            result.rejected.append(RejectedClaim(label, f"contradicts readable make badge {observed_make!r}"))
        return  # same as the observed make: nothing weaker to add
    result.claims.append(
        AdmittedClaim(
            field="make",
            raw_value=candidate.value,
            canonical_value=resolved.canonical,
            display_value=candidate.value,
            provenance=PROV_CANDIDATE,
            basis={"basis": candidate.basis, "badge_supported": False},
        )
    )


def _admit_mileage(proposal: VisionProposal, result: AdmissionResult) -> None:
    """Requires a readable TOTAL odometer with a known unit.

    Everything here is a precondition the old code had none of: the
    odometer view must itself be visible, the reading must be marked total
    (not a trip meter), the unit must be explicit, and the digits must
    parse without repair.
    """
    odometer_view = proposal.views.get(VIEW_ODOMETER)
    readings = [r for r in proposal.readings if r.kind == READING_TOTAL_ODOMETER]
    if not readings:
        return

    for reading in readings:
        label = f"mileage:{reading.raw_text}"
        if odometer_view is None or odometer_view.visibility != VISIBILITY_VISIBLE:
            result.rejected.append(RejectedClaim(label, "odometer view is not visible"))
            continue
        if reading.readability != READABILITY_READABLE:
            result.rejected.append(RejectedClaim(label, f"odometer readability is {reading.readability!r}"))
            continue
        if reading.is_total is not True:
            result.rejected.append(RejectedClaim(label, "reading is not asserted to be a total odometer"))
            continue
        if reading.unit is None:
            result.rejected.append(RejectedClaim(label, "no unit stated"))
            continue
        if reading.region is None:
            # The contract requires the reading be locatable, so a person can
            # check the digits against the image.
            result.rejected.append(RejectedClaim(label, "no image region for the reading"))
            continue

        value = _parse_strict_int(reading.raw_text)
        if value is None:
            result.rejected.append(RejectedClaim(label, "digits are ambiguous or non-numeric"))
            continue

        km = value if reading.unit == "km" else round(value * 1.609344)
        if not 0 < km <= MAX_PLAUSIBLE_MILEAGE_KM:
            result.rejected.append(RejectedClaim(label, f"{km} km is outside the plausible range"))
            continue

        result.claims.append(
            AdmittedClaim(
                field="mileage_km",
                raw_value=reading.raw_text,
                canonical_value=str(km),
                display_value=f"{km:,} km".replace(",", "."),
                provenance=PROV_OBSERVED,
                basis={
                    "reading": READING_TOTAL_ODOMETER,
                    "unit": reading.unit,
                    "converted_from": None if reading.unit == "km" else reading.unit,
                    "region": reading.region.__dict__ if reading.region else None,
                },
            )
        )


def _admit_year(proposal: VisionProposal, result: AdmissionResult) -> None:
    for reading in proposal.readings:
        if reading.kind != READING_YEAR_PLATE:
            continue
        label = f"year:{reading.raw_text}"
        if reading.readability != READABILITY_READABLE:
            result.rejected.append(RejectedClaim(label, f"readability is {reading.readability!r}"))
            continue
        value = _parse_strict_int(reading.raw_text)
        if value is None or not MIN_PLAUSIBLE_YEAR <= value <= MAX_PLAUSIBLE_YEAR:
            result.rejected.append(RejectedClaim(label, "not a plausible four-digit year"))
            continue
        result.claims.append(
            AdmittedClaim(
                field="year",
                raw_value=reading.raw_text,
                canonical_value=str(value),
                display_value=str(value),
                provenance=PROV_OBSERVED,
                basis={"reading": READING_YEAR_PLATE},
            )
        )


def _admit_axle_geometry(proposal: VisionProposal, result: AdmissionResult) -> None:
    """Counts axles. Deliberately does NOT conclude a drive configuration.

    Three visible axles are consistent with 6x2 and 6x4 alike, so the count
    is recorded as its own observation and the gate treats a declared
    layout as declared — compatible with the count, never proven by it.
    """
    geometry = proposal.axle_geometry
    if geometry is None or geometry.visible_axle_count is None:
        return
    if not geometry.whole_relevant_geometry_visible:
        result.rejected.append(
            RejectedClaim(
                f"visible_axle_count:{geometry.visible_axle_count}",
                "relevant geometry not wholly visible, so the count may be partial",
            )
        )
        return
    side = proposal.views.get(VIEW_SIDE)
    if side is None or side.visibility != VISIBILITY_VISIBLE or not side.usable:
        result.rejected.append(
            RejectedClaim(
                f"visible_axle_count:{geometry.visible_axle_count}",
                "no usable side view to count axles from",
            )
        )
        return
    result.claims.append(
        AdmittedClaim(
            field="visible_axle_count",
            raw_value=str(geometry.visible_axle_count),
            canonical_value=str(geometry.visible_axle_count),
            display_value=f"{geometry.visible_axle_count} axles visible",
            provenance=PROV_OBSERVED,
            basis={"supporting_view": geometry.supporting_view or VIEW_SIDE},
        )
    )


def _admit_findings(proposal: VisionProposal, result: AdmissionResult) -> None:
    """Only visible, localized condition becomes a finding.

    An 'obstructed' or 'unclear' observation is a limitation — recording it
    as a finding is how "we could not see the chassis" turns into "the
    chassis is fine".
    """
    for finding in proposal.findings:
        if finding.visibility != VISIBILITY_VISIBLE:
            result.limitations.append(f"{finding.component}: {finding.observation}")
            result.rejected.append(
                RejectedClaim(
                    f"finding:{finding.component}",
                    f"not a visible observation (visibility={finding.visibility})",
                )
            )
            continue
        result.findings.append(
            {
                "component": finding.component,
                "observation": finding.observation,
                "visibility": finding.visibility,
                "recommended_action": finding.recommended_action,
                "region": finding.region.__dict__ if finding.region else None,
            }
        )
        if finding.severity in ("structural", "clarify"):
            view = component_view(finding.component)
            result.concerns.append(
                {
                    # A contract view when the component maps to one, so a
                    # later photo of THAT view can address it. Unmapped text
                    # is kept verbatim and is never auto-resolved.
                    "component": view or finding.component,
                    "severity": finding.severity,
                    "description": f"{finding.component}: {finding.observation}",
                }
            )


#: Explicit keyword -> view table for free-text finding components. Ordered
#: most specific first. No fuzzy matching: an unrecognised component simply
#: maps to nothing.
_COMPONENT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("odometer", VIEW_ODOMETER),
    ("dashboard", VIEW_DASHBOARD),
    ("fifth wheel", VIEW_CHASSIS),
    ("chassis", VIEW_CHASSIS),
    ("frame", VIEW_CHASSIS),
    ("suspension", VIEW_CHASSIS),
    ("axle", VIEW_CHASSIS),
    ("tyre", VIEW_TIRE),
    ("tire", VIEW_TIRE),
    ("tread", VIEW_TIRE),
    ("sidewall", VIEW_TIRE),
    ("cab exterior", VIEW_FRONT),
    ("interior", VIEW_CAB),
    ("seat", VIEW_CAB),
    ("cab", VIEW_CAB),
    ("badge", VIEW_BADGE),
    ("rear", VIEW_REAR),
    ("grille", VIEW_FRONT),
    ("bumper", VIEW_FRONT),
    ("headlight", VIEW_FRONT),
    ("windshield", VIEW_FRONT),
    ("front", VIEW_FRONT),
    ("side", VIEW_SIDE),
    ("door", VIEW_SIDE),
    ("fuel tank", VIEW_SIDE),
)


def component_view(component: str) -> str | None:
    text = (component or "").lower().replace("_", " ").replace("/", " ")
    for keyword, view in _COMPONENT_KEYWORDS:
        if keyword in text:
            return view
    return None
