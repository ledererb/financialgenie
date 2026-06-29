import { useEffect, useMemo, useState, useCallback } from "react";
import { useStore } from "@/store";
import type { MappingField } from "@/types";
import * as api from "@/api/client";

export default function FieldEditorPanel() {
  const activePdfId = useStore((s) => s.activePdfId);
  const mapping = useStore((s) => s.mapping);
  const mappingDirty = useStore((s) => s.mappingDirty);
  const mappingSaving = useStore((s) => s.mappingSaving);
  const mappingError = useStore((s) => s.mappingError);
  const selectedField = useStore((s) => s.selectedField);
  const multiSelected = useStore((s) => s.multiSelected);
  const canonicalFields = useStore((s) => s.canonicalFields);
  const updateMappingField = useStore((s) => s.updateMappingField);
  const saveMapping = useStore((s) => s.saveMapping);
  const setSelected = useStore((s) => s.selectField);

  const field = useMemo(() => {
    if (!mapping || !selectedField) return null;
    return mapping.fields.find((f) => f.pdf_field_name === selectedField) ?? null;
  }, [mapping, selectedField]);

  const [localNotes, setLocalNotes] = useState("");
  const [localCanonical, setLocalCanonical] = useState("");
  const [localConfidence, setLocalConfidence] = useState<string>("manual");

  useEffect(() => {
    if (field) {
      setLocalNotes(field.notes ?? "");
      setLocalCanonical(field.canonical_field ?? "");
      setLocalConfidence(field.confidence ?? "manual");
    }
  }, [field?.pdf_field_name]);

  const handleUpdate = useCallback(
    (patch: Partial<MappingField>) => {
      if (!selectedField) return;
      updateMappingField(selectedField, patch);
    },
    [selectedField, updateMappingField],
  );

  const handleCanonicalUpdate = useCallback(
    (val: string) => {
      setLocalCanonical(val);
      handleUpdate({ canonical_field: val || null });
    },
    [handleUpdate],
  );

  const handleConfidenceUpdate = useCallback(
    (val: string) => {
      setLocalConfidence(val);
      handleUpdate({ confidence: val as MappingField["confidence"] });
    },
    [handleUpdate],
  );

  if (!activePdfId) {
    return (
      <div className="w-72 bg-slate-100 dark:bg-slate-950 border-l border-slate-300 dark:border-slate-800 flex items-center justify-center text-slate-400 text-sm">
        No PDF loaded
      </div>
    );
  }

  if (!selectedField && multiSelected.length === 0) {
    return (
      <div className="w-72 bg-slate-100 dark:bg-slate-950 border-l border-slate-300 dark:border-slate-800 flex flex-col">
        <div className="p-3 border-b border-slate-300 dark:border-slate-800">
          <h3 className="font-medium text-sm text-slate-700 dark:text-slate-300">Field Editor</h3>
        </div>
        <div className="flex-1 flex items-center justify-center text-xs text-slate-400 px-4 text-center">
          Select a field from the list or PDF to edit its mapping
        </div>
        <div className="p-3 border-t border-slate-300 dark:border-slate-800">
          <button
            className={`w-full py-1.5 text-xs font-medium rounded ${
              mappingDirty
                ? "bg-green-500 hover:bg-green-600 text-white"
                : "bg-slate-300 dark:bg-slate-700 text-slate-500 cursor-not-allowed"
            }`}
            disabled={!mappingDirty || mappingSaving}
            onClick={saveMapping}
          >
            {mappingSaving ? "Saving..." : mappingDirty ? "💾 Save Changes" : "No changes"}
          </button>
          {mappingError && (
            <div className="mt-1 text-xs text-red-500">{mappingError}</div>
          )}
        </div>
      </div>
    );
  }

  // Multi-selection mode
  if (multiSelected.length > 0) {
    return (
      <div className="w-72 bg-slate-100 dark:bg-slate-950 border-l border-slate-300 dark:border-slate-800 flex flex-col">
        <div className="p-3 border-b border-slate-300 dark:border-slate-800">
          <h3 className="font-medium text-sm text-slate-700 dark:text-slate-300">
            {multiSelected.length} fields selected
          </h3>
        </div>
        <div className="p-3 text-xs text-slate-600 dark:text-slate-400 flex-1 overflow-auto">
          {multiSelected.map((name) => (
            <div key={name} className="truncate mb-0.5">
              {name}
            </div>
          ))}
        </div>
        <div className="p-3 border-t border-slate-300 dark:border-slate-800 space-y-1">
          <button
            className="w-full py-1 text-xs bg-purple-500 hover:bg-purple-600 text-white rounded"
            onClick={async () => {
              if (!activePdfId) return;
              try {
                await api.createGroup(activePdfId, {
                  member_fields: [...multiSelected],
                  direction: "left_to_right",
                  separator: "",
                });
                // Reload
                const newMapping = await api.getMapping(activePdfId);
                useStore.setState({ mapping: newMapping, multiSelected: [], mappingDirty: true });
              } catch (err) {
                console.error(err);
              }
            }}
          >
            🔗 Create Character Group
          </button>
          <button
            className="w-full py-1 text-xs bg-slate-300 dark:bg-slate-700 text-slate-600 dark:text-slate-300 rounded"
            onClick={() => useStore.getState().clearMultiSelect()}
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (!field) {
    // Field selected but not in mapping yet (unmapped)
    return (
      <div className="w-72 bg-slate-100 dark:bg-slate-950 border-l border-slate-300 dark:border-slate-800 flex flex-col">
        <div className="p-3 border-b border-slate-300 dark:border-slate-800">
          <h3 className="font-medium text-sm text-slate-700 dark:text-slate-300 truncate" title={selectedField!}>
            {selectedField}
          </h3>
          <span className="text-xs text-orange-500">Unmapped field</span>
        </div>
        <div className="p-3 space-y-3 flex-1">
          <div>
            <label className="block text-xs text-slate-500 mb-1">Canonical Field</label>
            <select
              className="w-full text-xs border border-slate-300 dark:border-slate-700 rounded p-1 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100"
              value={localCanonical}
              onChange={(e) => handleCanonicalUpdate(e.target.value)}
            >
              <option value="">— Unmapped —</option>
              {canonicalFields.map((c) => (
                <option key={c.path} value={c.path}>
                  {c.path}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Confidence</label>
            <select
              className="w-full text-xs border border-slate-300 dark:border-slate-700 rounded p-1 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100"
              value={localConfidence}
              onChange={(e) => handleConfidenceUpdate(e.target.value)}
            >
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
              <option value="manual">Manual</option>
            </select>
          </div>
        </div>
        <div className="p-3 border-t border-slate-300 dark:border-slate-800">
          <button
            className={`w-full py-1.5 text-xs font-medium rounded ${
              mappingDirty
                ? "bg-green-500 hover:bg-green-600 text-white"
                : "bg-slate-300 dark:bg-slate-700 text-slate-500 cursor-not-allowed"
            }`}
            disabled={!mappingDirty || mappingSaving}
            onClick={saveMapping}
          >
            {mappingSaving ? "Saving..." : mappingDirty ? "💾 Save Changes" : "No changes"}
          </button>
          {mappingError && (
            <div className="mt-1 text-xs text-red-500">{mappingError}</div>
          )}
        </div>
      </div>
    );
  }

  // Mapped field
  return (
    <div className="w-72 bg-slate-100 dark:bg-slate-950 border-l border-slate-300 dark:border-slate-800 flex flex-col min-h-0">
      <div className="p-3 border-b border-slate-300 dark:border-slate-800">
        <h3 className="font-medium text-sm text-slate-700 dark:text-slate-300 truncate" title={field.pdf_field_name}>
          {field.pdf_field_name}
        </h3>
        <span className="text-xs text-slate-400">{field.field_type} · page {field.page_number}</span>
      </div>

      <div className="p-3 space-y-3 flex-1 overflow-y-auto">
        <div>
          <label className="block text-xs text-slate-500 mb-1">Canonical Field</label>
          <select
            className="w-full text-xs border border-slate-300 dark:border-slate-700 rounded p-1 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100"
            value={localCanonical}
            onChange={(e) => handleCanonicalUpdate(e.target.value)}
          >
            <option value="">— Unmapped —</option>
            {canonicalFields.map((c) => (
              <option key={c.path} value={c.path}>
                {c.path}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs text-slate-500 mb-1">Confidence</label>
          <div className="flex gap-1">
            {(["high", "medium", "low", "manual"] as const).map((level) => (
              <button
                key={level}
                className={`flex-1 py-0.5 text-[10px] rounded border ${
                  localConfidence === level
                    ? "border-blue-500 bg-blue-100 dark:bg-blue-900/40 font-medium"
                    : "border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900"
                }`}
                onClick={() => handleConfidenceUpdate(level)}
              >
                {level}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-xs text-slate-500 mb-1">Notes</label>
          <textarea
            className="w-full text-xs border border-slate-300 dark:border-slate-700 rounded p-1 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 h-16 resize-none"
            value={localNotes}
            onChange={(e) => setLocalNotes(e.target.value)}
            onBlur={() => handleUpdate({ notes: localNotes || null })}
            placeholder="Optional notes..."
          />
        </div>

        {field.coordinates && (
          <div>
            <label className="block text-xs text-slate-500 mb-1">Coordinates</label>
            <div className="grid grid-cols-2 gap-1 text-[10px] text-slate-500 font-mono">
              <span>x: {Math.round(field.coordinates.x)}</span>
              <span>y: {Math.round(field.coordinates.y)}</span>
              <span>w: {Math.round(field.coordinates.width)}</span>
              <span>h: {Math.round(field.coordinates.height)}</span>
            </div>
          </div>
        )}
      </div>

      <div className="p-3 border-t border-slate-300 dark:border-slate-800 flex items-center justify-between">
        <span className="text-[10px] text-slate-400">
          {mappingDirty ? "● Unsaved" : "✓ Saved"}
        </span>
        <button
          className={`px-3 py-1.5 text-xs font-medium rounded ${
            mappingDirty
              ? "bg-green-500 hover:bg-green-600 text-white"
              : "bg-slate-300 dark:bg-slate-700 text-slate-500 cursor-not-allowed"
          }`}
          disabled={!mappingDirty || mappingSaving}
          onClick={saveMapping}
        >
          {mappingSaving ? "..." : "💾 Save"}
        </button>
      </div>
      {mappingError && (
        <div className="px-3 pb-2 text-xs text-red-500">{mappingError}</div>
      )}
    </div>
  );
}
