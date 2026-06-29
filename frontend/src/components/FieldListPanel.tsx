import React, { useMemo } from "react";
import { useStore } from "@/store";
import type { PdfField } from "@/types";

interface Props {
  onFieldClick: (field: PdfField, e: React.MouseEvent) => void;
  onFieldSelect: (field: PdfField, e: React.MouseEvent) => void;
}

export default function FieldListPanel({ onFieldClick, onFieldSelect }: Props) {
  const pdfFields = useStore((s) => s.pdfFields);
  const mapping = useStore((s) => s.mapping);
  const activePdfId = useStore((s) => s.activePdfId);
  const pdfFieldsLoading = useStore((s) => s.pdfFieldsLoading);
  const currentPage = useStore((s) => s.currentPage);
  const setCurrentPage = useStore((s) => s.setCurrentPage);
  const selectedField = useStore((s) => s.selectedField);
  const multiSelected = useStore((s) => s.multiSelected);
  const filterTab = useStore((s) => s.filterTab);
  const setFilterTab = useStore((s) => s.setFilterTab);
  const searchQuery = useStore((s) => s.searchQuery);
  const setSearchQuery = useStore((s) => s.setSearchQuery);
  const setSelected = useStore((s) => s.selectField);

  const mappingMap = useMemo(() => {
    if (!mapping) return new Map<string, { canonical: string; conf: string }>();
    const m = new Map();
    for (const f of mapping.fields) {
      m.set(f.pdf_field_name, { canonical: f.canonical_field ?? "", conf: f.confidence ?? "" });
    }
    return m;
  }, [mapping]);

  const filtered = useMemo(() => {
    let fields = pdfFields.filter((f) => f.page_number === currentPage);

    if (filterTab === "mapped") {
      fields = fields.filter((f) => mappingMap.has(f.pdf_field_name));
    } else if (filterTab === "unmapped") {
      fields = fields.filter((f) => !mappingMap.has(f.pdf_field_name));
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      fields = fields.filter(
        (f) =>
          f.pdf_field_name.toLowerCase().includes(q) ||
          (mappingMap.get(f.pdf_field_name)?.canonical ?? "").toLowerCase().includes(q),
      );
    }

    return fields;
  }, [pdfFields, currentPage, filterTab, searchQuery, mappingMap]);

  const totals = useMemo(() => {
    const total = pdfFields.length;
    const mapped = pdfFields.filter((f) => mappingMap.has(f.pdf_field_name)).length;
    return { total, mapped, unmapped: total - mapped };
  }, [pdfFields, mappingMap]);

  const confColor = (conf: string) => {
    switch (conf) {
      case "high": return "bg-green-500";
      case "medium": return "bg-yellow-500";
      case "low": return "bg-yellow-300";
      case "manual": return "bg-blue-500";
      default: return "bg-orange-400";
    }
  };

  if (!activePdfId) {
    return (
      <div className="w-72 bg-slate-100 dark:bg-slate-950 border-r border-slate-300 dark:border-slate-800 flex items-center justify-center text-slate-400 text-sm">
        No PDF loaded
      </div>
    );
  }

  return (
    <div className="w-72 bg-slate-100 dark:bg-slate-950 border-r border-slate-300 dark:border-slate-800 flex flex-col min-h-0">
      {/* Search */}
      <div className="p-2 border-b border-slate-300 dark:border-slate-800">
        <input
          type="text"
          placeholder="Search fields..."
          className="w-full px-2 py-1 text-xs border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 placeholder-slate-400"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
      </div>

      {/* Filter tabs */}
      <div className="flex border-b border-slate-300 dark:border-slate-800 text-xs">
        {(["all", "mapped", "unmapped"] as const).map((tab) => (
          <button
            key={tab}
            className={`flex-1 py-1.5 text-center ${
              filterTab === tab
                ? "bg-blue-500 text-white font-medium"
                : "text-slate-500 hover:bg-slate-200 dark:hover:bg-slate-800"
            }`}
            onClick={() => setFilterTab(tab)}
          >
            {tab === "all" && `All (${totals.total})`}
            {tab === "mapped" && `Mapped (${totals.mapped})`}
            {tab === "unmapped" && `Unmapped (${totals.unmapped})`}
          </button>
        ))}
      </div>

      {/* Field list */}
      <div className="flex-1 overflow-y-auto">
        {pdfFieldsLoading ? (
          <div className="p-3 text-xs text-slate-400">Loading...</div>
        ) : filtered.length === 0 ? (
          <div className="p-3 text-xs text-slate-400">No fields on this page</div>
        ) : (
          filtered.map((field) => {
            const map = mappingMap.get(field.pdf_field_name);
            const isSelected = selectedField === field.pdf_field_name;
            const isMulti = multiSelected.includes(field.pdf_field_name);

            return (
              <div
                key={field.pdf_field_name}
                className={`flex items-center gap-2 px-2 py-1 cursor-pointer text-xs border-b border-slate-200 dark:border-slate-800 hover:bg-slate-200 dark:hover:bg-slate-800 transition-colors ${
                  isSelected ? "bg-blue-100 dark:bg-blue-900/40" : ""
                } ${isMulti ? "bg-purple-100 dark:bg-purple-900/30" : ""}`}
                onClick={(e) => {
                  if (e.ctrlKey || e.metaKey) {
                    onFieldSelect(field, e);
                  } else {
                    onFieldClick(field, e);
                  }
                }}
              >
                <span
                  className={`w-2 h-2 rounded-full shrink-0 ${confColor(map?.conf ?? "")}`}
                  title={map?.conf || "Unmapped"}
                />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-slate-800 dark:text-slate-200 truncate">
                    {field.pdf_field_name}
                  </div>
                  {map?.canonical && (
                    <div className="text-slate-400 dark:text-slate-500 truncate">
                      → {map.canonical}
                    </div>
                  )}
                  {!map && (
                    <div className="text-slate-400 italic">— unmapped</div>
                  )}
                </div>
                <span className="text-slate-400 shrink-0 bg-slate-200 dark:bg-slate-800 px-1 rounded text-[10px]">
                  {field.field_type}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
