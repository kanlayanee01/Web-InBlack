import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { CheckoutForm } from "./checkout-form";
import { getProducts } from "@/lib/catalog";
import { isLocale } from "@/lib/i18n";

export const revalidate = 30;

export const metadata: Metadata = {
  title: "Checkout",
  robots: { index: false, follow: false },
};

export default async function CheckoutPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const products = await getProducts();
  return (
    <Suspense fallback={<div className="skeleton" />}>
      <CheckoutForm locale={locale} products={products} />
    </Suspense>
  );
}
