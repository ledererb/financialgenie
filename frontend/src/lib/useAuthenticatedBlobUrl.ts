import { useEffect, useState } from "react";

interface AuthenticatedBlobState {
  blobUrl: string | null;
  loading: boolean;
  error: string | null;
}

const IDLE_STATE: AuthenticatedBlobState = {
  blobUrl: null,
  loading: false,
  error: null,
};

/** Fetch a protected resource through the intercepted fetch and own its blob URL. */
export function useAuthenticatedBlobUrl(
  sourceUrl: string | null,
  enabled = true,
): AuthenticatedBlobState {
  const [state, setState] = useState<AuthenticatedBlobState>(IDLE_STATE);

  useEffect(() => {
    if (!sourceUrl || !enabled) {
      setState(IDLE_STATE);
      return;
    }

    let cancelled = false;
    let objectUrl: string | null = null;
    const controller = new AbortController();
    setState({ blobUrl: null, loading: true, error: null });

    const load = async () => {
      try {
        const response = await fetch(sourceUrl, { signal: controller.signal });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const blob = await response.blob();
        if (cancelled) return;

        objectUrl = URL.createObjectURL(blob);
        setState({ blobUrl: objectUrl, loading: false, error: null });
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") {
          if (!cancelled) setState(IDLE_STATE);
          return;
        }
        if (!cancelled) {
          setState({
            blobUrl: null,
            loading: false,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      }
    };

    void load();
    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [sourceUrl, enabled]);

  return state;
}
