"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";

import { CheckIcon } from "./icons";

const VISIBLE_MS = 5000;

export function Toast({ message, onClose }: { message: string | null; onClose: () => void }) {
  useEffect(() => {
    if (message === null) return;
    const timer = setTimeout(onClose, VISIBLE_MS);
    return () => clearTimeout(timer);
  }, [message, onClose]);

  if (message === null) return null;
  // Rendered on <body>: a glass card's backdrop-filter would otherwise trap position: fixed.
  return createPortal(
    <div className="toast" role="status">
      <CheckIcon />
      <span>{message}</span>
    </div>,
    document.body,
  );
}
