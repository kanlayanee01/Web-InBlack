import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { TrackView } from "./track-view";
import { isLocale } from "@/lib/i18n";

export const metadata: Metadata = {
  title: "Track order",
  robots: { index: false, follow: false },
};

export default async function TrackPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  return (
    <Suspense fallback={<div className="skeleton" />}>
      <TrackView locale={locale} />
    </Suspense>
  );
}
