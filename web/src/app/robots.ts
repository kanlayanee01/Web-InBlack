import type { MetadataRoute } from "next";

import { siteUrl } from "@/lib/catalog";

export const dynamic = "force-dynamic";

// Private pages are deliberately not listed here: Disallow would advertise their paths, and it
// would stop crawlers from reaching the noindex directive those pages carry. See SEO.md.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/" }],
    sitemap: `${siteUrl()}/sitemap.xml`,
  };
}
