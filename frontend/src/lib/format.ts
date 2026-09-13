/**
 * Turning backend state into user-facing language.
 *
 * Everything here is a pure function so the rules that matter — never show
 * a price the backend didn't produce, never render "unknown" as if it were
 * a value, never call an unphotographed component "good" — are testable
 * rather than buried in JSX.
 */

import type {
  Appraisal,
  CoverageState,
  EvidenceParticipant,
  FieldEvidence,
  FieldStatus,
  Finding,
  MediaResult,
  ObservedView,
} from "@/api/types";
import { participants } from "@/lib/contract";

/** Friendly labels for provenance — internal enum names never reach the UI. */
const STATUS_LABELS: Record<FieldStatus, string> = {
  observed_from_photo: "Seen in photo",
  seller_declared: "Seller provided",
  inferred_candidate: "Visual guess",
  user_corrected: "User corrected",
  // Agreement between sources is corroboration, never verification.
  sources_agree: "Sources agree",
  confirmed: "Sources agree",
  conflicting: "Conflict",
  unknown: "Unknown",
};

export function provenanceLabel(status: FieldStatus): string {
  return STATUS_LABELS[status] ?? "Unknown";
}

/**
 * Label for one participant's source. Same vocabulary as the field status,
 * but reached from a raw string: a participant list can carry a provenance
 * this build has never heard of, and an unrecognised source must be shown
 * as unrecognised rather than silently dropped from a conflict.
 */
export function sourceLabel(provenance: string): string {
  return STATUS_LABELS[provenance as FieldStatus] ?? humanize(provenance);
}

/** Why a participant is no longer active. Blank when it still is. */
export function participantStateLabel(participant: EvidenceParticipant): string {
  if (participant.state === "superseded") return "Superseded";
  if (participant.state === "disputed") return "Disputed";
  if (participant.state === "legacy_unverified") return "Not verified (older analysis)";
  return "";
}

export type StatusTone = "good" | "info" | "warn" | "muted";

export function statusTone(status: FieldStatus): StatusTone {
  switch (status) {
    case "sources_agree":
    case "confirmed":
      return "good";
    case "observed_from_photo":
      return "info";
    case "seller_declared":
    case "user_corrected":
    case "inferred_candidate":
      return "warn";
    case "conflicting":
      return "warn";
    default:
      return "muted";
  }
}

const FIELD_LABELS: Record<string, string> = {
  vehicle_category: "Vehicle type",
  make: "Make",
  model_family: "Model family",
  visible_axle_count: "Visible axles",
  axle_config: "Axle configuration",
  year: "Model year",
  mileage_km: "Mileage",
  vat_basis: "VAT basis",
};

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? humanize(field);
}

const COMPONENT_LABELS: Record<string, string> = {
  front_exterior: "Front exterior",
  rear_exterior: "Rear exterior",
  side_exterior: "Side profile",
  tire: "Tires",
  chassis_suspension: "Chassis & suspension",
  dashboard_odometer: "Dashboard / odometer",
  cab_interior: "Cab interior",
  vehicle_identity: "Vehicle identity",
};

export function componentLabel(component: string): string {
  return COMPONENT_LABELS[component] ?? VIEW_LABELS[component] ?? humanize(component);
}

/**
 * The contract's view vocabulary (contract.ALL_VIEWS) — what a photo can
 * be found to SHOW, as opposed to the checklist component it may credit.
 * Kept separate from COMPONENT_LABELS so the UI can put "you were asked
 * for the front exterior" and "a tire was detected" side by side without
 * the two vocabularies blurring into one another.
 */
const VIEW_LABELS: Record<string, string> = {
  front: "Front",
  rear: "Rear",
  side: "Side",
  tire: "Tire",
  dashboard: "Dashboard",
  odometer: "Odometer",
  cab: "Cab interior",
  chassis: "Chassis",
  badge: "Badge / plate",
};

export function viewLabel(view: string): string {
  return VIEW_LABELS[view] ?? humanize(view);
}

/**
 * One detected view, with its qualification attached. "Tire (partly
 * visible)" is a different claim from "Tire", and an unusable view is not
 * a detection the user can rely on at all.
 */
export function observedViewLabel(view: ObservedView): string {
  const base = viewLabel(view.view);
  if (view.usable === false) {
    return view.limitation ? `${base} — not usable: ${view.limitation}` : `${base} — not usable`;
  }
  if (view.limitation) return `${base} — ${view.limitation}`;
  if (view.visibility === "unclear") return `${base} — unclear`;
  return base;
}

function humanize(value: string): string {
  const spaced = value.replace(/_/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Renders a field's value, or the reason there isn't one. A conflicting
 * field deliberately has no single value — surfacing one would be picking
 * a winner the backend refused to pick.
 */
export function displayValue(evidence: FieldEvidence): string {
  if (evidence.status === "conflicting") return "Conflicting";
  if (evidence.value === null || evidence.value === "") return "Unknown";
  if (evidence.field === "mileage_km") return formatMileage(evidence.value);
  if (evidence.field === "vat_basis") return formatVatBasis(evidence.value);
  return evidence.value;
}

export function formatMileage(value: string | number): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return `${numeric.toLocaleString("tr-TR")} km`;
}

function formatVatBasis(value: string): string {
  const map: Record<string, string> = {
    vat_included: "VAT included",
    vat_excluded: "VAT excluded",
    vat_exempt: "VAT exempt",
    unknown: "Not stated",
  };
  return map[value] ?? humanize(value);
}

/** Turkish lira, the only currency this system prices in today. */
export function formatTRY(amount: number): string {
  return `₺${Math.round(amount).toLocaleString("tr-TR")}`;
}

export function formatPrice(amount: number, currency: string): string {
  if (currency === "TRY") return formatTRY(amount);
  return `${Math.round(amount).toLocaleString("tr-TR")} ${currency}`;
}

/** Human-readable reason a photo was rejected by the backend. */
const REJECT_REASONS: Record<string, string> = {
  too_dark: "Too dark — the truck isn't legible.",
  too_blurry: "Too blurry — hold steady and retake.",
  resolution_too_low: "Resolution too low for inspection.",
  duplicate_of_existing_photo: "Duplicate of a photo already uploaded.",
  unreadable_file: "That file couldn't be opened as an image.",
  no_vehicle_detected: "No vehicle found in this image.",
};

export function rejectionMessage(media: MediaResult): string {
  const reason = media.reject_reason;
  if (!reason) return "Photo rejected.";
  if (reason.startsWith("not_a_tractor_unit")) {
    const detected = reason.split(":")[1];
    return detected && detected !== "unknown"
      ? `This looks like a ${humanize(detected).toLowerCase()}, not a tractor unit.`
      : "This isn't a tractor unit.";
  }
  return REJECT_REASONS[reason] ?? humanize(reason);
}

/**
 * True when the photo was stored but never actually analyzed — a failed
 * or pending vision call. The session survives; the photo just carries no
 * evidence yet, and must not be shown as if it had been inspected.
 */
export function needsReanalysis(media: MediaResult): boolean {
  return media.accepted && media.vision_status !== "ok";
}

export const COVERAGE_ORDER = [
  "front_exterior",
  "rear_exterior",
  "side_exterior",
  "tire",
  "chassis_suspension",
  "dashboard_odometer",
  "cab_interior",
];

/**
 * The gate answers with the *field* a photo would resolve
 * ("axle_config", "vehicle_identity"); the request the user sees is
 * phrased as a physical view ("side_exterior"). This maps one to the
 * other so the upload can say what was ASKED for.
 *
 * That is the whole of its job. It used to double as the thing that
 * decided which coverage slot a photo credited — the client naming the
 * slot, the server storing the name, the UI reading it back as though a
 * view had been detected. Coverage now comes only from the server's
 * observed views, so this value is request metadata and nothing else: the
 * server uses it solely to report afterwards whether the request was
 * answered, and no screen may present it as a detection.
 */
const REQUEST_SLOTS = new Set<string>([
  // Gate targets the server accepts as request slots directly, so a badge
  // close-up asked for as "model_family" is judged against the badge view
  // rather than a front exterior it was never meant to show.
  "vehicle_identity",
  "model_family",
  "year",
  "axle_config",
  "mileage_km",
  // Contract views, used by clarify-concern close-up requests.
  "front",
  "rear",
  "side",
  "dashboard",
  "odometer",
  "cab",
  "chassis",
  "badge",
]);

export function requestedComponentFor(resolves: string | undefined): string | undefined {
  if (!resolves) return undefined;
  if (COVERAGE_ORDER.includes(resolves) || REQUEST_SLOTS.has(resolves)) return resolves;
  return undefined;
}

/**
 * Names every source actually disagreeing, not just the seller/photo pair.
 * A field conflicted by an inferred candidate used to render as two
 * dashes, which told the reader a conflict existed while hiding what it
 * was between.
 */
export function describeConflict(evidence: FieldEvidence): string {
  const active = participants(evidence);
  if (active.length === 0) return "no source values recorded";
  return active
    .map((participant) => `${sourceLabel(participant.provenance)}: ${participant.display_value}`)
    .join(", ");
}

/**
 * What the system explicitly does NOT know, phrased so it can never be
 * mistaken for a clean bill of health. Unphotographed is not "fine".
 */
export function deriveUnknowns(
  coverage: Record<string, CoverageState>,
  evidence: FieldEvidence[],
): string[] {
  const unknowns: string[] = [];

  for (const field of evidence) {
    if (field.status === "unknown") {
      unknowns.push(`${fieldLabel(field.field)} not established from any source`);
    } else if (field.status === "conflicting") {
      unknowns.push(`${fieldLabel(field.field)} disputed — ${describeConflict(field)}`);
    }
  }

  for (const component of COVERAGE_ORDER) {
    const state = coverage[component];
    if (state === "missing") {
      unknowns.push(`${componentLabel(component)} not photographed — condition not assessed`);
    } else if (state === "attention") {
      // "attention" covers a partial view (an odometer crop is not the whole
      // dashboard) as well as a flagged one; neither means the photo failed.
      unknowns.push(`${componentLabel(component)} only partly assessed — a clearer or wider photo would help`);
    }
  }

  return unknowns;
}

/**
 * Findings the vision model could not actually assess. The backend marks
 * these with an obstructed view or a follow-up action; grouping them out
 * of "findings" keeps "couldn't see it" from reading as "inspected it".
 */
export function splitFindings(findings: Finding[]): { observed: Finding[]; limited: Finding[] } {
  const observed: Finding[] = [];
  const limited: Finding[] = [];
  for (const finding of findings) {
    const unseen =
      finding.visibility === "obstructed" ||
      /not visible|cannot assess|not fully|couldn't see/i.test(finding.observation);
    (unseen ? limited : observed).push(finding);
  }
  return { observed, limited };
}

/**
 * Backend reason strings are written for engineers and name fields by
 * their internal keys ("axle_config is not yet identified"). Swap those
 * for the labels used everywhere else in the UI before showing them.
 */
export function humanizeReason(reason: string): string {
  let text = reason;
  for (const [field, label] of Object.entries(FIELD_LABELS)) {
    // Only snake_case keys: plain words such as "year" or "make" are
    // already prose, and rewriting them produced "model model year".
    if (!field.includes("_")) continue;
    text = text.replace(new RegExp(`\\b${field}\\b`, "g"), label.toLowerCase());
  }
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Headline + body for every non-priced appraisal outcome. */
export function appraisalMessage(appraisal: Appraisal): { title: string; body: string } {
  switch (appraisal.status) {
    case "needs_evidence":
      return {
        title: "More evidence needed",
        body: appraisal.reasons[0]
          ? humanizeReason(appraisal.reasons[0])
          : "We need one more piece of evidence before we can price this truck.",
      };
    case "unsupported":
      return {
        title: "Not a supported vehicle",
        body: appraisal.reasons[0]
          ? humanizeReason(appraisal.reasons[0])
          : "We couldn't identify a supported tractor unit in these photos.",
      };
    case "insufficient_market_data":
      return {
        title: "Not enough comparable listings",
        body: appraisal.reasons[0]
          ? humanizeReason(appraisal.reasons[0])
          : "We don't have enough compatible Turkish listings to produce a defensible range.",
      };
    case "inspection_required":
      return {
        title: "In-person inspection recommended",
        body: appraisal.reasons[0]
          ? humanizeReason(appraisal.reasons[0])
          : "Possible structural damage was noted, so an individual price is withheld.",
      };
    default:
      return { title: "Appraisal", body: "" };
  }
}

/** Message for an unexpected/transport-level failure. */
export function errorMessage(error: unknown): string {
  if (error && typeof error === "object" && "offline" in error && (error as { offline: boolean }).offline) {
    return "Can't reach the TIRage backend. Check that it's running, then try again.";
  }
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong. Your inspection session is safe — try again.";
}
