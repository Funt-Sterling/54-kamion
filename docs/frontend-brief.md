# Frontend brief — Kamion Inspect

## Platform recommendation

The original plan specced a React Native (Expo + VisionCamera) native app. **Recommendation: build this as a mobile-responsive web app for v1 instead**, and only wrap it natively if there's time left over. Reasoning:

- Judges appraise **live, on their own photos**, per the brief ("We will hand you photos of trucks that you have never seen"). A web page that opens instantly with no install/build/TestFlight/APK step is a much safer demo surface than a native app judges (or we) have to install on the spot.
- `<input type="file" accept="image/*" capture="environment">` gives camera-or-gallery capture on both iOS and Android browsers with zero native tooling, satisfying "seller with a phone" without React Native, Expo dev builds, or per-platform device testing.
- It removes an entire cross-platform testing axis (Android + iPhone camera/frame-processor parity) from a 24–48 hour budget, freeing time for the pricing/vision pipeline the judges actually score on ("is it sensible", "does it know its limits", "is it interesting").
- Nothing here blocks going native later — the backend API contract (`docs/api-contract.md`) is UI-agnostic.

This is a call made to fit the time budget, not a hard requirement — easy to revisit if the team has a strong reason to keep it native (e.g. someone's already deep into an RN build). Flag before investing more time either way.

## Visual language

Practical inspection interface: large touch targets, readable type, a truck silhouette for coverage status. Status color coding — green = captured, amber = needs attention, gray = unknown — always paired with text/icon, never color alone (accessibility, and it demos better on a projector).

## Screens

| Screen | Required experience |
|---|---|
| **Start** | "Inspect a truck," "Import photos/video," and recent sessions. |
| **Guided capture** | Camera/file input, photo controls, one instruction at a time (`GET /sessions/{id}.next_instruction`), component coverage checklist, connection/upload status. |
| **Evidence review** | Photo tiles with accepted/retake labels (`media.accepted`, `media.reject_reason`); extracted specifications with source; correct or confirm unresolved details (`PATCH /sessions/{id}/details`). |
| **Appraisal** | Price range when `status: priced`; asking-price/VAT/date labels; comparable count; findings list; explicit unknowns; "Improve this appraisal" → back to capture. |
| **Evidence detail** | Original photo/frame, the finding it supports, linked comparable listings with specs and prices. |

## Non-negotiables carried from the plan

- Every finding shown must link back to the photo that produced it (`finding.media_id`).
- Every price shown must link to the comparable listings used (`appraisal.comparables`).
- When `status` isn't `priced`, the refusal reason and the next useful action must be at least as prominent as a price would have been — a refusal is a feature, not an error state to bury.
- English strings for the judged demo; keep copy externalized so Turkish localization is a find-and-replace, not a rewrite.
- Currency formatting: TRY, e.g. `₺960.000`.

## Minimal v1 cut (if time is short)

Start → Guided capture (photo upload only, no live video sampling) → Appraisal. Evidence review and Evidence detail as separate screens can collapse into the Appraisal screen for the first working demo, then split out once the core loop works end-to-end.
