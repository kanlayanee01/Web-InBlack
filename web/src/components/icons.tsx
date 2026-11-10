// Clean line icons, decorative: the text beside them, or an aria-label on the link, carries the meaning.
import type { ReactNode } from "react";

function Icon({ size, children }: { size: number; children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export function GemIcon({ size = 44 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M6 3h12l4 6-10 12L2 9z" />
    </Icon>
  );
}

export function StarIcon({ size = 44 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M12 3l2.6 6 6.4.6-4.8 4.3 1.4 6.3L12 17l-5.6 3.2 1.4-6.3L3 9.6 9.4 9z" />
    </Icon>
  );
}

export function PassIcon({ size = 44 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M4 7h16v10H4zM9 7v10" />
    </Icon>
  );
}

export function SearchIcon({ size = 20 }: { size?: number }) {
  return (
    <Icon size={size}>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </Icon>
  );
}

export function UserIcon({ size = 20 }: { size?: number }) {
  return (
    <Icon size={size}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" />
    </Icon>
  );
}

export function CartIcon({ size = 20 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M3 4h2l2.4 11h11.2L21 7H6.2" />
      <circle cx="9" cy="19.5" r="1.3" />
      <circle cx="17" cy="19.5" r="1.3" />
    </Icon>
  );
}

export function MoonIcon({ size = 20 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />
    </Icon>
  );
}

export function SunIcon({ size = 20 }: { size?: number }) {
  return (
    <Icon size={size}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </Icon>
  );
}

export function CheckIcon({ size = 20 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M5 13l4 4L19 7" />
    </Icon>
  );
}

export function ProductIcon({ sku, size }: { sku: string; size?: number }) {
  if (sku.startsWith("MONTHLY")) return <PassIcon size={size} />;
  if (sku.startsWith("FLASH")) return <StarIcon size={size} />;
  return <GemIcon size={size} />;
}
