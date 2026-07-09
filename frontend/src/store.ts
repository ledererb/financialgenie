import { create } from "zustand";
import type {
  MappingConfig,
  MappingField,
  CanonicalField,
  PdfField,
  PdfSummary,
} from "@/types";
import {
  listPdfs,
  getMapping,
  saveMapping as apiSaveMapping,
  updateField as apiUpdateField,
  getPdfFields,
  getCanonicalFields,
  uploadPdf,
  startRecognition,
  recognizeStatus,
} from "@/api/client";

interface EditorState {
  // PDF list
  pdfs: PdfSummary[];
  pdfsLoading: boolean;

  // Active PDF
  activePdfId: string | null;
  activePdfPageCount: number;
  activePdfHasAcro: boolean;

  // Fields
  pdfFields: PdfField[];
  pdfFieldsLoading: boolean;

  // Mapping
  mapping: MappingConfig | null;
  mappingLoading: boolean;
  mappingDirty: boolean;
  mappingSaving: boolean;
  mappingError: string | null;

  // Selection
  selectedField: string | null;
  multiSelected: string[];

  // Upload
  uploading: boolean;
  uploadError: string | null;
  uploadedResult: { pdfId: string; filledPdfUrl: string } | null;

  // Recognition
  recognizing: boolean;
  recognitionError: string | null;

  // Canonical fields
  canonicalFields: CanonicalField[];

  // UI
  currentPage: number;
  zoom: number;
  filterTab: "all" | "mapped" | "unmapped";
  searchQuery: string;
  placeFieldMode: boolean;
  darkMode: boolean;

  // Actions
  loadPdfs: () => Promise<void>;
  selectPdf: (pdfId: string) => Promise<void>;
  setCurrentPage: (page: number) => void;
  setZoom: (zoom: number) => void;
  setFilterTab: (tab: "all" | "mapped" | "unmapped") => void;
  setSearchQuery: (q: string) => void;
  selectField: (name: string | null) => void;
  toggleMultiSelect: (name: string) => void;
  clearMultiSelect: () => void;
  updateMappingField: (field: string, patch: Partial<MappingField>) => Promise<void>;
  saveMapping: () => Promise<void>;
  setPlaceFieldMode: (v: boolean) => void;
  toggleDarkMode: () => void;
  uploadPdfFile: (file: File) => Promise<void>;
  clearUploadedResult: () => void;
  runAiRecognition: () => Promise<void>;
  clearRecognitionError: () => void;
}

export const useStore = create<EditorState>((set, get) => ({
  pdfs: [],
  pdfsLoading: false,
  activePdfId: null,
  activePdfPageCount: 0,
  activePdfHasAcro: false,
  pdfFields: [],
  pdfFieldsLoading: false,
  mapping: null,
  mappingLoading: false,
  mappingDirty: false,
  mappingSaving: false,
  mappingError: null,
  selectedField: null,
  multiSelected: [],
  uploading: false,
  uploadError: null,
  uploadedResult: null,
  recognizing: false,
  recognitionError: null,
  canonicalFields: [],
  currentPage: 0,
  zoom: 1,
  filterTab: "all",
  searchQuery: "",
  placeFieldMode: false,
  darkMode: false,

  loadPdfs: async () => {
    set({ pdfsLoading: true });
    try {
      const pdfs = await listPdfs();
      set({ pdfs, pdfsLoading: false });
    } catch {
      set({ pdfsLoading: false });
    }
  },

  selectPdf: async (pdfId: string) => {
    set({
      activePdfId: pdfId,
      mappingLoading: true,
      pdfFieldsLoading: true,
      selectedField: null,
      multiSelected: [],
      currentPage: 0,
      mapping: null,
      mappingDirty: false,
      mappingError: null,
    });

    try {
      const [mapping, fieldData, canonicals] = await Promise.all([
        getMapping(pdfId),
        getPdfFields(pdfId),
        getCanonicalFields(),
      ]);
      set({
        mapping,
        pdfFields: fieldData.fields,
        activePdfPageCount: fieldData.total_pages,
        activePdfHasAcro: fieldData.has_acroform,
        canonicalFields: canonicals,
        mappingLoading: false,
        pdfFieldsLoading: false,
        mappingDirty: false,
        currentPage: 1,
      });
    } catch (e) {
      set({
        mappingLoading: false,
        pdfFieldsLoading: false,
        mappingError: (e as Error).message,
      });
    }
  },

  setCurrentPage: (page) => set({ currentPage: page }),
  setZoom: (zoom) => set({ zoom: Math.max(0.5, Math.min(2.5, zoom)) }),
  setFilterTab: (tab) => set({ filterTab: tab }),
  setSearchQuery: (q) => set({ searchQuery: q }),

  selectField: (name) => set({ selectedField: name, multiSelected: [] }),

  toggleMultiSelect: (name) => {
    const { multiSelected } = get();
    if (multiSelected.includes(name)) {
      set({ multiSelected: multiSelected.filter((n) => n !== name) });
    } else {
      set({ multiSelected: [...multiSelected, name] });
    }
  },

  clearMultiSelect: () => set({ multiSelected: [] }),

  updateMappingField: async (field, patch) => {
    const { activePdfId, mapping } = get();
    if (!activePdfId || !mapping) return;

    const updatedFields = mapping.fields.map((f) =>
      f.pdf_field_name === field ? { ...f, ...patch, confidence: patch.confidence ?? "manual" } : f,
    );
    const updatedMapping = { ...mapping, fields: updatedFields };
    set({ mapping: updatedMapping, mappingDirty: true });

    try {
      const res = await apiUpdateField(activePdfId, field, patch);
      if (res && res._mtime) {
        set((state) => {
          if (!state.mapping) return {};
          return {
            mapping: {
              ...state.mapping,
              _mtime: res._mtime,
            },
          };
        });
      }
    } catch {
      // keep optimistic update
    }
  },

  saveMapping: async () => {
    const { activePdfId, mapping } = get();
    if (!activePdfId || !mapping) return;
    set({ mappingSaving: true });
    try {
      const res = await apiSaveMapping(activePdfId, mapping, mapping._mtime);
      set((state) => {
        if (!state.mapping) return { mappingSaving: false, mappingDirty: false };
        return {
          mapping: {
            ...state.mapping,
            _mtime: res._mtime,
          },
          mappingSaving: false,
          mappingDirty: false,
        };
      });
    } catch (e) {
      set({ mappingSaving: false, mappingError: (e as Error).message });
    }
  },

  setPlaceFieldMode: (v) => set({ placeFieldMode: v }),
  toggleDarkMode: () => set((s) => ({ darkMode: !s.darkMode })),

  uploadPdfFile: async (file: File) => {
    set({ uploading: true, uploadError: null, uploadedResult: null });
    try {
      const res = await uploadPdf(file);
      // Reload the PDFs list so the uploaded file appears
      await get().loadPdfs();
      set({
        uploading: false,
        uploadedResult: {
          pdfId: res.pdf_id,
          filledPdfUrl: res.filled_pdf_url,
        },
      });
    } catch (e) {
      set({ uploading: false, uploadError: (e as Error).message });
    }
  },

  clearUploadedResult: () => set({ uploadedResult: null, uploadError: null }),
  runAiRecognition: async () => {
    const { activePdfId } = get();
    if (!activePdfId) return;
    set({ recognizing: true, recognitionError: null });
    try {
      const { task_id } = await startRecognition(activePdfId, "auto");
      
      let attempts = 0;
      const maxAttempts = 120; // 4 minutes max
      
      while (attempts < maxAttempts) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        attempts++;
        
        const statusRes = await recognizeStatus(task_id);
        if (statusRes.status === "done") {
          break;
        }
        if (statusRes.status === "error") {
          throw new Error(statusRes.error || "AI recognition failed.");
        }
      }
      
      if (attempts >= maxAttempts) {
        throw new Error("AI recognition timed out.");
      }
      
      // Reload PDF mapping and fields
      await get().selectPdf(activePdfId);
      set({ recognizing: false });
    } catch (e) {
      set({ recognizing: false, recognitionError: (e as Error).message });
    }
  },
  clearRecognitionError: () => set({ recognitionError: null }),
}));
