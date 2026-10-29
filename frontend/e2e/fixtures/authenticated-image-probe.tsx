import { createRoot, type Root } from "react-dom/client";
import "@/api/client";
import AuthenticatedImage from "@/components/AuthenticatedImage";

let root: Root | null = null;
let host: HTMLDivElement | null = null;

function probeRoot(): Root {
  if (!root) {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  }
  return root;
}

function render(sourceUrl: string | null): void {
  probeRoot().render(
    <AuthenticatedImage
      sourceUrl={sourceUrl}
      alt="Authenticated probe"
      data-testid="authenticated-image"
    />,
  );
}

(window as any).mountAuthenticatedImage = render;
(window as any).mountAuthenticatedImageGallery = (count: number) => {
  document.getElementById("root")?.setAttribute("hidden", "true");
  probeRoot().render(
    <div>
      {Array.from({ length: count }, (_, index) => (
        <div key={index} style={{ height: 600 }}>
          <AuthenticatedImage
            sourceUrl={`/api/pdf/page/${index + 1}/image?pdf_id=samples%2Fgallery.pdf`}
            alt={`Gallery ${index + 1}`}
            data-testid="gallery-image"
            loading="lazy"
            style={{ display: "block", width: 200, height: 200 }}
          />
        </div>
      ))}
    </div>,
  );
};
(window as any).unmountAuthenticatedImage = () => {
  root?.unmount();
  host?.remove();
  root = null;
  host = null;
};
