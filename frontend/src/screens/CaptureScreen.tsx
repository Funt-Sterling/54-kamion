import { useRef, useState } from "react";

import type { CaptureOrigin, MediaResult, SessionDetail } from "@/api/types";
import { BackButton, CoverageChip, ErrorBanner, ProcessingSteps } from "@/components/atoms";
import {
  analyzedWithNoUsableView,
  captureOrigin,
  captureOriginLabel,
  gateReasons,
  gateStatus,
  gateView,
  observedViews,
  requestedView,
  requestedViewSatisfied,
} from "@/lib/contract";
import {
  COVERAGE_ORDER,
  componentLabel,
  humanizeReason,
  needsReanalysis,
  observedViewLabel,
  rejectionMessage,
  requestedComponentFor,
} from "@/lib/format";
import type { BusyState } from "@/lib/progress";

export interface UploadOutcome {
  media: MediaResult;
  previewUrl: string;
  /** Set when the session refresh after this upload failed. */
  stale?: boolean;
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
  sessionStale,
  onRefresh,
  error,
  onDismissError,
}: {
  session: SessionDetail;
  busy: BusyState | null;
  canNavigate: boolean;
  onBack: () => void;
  onUpload: (file: File, componentHint: string | undefined, origin: CaptureOrigin) => void;
  onReview: () => void;
  lastOutcome: UploadOutcome | null;
  /** The last session refresh failed: coverage and requests may be behind. */
  sessionStale: boolean;
  onRefresh: () => void;
  error: string | null;
  onDismissError: () => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState<{ hint?: string; origin: CaptureOrigin }>({
    origin: "unknown",
  });

  const processing = busy?.kind === "processing";
  const nextPhoto = session.next_photo;
  const requestedComponent = requestedComponentFor(nextPhoto?.resolves);

  const status = gateStatus(session);
  const gate = gateView(status);
  const reasons = gateReasons(session);

  /**
   * Origin is decided by WHICH input is opened, not assumed. The
   * `capture="environment"` input is the camera path; the plain one is a
   * file the user already had.
   */
  function pickFromCamera(hint?: string) {
    if (busy) return;
    setPending({ hint, origin: "camera" });
    cameraInput.current?.click();
  }

  function pickFromGallery(hint?: string) {
    if (busy) return;
    setPending({ hint, origin: "gallery" });
    fileInput.current?.click();
  }

  function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Clearing first means a second pick of the same file still fires
    // onChange, while an upload already in flight is simply ignored.
    event.target.value = "";
    if (file && !busy) onUpload(file, pending.hint, pending.origin);
  }

  // The checklist is shown in a fixed order, plus any slot the server
  // tracks that this build doesn't know about — hiding one would under-
  // report what is still unphotographed.
  const coverageKeys = [
    ...COVERAGE_ORDER,
    ...Object.keys(session.coverage ?? {}).filter((key) => !COVERAGE_ORDER.includes(key)),
  ];
  const capturedCount = Object.values(session.coverage ?? {}).filter((s) => s === "captured").length;

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
            {capturedCount} of {coverageKeys.length} views captured
          </p>
        </div>
      </div>

      <div className="flex-1 max-w-[430px] mx-auto w-full px-4 py-5 flex flex-col gap-4 pb-8">
        {error && <ErrorBanner message={error} onRetry={onDismissError} />}

        {sessionStale && (
          <div className="rounded-lg border border-[#fed7aa] bg-[#fff7ed] px-4 py-3" data-testid="stale-session">
            <p className="text-[12px] text-[#9a3412] leading-relaxed">
              The latest inspection state did not load, so the request and coverage below may be out of date.
            </p>
            <button
              onClick={onRefresh}
              disabled={!!busy}
              className="mt-1.5 text-[12px] font-mono font-semibold text-[#1d4ed8] hover:underline disabled:opacity-40"
            >
              Reload session
            </button>
          </div>
        )}

        {processing && <ProcessingSteps phase={busy.phase} />}

        {/* Readiness comes from the gate's own verdict. `next_photo == null`
            answers a different question: the gate can be blocked with no
            photo that would help, and ready while still suggesting one. */}
        {!processing && (
          <GateCard status={gate} reasons={reasons} showNextPhoto={nextPhoto !== null} />
        )}

        {!processing &&
          (nextPhoto && gate.acceptsMorePhotos ? (
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
                Would resolve: {componentLabel(nextPhoto.resolves)}
              </p>

              <div className="flex gap-2">
                <button
                  onClick={() => pickFromCamera(requestedComponent)}
                  disabled={!!busy}
                  className="flex-1 px-4 py-3 rounded-[8px] bg-[#1d4ed8] text-[14px] font-semibold text-white hover:bg-[#1e40af] active:scale-[0.98] transition-all disabled:opacity-50 disabled:active:scale-100"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  Take photo
                </button>
                <button
                  onClick={() => pickFromGallery(requestedComponent)}
                  disabled={!!busy}
                  className="flex-1 px-4 py-3 rounded-[8px] border border-[#dde2ef] bg-white text-[14px] font-semibold text-[#3b4a6b] hover:bg-[#f8faff] active:scale-[0.98] transition-all disabled:opacity-50 disabled:active:scale-100"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  Choose file
                </button>
              </div>
            </div>
          ) : gate.acceptsMorePhotos ? (
            <div className="rounded-xl border border-[#dde2ef] bg-white p-4">
              <p className="text-[12px] text-[#6b7a9e] leading-relaxed mb-3">
                No specific photo is being requested right now. Anything you add is analyzed the same way.
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => pickFromCamera(undefined)}
                  disabled={!!busy}
                  className="flex-1 px-4 py-2.5 rounded-[8px] border border-[#dde2ef] bg-white text-[13px] font-semibold text-[#3b4a6b] hover:bg-[#f8faff] active:scale-[0.98] transition-all disabled:opacity-50"
                >
                  Take photo
                </button>
                <button
                  onClick={() => pickFromGallery(undefined)}
                  disabled={!!busy}
                  className="flex-1 px-4 py-2.5 rounded-[8px] border border-[#dde2ef] bg-white text-[13px] font-semibold text-[#3b4a6b] hover:bg-[#f8faff] active:scale-[0.98] transition-all disabled:opacity-50"
                >
                  Choose file
                </button>
              </div>
            </div>
          ) : null)}

        {lastOutcome && !processing && <UploadResultCard outcome={lastOutcome} />}

        <div className="bg-white rounded-lg border border-[#dde2ef] p-4">
          <h2 className="text-[11px] font-mono text-[#6b7a9e] tracking-widest uppercase mb-3">Coverage</h2>
          <div className="flex flex-wrap gap-2">
            {coverageKeys.map((component) => (
              <CoverageChip
                key={component}
                state={session.coverage?.[component] ?? "missing"}
                label={componentLabel(component)}
              />
            ))}
          </div>
          <p className="text-[11px] font-mono text-[#a0abbe] mt-3 leading-relaxed">
            A slot fills only when a photo was found to show that view.
          </p>
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

const GATE_TONES = {
  ready: { box: "border-[#16a34a] bg-[#f0fdf4]", title: "text-[#166534]", body: "text-[#166534]/80" },
  working: { box: "border-[#bfdbfe] bg-[#eff6ff]", title: "text-[#1e3a8a]", body: "text-[#1e3a8a]/80" },
  blocked: { box: "border-[#fed7aa] bg-[#fff7ed]", title: "text-[#9a3412]", body: "text-[#9a3412]/85" },
} as const;

function GateCard({
  status,
  reasons,
  showNextPhoto,
}: {
  status: ReturnType<typeof gateView>;
  reasons: string[];
  showNextPhoto: boolean;
}) {
  const tone = GATE_TONES[status.tone];
  return (
    <div className={`rounded-xl border-2 p-4 ${tone.box}`}>
      <p
        className={`text-[17px] font-bold leading-snug mb-1 ${tone.title}`}
        style={{ fontFamily: "var(--font-display)" }}
      >
        {status.title}
      </p>
      <p className={`text-[12px] leading-relaxed ${tone.body}`}>{status.body}</p>

      {reasons.length > 0 && (
        <ul className="mt-2.5 flex flex-col gap-1">
          {reasons.map((reason) => (
            <li key={reason} className={`text-[12px] leading-snug flex gap-2 ${tone.body}`}>
              <span aria-hidden="true">·</span>
              <span>{humanizeReason(reason)}</span>
            </li>
          ))}
        </ul>
      )}

      {!status.acceptsMorePhotos && (
        <p className="text-[11px] font-mono mt-2.5 leading-relaxed text-[#9a3412]">
          No further photo is requested — this blocker is not something a photo can fix.
        </p>
      )}
      {status.acceptsMorePhotos && !showNextPhoto && status.tone === "blocked" && (
        <p className="text-[11px] font-mono mt-2.5 leading-relaxed text-[#9a3412]">
          You can still upload another photo.
        </p>
      )}
    </div>
  );
}

function UploadResultCard({ outcome }: { outcome: UploadOutcome }) {
  const { media, previewUrl } = outcome;
  const accepted = media.accepted && media.vision_status === "ok";
  const unanalyzed = needsReanalysis(media);

  const asked = requestedView(media);
  const detected = observedViews(media);
  const satisfied = requestedViewSatisfied(media);
  const analyzedButEmpty = analyzedWithNoUsableView(media);

  return (
    <div className="bg-white rounded-lg border border-[#dde2ef] overflow-hidden">
      {outcome.stale && (
        <p className="px-3 py-2 bg-[#fff7ed] border-b border-[#fed7aa] text-[11px] font-mono text-[#9a3412] leading-relaxed">
          This photo was analyzed, but the inspection below could not be reloaded — the evidence and coverage
          shown may be out of date.
        </p>
      )}
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
                ? "Analyzed."
                : rejectionMessage(media)}
          </p>

          {/* The request and the detection are two different facts and are
              never merged. The old card printed the client's own hint as
              "Tagged: …", which read as though it had been detected. */}
          <div className="mt-2 flex flex-col gap-1">
            {asked && (
              <p className="text-[11px] font-mono text-[#6b7a9e] leading-snug">
                You were asked for: {componentLabel(asked)}
              </p>
            )}

            {detected.length > 0 ? (
              <p className="text-[11px] font-mono text-[#1d4ed8] leading-snug">
                Detected in this photo: {detected.map(observedViewLabel).join(", ")}
              </p>
            ) : analyzedButEmpty ? (
              <p className="text-[11px] font-mono text-[#9a3412] leading-snug">
                Analyzed; no usable view identified in this photo.
              </p>
            ) : null}

            {satisfied === false && (
              <p className="text-[11px] font-mono text-[#9a3412] leading-snug">
                This isn&apos;t the view that was requested — the request is still open.
              </p>
            )}

            <p className="text-[10px] font-mono text-[#a0abbe] leading-snug">
              {captureOriginLabel(captureOrigin(media))} — not a check of authenticity.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
