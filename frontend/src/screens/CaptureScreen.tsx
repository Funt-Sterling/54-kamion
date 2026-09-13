import { useRef, useState } from "react";

import type { MediaResult, SessionDetail } from "@/api/types";
import { BackButton, CoverageChip, ErrorBanner, ProcessingSteps } from "@/components/atoms";
import { COVERAGE_ORDER, captureHintFor, componentLabel, needsReanalysis, rejectionMessage } from "@/lib/format";
import type { BusyState } from "@/lib/progress";

export interface UploadOutcome {
  media: MediaResult;
  previewUrl: string;
}

/**
 * Replaces the prototype's simulated camera with real uploads. The
 * coverage strip survives as secondary context; the primary call to
 * action is always the single deterministic next-best-photo.
 *
 * Everything that could advance the user is disabled while a photo is in
 * flight — including "Review evidence", which would otherwise show
 * evidence from before this photo was analyzed.
 */
export function CaptureScreen({
  session,
  busy,
  canNavigate,
  onBack,
  onUpload,
  onReview,
  lastOutcome,
  error,
  onDismissError,
}: {
  session: SessionDetail;
  busy: BusyState | null;
  canNavigate: boolean;
  onBack: () => void;
  onUpload: (file: File, componentHint?: string) => void;
  onReview: () => void;
  lastOutcome: UploadOutcome | null;
  error: string | null;
  onDismissError: () => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const [pendingHint, setPendingHint] = useState<string | undefined>(undefined);

  const processing = busy?.kind === "processing";
  const nextPhoto = session.next_photo;
  const requestedComponent = captureHintFor(nextPhoto?.resolves);

  function pick(ref: React.RefObject<HTMLInputElement | null>, hint?: string) {
    if (busy) return;
    setPendingHint(hint);
    ref.current?.click();
  }

  function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Clearing first means a second pick of the same file still fires
    // onChange, while an upload already in flight is simply ignored.
    event.target.value = "";
    if (file && !busy) onUpload(file, pendingHint);
  }

  const capturedCount = Object.values(session.coverage).filter((s) => s === "captured").length;

  return (
    <div className="min-h-screen bg-[#f4f6fa] flex flex-col">
      <input ref={fileInput} type="file" accept="image/*" className="hidden" onChange={handleFile} disabled={!!busy} />
      <input
        ref={cameraInput}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={handleFile}
        disabled={!!busy}
      />

      <div className="bg-[#1d4ed8] px-4 pt-12 pb-6">
        <div className="max-w-[430px] mx-auto">
          {canNavigate ? (
            <BackButton label="Start" onClick={onBack} />
          ) : (
            <span className="flex items-center gap-1.5 text-blue-300/50 text-[12px] font-mono mb-4">Start</span>
          )}
          <p className="text-[10px] font-mono text-blue-300 tracking-widest uppercase mb-1">Capture evidence</p>
          <p className="text-[13px] font-mono text-blue-200/80">
            {capturedCount} of {COVERAGE_ORDER.length} views captured
          </p>
        </div>
      </div>

      <div className="flex-1 max-w-[430px] mx-auto w-full px-4 py-5 flex flex-col gap-4 pb-8">
        {error && <ErrorBanner message={error} onRetry={onDismissError} />}

        {processing && <ProcessingSteps phase={busy.phase} />}

        {/* The hero: exactly one next-best-photo request from the backend. */}
        {!processing &&
          (nextPhoto ? (
            <div className="rounded-xl border-2 border-[#1d4ed8] bg-white p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="w-2 h-2 rounded-full bg-[#1d4ed8]" />
                <span className="text-[10px] font-mono text-[#1d4ed8] tracking-widest uppercase">
                  Next photo needed
                </span>
              </div>
              <p
                className="text-[17px] font-bold text-[#0f1523] leading-snug mb-1.5"
                style={{ fontFamily: "var(--font-display)" }}
              >
                {nextPhoto.requested_view}
              </p>
              <p className="text-[12px] text-[#6b7a9e] leading-relaxed mb-3">{nextPhoto.reason}</p>
              <p className="text-[11px] font-mono text-[#a0abbe] mb-3">
                Resolves: {componentLabel(nextPhoto.resolves)}
              </p>

              <div className="flex gap-2">
                <button
                  onClick={() => pick(cameraInput, requestedComponent)}
                  disabled={!!busy}
                  className="flex-1 px-4 py-3 rounded-[8px] bg-[#1d4ed8] text-[14px] font-semibold text-white hover:bg-[#1e40af] active:scale-[0.98] transition-all disabled:opacity-50 disabled:active:scale-100"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  Take photo
                </button>
                <button
                  onClick={() => pick(fileInput, requestedComponent)}
                  disabled={!!busy}
                  className="flex-1 px-4 py-3 rounded-[8px] border border-[#dde2ef] bg-white text-[14px] font-semibold text-[#3b4a6b] hover:bg-[#f8faff] active:scale-[0.98] transition-all disabled:opacity-50 disabled:active:scale-100"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  Choose file
                </button>
              </div>
            </div>
          ) : (
            <div className="rounded-xl border-2 border-[#16a34a] bg-[#f0fdf4] p-4">
              <p
                className="text-[17px] font-bold text-[#166534] leading-snug mb-1"
                style={{ fontFamily: "var(--font-display)" }}
              >
                Enough evidence to price
              </p>
              <p className="text-[12px] text-[#166534]/80 leading-relaxed mb-3">
                Every pricing input is resolved. You can still add photos to tighten the condition report.
              </p>
              <button
                onClick={() => pick(cameraInput, undefined)}
                disabled={!!busy}
                className="w-full px-4 py-2.5 rounded-[8px] border border-[#bbf7d0] bg-white text-[13px] font-semibold text-[#166534] hover:bg-[#f0fdf4] active:scale-[0.98] transition-all disabled:opacity-50"
              >
                Add another photo
              </button>
            </div>
          ))}

        {lastOutcome && !processing && <UploadResultCard outcome={lastOutcome} />}

        <div className="bg-white rounded-lg border border-[#dde2ef] p-4">
          <h2 className="text-[11px] font-mono text-[#6b7a9e] tracking-widest uppercase mb-3">Coverage</h2>
          <div className="flex flex-wrap gap-2">
            {COVERAGE_ORDER.map((component) => (
              <CoverageChip
                key={component}
                state={session.coverage[component] ?? "missing"}
                label={componentLabel(component)}
              />
            ))}
          </div>
        </div>

        <button
          onClick={onReview}
          disabled={!!busy}
          title={busy ? "Wait for the current photo to finish processing" : undefined}
          className="w-full px-5 py-4 rounded-[8px] border-2 border-[#1d4ed8] bg-white hover:bg-[#f0f4ff] active:scale-[0.98] transition-all disabled:opacity-40 disabled:active:scale-100 disabled:cursor-not-allowed"
        >
          <p className="text-[16px] font-bold text-[#1d4ed8]" style={{ fontFamily: "var(--font-display)" }}>
            {processing ? "Analyzing photo…" : "Review evidence →"}
          </p>
        </button>
      </div>
    </div>
  );
}

function UploadResultCard({ outcome }: { outcome: UploadOutcome }) {
  const { media, previewUrl } = outcome;
  const accepted = media.accepted && media.vision_status === "ok";
  const unanalyzed = needsReanalysis(media);

  return (
    <div className="bg-white rounded-lg border border-[#dde2ef] overflow-hidden">
      <div className="flex gap-3 p-3">
        <div className="w-[92px] h-[68px] rounded overflow-hidden bg-[#e8edf7] shrink-0 relative">
          <img src={previewUrl} alt="" className="w-full h-full object-cover" />
          {!accepted && <div className="absolute inset-0 bg-[#0f1523]/50" />}
        </div>
        <div className="flex-1 min-w-0">
          {accepted ? (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#dcfce7] text-[10px] font-mono text-[#166534] font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-[#22c55e]" />
              Accepted
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#fff7ed] border border-[#fed7aa] text-[10px] font-mono text-[#9a3412] font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-[#f97316]" />
              Retake
            </span>
          )}

          <p className="text-[13px] text-[#0f1523] leading-snug mt-1.5">
            {unanalyzed
              ? "Photo analysis failed. Your inspection session is safe — try this photo again."
              : accepted
                ? "Evidence extracted from this photo."
                : rejectionMessage(media)}
          </p>

          {media.component_tag && (
            <p className="text-[11px] font-mono text-[#a0abbe] mt-1">
              Tagged: {componentLabel(media.component_tag)}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
