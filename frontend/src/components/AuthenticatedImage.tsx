import { useEffect, useRef, useState } from "react";
import type { ImgHTMLAttributes } from "react";
import { useAuthenticatedBlobUrl } from "@/lib/useAuthenticatedBlobUrl";

interface AuthenticatedImageProps
  extends Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> {
  sourceUrl: string | null;
  alt: string;
}

const TRANSPARENT_PIXEL =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";
const VIEWPORT_ROOT_MARGIN = "300px 0px";

/** Image element for protected URLs, with authenticated fetch and blob cleanup. */
export default function AuthenticatedImage({
  sourceUrl,
  alt,
  ...imageProps
}: AuthenticatedImageProps) {
  const imageRef = useRef<HTMLImageElement>(null);
  const [shouldLoad, setShouldLoad] = useState(false);
  const { blobUrl, loading, error } = useAuthenticatedBlobUrl(sourceUrl, shouldLoad);
  const { onLoad, ...layoutProps } = imageProps;

  useEffect(() => {
    if (shouldLoad) return;
    const image = imageRef.current;
    if (!image) return;

    if (typeof IntersectionObserver === "undefined") {
      setShouldLoad(true);
      return;
    }

    let active = true;
    const observer = new IntersectionObserver(
      (entries) => {
        if (active && entries.some((entry) => entry.isIntersecting)) {
          setShouldLoad(true);
        }
      },
      { rootMargin: VIEWPORT_ROOT_MARGIN },
    );
    observer.observe(image);
    return () => {
      active = false;
      observer.disconnect();
    };
  }, [sourceUrl, shouldLoad]);

  return (
    <>
      {error && <span role="alert">A kép nem tölthető be: {error}</span>}
      {loading && <span role="status">Oldalkép betöltése…</span>}
      <img
        {...layoutProps}
        ref={imageRef}
        src={blobUrl ?? TRANSPARENT_PIXEL}
        alt={alt}
        onLoad={(event) => {
          if (blobUrl) onLoad?.(event);
        }}
      />
    </>
  );
}
