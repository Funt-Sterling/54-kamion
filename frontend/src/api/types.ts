/**
 * Mirrors backend/app/schemas.py exactly.
 *
 * These are transcribed from the real FastAPI response models, not from the
 * Figma prototype's mock shapes. Where the prototype invented a field the
 * backend doesn't produce (engine, VIN, cab type, condition score,
 * confidence percentages), the field simply doesn't exist here — the UI
 * adapts to the backend rather than the other way round.
 *
 * Fields the contract gained in the evidence-contract rework are typed as
 * REQUIRED here because the schema gives them defaults, so a conforming
 * response always carries them. Every consumer still treats a missing or
 * malformed value as unknown at runtime: an older server, a half-deployed
 * one, or a proxy that drops fields must degrade to "we don't know", never
 * to a crash and never to a confident claim.
 */

/** Provenance of a single recorded fact (backend: contract.ALL_PROVENANCES). */
export type Provenance =
  | "seller_declared"
  | "observed_from_photo"
  | "inferred_candidate"
  | "user_corrected";

/**
 * Resolved status of a field. Either one of the provenance names (exactly
 * one source has a value), or a derived state.
 */
export type FieldStatus = Provenance | "sources_agree" | "confirmed" | "conflicting" | "unknown";

/** backend: contract.ALL_EVIDENCE_STATES */
export type EvidenceState = "active" | "superseded" | "disputed" | "legacy_unverified";

/** backend: schemas.EvidenceParticipantOut — one source's claim about a field. */
export interface EvidenceParticipant {
  provenance: string;
  raw_value: string;
  canonical_value: string | null;
  display_value: string;
  media_id: string | null;
  state: string;
  state_reason: string | null;
}

/** backend: schemas.FieldEvidenceOut */
export interface FieldEvidence {
  field: string;
  status: FieldStatus;
  value: string | null;
  seller_declared: string | null;
  observed_from_photo: string | null;
  inferred_candidates: string[];
  supporting_media_id: string | null;
  /** Every active source for this field — the real participants in a conflict. */
  participants: EvidenceParticipant[];
  /** Market-lookup key (e.g. "f-max"), distinct from the raw reading shown. */
  canonical_value: string | null;
  /** Superseded/disputed history, retained rather than deleted. */
  superseded: EvidenceParticipant[];
}

/** backend: schemas.NextPhotoOut — the deterministic next-best-photo. */
export interface NextPhoto {
  requested_view: string;
  reason: string;
  resolves: string;
}

/** backend: constants.COVERAGE_* */
export type CoverageState = "missing" | "captured" | "attention";

/** backend: contract.ALL_VISIBILITIES, plus whatever else a server may send. */
export type ViewVisibility = "visible" | "absent" | "unclear";

/** backend: schemas.ObservedViewOut — what the photo was found to show. */
export interface ObservedView {
  view: string;
  visibility: string;
  usable: boolean;
  limitation: string | null;
}

/** backend: contract.ALL_ORIGINS. Reported origin, never authenticity proof. */
export type CaptureOrigin = "camera" | "gallery" | "unknown";

/**
 * backend: schemas.MediaOut
 *
 * `component_tag` is deliberately absent: it was the client's own hint
 * echoed back, and the UI rendered it as though it were detected.
 */
export interface MediaResult {
  id: string;
  session_id: string;
  kind: string;
  accepted: boolean;
  reject_reason: string | null;
  quality_notes: string[];
  vision_status: "pending" | "ok" | "failed";
  /** What the client ASKED for. Request metadata — never a detected view. */
  requested_view: string | null;
  /** What was actually admitted. Empty is meaningful: analyzed, supported nothing. */
  observed_views: ObservedView[];
  capture_origin: CaptureOrigin;
  /** null when nothing specific was requested. */
  requested_view_satisfied: boolean | null;
}

/** backend: schemas.FindingOut */
export interface Finding {
  component: string;
  observation: string;
  media_id: string | null;
  visibility: string;
  recommended_action: string;
}

/** backend: contract severities / concern states. */
export type ConcernSeverity = "info" | "clarify" | "structural";
export type ConcernStatus = "open" | "resolved" | "review_required";

/** backend: schemas.ConcernOut — an open question with its own lifecycle. */
export interface Concern {
  id: string;
  component: string;
  severity: string;
  description: string;
  status: string;
}

/** backend: schemas.SessionOut */
export interface SessionSummary {
  id: string;
  status: string;
  created_at: string;
}

/** backend: contract.GATE_* — the gate's own verdict, not an inference. */
export type GateStatus =
  | "ready_to_price"
  | "needs_evidence"
  | "unsupported"
  | "inspection_required"
  | "insufficient_market_data";

/** backend: schemas.SessionDetailOut */
export interface SessionDetail {
  id: string;
  status: string;
  coverage: Record<string, CoverageState>;
  evidence: FieldEvidence[];
  next_photo: NextPhoto | null;
  findings: Finding[];
  latest_appraisal_id: string | null;
  /** Readiness comes from here, never from `next_photo == null`. */
  gate_status: GateStatus;
  gate_reasons: string[];
  /** Bumped on every material evidence change. */
  evidence_revision: number;
  concerns: Concern[];
  /** Things that could NOT be assessed. Never merged into findings. */
  limitations: string[];
}

/** backend: schemas.ComparableOut — a real market listing. */
export interface Comparable {
  listing_id: string;
  similarity_weight: number;
  make: string;
  model: string;
  year: number;
  mileage_km: number;
  axle_config: string;
  price: number;
  currency: string;
  source_url: string;
}

/** backend: constants.APPRAISAL_STATUSES */
export type AppraisalStatus =
  | "priced"
  | "needs_evidence"
  | "unsupported"
  | "insufficient_market_data"
  | "inspection_required";

/** backend: schemas.AppraisalOut */
export interface Appraisal {
  id: string;
  session_id: string;
  version: number;
  status: AppraisalStatus;
  price_low: number | null;
  price_mid: number | null;
  price_high: number | null;
  currency: string;
  comparable_count: number;
  comparables: Comparable[];
  matched_attributes: Record<string, string>;
  findings: Finding[];
  reasons: string[];
  next_photo: NextPhoto | null;
  /** Revision this range was computed from; compared with the session's. */
  evidence_revision: number;
  /** How the range was produced and what it does NOT mean. */
  method_note: string;
  /** e.g. "source asking prices; VAT treatment not stated". */
  tax_note: string;
  /** Distinct vehicle groups behind the range, not a raw row count. */
  distinct_vehicle_groups: number;
}

/**
 * backend: schemas.EvidenceIn
 *
 * There is no `provenance` here any more. It used to be client-supplied,
 * which let any caller mint photo-grade evidence with no photo; the server
 * now assigns provenance and the client may only state its intent.
 */
export interface DeclareDetailBody {
  field: string;
  value: string;
  intent?: "seller_declared" | "user_corrected";
}
