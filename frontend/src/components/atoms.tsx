/**
 * Visual atoms carried over from the Figma prototype (badges, chips,
 * panels). Styling is intentionally unchanged — only the data feeding
 * them is real now.
 */

import type { ReactNode } from "react";

import type { CoverageState, FieldStatus } from "@/api/types";
import { provenanceLabel, statusTone, type StatusTone } from "@/lib/format";
import {
  PHASE_ORDER,
  isPhaseActive,
  isPhaseComplete,
  phaseLabel,
  type ProcessingPhase,
} from "@/lib/progress";

const TONE_STYLES: Record<StatusTone, { badge: string; text: string; dot: string }> = {
  good: { badge: "bg-[#f0fdf4] border border-[#bbf7d0]", text: "text-[#166534]", dot: "bg-[#16a34a]" },
  info: { badge: "bg-[#eff6ff] border border-[#bfdbfe]", text: "text-[#1d4ed8]", dot: "bg-[#1d4ed8]" },
  warn: { badge: "bg-[#fff7ed] border border-[#fed7aa]", text: "text-[#9a3412]", dot: "bg-[#ea580c]" },
  muted: { badge: "bg-[#f4f6fa] border border-[#dde2ef]", text: "text-[#6b7a9e]", dot: "bg-[#cbd5e1]" },
};

export function ProvenanceBadge({ status }: { status: FieldStatus }) {
  const tone = TONE_STYLES[statusTone(status)];
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono font-medium tracking-wide shrink-0 ${tone.badge} ${tone.text}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${tone.dot}`} />
      {provenanceLabel(status)}
    </span>
  );
}

const COVERAGE_STYLES: Record<CoverageState, { dot: string; chip: string; label: string }> = {
  captured: { dot: "bg-[#16a34a]", chip: "bg-[#dcfce7] border-[#bbf7d0] text-[#166534]", label: "Captured" },
  attention: { dot: "bg-[#ea580c]", chip: "bg-[#fff7ed] border-[#fed7aa] text-[#9a3412]", label: "Retake" },
  missing: { dot: "bg-[#cbd5e1]", chip: "bg-[#f4f6fa] border-[#dde2ef] text-[#6b7a9e]", label: "Missing" },
};

export function CoverageChip({ state, label }: { state: CoverageState; label: string }) {
  const style = COVERAGE_STYLES[state] ?? COVERAGE_STYLES.missing;
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-1 rounded border text-[11px] font-mono whitespace-nowrap ${style.chip}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${style.dot}`} />
      {label}
    </span>
  );
}

export function BackButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 text-blue-200 text-[12px] font-mono mb-4 hover:text-white transition-colors"
    >
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
        <path d="M9 3L5 7l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {label}
    </button>
  );
}

export function Panel({
  title,
  count,
  dot,
  children,
}: {
  title: string;
  count?: string;
  dot?: string;
  children: ReactNode;
}) {
  return (
    <div className="bg-white rounded-lg border border-[#dde2ef] overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[#f0f2f8]">
        {dot && <span className={`w-2 h-2 rounded-full shrink-0 ${dot}`} />}
        <h2 className="text-[11px] font-mono text-[#6b7a9e] tracking-widest uppercase flex-1">{title}</h2>
        {count && <span className="text-[10px] font-mono text-[#a0abbe]">{count}</span>}
      </div>
      {children}
    </div>
  );
}

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="rounded-lg border border-[#fecaca] bg-[#fef2f2] px-4 py-3 flex items-start gap-3">
      <span className="w-2 h-2 rounded-full bg-[#dc2626] shrink-0 mt-1.5" />
      <div className="flex-1 min-w-0">
        <p className="text-[13px] text-[#991b1b] leading-snug">{message}</p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-2 px-3 py-1.5 rounded bg-white border border-[#fecaca] text-[12px] font-mono font-semibold text-[#991b1b] hover:bg-[#fef2f2] active:scale-[0.98] transition-all"
          >
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="w-3.5 h-3.5 rounded-full border-2 border-[#bfdbfe] border-t-[#1d4ed8] animate-spin shrink-0" />
      <span className="text-[12px] font-mono text-[#6b7a9e]">{label}</span>
    </div>
  );
}

/**
 * Step list for photo processing. Deliberately no percentage or progress
 * bar — the browser can't see inside the server's quality-check-then-
 * vision-call sequence, and a fake bar would be the kind of false
 * precision this product argues against everywhere else.
 */
export function ProcessingSteps({ phase }: { phase: ProcessingPhase | null }) {
  return (
    <div className="rounded-lg border-2 border-[#1d4ed8] bg-white p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="w-3.5 h-3.5 rounded-full border-2 border-[#bfdbfe] border-t-[#1d4ed8] animate-spin shrink-0" />
        <span className="text-[10px] font-mono text-[#1d4ed8] tracking-widest uppercase">Processing photo</span>
      </div>
      <ol className="flex flex-col gap-2">
        {PHASE_ORDER.map((step) => {
          const done = isPhaseComplete(step, phase);
          const active = isPhaseActive(step, phase);
          return (
            <li key={step} className="flex items-center gap-2.5">
              <span
                className={`w-4 h-4 rounded-full shrink-0 flex items-center justify-center text-[9px] font-mono ${
                  done
                    ? "bg-[#16a34a] text-white"
                    : active
                      ? "bg-[#1d4ed8] text-white animate-pulse"
                      : "bg-[#e8edf7] text-[#a0abbe]"
                }`}
              >
                {done ? "✓" : ""}
              </span>
              <span
                className={`text-[13px] ${
                  active ? "text-[#0f1523] font-semibold" : done ? "text-[#6b7a9e]" : "text-[#a0abbe]"
                }`}
              >
                {phaseLabel(step)}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="text-[11px] font-mono text-[#a0abbe] mt-3 leading-relaxed">
        Analysis can take a few seconds. Your inspection is saved either way.
      </p>
    </div>
  );
}
