import type { Finding } from "@/api/types";
import { BackButton } from "@/components/atoms";
import { componentLabel } from "@/lib/format";

/**
 * The prototype's EvidenceDetail, minus the parts the backend can't back
 * up: the fabricated video timestamps ("frame at 0:34"), the per-finding
 * "impact on price" claims, and the mock comparable reasoning. The backend
 * links findings to a photo and a visibility level, so that is what's shown.
 */
export function EvidenceDetail({
  finding,
  photoUrl,
  onBack,
}: {
  finding: Finding;
  photoUrl: string | null;
  onBack: () => void;
}) {
  const visibilityNote: Record<string, string> = {
    clear: "This component was clearly visible in the photo.",
    partial: "Only partially visible — the assessment is limited to what was in frame.",
    obstructed: "Not visible in this photo. Nothing about its condition was established.",
  };

  return (
    <div className="min-h-screen bg-[#f4f6fa] flex flex-col">
      <div className="bg-[#1d4ed8] px-4 pt-12 pb-6">
        <div className="max-w-[430px] mx-auto">
          <BackButton label="Appraisal" onClick={onBack} />
          <p className="text-[10px] font-mono text-blue-300 tracking-widest uppercase mb-1">Evidence detail</p>
          <p className="text-[18px] font-semibold text-white leading-snug" style={{ fontFamily: "var(--font-display)" }}>
            {componentLabel(finding.component)}
          </p>
        </div>
      </div>

      <div className="flex-1 max-w-[430px] mx-auto w-full px-4 py-5 flex flex-col gap-4">
        {photoUrl ? (
          <div className="rounded-lg overflow-hidden border border-[#dde2ef] bg-[#e8edf7]">
            <img src={photoUrl} alt={finding.component} className="w-full object-cover" />
          </div>
        ) : (
          <div className="rounded-lg border border-[#dde2ef] bg-white px-4 py-6 text-center">
            <p className="text-[13px] text-[#6b7a9e]">
              The source photo isn't available in this session view.
            </p>
          </div>
        )}

        <div className="bg-white rounded-lg border border-[#dde2ef] p-4">
          <p className="text-[11px] font-mono text-[#6b7a9e] uppercase tracking-wider mb-2">Observation</p>
          <p className="text-[14px] text-[#0f1523] leading-relaxed">{finding.observation}</p>
        </div>

        <div className="bg-white rounded-lg border border-[#dde2ef] p-4">
          <p className="text-[11px] font-mono text-[#6b7a9e] uppercase tracking-wider mb-2">What was visible</p>
          <p className="text-[13px] text-[#3b4a6b] leading-relaxed">
            {visibilityNote[finding.visibility] ?? `Visibility: ${finding.visibility}`}
          </p>
        </div>

        {finding.recommended_action !== "none" && (
          <div className="rounded-lg border border-[#fed7aa] bg-[#fff7ed] p-4">
            <p className="text-[11px] font-mono text-[#9a3412] uppercase tracking-wider mb-2">Recommended next step</p>
            <p className="text-[13px] text-[#9a3412] leading-relaxed">{finding.recommended_action}</p>
          </div>
        )}

        <p className="text-[11px] font-mono text-[#a0abbe] leading-relaxed px-1">
          This is a visual observation only. It does not establish mechanical condition.
        </p>
      </div>
    </div>
  );
}
