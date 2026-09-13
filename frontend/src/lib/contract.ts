/**
 * Defensive readers for the evidence contract.
 *
 * Everything the backend gained in the contract rework (gate verdict,
 * observed views, participants, revisions, limitations, concerns) is read
 * through this module rather than off the response object directly. Two
 * reasons:
 *
 *  1. A field that is absent, null, or the wrong shape — an older server,
 *     a partial deploy, a proxy that drops keys — must degrade to "we
 *     don't know". It must never crash a screen and it must never be
 *     rounded up into a confident claim.
 *  2. The rules that matter (readiness comes from the gate; a range
 *     computed at an older revision is stale; a request is not a
 *     detection) are pure functions here, so they are testable instead of
 *     being scattered through JSX.
 */

import type {
  Appraisal,
  CaptureOrigin,
  Concern,
  EvidenceParticipant,
  FieldEvidence,
  GateStatus,
  MediaResult,
  ObservedView,
  SessionDetail,
} from "@/api/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function stringsOf(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim() !== "");
}

/* -------------------------------------------------------------------------
 * Capture origin — reported, never proof
 * ---------------------------------------------------------------------- */

const ORIGINS: readonly CaptureOrigin[] = ["camera", "gallery", "unknown"];

export function captureOrigin(media: Partial<MediaResult> | null | undefined): CaptureOrigin {
  const raw = media?.capture_origin;
  return ORIGINS.includes(raw as CaptureOrigin) ? (raw as CaptureOrigin) : "unknown";
}

/**
 * Wording is deliberately about where the FILE came from. "Camera" means
 * this browser opened a camera intent — not that the photo is of this
 * vehicle, taken today, or unedited.
 */
const ORIGIN_LABELS: Record<CaptureOrigin, string> = {
  camera: "Reported origin: camera in this app",
  gallery: "Reported origin: existing file from this device",
  unknown: "Reported origin: not stated",
};

export function captureOriginLabel(origin: CaptureOrigin): string {
  return ORIGIN_LABELS[origin] ?? ORIGIN_LABELS.unknown;
}

/* -------------------------------------------------------------------------
 * Requested view vs observed views
 * ---------------------------------------------------------------------- */

export function observedViews(media: Partial<MediaResult> | null | undefined): ObservedView[] {
  const raw = media?.observed_views;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (view): view is ObservedView => isRecord(view) && typeof (view as ObservedView).view === "string",
  );
}

/** Only views the server marked usable can stand for anything. */
export function usableViews(media: Partial<MediaResult> | null | undefined): ObservedView[] {
  return observedViews(media).filter((view) => view.usable === true);
}

/** The view the client ASKED for. Request metadata — never a detection. */
export function requestedView(media: Partial<MediaResult> | null | undefined): string | null {
  const raw = media?.requested_view;
  return typeof raw === "string" && raw.trim() !== "" ? raw : null;
}

/** null when nothing specific was requested, or the server didn't say. */
export function requestedViewSatisfied(media: Partial<MediaResult> | null | undefined): boolean | null {
  const raw = media?.requested_view_satisfied;
  return typeof raw === "boolean" ? raw : null;
}

/**
 * True when the photo was analyzed successfully and supported no usable
 * view at all. An empty `observed_views` after a good analysis is
 * meaningful — it must be said out loud, not rendered as a blank space
 * that reads like success.
 */
export function analyzedWithNoUsableView(media: Partial<MediaResult> | null | undefined): boolean {
  if (!media) return false;
  if (media.vision_status !== "ok") return false;
  if (media.accepted !== true) return false;
  return usableViews(media).length === 0;
}

/* -------------------------------------------------------------------------
 * Participants in a field's evidence
 * ---------------------------------------------------------------------- */

function participantsFrom(value: unknown): EvidenceParticipant[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is EvidenceParticipant =>
      isRecord(item) && typeof (item as unknown as EvidenceParticipant).provenance === "string",
  );
}

/**
 * Active sources for a field.
 *
 * Falls back to the legacy seller/photo pair when a server hasn't sent
 * `participants` yet, so a conflict still shows who is disagreeing rather
 * than two dashes. The fallback cannot invent the inferred candidates the
 * old UI dropped — it only reconstructs what the old shape carried.
 */
export function participants(evidence: FieldEvidence): EvidenceParticipant[] {
  const listed = participantsFrom(evidence.participants);
  if (listed.length > 0) return listed;

  const rebuilt: EvidenceParticipant[] = [];
  const add = (provenance: string, value: string | null) => {
    if (value === null || value === "") return;
    rebuilt.push({
      provenance,
      raw_value: value,
      canonical_value: null,
      display_value: value,
      media_id: provenance === "observed_from_photo" ? evidence.supporting_media_id : null,
      state: "active",
      state_reason: null,
    });
  };
  add("seller_declared", evidence.seller_declared);
  add("observed_from_photo", evidence.observed_from_photo);
  for (const candidate of stringsOf(evidence.inferred_candidates)) {
    add("inferred_candidate", candidate);
  }
  return rebuilt;
}

/** History: superseded or disputed claims, kept rather than deleted. */
export function supersededParticipants(evidence: FieldEvidence): EvidenceParticipant[] {
  return participantsFrom(evidence.superseded);
}

/* -------------------------------------------------------------------------
 * Session-level additions
 * ---------------------------------------------------------------------- */

const GATE_STATUSES: readonly GateStatus[] = [
  "ready_to_price",
  "needs_evidence",
  "unsupported",
  "inspection_required",
  "insufficient_market_data",
];

/**
 * The gate's own verdict. An unrecognised or missing value falls back to
 * "needs_evidence": the conservative direction, because the failure mode
 * that matters is showing "ready to price" when nothing said so.
 */
export function gateStatus(session: Pick<SessionDetail, "gate_status"> | null | undefined): GateStatus {
  const raw = session?.gate_status;
  return GATE_STATUSES.includes(raw as GateStatus) ? (raw as GateStatus) : "needs_evidence";
}

export function gateReasons(session: Pick<SessionDetail, "gate_reasons"> | null | undefined): string[] {
  return stringsOf(session?.gate_reasons);
}

export function limitations(session: Pick<SessionDetail, "limitations"> | null | undefined): string[] {
  return stringsOf(session?.limitations);
}

/** Concerns still needing an answer. Resolved ones are not open questions. */
export function openConcerns(session: Pick<SessionDetail, "concerns"> | null | undefined): Concern[] {
  const raw = session?.concerns;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (concern): concern is Concern =>
      isRecord(concern) && typeof concern.description === "string" && concern.status !== "resolved",
  );
}

/* -------------------------------------------------------------------------
 * Gate → user-facing state
 * ---------------------------------------------------------------------- */

export interface GateView {
  title: string;
  body: string;
  tone: "ready" | "working" | "blocked";
  /** Whether another photo can move this state at all. */
  acceptsMorePhotos: boolean;
}

const GATE_VIEWS: Record<GateStatus, GateView> = {
  ready_to_price: {
    title: "Ready for a provisional range",
    body: "The gate has the inputs it needs to price. More photos still sharpen the condition report.",
    tone: "ready",
    acceptsMorePhotos: true,
  },
  needs_evidence: {
    title: "More evidence needed",
    body: "Something required for pricing is still unestablished.",
    tone: "working",
    acceptsMorePhotos: true,
  },
  unsupported: {
    title: "No supported tractor unit established",
    body: "Nothing admitted so far supports a tractor unit, so there is no range and no truck coverage. Another photo of the vehicle can still change this.",
    tone: "blocked",
    acceptsMorePhotos: true,
  },
  inspection_required: {
    title: "In-person inspection needed",
    body: "Possible structural damage was noted. No individual price is shown until that is settled; a clearer photo of the area is the only thing that can resolve it here.",
    tone: "blocked",
    acceptsMorePhotos: true,
  },
  insufficient_market_data: {
    title: "Too few compatible Turkish listings",
    body: "The inspection can continue, but there are not enough compatible Turkish listings for a defensible range. More photos cannot fix this.",
    tone: "blocked",
    acceptsMorePhotos: false,
  },
};

export function gateView(status: GateStatus): GateView {
  return GATE_VIEWS[status] ?? GATE_VIEWS.needs_evidence;
}

/** Only the gate may say the session is ready. Never `next_photo == null`. */
export function isReadyToPrice(session: Pick<SessionDetail, "gate_status"> | null | undefined): boolean {
  return gateStatus(session) === "ready_to_price";
}

/* -------------------------------------------------------------------------
 * Evidence revision / range staleness
 * ---------------------------------------------------------------------- */

function revisionOf(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * "unknown" is a real answer: when either side doesn't report a usable
 * revision we cannot tell whether the range still matches the evidence,
 * so the UI says nothing about currency rather than claiming it.
 */
export type RangeFreshness = "current" | "stale" | "unknown";

export function rangeFreshness(
  appraisal: Pick<Appraisal, "evidence_revision"> | null | undefined,
  session: Pick<SessionDetail, "evidence_revision"> | null | undefined,
): RangeFreshness {
  const priced = revisionOf(appraisal?.evidence_revision);
  const current = revisionOf(session?.evidence_revision);
  if (priced === null || current === null) return "unknown";
  return priced === current ? "current" : "stale";
}

export function isRangeStale(
  appraisal: Pick<Appraisal, "evidence_revision"> | null | undefined,
  session: Pick<SessionDetail, "evidence_revision"> | null | undefined,
): boolean {
  return rangeFreshness(appraisal, session) === "stale";
}

/* -------------------------------------------------------------------------
 * Response normalisation at the client boundary
 * ---------------------------------------------------------------------- */

function arrayOr<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function recordOr<T>(value: unknown): Record<string, T> {
  return isRecord(value) && !Array.isArray(value) ? (value as Record<string, T>) : {};
}

/**
 * A proxy that drops a key or an older server must not crash a screen.
 * Collections default to empty (nothing known), never to a guess.
 */
export function normalizeSession(raw: SessionDetail): SessionDetail {
  return {
    ...raw,
    coverage: recordOr(raw?.coverage),
    evidence: arrayOr<FieldEvidence>(raw?.evidence).filter((e) => isRecord(e) && typeof e.field === "string"),
    findings: arrayOr(raw?.findings),
    concerns: arrayOr(raw?.concerns),
    limitations: stringsOf(raw?.limitations),
    gate_reasons: stringsOf(raw?.gate_reasons),
  };
}

export function normalizeAppraisal(raw: Appraisal): Appraisal {
  return {
    ...raw,
    comparables: arrayOr(raw?.comparables),
    findings: arrayOr(raw?.findings),
    reasons: stringsOf(raw?.reasons),
    matched_attributes: recordOr<string>(raw?.matched_attributes),
    method_note: typeof raw?.method_note === "string" ? raw.method_note : "",
    tax_note: typeof raw?.tax_note === "string" ? raw.tax_note : "",
  };
}
