import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD ?? "";
// Node-side requests do not go through the browser host mapping.
const API = "http://proxy";
const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

function watchForCspViolations(page: Page): string[] {
  const violations: string[] = [];
  page.on("console", (message) => {
    if (message.text().includes("Content Security Policy")) violations.push(message.text());
  });
  return violations;
}

async function fillCheckout(page: Page, email: string) {
  await page.getByLabel("อีเมล").fill(email);
  await page.getByLabel("Game UID").fill("123456789");
}

test.beforeEach(async ({ request }) => {
  expect((await request.post(`${API}/api/v1/_test/reset`)).status()).toBe(200);
});

test("buy, pay, see the status change live, then refund in admin", async ({ page, browser }) => {
  const violations = watchForCspViolations(page);
  const email = `e2e-${Date.now()}@example.com`;

  await page.goto("/th/checkout?sku=DIAMOND-1000");
  await page.getByRole("textbox", { name: "จำนวน 1,000 Diamonds" }).fill("2");
  await fillCheckout(page, email);
  await page.getByLabel("โค้ดส่วนลด").fill("SAVE10");
  await page.getByRole("radio", { name: /บัตรเครดิต/ }).check();
  await expect(page.locator(".sum .total dd")).toHaveText("฿669.80");

  await page.getByRole("button", { name: "สร้างออเดอร์" }).click();
  await expect(page).toHaveURL(/\/th\/track\?order=ORD-/);
  await expect(page.locator(".badge")).toHaveText("รอชำระเงิน");
  const orderId = new URL(page.url()).searchParams.get("order") as string;

  const [provider] = await Promise.all([
    page.context().waitForEvent("page"),
    page.getByRole("link", { name: "ไปหน้าชำระเงิน" }).click(),
  ]);
  await provider.getByRole("button", { name: "จ่ายสำเร็จ", exact: true }).click();

  // No reload here: the page has to receive this through SSE.
  await expect(page.locator(".badge")).toHaveText("เสร็จสิ้น", { timeout: 20_000 });
  await expect(page.getByRole("status")).toHaveText("สถานะออเดอร์เปลี่ยนเป็น เสร็จสิ้น");

  const adminContext = await browser.newContext();
  const admin = await adminContext.newPage();
  await admin.goto("/admin");
  await admin.getByLabel("อีเมล").fill("finance@inblackm.test");
  await admin.getByLabel("รหัสผ่าน").fill(ADMIN_PASSWORD);
  await admin.getByRole("button", { name: "เข้าสู่ระบบ" }).click();
  await expect(admin.getByRole("heading", { name: "แดชบอร์ด" })).toBeVisible();

  await admin.goto(`/admin/orders/${orderId}`);
  await admin.getByRole("button", { name: "คืนเงิน", exact: true }).click();
  await admin.getByLabel("จำนวนเงิน (บาท)").fill("669.80");
  await admin.getByLabel("เหตุผล").fill("e2e full refund");
  await admin.getByRole("button", { name: "ถัดไป" }).click();
  await expect(admin.getByRole("heading", { name: "ยืนยันการคืนเงิน" })).toBeVisible();
  await admin.getByRole("button", { name: "ยืนยันคืนเงิน" }).click();
  await expect(admin.getByText("บันทึกการคืนเงินแล้ว")).toBeVisible();

  await expect(page.locator(".badge")).toHaveText("คืนเงินแล้ว", { timeout: 20_000 });
  expect(violations).toEqual([]);
  await adminContext.close();
});

test("checkout can be completed with the keyboard alone", async ({ page }) => {
  await page.goto("/th/checkout?sku=MONTHLY-PASS");
  await expect(page.locator(".sum .total dd")).toHaveText("฿179.00");

  await page.getByLabel("อีเมล").focus();
  await page.keyboard.type("keyboard@example.com");
  await page.keyboard.press("Tab");
  await page.keyboard.type("123456789");
  await page.keyboard.press("Tab"); // voucher
  await page.keyboard.press("Tab"); // payment method group
  await expect(page.getByRole("radio", { name: /PromptPay/ })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("radio", { name: /TrueMoney/ })).toBeChecked();
  await expect(page.locator(".sum .total dd")).toHaveText("฿181.69");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "สร้างออเดอร์" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/th\/track\?order=ORD-/);
});

test("errors are tied to their inputs", async ({ page }) => {
  await page.goto("/th/checkout?sku=DIAMOND-1000");
  await page.getByRole("button", { name: "สร้างออเดอร์" }).click();
  const email = page.getByLabel("อีเมล");
  await expect(email).toHaveAttribute("aria-invalid", "true");
  await expect(email).toBeFocused();
  const describedBy = (await email.getAttribute("aria-describedby")) ?? "";
  await expect(page.locator(`[id="${describedBy.split(" ").at(-1)}"]`)).toHaveText(
    "กรุณากรอกอีเมลให้ถูกต้อง",
  );
});

test("a bad checksum is refused before any request is made", async ({ page }) => {
  let trackCalls = 0;
  page.on("request", (request) => {
    if (request.url().includes("/orders/track")) trackCalls += 1;
  });
  await page.goto("/th/track");
  await page.getByLabel("เลขออเดอร์").fill("ORD-2587965432159-458");
  await page.getByLabel("อีเมลที่ใช้สั่งซื้อ").fill("a@example.com");
  await page.getByRole("button", { name: "ค้นหา" }).click();
  await expect(page.getByText("เลขออเดอร์ไม่ถูกต้อง")).toBeVisible();
  expect(trackCalls).toBe(0);
});

test("a product name containing markup stays plain text", async ({ page, request }) => {
  const login = await request.post(`${API}/api/v1/admin/auth/login`, {
    data: { email: "superadmin@inblackm.test", password: ADMIN_PASSWORD },
  });
  const cookie = (login.headers()["set-cookie"] as string).split(";")[0];
  const csrf = ((await login.json()) as { csrf_token: string }).csrf_token;
  const name = "<img src=x onerror=alert(1)>";
  await request.patch(`${API}/api/v1/admin/products/DIAMOND-1000`, {
    data: { name },
    headers: { Cookie: cookie, "X-CSRF-Token": csrf },
  });
  let dialogOpened = false;
  page.on("dialog", (dialog) => {
    dialogOpened = true;
    void dialog.dismiss();
  });
  // The page is ISR, so the new name appears once its 30 second cache has been refreshed.
  await expect(async () => {
    await page.goto("/th/checkout?sku=DIAMOND-1000");
    await expect(page.getByText(name)).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 45_000 });
  expect(await page.locator("main img").count()).toBe(0);
  expect(dialogOpened).toBe(false);
});

for (const path of ["/th", "/en", "/th/products/flash-skin-01", "/th/checkout?sku=DIAMOND-1000", "/th/track", "/th/my-orders", "/admin"]) {
  test(`accessibility and narrow layout: ${path}`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 700 });
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBe(0);
  });
}

test("dark mode meets contrast requirements", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "dark" });
  const page = await context.newPage();
  await page.goto("http://localhost:8088/th/checkout?sku=DIAMOND-1000");
  await page.waitForLoadState("networkidle");
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(results.violations.map((violation) => violation.id)).toEqual([]);
  await context.close();
});
