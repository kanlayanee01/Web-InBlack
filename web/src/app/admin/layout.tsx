import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "@fontsource/ibm-plex-sans-thai/thai-400.css";
import "@fontsource/ibm-plex-sans-thai/thai-600.css";
import "@fontsource/ibm-plex-sans-thai/thai-700.css";
import "@fontsource/ibm-plex-sans-thai/latin-400.css";
import "@fontsource/ibm-plex-sans-thai/latin-600.css";
import "@fontsource/ibm-plex-sans-thai/latin-700.css";
import "../globals.css";
import { AdminShell } from "./admin-shell";
import { THEME_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  title: "InBlack Admin",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="th" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <AdminShell>{children}</AdminShell>
      </body>
    </html>
  );
}
