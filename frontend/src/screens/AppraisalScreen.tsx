import { useState } from "react";

import type { Appraisal, Comparable, Finding, SessionDetail } from "@/api/types";
import { BackButton, Panel } from "@/components/atoms";
import {
  appraisalMessage,
  componentLabel,
  deriveUnknowns,
  fieldLabel,
  formatMileage,
  formatPrice,
  humanizeReason,
  splitFindings,
} from "@/lib/format";

/**
 * The prototype's Appraisal layout, with every fabricated number replaced.
 * Gone: the USD range, the invented "asking price", the 14 fake
 * comparables, the confidence pip, and the "$2,400 year-uncertainty band"
 * style claims. What's left is exactly what the pricing engine returned.
 */
export function AppraisalScreen({
  appraisal,
  session,
  onBack,
  onImprove,
  onFindingTap,
}: {
  appraisal: Appraisal;
  session: SessionDetail;
  onBack: () => void;
  onImprove: () => void;
  onFindingTap: (finding: Finding) => void;
}) {
  const [comparablesOpen, setComparablesOpen] = useState(true);
  const priced = appraisal.status === "priced" && appraisal.price_low !== null && appraisal.price_high !== null;
  const { observed, limited } = splitFindings(appraisal.findings);
  const unknowns = deriveUnknowns(session.coverage, session.evidence);
  const message = priced ? null : appraisalMessage(appraisal);

  return (
    <div className="min-h-screen bg-[#f4f6fa] flex flex-col">
      <div className="bg-[#1d4ed8] px-4 pt-12 pb-6">
        <div className="max-w-[430px] mx-auto">
          <BackButton label="Evidence review" onClick={onBack} />
          <p className="text-[10px] font-mono text-blue-300 tracking-widest uppercase mb-1">Appraisal</p>
          <p className="text-[15px] font-semibold text-white mb-5" style={{ fontFamily: "var(--font-display)" }}>
            {describeVehicle(appraisal)}
          </p>

          {priced ? (
            <div className="bg-white/10 rounded-xl px-4 py-4 backdrop-blur-sm border border-white/15">
              <p className="text-[11px] font-mono text-blue-300 uppercase tracking-wider mb-3">
                Estimated asking-price range
              </p>
              <div className="flex items-baseline gap-2 flex-wrap">
                <span
                  className="text-[32px] font-bold text-white tracking-tight leading-none"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  {formatPrice(appraisal.price_low!, appraisal.currency)}
                </span>
                <span className="text-[20px] font-medium text-white/50 leading-none">—</span>
                <span
                  className="text-[32px] font-bold text-white tracking-tight leading-none"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  {formatPrice(appraisal.price_high!, appraisal.currency)}
                </span>
              </div>
              {appraisal.price_mid !== null && (
                <p className="text-[12px] font-mono text-blue-200/80 mt-2">
                  Midpoint {formatPrice(appraisal.price_mid, appraisal.currency)} · from{" "}
                  {appraisal.comparable_count} comparable listing
                  {appraisal.comparable_count === 1 ? "" : "s"}
                </p>
              )}
            </div>
          ) : (
            <div className="bg-white/10 rounded-xl px-4 py-4 backdrop-blur-sm border border-white/15">
              <p className="text-[18px] font-bold text-white leading-snug mb-1.5" style={{ fontFamily: "var(--font-display)" }}>
                {message!.title}
              </p>
              <p className="text-[12px] text-blue-100/90 leading-relaxed">{message!.body}</p>
            </div>
          )}

          {Object.keys(appraisal.matched_attributes).length > 0 && (
            <div className="flex flex-wrap gap-x-4 gap-y-1.5 mt-3">
              {Object.entries(appraisal.matched_attributes).map(([field, value]) => (
                <div key={field}>
                  <p className="text-[10px] font-mono text-blue-300/70">{fieldLabel(field)}</p>
                  <p className="text-[12px] font-semibold text-white/85 font-mono">
                    {field === "mileage_km" ? formatMileage(value) : value}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 max-w-[430px] mx-auto w-full px-4 py-5 flex flex-col gap-4 pb-32">
        {/* Next-best-photo stays visible even after a price: more evidence
            can still change the picture. */}
        {appraisal.next_photo && (
          <div className="rounded-lg border border-[#bfdbfe] bg-[#eff6ff] px-4 py-3">
            <p className="text-[10px] font-mono text-[#1d4ed8] tracking-widest uppercase mb-1">Improve this appraisal</p>
            <p className="text-[13px] text-[#0f1523] leading-snug font-medium">
              {appraisal.next_photo.requested_view}
            </p>
            <p className="text-[12px] text-[#6b7a9e] leading-relaxed mt-1">{appraisal.next_photo.reason}</p>
          </div>
        )}

        {appraisal.comparables.length > 0 && (
          <>
            <button
              onClick={() => setComparablesOpen((open) => !open)}
              className="w-full flex items-center justify-between px-4 py-3 bg-white rounded-lg border border-[#dde2ef] hover:border-[#b8c1d9] transition-colors active:scale-[0.99]"
            >
              <span className="text-[13px] font-semibold text-[#0f1523]" style={{ fontFamily: "var(--font-display)" }}>
                Based on {appraisal.comparable_count} real listing
                {appraisal.comparable_count === 1 ? "" : "s"}
              </span>
              <span className="text-[11px] font-mono text-[#6b7a9e]">{comparablesOpen ? "Hide ↑" : "Show ↓"}</span>
            </button>

            {comparablesOpen && (
              <div className="bg-white rounded-lg border border-[#dde2ef] overflow-hidden -mt-2">
                {appraisal.comparables.map((comparable, index) => (
                  <ComparableRow
                    key={comparable.listing_id}
                    comparable={comparable}
                    last={index === appraisal.comparables.length - 1}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {appraisal.reasons.length > 0 && (
          <div className="rounded-lg border border-[#fde68a] bg-[#fffbeb] px-4 py-3">
            {appraisal.reasons.map((reason) => (
              <p key={reason} className="text-[12px] text-[#92400e] leading-relaxed">
                {humanizeReason(reason)}
              </p>
            ))}
          </div>
        )}

        <Panel title="Findings" count={`${observed.length} items`} dot="bg-[#16a34a]">
          {observed.length === 0 ? (
            <p className="px-4 py-3.5 text-[13px] text-[#6b7a9e]">No condition observations recorded yet.</p>
          ) : (
            <div className="py-1">
              {observed.map((finding, index) => (
                <button
                  key={`${finding.component}-${index}`}
                  onClick={() => onFindingTap(finding)}
                  className={`w-full flex items-start gap-2.5 px-4 py-2.5 text-left hover:bg-[#f8faff] active:bg-[#eef2ff] transition-colors ${
                    index < observed.length - 1 ? "border-b border-[#f4f6fa]" : ""
                  }`}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-[#16a34a] shrink-0 mt-1.5" />
                  <span className="flex-1 min-w-0">
                    <span className="block text-[11px] font-mono text-[#6b7a9e] uppercase tracking-wider">
                      {componentLabel(finding.component)}
                    </span>
                    <span className="block text-[13px] text-[#0f1523] leading-snug">{finding.observation}</span>
                  </span>
                  <span className="text-[11px] font-mono text-[#1d4ed8] shrink-0 mt-0.5">Detail →</span>
                </button>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Unknowns" count={`${unknowns.length + limited.length} items`} dot="bg-[#ea580c]">
          <div className="py-1">
            {limited.map((finding, index) => (
              <div key={`limited-${index}`} className="flex items-start gap-2.5 px-4 py-2.5 border-b border-[#f4f6fa]">
                <span className="w-1.5 h-1.5 rounded-full bg-[#ea580c] shrink-0 mt-1.5" />
                <p className="text-[13px] text-[#0f1523] leading-snug">
                  {componentLabel(finding.component)}: {finding.observation}
                </p>
              </div>
            ))}
            {unknowns.map((unknown, index) => (
              <div
                key={unknown}
                className={`flex items-start gap-2.5 px-4 py-2.5 ${index < unknowns.length - 1 ? "border-b border-[#f4f6fa]" : ""}`}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-[#ea580c] shrink-0 mt-1.5" />
                <p className="text-[13px] text-[#0f1523] leading-snug">{unknown}</p>
              </div>
            ))}
            {unknowns.length === 0 && limited.length === 0 && (
              <p className="px-4 py-3.5 text-[13px] text-[#6b7a9e]">Nothing outstanding.</p>
            )}
          </div>
        </Panel>

        <p className="text-[11px] font-mono text-[#a0abbe] leading-relaxed px-1">
          Unphotographed areas are not assessed — they are not a clean bill of health.
        </p>
      </div>

      <div className="fixed bottom-0 left-0 right-0 bg-white border-t border-[#dde2ef] px-4 py-4">
        <div className="max-w-[430px] mx-auto">
          <button
            onClick={onImprove}
            className="w-full flex items-center justify-between px-5 py-4 rounded-[8px] border-2 border-[#1d4ed8] hover:bg-[#f0f4ff] active:scale-[0.98] transition-all"
          >
            <div className="text-left">
              <p className="text-[17px] font-bold text-[#1d4ed8] leading-tight" style={{ fontFamily: "var(--font-display)" }}>
                Add more evidence
              </p>
              <p className="text-[11px] font-mono text-[#6b7a9e] mt-0.5">
                {appraisal.next_photo
                  ? componentLabel(appraisal.next_photo.resolves)
                  : `${unknowns.length} item${unknowns.length === 1 ? "" : "s"} still unknown`}
              </p>
            </div>
          </button>
        </div>
      </div>
    </div>
  );
}

function ComparableRow({ comparable, last }: { comparable: Comparable; last: boolean }) {
  return (
    <a
      href={comparable.source_url}
      target="_blank"
      rel="noreferrer noopener"
      className={`flex items-center justify-between px-4 py-3 hover:bg-[#f8faff] transition-colors ${
        last ? "" : "border-b border-[#f0f2f8]"
      }`}
    >
      <div className="min-w-0 flex-1 mr-3">
        <p className="text-[12px] text-[#0f1523] font-medium leading-snug truncate">
          {comparable.year} {comparable.make} {comparable.model}
        </p>
        <p className="text-[11px] font-mono text-[#a0abbe] mt-0.5">
          {formatMileage(comparable.mileage_km)} · {comparable.axle_config} · source ↗
        </p>
      </div>
      <span
        className="text-[13px] font-bold text-[#0f1523] font-mono shrink-0"
        style={{ fontFamily: "var(--font-display)" }}
      >
        {formatPrice(comparable.price, comparable.currency)}
      </span>
    </a>
  );
}

function describeVehicle(appraisal: Appraisal): string {
  const { matched_attributes: attrs } = appraisal;
  const parts = [attrs.year, attrs.model_family?.toUpperCase(), attrs.axle_config].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : "Unidentified vehicle";
}
