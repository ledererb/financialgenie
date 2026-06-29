import { useCallback } from "react";
import { useStore } from "@/store";
import type { PdfField } from "@/types";
import PdfSelector from "@/components/PdfSelector";
import FieldListPanel from "@/components/FieldListPanel";
import PdfViewerPanel from "@/components/PdfViewerPanel";
import FieldEditorPanel from "@/components/FieldEditorPanel";

export default function App() {
  const darkMode = useStore((s) => s.darkMode);
  const toggleDarkMode = useStore((s) => s.toggleDarkMode);

  const handleFieldClick = useCallback(
    (field: PdfField, e: React.MouseEvent) => {
      useStore.getState().selectField(field.pdf_field_name);
    },
    [],
  );

  const handleFieldSelect = useCallback(
    (field: PdfField, e: React.MouseEvent) => {
      useStore.getState().toggleMultiSelect(field.pdf_field_name);
    },
    [],
  );

  return (
    <div className={`h-full flex flex-col ${darkMode ? "dark" : ""}`}>
      {/* Top bar */}
      <header className="flex items-center gap-3 px-3 py-1.5 bg-slate-200 dark:bg-slate-900 border-b border-slate-300 dark:border-slate-700 shrink-0">
        <h1 className="font-bold text-sm text-slate-800 dark:text-slate-200 whitespace-nowrap">
          🧬 FinancialGenie Mapping Editor
        </h1>
        <PdfSelector />
        <div className="flex-1" />
        <button
          className="text-xs px-2 py-1 rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800"
          onClick={toggleDarkMode}
        >
          {darkMode ? "☀️" : "🌙"}
        </button>
      </header>

      {/* Three-panel layout */}
      <div className="flex-1 flex min-h-0">
        <FieldListPanel onFieldClick={handleFieldClick} onFieldSelect={handleFieldSelect} />
        <PdfViewerPanel onFieldClick={handleFieldClick} />
        <FieldEditorPanel />
      </div>
    </div>
  );
}
