export type Badge =
  | "awaiting_payment"
  | "in_queue"
  | "processing"
  | "completed"
  | "expired"
  | "payment_failed"
  | "refunded"
  | "action_needed";

export interface Product {
  sku: string;
  slug: string;
  name: string;
  description_th: string;
  description_en: string;
  price_satang: number;
  stock_remaining: number | null;
  max_qty_per_order: number | null;
  max_qty_per_email: number | null;
}

export interface Quote {
  subtotal_satang: number;
  discount_satang: number;
  net_satang: number;
  fee_satang: number;
  total_satang: number;
  vat_satang: number;
}

export interface TimelineEvent {
  seq: number;
  status: string;
  badge: Badge;
  created_at: string;
}

export interface Order extends Quote {
  id: string;
  status: string;
  badge: Badge;
  email: string;
  game_uid: string;
  payment_method: string;
  voucher_code: string | null;
  items: { sku: string; name: string; unit_price_satang: number; qty: number }[];
  expires_at: string;
  created_at: string;
  checkout_url: string;
  tracking_token: string;
  timeline?: TimelineEvent[];
}

export interface OrderSummary {
  id: string;
  status: string;
  badge: Badge;
  total_satang: number;
  created_at: string;
}

export type PaymentMethod = "promptpay" | "truemoney" | "card";
