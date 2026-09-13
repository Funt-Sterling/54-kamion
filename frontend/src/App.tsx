import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import type { Appraisal, CaptureOrigin, Finding, SessionDetail } from "@/api/types";
import { normalizeAppraisal, normalizeSession } from "@/lib/contract";
import { errorMessage } from "@/lib/format";
import { QUALITY_PHASE_MS, type BusyState, type ProcessingPhase } from "@/lib/progress";
import { AppraisalScreen } from "@/screens/AppraisalScreen";
import { CaptureScreen, type UploadOutcome } from "@/screens/CaptureScreen";
import { EvidenceDetail } from "@/screens/EvidenceDetail";
import { EvidenceReview } from "@/screens/EvidenceReview";
import { StartScreen } from "@/screens/StartScreen";

type Screen = "start" | "capture" | "review" | "appraisal" | "evidence-detail";

export default function App() {
  const [screen, setScreen] = useState<Screen>("start");
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [appraisal, setAppraisal] = useState<Appraisal | null>(null);
  const [activeFinding, setActiveFinding] = useState<Finding | null>(null);

  /**
   * Exactly one thing can be in flight at a time. Every screen disables
   * its actions and its forward navigation while this is set, which is
   * what stops a judge from tapping through to evidence the backend
   * hasn't finished updating.
   */
  const [busy, setBusy] = useState<BusyState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastOutcome, setLastOutcome] = useState<UploadOutcome | null>(null);
  const [backendUp, setBackendUp] = useState<boolean | null>(null);
  /**
   * True from the moment a mutation reaches the server until a session
   * refresh succeeds. While set, what's on screen may predate the server's
   * evidence, so no range may be presented as current.
   */
  const [sessionStale, setSessionStale] = useState(false);

  /**
   * A ref, not the state flag: two clicks in the same tick both read the
   * pre-update state, so a state-only guard still lets a double-submit
   * through. This closes that window.
   */
  const inFlight = useRef(false);

  const photoUrls = useRef<Map<string, string>>(new Map());
  useEffect(() => {
    const urls = photoUrls.current;
    return () => {
      for (const url of urls.values()) URL.revokeObjectURL(url);
      urls.clear();
    };
  }, []);

  const phaseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearPhaseTimer = useCallback(() => {
    if (phaseTimer.current) {
      clearTimeout(phaseTimer.current);
      phaseTimer.current = null;
    }
  }, []);
  useEffect(() => clearPhaseTimer, [clearPhaseTimer]);

  const checkHealth = useCallback(async () => {
    setBackendUp(null);
    setBackendUp(await api.health());
  }, []);

  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);

  const refreshSession = useCallback(async (sessionId: string) => {
    const detail = normalizeSession(await api.getSession(sessionId));
    setSession(detail);
    setSessionStale(false);
    return detail;
  }, []);

  /**
   * Refresh after a mutation that already succeeded. A failure here must
   * not look like the mutation failed (its result is real and kept), and
   * must not leave old evidence looking current either.
   */
  async function refreshAfterMutation(sessionId: string): Promise<boolean> {
    try {
      await refreshSession(sessionId);
      return true;
    } catch (caught) {
      setSessionStale(true);
      setError(`Saved, but the latest session state could not be loaded: ${errorMessage(caught)} What you see may be out of date.`);
      return false;
    }
  }

  async function retryRefresh() {
    if (!session) return;
    await run({ kind: "refreshing", phase: null }, async () => {
      await refreshSession(session.id);
    });
  }

  /** Wraps every mutating call: one at a time, errors never lose the session. */
  async function run<T>(state: BusyState, work: () => Promise<T>): Promise<T | undefined> {
    if (inFlight.current) return undefined;
    inFlight.current = true;
    setBusy(state);
    setError(null);
    try {
      return await work();
    } catch (caught) {
      setError(errorMessage(caught));
      return undefined;
    } finally {
      inFlight.current = false;
      clearPhaseTimer();
      setBusy(null);
    }
  }

  function setPhase(phase: ProcessingPhase) {
    setBusy((current) => (current?.kind === "processing" ? { ...current, phase } : current));
  }

  async function startInspection() {
    const created = await run({ kind: "starting", phase: null }, async () => {
      const summary = await api.createSession();
      await refreshSession(summary.id);
      return summary;
    });
    if (!created) return;
    setAppraisal(null);
    setLastOutcome(null);
    setSessionStale(false);
    setScreen("capture");
  }

  async function uploadPhoto(file: File, componentHint: string | undefined, origin: CaptureOrigin) {
    if (!session) return;
    await run({ kind: "processing", phase: "uploading" }, async () => {
      const media = await api.uploadPhoto(session.id, file, componentHint, origin, () => {
        // Bytes are on the server now — a real event, not a guess.
        setPhase("checking");
        clearPhaseTimer();
        phaseTimer.current = setTimeout(() => setPhase("analyzing"), QUALITY_PHASE_MS);
      });

      clearPhaseTimer();
      setPhase("updating");

      // The server has changed the evidence: until a refresh lands, the
      // session on screen is behind it.
      setSessionStale(true);
      const previewUrl = URL.createObjectURL(file);
      photoUrls.current.set(media.id, previewUrl);
      setLastOutcome({ media, previewUrl });
      if (!(await refreshAfterMutation(session.id))) {
        setLastOutcome({ media, previewUrl, stale: true });
      }
    });
  }

  async function declareDetail(field: string, value: string, intent: "seller_declared" | "user_corrected" = "seller_declared") {
    if (!session) return;
    await run({ kind: "declaring", phase: null }, async () => {
      await api.declareDetail(session.id, { field, value, intent });
      setSessionStale(true);
      await refreshAfterMutation(session.id);
    });
  }

  async function requestAppraisal() {
    if (!session) return;
    const result = await run({ kind: "appraising", phase: null }, async () => {
      const created = normalizeAppraisal(await api.createAppraisal(session.id));
      // Keep the appraisal the server made even if the refresh below fails;
      // the appraisal screen compares revisions and says so when it can't.
      setAppraisal(created);
      await refreshAfterMutation(session.id);
      return created;
    });
    if (!result) return;
    setScreen("appraisal");
  }

  /** Abandoning mid-flight would orphan the in-flight request's results. */
  const canNavigate = busy === null;

  if (screen === "capture" && session) {
    return (
      <CaptureScreen
        session={session}
        busy={busy}
        canNavigate={canNavigate}
        onBack={() => canNavigate && setScreen("start")}
        onUpload={uploadPhoto}
        onReview={() => canNavigate && setScreen("review")}
        lastOutcome={lastOutcome}
        sessionStale={sessionStale}
        onRefresh={retryRefresh}
        error={error}
        onDismissError={() => setError(null)}
      />
    );
  }

  if (screen === "review" && session) {
    return (
      <EvidenceReview
        session={session}
        busy={busy}
        canNavigate={canNavigate}
        onBack={() => canNavigate && setScreen("start")}
        onCapture={() => canNavigate && setScreen("capture")}
        onAppraise={requestAppraisal}
        onDeclare={declareDetail}
        sessionStale={sessionStale}
        onRefresh={retryRefresh}
        error={error}
        onDismissError={() => setError(null)}
      />
    );
  }

  if (screen === "appraisal" && appraisal && session) {
    return (
      <AppraisalScreen
        appraisal={appraisal}
        session={session}
        sessionStale={sessionStale}
        onRefresh={retryRefresh}
        busy={busy}
        error={error}
        onBack={() => canNavigate && setScreen("review")}
        onImprove={() => canNavigate && setScreen("capture")}
        onFindingTap={(finding) => {
          setActiveFinding(finding);
          setScreen("evidence-detail");
        }}
      />
    );
  }

  if (screen === "evidence-detail" && activeFinding) {
    return (
      <EvidenceDetail
        finding={activeFinding}
        photoUrl={activeFinding.media_id ? (photoUrls.current.get(activeFinding.media_id) ?? null) : null}
        onBack={() => setScreen("appraisal")}
      />
    );
  }

  return (
    <StartScreen
      onInspect={startInspection}
      busy={busy}
      backendUp={backendUp}
      onRecheckHealth={checkHealth}
      error={error}
      onDismissError={() => setError(null)}
    />
  );
}
