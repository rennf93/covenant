"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Tiny copy-to-clipboard chip for evidence URIs: writes via
 * navigator.clipboard, flashes "copied" for 1.5s, then resets.
 */
export default function CopyButton({ value }: { value: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, []);

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setState("copied");
    } catch {
      setState("failed");
    }
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 1500);
  }

  return (
    <button
      type="button"
      className={`copy-chip ${state === "copied" ? "copied" : ""}`}
      onClick={onCopy}
      aria-label={`Copy evidence URI: ${value}`}
    >
      {state === "copied" ? "copied" : state === "failed" ? "failed" : "copy"}
    </button>
  );
}
