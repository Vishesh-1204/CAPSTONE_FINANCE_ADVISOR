// frontend/components/file-dropzone.tsx
//
// Purpose: pick ONE statement file by dragging it onto the box or clicking to
// browse. Uses only the browser's built-in drag-and-drop events (no library).
// Wrong extensions and oversized files are rejected HERE, before any upload;
// the backend checks again (never trust the client alone).

"use client";

import { FileUp, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";

import { Button } from "@/components/ui/button";

export const ACCEPTED_EXTENSIONS = [".csv", ".xlsx", ".pdf"];
export const MAX_FILE_BYTES = 10 * 1024 * 1024; // same limit as the backend (MAX_STATEMENT_BYTES)

/** Returns an error message, or null when the file is acceptable. */
function validate(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))) {
    return `"${file.name}" is not a supported file. Upload one of: ${ACCEPTED_EXTENSIONS.join(", ")}.`;
  }
  if (file.size > MAX_FILE_BYTES) return `"${file.name}" is larger than 10 MB.`;
  return null;
}

function formatSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export function FileDropzone({
  file,
  onFileChange,
  disabled = false,
}: {
  file: File | null;
  onFileChange: (file: File | null) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [rejection, setRejection] = useState<string | null>(null);

  function choose(candidate: File | undefined) {
    if (!candidate) return;
    const problem = validate(candidate);
    setRejection(problem);
    onFileChange(problem ? null : candidate);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); // without this the browser OPENS the dropped file in the tab
    setDragging(false);
    if (!disabled) choose(event.dataTransfer.files[0]);
  }

  return (
    <div className="space-y-2">
      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        onClick={() => !disabled && inputRef.current?.click()}
        onKeyDown={(event) => {
          if (!disabled && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        // dragover must call preventDefault, otherwise the browser never fires "drop"
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none ${
          dragging ? "border-primary bg-muted" : "border-border hover:bg-muted/50"
        } ${disabled ? "pointer-events-none opacity-50" : ""}`}
      >
        <FileUp className="size-8 text-muted-foreground" />
        <p className="text-sm font-medium">Drop a statement here, or click to choose</p>
        <p className="text-xs text-muted-foreground">CSV, XLSX or PDF · up to 10 MB · synthetic data only</p>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_EXTENSIONS.join(",")}
          className="hidden"
          onChange={(event) => {
            choose(event.target.files?.[0]);
            event.target.value = ""; // allow choosing the same file again (e.g. to test duplicates)
          }}
        />
      </div>

      {file && (
        <div className="flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm">
          <span className="truncate">
            <span className="font-medium">{file.name}</span>{" "}
            <span className="text-muted-foreground">({formatSize(file.size)})</span>
          </span>
          <Button variant="ghost" size="icon-sm" aria-label="Remove file" onClick={() => onFileChange(null)} disabled={disabled}>
            <X />
          </Button>
        </div>
      )}
      {rejection && <p className="text-sm text-destructive">{rejection}</p>}
    </div>
  );
}
