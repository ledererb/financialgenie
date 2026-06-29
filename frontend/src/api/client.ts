// ---------------------------------------------------------------------------
// API client for the FinancialGenie Mapping Editor backend.
// Base URL defaults to http://localhost:8765; the Vite dev server proxies
// /api → backend so requests also work via the same origin.
// ---------------------------------------------------------------------------

import type {
  CanonicalField,
  CharacterGroup,
  CharacterGroupCreate,
  CharacterGroupUpdate,
  MappingConfig,
  MappingField,
  PdfField,
  PdfFieldsResponse,
  PdfInfo,
  PdfSummary,
} from "@/types";

// Empty string = same-origin requests go through Vite proxy (/api → localhost:8765)
export const API_BASE: string =
  (import.meta as unknown as { env?: { VITE_API_BASE?: string } }).env
    ?.VITE_API_BASE ?? "";

const enc = encodeURIComponent;

function qs(params: Record<string, string | number | undefined>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    parts.push(`${enc(k)}=${enc(String(v))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

async function http<T>(
  path: string,
  init?: RequestInit & { query?: Record<string, string | number | undefined> },
): Promise<T> {
  const url = `${API_BASE}${path}${qs(init?.query ?? {})}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as unknown as T;
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

// --- PDF service -----------------------------------------------------------

export async function listPdfs(): Promise<PdfSummary[]> {
  const data = await http<{ pdfs: PdfSummary[] }>("/api/pdfs");
  return data.pdfs;
}

export async function deletePdf(
  pdfId: string,
): Promise<{ deleted: boolean; pdf_id: string; mapping_deleted: boolean }> {
  return http("/api/pdf", {
    method: "DELETE",
    query: { pdf_id: pdfId },
  });
}

export async function getPdfInfo(pdfId: string): Promise<PdfInfo> {
  return http<PdfInfo>("/api/pdf/info", { query: { pdf_id: pdfId } });
}

/** PNG URL for a rendered page (used directly as <img src>). */
export function pageImageUrl(pdfId: string, page: number, dpr = 1): string {
  return `${API_BASE}/api/pdf/page/${page}/image${qs({
    pdf_id: pdfId,
    dpr: dpr !== 1 ? dpr : undefined,
  })}`;
}

export async function getPdfFields(pdfId: string): Promise<PdfFieldsResponse> {
  return http<PdfFieldsResponse>("/api/pdf/fields", { query: { pdf_id: pdfId } });
}

// --- Mapping service -------------------------------------------------------

export async function getMapping(pdfId: string): Promise<MappingConfig> {
  return http<MappingConfig>("/api/mapping", { query: { pdf_id: pdfId } });
}

export async function saveMapping(
  pdfId: string,
  mapping: MappingConfig,
  originalMtime?: number,
): Promise<{ saved: boolean; _mtime?: number }> {
  const body = { ...mapping, _mtime: originalMtime };
  return http("/api/mapping", {
    method: "PUT",
    query: { pdf_id: pdfId },
    body: JSON.stringify(body),
  });
}

export async function updateField(
  pdfId: string,
  field: string,
  patch: Partial<MappingField>,
): Promise<any> {
  return http("/api/mapping/field", {
    method: "PUT",
    query: { pdf_id: pdfId, field },
    body: JSON.stringify(patch),
  });
}

export async function addField(
  pdfId: string,
  payload: Partial<MappingField> & { pdf_field_name: string },
): Promise<any> {
  return http("/api/mapping/field", {
    method: "POST",
    query: { pdf_id: pdfId },
    body: JSON.stringify(payload),
  });
}

export async function deleteField(
  pdfId: string,
  field: string,
): Promise<any> {
  return http("/api/mapping/field", {
    method: "DELETE",
    query: { pdf_id: pdfId, field },
  });
}

export async function createGroup(
  pdfId: string,
  payload: Partial<CharacterGroupCreate> & { member_fields: string[] },
): Promise<CharacterGroup> {
  return http<CharacterGroup>("/api/mapping/group", {
    method: "POST",
    query: { pdf_id: pdfId },
    body: JSON.stringify(payload),
  });
}

export async function updateGroup(
  pdfId: string,
  groupId: string,
  patch: Partial<CharacterGroupUpdate>,
): Promise<CharacterGroup> {
  return http<CharacterGroup>("/api/mapping/group", {
    method: "PUT",
    query: { pdf_id: pdfId, group_id: groupId },
    body: JSON.stringify(patch),
  });
}

export async function deleteGroup(
  pdfId: string,
  groupId: string,
): Promise<{ deleted: boolean }> {
  return http("/api/mapping/group", {
    method: "DELETE",
    query: { pdf_id: pdfId, group_id: groupId },
  });
}

export async function suggestGroups(
  pdfId: string,
): Promise<{ suggestions: CharacterGroup[] }> {
  return http("/api/mapping/suggest-groups", { query: { pdf_id: pdfId } });
}

export async function getCanonicalFields(): Promise<CanonicalField[]> {
  const data = await http<{ fields: CanonicalField[] }>(
    "/api/mapping/canonical-fields",
  );
  return data.fields;
}

export function exportMappingUrl(pdfId: string): string {
  return `${API_BASE}/api/mapping/export${qs({ pdf_id: pdfId })}`;
}

export async function importMapping(
  pdfId: string,
  file: File,
): Promise<{ saved: boolean }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(
    `${API_BASE}/api/mapping/import${qs({ pdf_id: pdfId })}`,
    { method: "POST", body: form },
  );
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json();
}

// --- Recognition service ---------------------------------------------------

export async function startRecognition(
  pdfId: string,
  mode: "auto" | "acroform" | "flat" = "auto",
): Promise<{ status: string; task_id: string }> {
  return http("/api/mapping/recognize", {
    method: "POST",
    query: { pdf_id: pdfId },
    body: JSON.stringify({ mode }),
  });
}

export interface RecognizeStatus {
  task_id: string;
  pdf_id: string;
  status: "running" | "done" | "error";
  progress: number;
  message: string | null;
  error: string | null;
}

export async function recognizeStatus(taskId: string): Promise<RecognizeStatus> {
  return http<RecognizeStatus>(`/api/recognize/${enc(taskId)}/status`);
}

export async function recognizeResult(
  taskId: string,
): Promise<{ task_id: string; mapping: MappingConfig }> {
  return http(`/api/recognize/${enc(taskId)}/result`);
}

export async function uploadPdf(
  file: File,
): Promise<{ success: boolean; pdf_id: string; filename: string; filled_pdf_url: string; message: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/pdf/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

