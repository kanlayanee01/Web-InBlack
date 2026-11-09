import type { Metadata, Viewport } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import type { ReactNode } from "react";

import "@fontsource/ibm-plex-sans-thai/thai-400.css";
import "@fontsource/ibm-plex-sans-thai/thai-600.css";
import "@fontsource/ibm-plex-sans-thai/thai-700.css";
import "@fontsource/ibm-plex-sans-thai/latin-400.css";
import "@fontsource/ibm-plex-sans-thai/latin-600.css";
import "@fontsource/ibm-plex-sans-thai/latin-700.css";
import "../globals.css";
import { GemIcon, SearchIcon, UserIcon } from "@/components/icons";
import { siteUrl } from "@/lib/catalog";
import { dict, isLocale, LOCALES, type Locale } from "@/lib/i18n";

export const dynamicParams = false;

export function generateStaticParams() {
  return LOCALES.map((locale) => ({ locale }));
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f6f8" },
    { media: "(prefers-color-scheme: dark)", color: "#0e0e12" },
  ],
};

export function generateMetadata(): Metadata {
  return {
    metadataBase: new URL(siteUrl()),
    title: { default: "InBlack Top-Up", template: "%s | InBlack" },
  };
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = dict(locale);
  const other: Locale = locale === "th" ? "en" : "th";
  return (
    <html lang={locale}>
      <body>
        <a className="skip" href="#main">
          {locale === "th" ? "ข้ามไปเนื้อหา" : "Skip to content"}
        </a>
        <header className="header">
          <div className="header-inner">
            <Link className="brand" href={`/${locale}`}>
              <GemIcon size={26} />
              {t.brand}
            </Link>
            <nav className="nav" aria-label={locale === "th" ? "เมนูหลัก" : "Main"}>
              <Link href={`/${locale}`} className="nav-text">
                {t.nav.catalog}
              </Link>
              <Link href={`/${locale}/track`} aria-label={t.nav.track}>
                <SearchIcon />
                <span className="nav-text">{t.nav.track}</span>
              </Link>
              <Link href={`/${locale}/my-orders`} aria-label={t.nav.myOrders}>
                <UserIcon />
                <span className="nav-text">{t.nav.myOrders}</span>
              </Link>
              <Link href={`/${other}`} lang={other} hrefLang={other}>
                {t.otherLocale}
              </Link>
            </nav>
          </div>
        </header>
        <main id="main" className="container">
          {children}
        </main>
        <footer className="footer">{t.footer}</footer>
      </body>
    </html>
  );
}
