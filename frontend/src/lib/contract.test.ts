import { describe, expect, it } from "vitest";

import type { MediaResult } from "@/api/types";
import {
  analyzedWithNoUsableView,
  captureOrigin,
  captureOriginLabel,
  gateStatus,
  gateView,
  isReadyToPrice,
  observedViews,
  participants,
  rangeFreshness,
  requestedView,
  requestedViewSatisfied,
} from "@/lib/contract";

function media(overrides: Partial<MediaResult> = {}): MediaResult {
  return {
    id: "media_1",
    session_id: "sess_1",
    kind: "photo",
    accepted: true,
    reject_reason: null,
    quality_notes: [],
    vision_status: "ok",
    requested_view: null,
    observed_views: [],
    capture_origin: "unknown",
    requested_view_satisfied: null,
    ...overrides,
  };
}

describe("requested versus detected views (case 1, 14)", () => {
  it("keeps the request and the detection apart", () => {
    const tireForFront = media({
      requested_view: "front_exterior",
      observed_views: [{ view: "tire", visibility: "visible", usable: true, limitation: null }],
      requested_view_satisfied: false,
    });
    expect(requestedView(tireForFront)).toBe("front_exterior");
    expect(observedViews(tireForFront).map((v) => v.view)).toEqual(["tire"]);
    expect(requestedViewSatisfied(tireForFront)).toBe(false);
  });

  it("says out loud when an analysed photo supported nothing", () => {
    expect(analyzedWithNoUsableView(media())).toBe(true);
    expect(analyzedWithNoUsableView(media({ vision_status: "failed" }))).toBe(false);
  });

  it("treats a malformed observed_views payload as no detection", () => {
    expect(observedViews(media({ observed_views: "front" as unknown as [] }))).toEqual([]);
  });
});

describe("capture origin (case 14)", () => {
  it("never reports a gallery pick as a camera capture", () => {
    expect(captureOriginLabel(captureOrigin(media({ capture_origin: "gallery" })))).not.toMatch(/camera/i);
    expect(captureOriginLabel("camera")).toMatch(/reported/i);
  });

  it("falls back to unknown for an unrecognised origin", () => {
    expect(captureOrigin(media({ capture_origin: "verified" as never }))).toBe("unknown");
  });
});

describe("gate verdict", () => {
  it("only the gate says ready; a missing or odd status is conservative", () => {
    expect(isReadyToPrice({ gate_status: "ready_to_price" })).toBe(true);
    expect(gateStatus({ gate_status: "priced" as never })).toBe("needs_evidence");
    expect(gateStatus(null)).toBe("needs_evidence");
    expect(gateView("insufficient_market_data").acceptsMorePhotos).toBe(false);
  });
});

describe("range freshness (case 13, 15)", () => {
  it("marks a range computed at an older revision as stale", () => {
    expect(rangeFreshness({ evidence_revision: 4 }, { evidence_revision: 5 })).toBe("stale");
    expect(rangeFreshness({ evidence_revision: 5 }, { evidence_revision: 5 })).toBe("current");
  });

  it("never claims currency it cannot establish", () => {
    expect(rangeFreshness({ evidence_revision: 0 }, { evidence_revision: 5 })).toBe("unknown");
    expect(rangeFreshness(null, { evidence_revision: 5 })).toBe("unknown");
  });
});

describe("participants fallback", () => {
  it("rebuilds seller/photo/candidate sources from an older payload", () => {
    const rebuilt = participants({
      field: "model_family",
      status: "conflicting",
      value: null,
      seller_declared: "F-MAX",
      observed_from_photo: null,
      inferred_candidates: ["R-series"],
      supporting_media_id: null,
      participants: undefined as unknown as [],
      canonical_value: null,
      superseded: [],
    });
    expect(rebuilt.map((p) => p.provenance)).toEqual(["seller_declared", "inferred_candidate"]);
  });
});

describe("response normalisation (malformed or older server)", () => {
  it("defaults missing collections to empty instead of crashing", async () => {
    const { normalizeAppraisal, normalizeSession } = await import("@/lib/contract");
    const session = normalizeSession({ id: "s", status: "open" } as never);
    expect(session.evidence).toEqual([]);
    expect(session.coverage).toEqual({});
    expect(session.limitations).toEqual([]);
    const appraisal = normalizeAppraisal({ id: "a", status: "priced" } as never);
    expect(appraisal.comparables).toEqual([]);
    expect(appraisal.matched_attributes).toEqual({});
    expect(appraisal.reasons).toEqual([]);
  });
});
