import { ImageResponse } from "next/og";

import { getProduct } from "@/lib/catalog";
import { formatBaht } from "@/lib/money";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "InBlack product";

export default async function Image({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  const { slug } = await params;
  const product = await getProduct(slug);
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: 80,
          background: "linear-gradient(135deg, #5b34d6, #17171d)",
          color: "#ffffff",
        }}
      >
        <div style={{ display: "flex", fontSize: 40, opacity: 0.8 }}>InBlack</div>
        <div style={{ display: "flex", fontSize: 84, fontWeight: 700, marginTop: 24 }}>
          {product?.name ?? "InBlack Top-Up"}
        </div>
        {product ? (
          <div style={{ display: "flex", fontSize: 56, marginTop: 24 }}>
            {`THB ${formatBaht(product.price_satang)}`}
          </div>
        ) : null}
      </div>
    ),
    size,
  );
}
