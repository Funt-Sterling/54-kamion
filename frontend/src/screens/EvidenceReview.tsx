import { useState } from "react";

import type { FieldEvidence, SessionDetail } from "@/api/types";
import { BackButton, ErrorBanner, Panel, ProvenanceBadge, Spinner } from "@/components/atoms";
import { busyLabel, type BusyState } from "@/lib/progress";
import { componentLabel, displayValue, fieldLabel, splitFindings } from "@/lib/format";

/** Fields a seller is allowed to type in when no photo established them. */
const DECLARABLE: Record<string, { label: string; placeholder: string; numeric: boolean }> = {
  year: { label: "Model year", placeholder: "e.g. 2021", numeric: true },
  mileage_km: { label: "Mileage (km)", placeholder: "e.g. 365000", numeric: true },
  axle_config: { label: "Axle configuration", placeholder: "e.g. 4x2", numeric: false },
  model_family: { label: "Model family", placeholder: "e.g. f-max", numeric: false },
};

/**
 * The prototype's EvidenceReview layout (spec rows + unresolved cards),
 * wired to real provenanced evidence. Its "confidence: high/medium" chips
 * are gone: the backend exposes provenance, not calibrated confidence, and
 * inventing a percentage would be exactly the kind of false precision this
 * product is arguing against.
 */
export function EvidenceReview({
  session,
  busy,
  canNavigate,
  onBack,
  onCapture,
  onAppraise,
  onDeclare,
  error,
  onDismissError,
}: {
  session: SessionDetail;
  busy: BusyState | null;
  canNavigate: boolean;
  onBack: () => void;
  onCapture: () => void;
  onAppraise: () => void;
  onDeclare: (field: string, value: string) => void;
  error: string | null;
  onDismissError: () => void;
}) {
  const appraising = busy?.kind === "appraising";
  const known = session.evidence.filter((e) => e.status !== "unknown");
  const unresolved = session.evidence.filter(
    (e) => (e.status === "unknown" || e.status === "conflicting") && e.field in DECLARABLE,
  );
  const { observed, limited } = splitFindings(session.findings);

  return (
    <div className="min-h-screen bg-[#f4f6fa] flex flex-col">
      <div className="bg-[#1d4ed8] px-4 pt-12 pb-6">
        <div className="max-w-[430px] mx-auto">
          {canNavigate ? (
            <BackButton label="Capture" onClick={onCapture} />
          ) : (
            <span className="flex items-center gap-1.5 text-blue-300/50 text-[12px] font-mono mb-4">Capture</span>
          )}
          <p className="text-[10px] font-mono text-blue-300 tracking-widest uppercase mb-1">Evidence review</p>
          <p className="text-[15px] font-semibold text-white" style={{ fontFamily: "var(--font-display)" }}>
            What we can actually see
          </p>
        </div>
      </div>

      <div className="flex-1 max-w-[430px] mx-auto w-full px-4 py-5 flex flex-col gap-4 pb-32">
        {error && <ErrorBanner message={error} onRetry={onDismissError} />}

        <Panel title="Extracted specifications" count={`${known.length} known`} dot="bg-[#1d4ed8]">
          {known.length === 0 ? (
            <p className="px-4 py-4 text-[13px] text-[#6b7a9e]">
              Nothing established yet. Upload a photo to begin.
            </p>
          ) : (
            <div className="px-4">
              {known.map((evidence) => (
                <SpecRow key={evidence.field} evidence={evidence} />
              ))}
            </div>
          )}
        </Panel>

        {unresolved.length > 0 && (
          <Panel title="Unresolved — needed for pricing" count={`${unresolved.length}`} dot="bg-[#ea580c]">
            <div className="p-3 flex flex-col gap-2.5">
              {unresolved.map((evidence) => (
                <UnresolvedCard
                  key={evidence.field}
                  evidence={evidence}
                  busy={!!busy}
                  onDeclare={onDeclare}
                  onCapture={() => canNavigate && onCapture()}
                />
              ))}
            </div>
          </Panel>
        )}

        {observed.length > 0 && (
          <Panel title="Observations" count={`${observed.length} items`} dot="bg-[#16a34a]">
            <div className="py-1">
              {observed.map((finding, index) => (
                <div
                  key={`${finding.component}-${index}`}
                  className={`px-4 py-2.5 ${index < observed.length - 1 ? "border-b border-[#f4f6fa]" : ""}`}
                >
                  <p className="text-[11px] font-mono text-[#6b7a9e] uppercase tracking-wider mb-0.5">
                    {componentLabel(finding.component)}
                  </p>
                  <p className="text-[13px] text-[#0f1523] leading-snug">{finding.observation}</p>
                </div>
              ))}
            </div>
          </Panel>
        )}

        {limited.length > 0 && (
          <Panel title="Could not be assessed" count={`${limited.length} items`} dot="bg-[#ea580c]">
            <div className="py-1">
              {limited.map((finding, index) => (
                <div
                  key={`${finding.component}-${index}`}
                  className={`px-4 py-2.5 ${index < limited.length - 1 ? "border-b border-[#f4f6fa]" : ""}`}
                >
                  <p className="text-[11px] font-mono text-[#6b7a9e] uppercase tracking-wider mb-0.5">
                    {componentLabel(finding.component)}
                  </p>
                  <p className="text-[13px] text-[#0f1523] leading-snug">{finding.observation}</p>
                  {finding.recommended_action !== "none" && (
                    <p className="text-[11px] font-mono text-[#9a3412] mt-1">→ {finding.recommended_action}</p>
                  )}
                </div>
              ))}
            </div>
          </Panel>
        )}
      </div>

      <div className="fixed bottom-0 left-0 right-0 bg-white border-t border-[#dde2ef] px-4 py-4">
        <div className="max-w-[430px] mx-auto">
          <button
            onClick={onAppraise}
            disabled={!!busy}
            title={busy && !appraising ? "Wait for the current step to finish" : undefined}
            className="w-full flex items-center justify-between px-5 py-4 rounded-[8px] bg-[#1d4ed8] hover:bg-[#1e40af] active:scale-[0.98] transition-all disabled:opacity-60"
          >
            <div className="text-left">
              <p className="text-[17px] font-bold text-white leading-tight" style={{ fontFamily: "var(--font-display)" }}>
                {appraising ? "Appraising…" : busy ? "Please wait…" : "Get appraisal"}
              </p>
              <p className="text-[11px] font-mono text-blue-200 mt-0.5">Prices against real Turkish listings</p>
            </div>
          </button>
          {busy && (
            <div className="mt-3">
              <Spinner label={`${busyLabel(busy)}…`} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SpecRow({ evidence }: { evidence: FieldEvidence }) {
  const conflicting = evidence.status === "conflicting";
  return (
    <div className="flex items-start justify-between gap-3 py-3 border-b border-[#edf0f7] last:border-0">
      <div className="min-w-0">
        <p className="text-[11px] font-mono text-[#6b7a9e] mb-0.5 uppercase tracking-wider">
          {fieldLabel(evidence.field)}
        </p>
        <p className="text-[15px] font-semibold text-[#0f1523]" style={{ fontFamily: "var(--font-display)" }}>
          {displayValue(evidence)}
        </p>
        {conflicting && (
          <div className="mt-1.5 text-[11px] font-mono text-[#9a3412] leading-relaxed">
            <p>Seller says: {evidence.seller_declared ?? "—"}</p>
            <p>Photo shows: {evidence.observed_from_photo ?? "—"}</p>
          </div>
        )}
      </div>
      <ProvenanceBadge status={evidence.status} />
    </div>
  );
}

function UnresolvedCard({
  evidence,
  busy,
  onDeclare,
  onCapture,
}: {
  evidence: FieldEvidence;
  busy: boolean;
  onDeclare: (field: string, value: string) => void;
  onCapture: () => void;
}) {
  const [value, setValue] = useState("");
  const config = DECLARABLE[evidence.field];
  const conflicting = evidence.status === "conflicting";

  return (
    <div
      className={`rounded-lg border p-3.5 ${conflicting ? "bg-[#fff7ed] border-[#fed7aa]" : "bg-white border-[#fed7aa]"}`}
    >
      <p className="text-[13px] font-semibold text-[#0f1523] leading-snug mb-1" style={{ fontFamily: "var(--font-display)" }}>
        {config.label}
      </p>
      <p className="text-[12px] text-[#6b7a9e] leading-relaxed mb-2.5">
        {conflicting
          ? `The seller and the photo disagree. Re-enter the correct value, or add a clearer photo — we won't pick one for you.`
          : `No photo established this yet. Add a photo, or enter it yourself — it will be recorded as seller-provided.`}
      </p>

      <div className="flex gap-2">
        <input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          inputMode={config.numeric ? "numeric" : "text"}
          placeholder={config.placeholder}
          className="flex-1 min-w-0 text-[13px] font-mono text-[#0f1523] bg-[#f4f6fa] border border-[#dde2ef] rounded px-2.5 py-2 placeholder:text-[#a0abbe] focus:outline-none focus:border-[#1d4ed8] transition-colors"
        />
        <button
          onClick={() => {
            const trimmed = value.trim();
            if (trimmed) onDeclare(evidence.field, trimmed);
            setValue("");
          }}
          disabled={busy || !value.trim()}
          className="px-3 py-2 rounded bg-[#1d4ed8] text-[12px] font-mono font-semibold text-white hover:bg-[#1e40af] active:scale-[0.98] transition-all disabled:opacity-40 shrink-0"
        >
          Save
        </button>
      </div>

      <button
        onClick={onCapture}
        className="mt-2 text-[11px] font-mono text-[#1d4ed8] hover:underline"
      >
        …or add a photo instead →
      </button>
    </div>
  );
}
