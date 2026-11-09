import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "../globals.css";
import { AdminShell } from "./admin-shell";

export const metadata: Metadata = {
  title: "InBlack Admin",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="th">
      <body>
        <AdminShell>{children}</AdminShell>
      </body>
    </html>
  );
}
