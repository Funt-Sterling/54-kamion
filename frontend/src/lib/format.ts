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
  FieldEvidence,
  FieldStatus,
  Finding,
  MediaResult,
} from "@/api/types";

/** Friendly labels for provenance — internal enum names never reach the UI. */
const STATUS_LABELS: Record<FieldStatus, string> = {
  observed_from_photo: "Seen in photo",
  seller_declared: "Seller provided",
  inferred_candidate: "Visual guess",
  confirmed: "Confirmed",
  conflicting: "Conflict",
  unknown: "Unknown",
};

export function provenanceLabel(status: FieldStatus): string {
  return STATUS_LABELS[status] ?? "Unknown";
}

export type StatusTone = "good" | "info" | "warn" | "muted";

export function statusTone(status: FieldStatus): StatusTone {
  switch (status) {
    case "confirmed":
      return "good";
    case "observed_from_photo":
      return "info";
    case "seller_declared":
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
  model_family: "Model family",
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
  return COMPONENT_LABELS[component] ?? humanize(component);
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
 * ("axle_config", "vehicle_identity"), but coverage is tracked by the
 * physical view a photo shows ("side_exterior"). Uploading with the raw
 * field as the component hint leaves coverage permanently empty — the
 * appraisal then claims the front was never photographed immediately
 * after the user photographed it. This maps request to view.
 */
const RESOLVES_TO_COMPONENT: Record<string, string> = {
  vehicle_identity: "front_exterior",
  model_family: "front_exterior",
  year: "front_exterior",
  axle_config: "side_exterior",
  mileage_km: "dashboard_odometer",
};

export function captureHintFor(resolves: string | undefined): string | undefined {
  if (!resolves) return undefined;
  if (COVERAGE_ORDER.includes(resolves)) return resolves;
  return RESOLVES_TO_COMPONENT[resolves];
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
      unknowns.push(
        `${fieldLabel(field.field)} disputed — seller says ${field.seller_declared ?? "—"}, photo shows ${field.observed_from_photo ?? "—"}`,
      );
    }
  }

  for (const component of COVERAGE_ORDER) {
    const state = coverage[component];
    if (state === "missing") {
      unknowns.push(`${componentLabel(component)} not photographed — condition not assessed`);
    } else if (state === "attention") {
      unknowns.push(`${componentLabel(component)} photo needs a retake — not yet assessed`);
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
