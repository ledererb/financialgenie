import { useEffect, useState } from "react";
import { useStore } from "@/store";
import type { PdfSummary } from "@/types";

export default function PdfSelector() {
  const pdfs = useStore((s) => s.pdfs);
  const pdfsLoading = useStore((s) => s.pdfsLoading);
  const activePdfId = useStore((s) => s.activePdfId);
  const loadPdfs = useStore((s) => s.loadPdfs);
  const selectPdf = useStore((s) => s.selectPdf);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    loadPdfs();
  }, [loadPdfs]);

  const currentName = pdfs.find((p) => p.pdf_id === activePdfId)?.name ?? "Select a PDF...";

  return (
    <div className="relative">
      <button
        className="flex items-center gap-2 px-3 py-1 text-sm bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded hover:bg-slate-50 dark:hover:bg-slate-700 max-w-[400px] truncate"
        onClick={() => setOpen(!open)}
      >
        <span className="truncate">{currentName}</span>
        <span className="text-slate-400 text-xs">▾</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute top-full left-0 mt-1 w-[500px] max-h-80 overflow-y-auto bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded shadow-lg z-20">
            {pdfsLoading ? (
              <div className="p-2 text-sm text-slate-400">Loading...</div>
            ) : pdfs.length === 0 ? (
              <div className="p-2 text-sm text-slate-400">No PDFs found</div>
            ) : (
              pdfs.map((pdf) => (
                <button
                  key={pdf.pdf_id}
                  className={`w-full text-left px-3 py-1.5 text-sm hover:bg-slate-100 dark:hover:bg-slate-700 truncate ${
                    pdf.pdf_id === activePdfId ? "bg-blue-100 dark:bg-blue-900/40" : ""
                  }`}
                  onClick={() => {
                    selectPdf(pdf.pdf_id);
                    setOpen(false);
                  }}
                >
                  <div className="font-medium truncate">{pdf.name}</div>
                  <div className="text-xs text-slate-400 truncate">
                    {pdf.parent !== "." ? `${pdf.parent}/` : ""}
                    {(pdf.size_bytes / 1024).toFixed(0)} KB
                  </div>
                </button>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
