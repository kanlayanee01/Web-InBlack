"use client";

import { useEffect } from "react";

import { CheckIcon } from "./icons";

const VISIBLE_MS = 5000;

export function Toast({ message, onClose }: { message: string | null; onClose: () => void }) {
  useEffect(() => {
    if (message === null) return;
    const timer = setTimeout(onClose, VISIBLE_MS);
    return () => clearTimeout(timer);
  }, [message, onClose]);

  if (message === null) return null;
  return (
    <div className="toast" role="status">
      <CheckIcon />
      <span>{message}</span>
    </div>
  );
}
