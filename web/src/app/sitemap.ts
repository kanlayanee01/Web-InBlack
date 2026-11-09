import type { MetadataRoute } from "next";

import { getProducts, siteUrl } from "@/lib/catalog";
import { LOCALES } from "@/lib/i18n";

export const dynamic = "force-dynamic";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const base = siteUrl();
  const products = await getProducts();
  return LOCALES.flatMap((locale) => [
    { url: `${base}/${locale}` },
    ...products.map((product) => ({ url: `${base}/${locale}/products/${product.slug}` })),
  ]);
}
