import { describe, expect, it } from "vitest";

import {
  PHASE_LABELS,
  PHASE_ORDER,
  busyLabel,
  isBusy,
  isPhaseActive,
  isPhaseComplete,
  phaseIndex,
  phaseLabel,
  type BusyState,
} from "@/lib/progress";

describe("processing phases", () => {
  it("uses the agreed user-facing wording", () => {
    expect(phaseLabel("uploading")).toBe("Uploading photo");
    expect(phaseLabel("checking")).toBe("Checking image quality");
    expect(phaseLabel("analyzing")).toBe("Analyzing visible evidence");
    expect(phaseLabel("updating")).toBe("Updating inspection");
  });

  it("never exposes a percentage", () => {
    for (const label of Object.values(PHASE_LABELS)) {
      expect(label).not.toMatch(/%|\d+\s*(percent|\/)/i);
    }
  });

  it("runs in the order the backend actually works", () => {
    expect(PHASE_ORDER).toEqual(["uploading", "checking", "analyzing", "updating"]);
  });

  it("marks earlier steps complete and the current one active", () => {
    expect(isPhaseComplete("uploading", "analyzing")).toBe(true);
    expect(isPhaseComplete("checking", "analyzing")).toBe(true);
    expect(isPhaseComplete("analyzing", "analyzing")).toBe(false);
    expect(isPhaseActive("analyzing", "analyzing")).toBe(true);
    expect(isPhaseActive("updating", "analyzing")).toBe(false);
  });

  it("treats a null phase as not started", () => {
    expect(phaseIndex(null)).toBe(-1);
    expect(isPhaseComplete("uploading", null)).toBe(false);
    expect(isPhaseActive("uploading", null)).toBe(false);
  });
});

describe("busy state", () => {
  it("blocks whenever any work is in flight", () => {
    const states: BusyState[] = [
      { kind: "starting", phase: null },
      { kind: "processing", phase: "analyzing" },
      { kind: "declaring", phase: null },
      { kind: "appraising", phase: null },
    ];
    for (const state of states) expect(isBusy(state)).toBe(true);
    expect(isBusy(null)).toBe(false);
  });

  it("describes each kind of work in plain language", () => {
    expect(busyLabel({ kind: "starting", phase: null })).toBe("Starting inspection");
    expect(busyLabel({ kind: "processing", phase: "checking" })).toBe("Checking image quality");
    expect(busyLabel({ kind: "declaring", phase: null })).toBe("Saving detail");
    expect(busyLabel({ kind: "appraising", phase: null })).toMatch(/comparable/i);
    expect(busyLabel(null)).toBeNull();
  });

  it("falls back to the first phase if processing has no phase yet", () => {
    expect(busyLabel({ kind: "processing", phase: null })).toBe("Uploading photo");
  });
});
