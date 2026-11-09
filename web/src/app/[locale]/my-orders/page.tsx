import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { MyOrders } from "./my-orders";
import { isLocale } from "@/lib/i18n";

export const metadata: Metadata = {
  title: "My orders",
  robots: { index: false, follow: false },
};

export default async function MyOrdersPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  return <MyOrders locale={locale} />;
}
