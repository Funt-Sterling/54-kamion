import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Last line of defence: a rendering failure shows a recoverable message
 * instead of a blank page. The server session is untouched by a client
 * render error, so reloading resumes nothing but loses nothing server-side.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Intentionally silent: no telemetry exists, and error text can carry
    // response content that should not be echoed to the screen.
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="min-h-screen bg-[#f4f6fa] flex items-center justify-center px-6">
        <div className="max-w-[380px] rounded-lg border border-[#fed7aa] bg-white px-5 py-4">
          <p className="text-[15px] font-semibold text-[#0f1523]">Something on this screen could not be shown.</p>
          <p className="text-[13px] text-[#6b7a9e] mt-1.5 leading-relaxed">
            No price or evidence is shown in this state. Reload to start again.
          </p>
          <button
            onClick={() => window.location.reload()}
            className="mt-3 px-4 py-2 rounded bg-[#1d4ed8] text-[13px] font-semibold text-white"
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
