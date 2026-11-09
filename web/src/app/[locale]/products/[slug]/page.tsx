import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { LiveStock } from "./live-stock";
import { buildSlugs, getProduct, siteUrl } from "@/lib/catalog";
import { dict, isLocale, LOCALES } from "@/lib/i18n";
import { decimalPrice, formatBaht } from "@/lib/money";

export const revalidate = 30;
export const dynamicParams = true;

type Props = { params: Promise<{ locale: string; slug: string }> };

export function generateStaticParams() {
  return LOCALES.flatMap((locale) => buildSlugs().map((slug) => ({ locale, slug })));
}

function describe(locale: string, name: string, price: string): string {
  return locale === "th"
    ? `ซื้อ ${name} ราคา ${price} บาท เติมเข้าเกมอัตโนมัติ พร้อมติดตามสถานะแบบเรียลไทม์`
    : `Buy ${name} for ${price} THB, delivered to your game with live order tracking.`;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale, slug } = await params;
  const product = await getProduct(slug);
  if (product === null) return {};
  const description = describe(locale, product.name, formatBaht(product.price_satang));
  return {
    title: product.name,
    description,
    alternates: {
      canonical: `/${locale}/products/${slug}`,
      languages: {
        th: `/th/products/${slug}`,
        en: `/en/products/${slug}`,
        "x-default": `/th/products/${slug}`,
      },
    },
    openGraph: { title: product.name, description, type: "website" },
  };
}

export default async function ProductPage({ params }: Props) {
  const { locale, slug } = await params;
  if (!isLocale(locale)) notFound();
  const product = await getProduct(slug);
  if (product === null) notFound();
  const t = dict(locale);
  const description = describe(locale, product.name, formatBaht(product.price_satang));
  const soldOut = product.stock_remaining === 0;
  const structuredData = {
    "@context": "https://schema.org",
    "@type": "Product",
    name: product.name,
    sku: product.sku,
    description,
    image: `${siteUrl()}/${locale}/products/${slug}/opengraph-image`,
    brand: { "@type": "Brand", name: "InBlack" },
    offers: {
      "@type": "Offer",
      url: `${siteUrl()}/${locale}/products/${slug}`,
      price: decimalPrice(product.price_satang),
      priceCurrency: "THB",
      availability: soldOut ? "https://schema.org/SoldOut" : "https://schema.org/InStock",
    },
  };
  return (
    <article className="card stack">
      <script
        type="application/ld+json"
        // Product names are admin input. Escaping "<" stops one from closing this script tag.
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(structuredData).replace(/</g, "\\u003c"),
        }}
      />
      <div className="product-art" aria-hidden="true" />
      <h1>{product.name}</h1>
      <p>{description}</p>
      <p>
        <span className="price">฿{formatBaht(product.price_satang)}</span>{" "}
        <span className="muted small">{t.product.vatIncluded}</span>
      </p>
      <LiveStock
        slug={product.slug}
        initial={product.stock_remaining}
        labels={{
          stock: t.product.stock,
          unlimited: t.product.unlimited,
          soldOut: t.product.soldOut,
        }}
      />
      {product.max_qty_per_order !== null ? (
        <p className="muted small">{t.product.perOrder(product.max_qty_per_order)}</p>
      ) : null}
      {product.max_qty_per_email !== null ? (
        <p className="muted small">{t.product.perEmail(product.max_qty_per_email)}</p>
      ) : null}
      <Link className="btn" href={`/${locale}/checkout?sku=${encodeURIComponent(product.sku)}`}>
        {t.product.buy}
      </Link>
    </article>
  );
}
