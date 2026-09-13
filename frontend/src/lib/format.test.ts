import { describe, expect, it } from "vitest";

import type { Appraisal, CoverageState, FieldEvidence, Finding, MediaResult } from "@/api/types";
import {
  appraisalMessage,
  deriveUnknowns,
  describeConflict,
  displayValue,
  formatTRY,
  humanizeReason,
  needsReanalysis,
  provenanceLabel,
  rejectionMessage,
  requestedComponentFor,
  sourceLabel,
  splitFindings,
} from "@/lib/format";

function evidence(overrides: Partial<FieldEvidence> & { field: string }): FieldEvidence {
  return {
    status: "unknown",
    value: null,
    seller_declared: null,
    observed_from_photo: null,
    inferred_candidates: [],
    supporting_media_id: null,
    participants: [],
    canonical_value: null,
    superseded: [],
    ...overrides,
  };
}

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

describe("provenance labels", () => {
  it("never leaks internal enum names to the UI", () => {
    expect(provenanceLabel("observed_from_photo")).toBe("Seen in photo");
    expect(provenanceLabel("seller_declared")).toBe("Seller provided");
    // Agreement between sources is corroboration, never "confirmed"/"verified".
    expect(provenanceLabel("sources_agree")).toBe("Sources agree");
    expect(provenanceLabel("confirmed")).toBe("Sources agree");
    expect(provenanceLabel("user_corrected")).toBe("User corrected");
    expect(provenanceLabel("conflicting")).toBe("Conflict");
    expect(provenanceLabel("unknown")).toBe("Unknown");
  });
});

describe("displayValue", () => {
  it("renders a conflicting field as a conflict, never as one side's value", () => {
    const conflicted = evidence({
      field: "mileage_km",
      status: "conflicting",
      value: null,
      seller_declared: "250000",
      observed_from_photo: "650000",
    });
    const shown = displayValue(conflicted);
    expect(shown).toBe("Conflicting");
    expect(shown).not.toContain("250000");
    expect(shown).not.toContain("650000");
  });

  it("renders an unknown field as Unknown rather than blank", () => {
    expect(displayValue(evidence({ field: "year" }))).toBe("Unknown");
  });

  it("formats mileage with units", () => {
    expect(displayValue(evidence({ field: "mileage_km", status: "seller_declared", value: "365000" }))).toContain("km");
  });

  it("describes an unstated VAT basis instead of showing the raw enum", () => {
    expect(displayValue(evidence({ field: "vat_basis", status: "observed_from_photo", value: "unknown" }))).toBe(
      "Not stated",
    );
  });
});

describe("currency", () => {
  it("formats Turkish lira, never dollars", () => {
    const formatted = formatTRY(2500000);
    expect(formatted.startsWith("₺")).toBe(true);
    expect(formatted).not.toContain("$");
  });
});

describe("rejection messages", () => {
  it("explains a quality rejection in plain language", () => {
    expect(rejectionMessage(media({ accepted: false, reject_reason: "too_dark" }))).toMatch(/dark/i);
  });

  it("names the detected vehicle for an unsupported type", () => {
    const message = rejectionMessage(media({ accepted: false, reject_reason: "not_a_tractor_unit:motorcycle" }));
    expect(message).toMatch(/motorcycle/i);
    expect(message).toMatch(/not a tractor unit/i);
  });

  it("handles an unsupported type with no detected category", () => {
    expect(rejectionMessage(media({ accepted: false, reject_reason: "not_a_tractor_unit:unknown" }))).toMatch(
      /tractor unit/i,
    );
  });
});

describe("failed vision analysis", () => {
  it("flags an accepted-but-unanalyzed photo so it is not shown as inspected", () => {
    expect(needsReanalysis(media({ vision_status: "failed" }))).toBe(true);
    expect(needsReanalysis(media({ vision_status: "ok" }))).toBe(false);
  });
});

describe("deriveUnknowns", () => {
  const coverage: Record<string, CoverageState> = {
    front_exterior: "captured",
    rear_exterior: "missing",
    side_exterior: "captured",
    tire: "missing",
    chassis_suspension: "missing",
    dashboard_odometer: "missing",
    cab_interior: "missing",
  };

  it("never reports an unphotographed component as fine", () => {
    const unknowns = deriveUnknowns(coverage, []);
    const tire = unknowns.find((u) => u.startsWith("Tires"));
    expect(tire).toBeDefined();
    expect(tire).toMatch(/not photographed|not assessed/i);
    expect(unknowns.join(" ")).not.toMatch(/\bgood\b|\bfine\b|no damage/i);
  });

  it("lists unknown pricing fields", () => {
    const unknowns = deriveUnknowns(coverage, [evidence({ field: "year" })]);
    expect(unknowns.some((u) => u.includes("Model year"))).toBe(true);
  });

  it("surfaces both sides of a conflict", () => {
    const unknowns = deriveUnknowns(coverage, [
      evidence({
        field: "mileage_km",
        status: "conflicting",
        seller_declared: "250000",
        observed_from_photo: "650000",
      }),
    ]);
    const conflict = unknowns.find((u) => u.includes("Mileage"));
    expect(conflict).toContain("250000");
    expect(conflict).toContain("650000");
  });

  it("omits components that were captured", () => {
    const unknowns = deriveUnknowns(coverage, []);
    expect(unknowns.some((u) => u.startsWith("Front exterior"))).toBe(false);
  });
});

describe("humanizeReason", () => {
  it("replaces internal field keys in backend copy", () => {
    expect(humanizeReason("axle_config is not yet identified.")).toBe("Axle configuration is not yet identified.");
    expect(humanizeReason("mileage_km is not yet identified.")).toBe("Mileage is not yet identified.");
  });

  it("leaves prose without field keys alone apart from capitalisation", () => {
    const reason = "Only 2 compatible Turkish comparable(s) found; 5 required for a defensible range.";
    expect(humanizeReason(reason)).toBe(reason);
  });

  it("never leaves a snake_case token in user-facing text", () => {
    const out = humanizeReason("model_family is not yet identified.");
    expect(out).not.toMatch(/[a-z]+_[a-z]+/);
  });
});

describe("requestedComponentFor", () => {
  it("sends the gate's own target, so a badge close-up is judged against the badge", () => {
    expect(requestedComponentFor("model_family")).toBe("model_family");
    expect(requestedComponentFor("mileage_km")).toBe("mileage_km");
    expect(requestedComponentFor("axle_config")).toBe("axle_config");
  });

  it("passes through checklist components and contract views", () => {
    expect(requestedComponentFor("tire")).toBe("tire");
    expect(requestedComponentFor("chassis")).toBe("chassis");
  });

  it("returns undefined when there is nothing to request", () => {
    expect(requestedComponentFor(undefined)).toBeUndefined();
    expect(requestedComponentFor("something_unmapped")).toBeUndefined();
  });
});

describe("conflict participants (case 14)", () => {
  it("names every active source, including a visual guess the old UI dropped", () => {
    const conflicted = evidence({
      field: "model_family",
      status: "conflicting",
      seller_declared: "F-MAX",
      participants: [
        { provenance: "seller_declared", raw_value: "F-MAX", canonical_value: "f-max", display_value: "F-MAX", media_id: null, state: "active", state_reason: null },
        { provenance: "inferred_candidate", raw_value: "R-series", canonical_value: "r-series", display_value: "R-series", media_id: "m1", state: "active", state_reason: null },
      ],
    });
    const text = describeConflict(conflicted);
    expect(text).toContain("Seller provided: F-MAX");
    expect(text).toContain("Visual guess: R-series");
  });

  it("labels a user correction distinctly from a seller claim", () => {
    expect(sourceLabel("user_corrected")).toBe("User corrected");
    expect(sourceLabel("seller_declared")).toBe("Seller provided");
    expect(sourceLabel("some_future_source")).toBe("Some future source");
  });
});

describe("splitFindings", () => {
  function finding(overrides: Partial<Finding>): Finding {
    return {
      component: "tires",
      observation: "Tread looks fine",
      media_id: "media_1",
      visibility: "clear",
      recommended_action: "none",
      ...overrides,
    };
  }

  it("separates things that could not be seen from real observations", () => {
    const { observed, limited } = splitFindings([
      finding({ component: "grille", observation: "Grille intact, no cracks" }),
      finding({ component: "tires", observation: "Front tires not visible in this frontal shot", visibility: "obstructed" }),
    ]);
    expect(observed).toHaveLength(1);
    expect(limited).toHaveLength(1);
    expect(limited[0].component).toBe("tires");
  });

  it("treats a 'not visible' note as a limitation even when visibility says clear", () => {
    const { limited } = splitFindings([
      finding({ observation: "Fifth wheel not visible in this side profile shot", visibility: "clear" }),
    ]);
    expect(limited).toHaveLength(1);
  });
});

describe("appraisalMessage", () => {
  function appraisal(overrides: Partial<Appraisal>): Appraisal {
    return {
      id: "appr_1",
      session_id: "sess_1",
      version: 1,
      status: "needs_evidence",
      price_low: null,
      price_mid: null,
      price_high: null,
      currency: "TRY",
      comparable_count: 0,
      comparables: [],
      matched_attributes: {},
      findings: [],
      reasons: [],
      next_photo: null,
      evidence_revision: 1,
      method_note: "",
      tax_note: "",
      distinct_vehicle_groups: 0,
      ...overrides,
    };
  }

  it("explains insufficient market data without implying a price exists", () => {
    const { title, body } = appraisalMessage(appraisal({ status: "insufficient_market_data" }));
    expect(title).toMatch(/comparable/i);
    expect(body).not.toMatch(/₺|\$/);
  });

  it("prefers the backend's own reason, with field keys humanized", () => {
    const { body } = appraisalMessage(
      appraisal({ status: "needs_evidence", reasons: ["axle_config is not yet identified."] }),
    );
    expect(body).toBe("Axle configuration is not yet identified.");
    expect(body).not.toMatch(/[a-z]+_[a-z]+/);
  });

  it("explains an unsupported vehicle", () => {
    expect(appraisalMessage(appraisal({ status: "unsupported" })).title).toMatch(/not a supported/i);
  });

  it("explains a withheld price for suspected damage", () => {
    expect(appraisalMessage(appraisal({ status: "inspection_required" })).title).toMatch(/inspection/i);
  });
});

describe("review copy regressions", () => {
  it("does not rewrite plain words in backend prose", () => {
    expect(humanizeReason("model year 2021 as declared by the seller")).toBe("Model year 2021 as declared by the seller");
  });

  it("describes a partial view as partial, not as a failed photo", () => {
    const unknowns = deriveUnknowns({ dashboard_odometer: "attention" } as Record<string, CoverageState>, []);
    const line = unknowns.find((u) => u.startsWith("Dashboard"));
    expect(line).toMatch(/partly assessed/);
    expect(line).not.toMatch(/retake/i);
  });
});
