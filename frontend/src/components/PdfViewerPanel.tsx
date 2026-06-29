import { useEffect, useMemo } from "react";
import { useStore } from "@/store";
import type { PdfField } from "@/types";
import { pageImageUrl } from "@/api/client";

interface Props {
  onFieldClick: (field: PdfField, e: React.MouseEvent) => void;
}

const COLORS: Record<string, string> = {
  high: "rgba(34,197,94,0.35)",
  medium: "rgba(234,179,8,0.35)",
  low: "rgba(234,179,8,0.2)",
  manual: "rgba(59,130,246,0.4)",
  unmapped: "rgba(249,115,22,0.2)",
  selected: "rgba(59,130,246,0.6)",
  group: "rgba(168,85,247,0.4)",
};

export default function PdfViewerPanel({ onFieldClick }: Props) {
  const activePdfId = useStore((s) => s.activePdfId);
  const currentPage = useStore((s) => s.currentPage);
  const zoom = useStore((s) => s.zoom);
  const setCurrentPage = useStore((s) => s.setCurrentPage);
  const setZoom = useStore((s) => s.setZoom);
  const pdfFields = useStore((s) => s.pdfFields);
  const mapping = useStore((s) => s.mapping);
  const selectedField = useStore((s) => s.selectedField);
  const multiSelected = useStore((s) => s.multiSelected);
  const pdfFieldsLoading = useStore((s) => s.pdfFieldsLoading);
  const pageCount = useStore((s) => s.activePdfPageCount);
  const placeFieldMode = useStore((s) => s.placeFieldMode);
  const setPlaceFieldMode = useStore((s) => s.setPlaceFieldMode);

  const pageFields = useMemo(() => {
    if (!pdfFields) return [];
    return pdfFields.filter((f) => f.page_number === currentPage);
  }, [pdfFields, currentPage]);

  const mappingMap = useMemo(() => {
    if (!mapping) return new Map<string, string>();
    const m = new Map<string, string>();
    for (const f of mapping.fields) {
      m.set(f.pdf_field_name, f.confidence ?? "");
      m.set(f.pdf_field_name + "_canon", f.canonical_field ?? "");
    }
    return m;
  }, [mapping]);

  const groupSet = useMemo(() => {
    if (!mapping) return new Set<string>();
    const s = new Set<string>();
    for (const g of mapping.character_groups ?? []) {
      for (const f of g.member_fields) s.add(f);
    }
    return s;
  }, [mapping]);

  function fieldColor(field: PdfField): string {
    if (selectedField === field.pdf_field_name || multiSelected.includes(field.pdf_field_name)) {
      return COLORS.selected;
    }
    if (groupSet.has(field.pdf_field_name)) return COLORS.group;
    const conf = mappingMap.get(field.pdf_field_name);
    if (conf) return COLORS[conf] ?? COLORS.manual;
    return COLORS.unmapped;
  }

  const imgUrl = activePdfId
    ? pageImageUrl(activePdfId, currentPage)
    : null;

  // Keyboard nav
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "F" && e.shiftKey) {
        setPlaceFieldMode(!placeFieldMode);
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [placeFieldMode, setPlaceFieldMode]);

  if (!activePdfId) {
    return (
      <div className="flex-1 flex items-center justify-center bg-slate-200 dark:bg-slate-900 text-slate-400 text-lg">
        Open a PDF to begin
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 min-w-0 bg-slate-200 dark:bg-slate-900">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-300 dark:bg-slate-800 border-b border-slate-400 dark:border-slate-700 shrink-0">
        <div className="flex items-center gap-1">
          <button
            className="px-2 py-0.5 text-xs bg-white dark:bg-slate-700 rounded border border-slate-400 dark:border-slate-600 disabled:opacity-30"
            disabled={currentPage <= 1}
            onClick={() => setCurrentPage(currentPage - 1)}
          >
            ◀ Prev
          </button>
          <span className="text-xs font-mono text-slate-700 dark:text-slate-300 min-w-[80px] text-center">
            {currentPage} / {pageCount}
          </span>
          <button
            className="px-2 py-0.5 text-xs bg-white dark:bg-slate-700 rounded border border-slate-400 dark:border-slate-600 disabled:opacity-30"
            disabled={currentPage >= pageCount}
            onClick={() => setCurrentPage(currentPage + 1)}
          >
            Next ▶
          </button>
        </div>

        <div className="flex items-center gap-1 ml-2">
          <button
            className="px-1.5 text-xs bg-white dark:bg-slate-700 rounded border border-slate-400 dark:border-slate-600"
            onClick={() => setZoom(zoom - 0.25)}
          >
            −
          </button>
          <span className="text-xs font-mono text-slate-600 dark:text-slate-400 w-10 text-center">
            {Math.round(zoom * 100)}%
          </span>
          <button
            className="px-1.5 text-xs bg-white dark:bg-slate-700 rounded border border-slate-400 dark:border-slate-600"
            onClick={() => setZoom(zoom + 0.25)}
          >
            +
          </button>
        </div>

        <button
          className={`ml-auto px-2 py-0.5 text-xs rounded border ${
            placeFieldMode
              ? "bg-orange-200 dark:bg-orange-900 border-orange-400"
              : "bg-white dark:bg-slate-700 border-slate-400 dark:border-slate-600"
          }`}
          onClick={() => setPlaceFieldMode(!placeFieldMode)}
        >
          {placeFieldMode ? "📍 Place Mode" : "🔍 View Mode"}
        </button>

        <span className="text-xs text-slate-500">
          {pageFields.length} fields
        </span>
      </div>

      {/* PDF area */}
      <div className="flex-1 overflow-auto flex items-start justify-center p-2">
        {pdfFieldsLoading ? (
          <div className="text-slate-400 mt-10">Loading page...</div>
        ) : imgUrl ? (
          <div className="relative inline-block" style={{ transform: `scale(${zoom})`, transformOrigin: "top center" }}>
            <img
              src={imgUrl}
              alt={`Page ${currentPage}`}
              className="block shadow-lg"
              style={{ maxWidth: "none", imageRendering: "auto" }}
            />

            {/* Field overlay */}
            {pageFields.map((field) => {
              const rect = field.rect;
              if (!rect || rect.width <= 0 || rect.height <= 0) return null;
              return (
                <div
                  key={field.pdf_field_name}
                  title={`${field.pdf_field_name} (${field.field_type})`}
                  className={`absolute cursor-pointer border ${
                    selectedField === field.pdf_field_name ? "border-blue-500 border-2" : "border-transparent"
                  } hover:border-blue-400 hover:border-2 transition-colors`}
                  style={{
                    left: rect.x,
                    top: rect.y,
                    width: rect.width,
                    height: rect.height,
                    backgroundColor: fieldColor(field),
                  }}
                  onClick={(e) => {
                    if (e.ctrlKey || e.metaKey) {
                      useStore.getState().toggleMultiSelect(field.pdf_field_name);
                    } else {
                      onFieldClick(field, e);
                    }
                  }}
                />
              );
            })}

            {/* Selection rect for place mode */}
            {placeFieldMode && (
              <div className="absolute inset-0 pointer-events-none">
                <div className="text-xs text-orange-600 font-bold p-1 bg-orange-100/70 rounded">
                  Click &amp; drag to place a new field (coming soon)
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="text-red-400 mt-10">Failed to load page image</div>
        )}
      </div>
    </div>
  );
}
