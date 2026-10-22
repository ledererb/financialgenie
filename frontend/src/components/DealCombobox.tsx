import { useState, useRef, useEffect, useMemo, useId } from "react";

export interface DealOption {
  Id: string;
  Name?: string;
  StageName?: string;
  CloseDate?: string;
  AccountName?: string;
}

interface DealComboboxProps {
  deals: DealOption[];
  value: string;
  onChange: (dealId: string) => void;
  disabled?: boolean;
  inputId?: string;
  placeholder?: string;
}

function foldText(str: string): string {
  return str
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

export default function DealCombobox({
  deals,
  value,
  onChange,
  disabled = false,
  inputId,
  placeholder = "Keresés név, státusz vagy azonosító alapján...",
}: DealComboboxProps) {
  const generatedId = useId();
  const controlId = inputId || `deal-combobox-${generatedId}`;
  const listboxId = `deal-listbox-${generatedId}`;

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlightedIndex, setHighlightedIndex] = useState(0);

  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const selectedDeal = useMemo(
    () => deals.find((d) => d.Id === value) || null,
    [deals, value],
  );

  const filteredDeals = useMemo(() => {
    const trimmed = query.trim();
    if (!trimmed) return deals;
    const tokens = foldText(trimmed).split(/\s+/).filter(Boolean);
    return deals.filter((d) => {
      const haystack = foldText(
        `${d.Name || ""} ${d.StageName || ""} ${d.AccountName || ""} ${d.Id || ""}`,
      );
      return tokens.every((tok) => haystack.includes(tok));
    });
  }, [deals, query]);

  // Reset highlighted index when query or open state changes
  useEffect(() => {
    if (!open) return;
    const idx = filteredDeals.findIndex((d) => d.Id === value);
    setHighlightedIndex(idx >= 0 ? idx : 0);
  }, [open, query, filteredDeals, value]);

  // Scroll highlighted option into view
  useEffect(() => {
    if (!open || !listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(
      `[data-deal-idx="${highlightedIndex}"]`,
    );
    if (el) {
      el.scrollIntoView({ block: "nearest" });
    }
  }, [highlightedIndex, open]);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handleMouseDown = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", handleMouseDown);
    return () => document.removeEventListener("mousedown", handleMouseDown);
  }, [open]);

  const handleSelect = (dealId: string) => {
    onChange(dealId);
    setOpen(false);
    setQuery("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setHighlightedIndex((prev) =>
        filteredDeals.length === 0 ? 0 : (prev + 1) % filteredDeals.length,
      );
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setHighlightedIndex((prev) =>
        filteredDeals.length === 0
          ? 0
          : (prev - 1 + filteredDeals.length) % filteredDeals.length,
      );
    } else if (e.key === "Enter") {
      if (open && filteredDeals[highlightedIndex]) {
        e.preventDefault();
        handleSelect(filteredDeals[highlightedIndex].Id);
      }
    } else if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        e.stopPropagation();
        setOpen(false);
        setQuery("");
        inputRef.current?.blur();
      }
    }
  };

  const displayText = selectedDeal
    ? `${selectedDeal.Name || selectedDeal.Id}${
        selectedDeal.StageName ? ` (${selectedDeal.StageName})` : ""
      }`
    : "";

  const activeDescendantId =
    open && filteredDeals[highlightedIndex]
      ? `${listboxId}-opt-${highlightedIndex}`
      : undefined;

  return (
    <div
      ref={containerRef}
      style={{ position: "relative", width: "100%" }}
    >
      <div
        style={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          width: "100%",
        }}
      >
        {/* Search icon */}
        <svg
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--text-tertiary)"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{
            position: "absolute",
            left: 11,
            pointerEvents: "none",
            flexShrink: 0,
          }}
        >
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>

        <input
          ref={inputRef}
          id={controlId}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-activedescendant={activeDescendantId}
          aria-autocomplete="list"
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          disabled={disabled}
          value={open ? query : displayText}
          placeholder={open && selectedDeal ? `${displayText} — írj a szűréshez...` : placeholder}
          onFocus={() => {
            if (!disabled) {
              setOpen(true);
            }
          }}
          onClick={() => {
            if (!disabled && !open) {
              setOpen(true);
            }
          }}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!open) setOpen(true);
          }}
          onKeyDown={handleKeyDown}
          style={{
            width: "100%",
            padding: "9px 36px 9px 34px",
            background: "var(--bg-tertiary)",
            border: open
              ? "1px solid var(--accent-blue)"
              : "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-sm)",
            color: "var(--text-primary)",
            fontSize: "0.85rem",
            outline: "none",
            boxSizing: "border-box",
            cursor: disabled ? "not-allowed" : "text",
            opacity: disabled ? 0.6 : 1,
            transition: "border-color 0.15s ease",
          }}
        />

        {/* Clear query button or dropdown chevron */}
        {open && query ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setQuery("");
              inputRef.current?.focus();
            }}
            aria-label="Keresés törlése"
            style={{
              position: "absolute",
              right: 8,
              background: "transparent",
              border: "none",
              color: "var(--text-tertiary)",
              cursor: "pointer",
              padding: "2px 6px",
              fontSize: "0.9rem",
              lineHeight: 1,
            }}
          >
            ✕
          </button>
        ) : (
          <button
            type="button"
            tabIndex={-1}
            disabled={disabled}
            onClick={() => {
              if (disabled) return;
              if (open) {
                setOpen(false);
                setQuery("");
              } else {
                setOpen(true);
                inputRef.current?.focus();
              }
            }}
            aria-label="Ügylet lista megnyitása"
            style={{
              position: "absolute",
              right: 6,
              background: "transparent",
              border: "none",
              color: "var(--text-tertiary)",
              cursor: disabled ? "not-allowed" : "pointer",
              padding: "4px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              style={{
                transform: open ? "rotate(180deg)" : "none",
                transition: "transform 0.15s ease",
              }}
            >
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
        )}
      </div>

      {/* Dropdown listbox */}
      {open && (
        <div
          id={listboxId}
          role="listbox"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            zIndex: 1200,
            background: "var(--bg-secondary)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-sm)",
            boxShadow: "0 12px 32px rgba(0, 0, 0, 0.45)",
            overflow: "hidden",
          }}
        >
          {/* Header status bar */}
          <div
            style={{
              padding: "6px 12px",
              fontSize: "0.72rem",
              color: "var(--text-tertiary)",
              borderBottom: "1px solid var(--border-subtle)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              background: "var(--bg-tertiary)",
            }}
          >
            <span>
              {query.trim()
                ? `${filteredDeals.length} találat (összesen ${deals.length} ügylet)`
                : `${deals.length} elérhető ügylet`}
            </span>
            <span>↑↓ navigáció • Enter kiválasztás</span>
          </div>

          <div
            ref={listRef}
            style={{
              maxHeight: 280,
              overflowY: "auto",
            }}
          >
            {filteredDeals.length === 0 ? (
              <div
                style={{
                  padding: "16px 12px",
                  fontSize: "0.85rem",
                  color: "var(--text-tertiary)",
                  textAlign: "center",
                }}
              >
                Nincs találat erre: „{query}”
              </div>
            ) : (
              filteredDeals.map((d, idx) => {
                const isSelected = d.Id === value;
                const isHighlighted = idx === highlightedIndex;
                return (
                  <div
                    key={d.Id}
                    id={`${listboxId}-opt-${idx}`}
                    role="option"
                    aria-selected={isSelected}
                    data-deal-idx={idx}
                    onMouseEnter={() => setHighlightedIndex(idx)}
                    onMouseDown={(e) => {
                      // Prevent input blur before selection
                      e.preventDefault();
                      handleSelect(d.Id);
                    }}
                    style={{
                      padding: "8px 12px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: "10px",
                      cursor: "pointer",
                      background: isHighlighted
                        ? "var(--bg-hover)"
                        : isSelected
                          ? "rgba(59, 130, 246, 0.1)"
                          : "transparent",
                      borderBottom: "1px solid rgba(255, 255, 255, 0.03)",
                      fontSize: "0.84rem",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                        minWidth: 0,
                        flex: 1,
                      }}
                    >
                      <span
                        style={{
                          width: 14,
                          color: "var(--accent-blue)",
                          fontWeight: 700,
                          flexShrink: 0,
                        }}
                      >
                        {isSelected ? "✓" : ""}
                      </span>
                      <span
                        style={{
                          color: "var(--text-primary)",
                          fontWeight: isSelected ? 600 : 400,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {d.Name || d.Id}
                      </span>
                    </div>

                    {d.StageName && (
                      <span
                        style={{
                          fontSize: "0.72rem",
                          padding: "2px 7px",
                          borderRadius: "999px",
                          background: "var(--bg-tertiary)",
                          border: "1px solid var(--border-subtle)",
                          color: "var(--text-secondary)",
                          whiteSpace: "nowrap",
                          flexShrink: 0,
                        }}
                      >
                        {d.StageName}
                      </span>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
