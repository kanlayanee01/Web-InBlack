import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { ProductIcon } from "@/components/icons";
import { getProducts } from "@/lib/catalog";
import { dict, isLocale } from "@/lib/i18n";
import { formatBaht } from "@/lib/money";

export const revalidate = 30;

type Props = { params: Promise<{ locale: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const title = locale === "th" ? "เติมเกมและซื้อแพ็กเกจ" : "Game top-ups and packages";
  const description =
    locale === "th"
      ? "เติมเกมและซื้อแพ็กเกจพร้อมติดตามสถานะแบบเรียลไทม์"
      : "Top up games and buy packages with live order tracking.";
  return {
    title,
    description,
    alternates: {
      canonical: `/${locale}`,
      languages: { th: "/th", en: "/en", "x-default": "/th" },
    },
    openGraph: { title, description, type: "website" },
  };
}

export default async function HomePage({ params }: Props) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = dict(locale);
  const products = await getProducts();
  return (
    <>
      <section className="glass hero">
        <h1>{t.home.title}</h1>
        <p className="muted" style={{ marginBottom: 0 }}>
          {t.home.lead}
        </p>
      </section>
      <div className="grid">
        {products.map((product) => (
          <article key={product.sku} className="card product-card">
            <div className="product-art">
              <ProductIcon sku={product.sku} />
            </div>
            <h2>
              <Link href={`/${locale}/products/${product.slug}`}>{product.name}</Link>
            </h2>
            <p className="price">฿{formatBaht(product.price_satang)}</p>
            <Link
              className="btn btn-block"
              href={`/${locale}/checkout?sku=${encodeURIComponent(product.sku)}`}
              aria-label={`${t.home.buy} ${product.name}`}
            >
              {t.home.buy}
            </Link>
          </article>
        ))}
      </div>
    </>
  );
}
