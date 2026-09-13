import { ErrorBanner } from "@/components/atoms";
import type { BusyState } from "@/lib/progress";

/**
 * Visual design carried over from the Figma StartScreen. The prototype's
 * four hardcoded "recent sessions" (Freightliner / Kenworth / Peterbilt /
 * Volvo) are gone — there is no session-history endpoint, and inventing
 * history during a live demo would be exactly the fake data we're removing.
 *
 * Adds a health preflight: the demo should refuse to start rather than
 * begin an inspection against a backend that isn't answering, which would
 * otherwise surface as a confusing failure two taps later.
 */
export function StartScreen({
  onInspect,
  busy,
  backendUp,
  onRecheckHealth,
  error,
  onDismissError,
}: {
  onInspect: () => void;
  busy: BusyState | null;
  backendUp: boolean | null;
  onRecheckHealth: () => void;
  error: string | null;
  onDismissError: () => void;
}) {
  const starting = busy?.kind === "starting";
  const blocked = backendUp === false;

  return (
    <div className="min-h-screen flex flex-col bg-[#f4f6fa]">
      <div className="bg-[#1d4ed8] px-5 pt-12 pb-10">
        <div className="max-w-[430px] mx-auto">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-2 h-2 rounded-full bg-[#f59e0b]" />
            <span className="text-[11px] font-mono text-blue-200 tracking-widest uppercase">TIRage</span>
          </div>
          <h1
            className="text-[36px] font-bold leading-none tracking-tight text-white mb-2"
            style={{ fontFamily: "var(--font-display)" }}
          >
            Truck
            <br />
            Appraisal
          </h1>
          <p className="text-[12px] font-mono text-blue-200/80 mb-6 leading-relaxed">
            Photo-based valuation against real Turkish market listings.
          </p>

          <button
            onClick={onInspect}
            disabled={!!busy || blocked || backendUp === null}
            className="w-full flex items-center justify-between px-5 py-5 rounded-[8px] bg-white hover:bg-[#f0f4ff] active:scale-[0.98] transition-all duration-150 shadow-[0_4px_24px_rgba(0,0,0,0.18)] disabled:opacity-70 disabled:active:scale-100"
          >
            <div className="text-left">
              <p
                className="text-[22px] font-bold text-[#1d4ed8] leading-tight"
                style={{ fontFamily: "var(--font-display)" }}
              >
                {starting ? "Starting…" : blocked ? "Backend unavailable" : "Inspect a truck"}
              </p>
              <p className="text-[12px] text-[#6b7a9e] font-mono mt-0.5">
                {backendUp === null
                  ? "Checking connection…"
                  : blocked
                    ? "Start the API, then re-check below"
                    : "Upload photos — we'll tell you what's missing"}
              </p>
            </div>
            <div className="w-11 h-11 rounded-full bg-[#1d4ed8] flex items-center justify-center shrink-0">
              <svg width="20" height="20" viewBox="0 0 22 22" fill="none">
                <circle cx="11" cy="11" r="4" fill="white" fillOpacity="0.9" />
                <rect x="2" y="5" width="18" height="13" rx="2" stroke="white" strokeWidth="1.5" strokeOpacity="0.9" />
                <path d="M9 5l1.5-2.5h1L13 5" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeOpacity="0.9" />
              </svg>
            </div>
          </button>
        </div>
      </div>

      <div className="flex-1 max-w-[430px] mx-auto w-full px-4 pt-5 pb-8">
        {blocked && (
          <div className="mb-5">
            <ErrorBanner
              message="The TIRage backend isn't responding. Start it, then re-check — no inspection is started until it's reachable."
              onRetry={onRecheckHealth}
            />
          </div>
        )}

        {error && (
          <div className="mb-5">
            <ErrorBanner message={error} onRetry={onDismissError} />
          </div>
        )}

        <h2 className="text-[11px] font-mono text-[#6b7a9e] tracking-widest uppercase mb-3">How it works</h2>
        <ol className="flex flex-col gap-2.5">
          {[
            "Upload a photo of the truck — any angle, phone quality is fine.",
            "TIRage reads what's actually visible and says what it still can't tell.",
            "It asks for the single most useful next photo.",
            "When there's enough evidence, you get a price range backed by real listings.",
          ].map((step, index) => (
            <li key={step} className="flex gap-3 items-start bg-white border border-[#dde2ef] rounded-lg px-3.5 py-3">
              <span className="w-5 h-5 rounded-full bg-[#e8edf7] text-[#1d4ed8] text-[11px] font-mono font-semibold flex items-center justify-center shrink-0 mt-0.5">
                {index + 1}
              </span>
              <p className="text-[13px] text-[#3b4a6b] leading-relaxed">{step}</p>
            </li>
          ))}
        </ol>

        <div className="mt-6 pt-4 border-t border-[#dde2ef]">
          <p className="text-[11px] font-mono text-[#a0abbe] leading-relaxed">
            Prices are estimated asking-price ranges from comparable Turkish listings — not guaranteed sale values.
          </p>
        </div>
      </div>
    </div>
  );
}
