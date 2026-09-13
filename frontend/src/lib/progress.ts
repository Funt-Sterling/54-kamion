/**
 * Progress phases for a photo going through the backend.
 *
 * There are no percentages here on purpose. A single POST covers quality
 * checks and a multimodal model call, and the browser cannot observe the
 * boundary between them — inventing "63%" would be theatre. What we do
 * know honestly:
 *
 *   - "uploading"  ends on a real event (XHR upload.onprogress completing)
 *   - "updating"   begins on a real event (the POST resolving)
 *
 * Between those two, the server genuinely runs quality checks first and
 * then the vision model, so those labels are shown in that order with the
 * long one ("analyzing") holding for the bulk of the wait. The labels
 * describe real backend stages; only the handover moment between the two
 * middle stages is estimated.
 */

export type ProcessingPhase = "uploading" | "checking" | "analyzing" | "updating";

export const PHASE_LABELS: Record<ProcessingPhase, string> = {
  uploading: "Uploading photo",
  checking: "Checking image quality",
  analyzing: "Analyzing visible evidence",
  updating: "Updating inspection",
};

export const PHASE_ORDER: ProcessingPhase[] = ["uploading", "checking", "analyzing", "updating"];

/**
 * How long to show "Checking image quality" once the bytes have landed,
 * before moving to "Analyzing visible evidence". The backend's quality
 * pass (decode, brightness, blur, perceptual hash) is fast; the vision
 * call is what takes seconds.
 */
export const QUALITY_PHASE_MS = 1_200;

export function phaseLabel(phase: ProcessingPhase): string {
  return PHASE_LABELS[phase];
}

/** Index used to render the step list; -1 when not processing. */
export function phaseIndex(phase: ProcessingPhase | null): number {
  return phase ? PHASE_ORDER.indexOf(phase) : -1;
}

export function isPhaseComplete(phase: ProcessingPhase, current: ProcessingPhase | null): boolean {
  const currentIndex = phaseIndex(current);
  const thisIndex = PHASE_ORDER.indexOf(phase);
  return currentIndex > thisIndex;
}

export function isPhaseActive(phase: ProcessingPhase, current: ProcessingPhase | null): boolean {
  return current === phase;
}

/**
 * Every kind of in-flight work. The UI blocks navigation on any of these
 * so a judge can't tap through to evidence that hasn't caught up yet.
 */
export type BusyKind = "starting" | "processing" | "declaring" | "appraising" | "refreshing";

export interface BusyState {
  kind: BusyKind;
  phase: ProcessingPhase | null;
}

export function isBusy(busy: BusyState | null): boolean {
  return busy !== null;
}

/** One short line describing whatever is currently in flight. */
export function busyLabel(busy: BusyState | null): string | null {
  if (!busy) return null;
  switch (busy.kind) {
    case "starting":
      return "Starting inspection";
    case "processing":
      return busy.phase ? phaseLabel(busy.phase) : PHASE_LABELS.uploading;
    case "declaring":
      return "Saving detail";
    case "appraising":
      return "Retrieving comparable listings";
    case "refreshing":
      return "Loading latest session state";
  }
}
