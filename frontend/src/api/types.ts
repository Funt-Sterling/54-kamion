/**
 * Mirrors backend/app/schemas.py exactly.
 *
 * These are transcribed from the real FastAPI response models, not from the
 * Figma prototype's mock shapes. Where the prototype invented a field the
 * backend doesn't produce (engine, VIN, cab type, condition score,
 * confidence percentages), the field simply doesn't exist here — the UI
 * adapts to the backend rather than the other way round.
 */

/** Provenance of a single recorded fact (backend: EvidenceRecord.provenance). */
export type Provenance = "seller_declared" | "observed_from_photo" | "inferred_candidate";

/**
 * Resolved status of a field. Either one of the provenance names (exactly
 * one source has a value), or a derived state.
 */
export type FieldStatus = Provenance | "confirmed" | "conflicting" | "unknown";

/** backend: schemas.FieldEvidenceOut */
export interface FieldEvidence {
  field: string;
  status: FieldStatus;
  value: string | null;
  seller_declared: string | null;
  observed_from_photo: string | null;
  inferred_candidates: string[];
  supporting_media_id: string | null;
}

/** backend: schemas.NextPhotoOut — the deterministic next-best-photo. */
export interface NextPhoto {
  requested_view: string;
  reason: string;
  resolves: string;
}

/** backend: constants.COVERAGE_* */
export type CoverageState = "missing" | "captured" | "attention";

/** backend: schemas.MediaOut */
export interface MediaResult {
  id: string;
  session_id: string;
  kind: string;
  accepted: boolean;
  reject_reason: string | null;
  component_tag: string | null;
  quality_notes: string[];
  vision_status: "pending" | "ok" | "failed";
}

/** backend: schemas.FindingOut */
export interface Finding {
  component: string;
  observation: string;
  media_id: string | null;
  visibility: string;
  recommended_action: string;
}

/** backend: schemas.SessionOut */
export interface SessionSummary {
  id: string;
  status: string;
  created_at: string;
}

/** backend: schemas.SessionDetailOut */
export interface SessionDetail {
  id: string;
  status: string;
  coverage: Record<string, CoverageState>;
  evidence: FieldEvidence[];
  next_photo: NextPhoto | null;
  findings: Finding[];
  latest_appraisal_id: string | null;
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
}

/** backend: schemas.EvidenceIn */
export interface DeclareDetailBody {
  field: string;
  value: string;
  provenance?: Provenance;
}
