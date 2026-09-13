import type {
  Appraisal,
  CaptureOrigin,
  DeclareDetailBody,
  FieldEvidence,
  MediaResult,
  SessionDetail,
  SessionSummary,
} from "./types";

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

/**
 * Generous: the request covers a real multimodal model call on the server
 * (~10-20s typical), and the backend's own vision timeout is 45s. This has
 * to outlast that so a slow-but-working analysis isn't killed from the
 * client, while still bounding a truly hung request.
 */
const UPLOAD_TIMEOUT_MS = 90_000;

/**
 * A failure that reached us as a real response or a transport error.
 * `offline` distinguishes "the backend isn't reachable" from "the backend
 * said no", because the UI tells the user different things in each case.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly offline: boolean;

  constructor(message: string, status: number, offline = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.offline = offline;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, init);
  } catch {
    // fetch only rejects on transport failure — DNS, refused connection,
    // CORS preflight rejection. The backend never got the request.
    throw new ApiError("Cannot reach the TIRage backend.", 0, true);
  }

  if (!response.ok) {
    throw new ApiError(await describeFailure(response), response.status);
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("The server sent a response we couldn't read.", response.status);
  }
}

async function describeFailure(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // Non-JSON error body — fall through to the generic message.
  }
  return `Request failed (${response.status}).`;
}

export const api = {
  createSession(): Promise<SessionSummary> {
    return request<SessionSummary>("/sessions", { method: "POST" });
  },

  getSession(sessionId: string): Promise<SessionDetail> {
    return request<SessionDetail>(`/sessions/${sessionId}`);
  },

  /** Liveness probe used as a demo preflight. */
  async health(): Promise<boolean> {
    try {
      const response = await fetch(`${BASE_URL}/health`, { method: "GET" });
      return response.ok;
    } catch {
      return false;
    }
  },

  /**
   * Uploads one photo.
   *
   * `component_hint` is the view the user was ASKED for. It is request
   * metadata: it never decides what the photo is found to show, and
   * nothing in the UI may present it as a detection. The server uses it
   * only to say afterwards whether the request was answered.
   *
   * `capture_origin` reports where the bytes came from — "camera" for the
   * in-app `capture="environment"` input, "gallery" for a file the user
   * picked. Both paths previously claimed `source=captured`, which
   * overstated a gallery pick. Neither value is proof of authenticity: a
   * camera origin only means this browser opened a camera intent, and the
   * UI must never present it as evidence the photo is of this vehicle,
   * taken today, or unedited.
   *
   * `source` is kept because the backend still requires it, mapped to the
   * meaning its column documents (captured | imported).
   *
   * Uses XHR rather than fetch so `upload.onprogress` can report when the
   * bytes have actually left the browser. That makes the switch from
   * "uploading" to "the server is working on it" an observed event instead
   * of a guess — the one honest boundary available from out here.
   */
  uploadPhoto(
    sessionId: string,
    file: File,
    componentHint: string | undefined,
    captureOrigin: CaptureOrigin,
    onUploadComplete?: () => void,
  ): Promise<MediaResult> {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", "photo");
    form.append("source", captureOrigin === "camera" ? "captured" : "imported");
    form.append("capture_origin", captureOrigin);
    if (componentHint) form.append("component_hint", componentHint);

    return new Promise<MediaResult>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE_URL}/sessions/${sessionId}/media`);
      xhr.timeout = UPLOAD_TIMEOUT_MS;

      let handedOff = false;
      xhr.upload.onprogress = (event) => {
        if (!handedOff && event.lengthComputable && event.loaded >= event.total) {
          handedOff = true;
          onUploadComplete?.();
        }
      };
      xhr.upload.onload = () => {
        if (!handedOff) {
          handedOff = true;
          onUploadComplete?.();
        }
      };

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as MediaResult);
          } catch {
            reject(new ApiError("The server sent a response we couldn't read.", xhr.status));
          }
          return;
        }
        let detail = `Upload failed (${xhr.status}).`;
        try {
          const body = JSON.parse(xhr.responseText) as { detail?: unknown };
          if (typeof body.detail === "string") detail = body.detail;
        } catch {
          // keep the generic message
        }
        reject(new ApiError(detail, xhr.status));
      };

      xhr.onerror = () => reject(new ApiError("Cannot reach the TIRage backend.", 0, true));
      xhr.ontimeout = () =>
        reject(new ApiError("Photo analysis took too long and timed out.", 0));
      xhr.onabort = () => reject(new ApiError("Upload cancelled.", 0));

      xhr.send(form);
    });
  },

  /**
   * Records a fact the human typed. The client states an INTENT only —
   * the server assigns provenance. Sending `provenance` from here is how
   * a caller used to mint photo-grade evidence with no photo, so this
   * request deliberately cannot express one.
   */
  declareDetail(sessionId: string, body: DeclareDetailBody): Promise<FieldEvidence[]> {
    return request<FieldEvidence[]>(`/sessions/${sessionId}/details`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intent: "seller_declared", ...body }),
    });
  },

  createAppraisal(sessionId: string): Promise<Appraisal> {
    return request<Appraisal>(`/sessions/${sessionId}/appraisals`, { method: "POST" });
  },
};

export { BASE_URL };
