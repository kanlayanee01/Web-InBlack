# InBlack Top-Up Platform

ระบบเติมเกมและซื้อแพ็กเกจแบบครบวงจร ตั้งแต่แคตตาล็อก ชำระเงิน ติดตามออเดอร์แบบ real-time จนถึงหลังบ้านของทีม Support และ Finance งานนี้เป็นโจทย์ทดสอบ Full Stack ของ InBlackM

| ส่วน | เทคโนโลยี |
|---|---|
| Frontend | Next.js 16 (App Router), TypeScript `strict` |
| API และ worker | Python 3.13, FastAPI, asyncpg (แยก process กัน) |
| ฐานข้อมูลและคิวงาน | PostgreSQL 17 |
| Rate limit | Redis 7 |
| Reverse proxy | Nginx แบ่งโหลด round-robin ไป API 2 ตัว |

## วิธีรัน

ต้องมี Docker และ Docker Compose เท่านั้น ไม่ต้องสร้างไฟล์ `.env`

```bash
docker compose up --build
```

| บริการ | URL |
|---|---|
| หน้าเว็บ | http://localhost:8088/th |
| หลังบ้าน | http://localhost:8088/admin |
| อีเมล OTP และใบเสร็จ (Mailpit) | http://localhost:8025 |
| Payment จำลอง | http://localhost:8090 (เปิดจากปุ่มในหน้าติดตามออเดอร์) |
| ระบบเติมเกมจำลอง | http://localhost:8091/stats |
| Metrics | http://localhost:8088/metrics |

ถ้าพอร์ตชน เปลี่ยนได้ด้วย `PROXY_PORT`, `MOCK_PAY_PORT`, `MOCK_GAME_PORT` ดูตัวแปรทั้งหมดได้ที่ [.env.example](.env.example)

เปิดใช้ `POST /api/v1/_test/reset` ได้ด้วย `APP_ENV=test docker compose up --build`

## บัญชีทดสอบ

รหัสผ่านของทุกบัญชีคือ `Admin-Test-2026` เป็นค่าทดสอบเท่านั้น

| Role | อีเมล | สิทธิ์ |
|---|---|---|
| viewer | `viewer@inblackm.test` | ดูออเดอร์และ dashboard โดย PII ถูก mask |
| support | `support@inblackm.test` | + ดู PII เต็ม, สั่ง retry fulfillment |
| finance | `finance@inblackm.test` | + refund, reconciliation, audit log |
| superadmin | `superadmin@inblackm.test` | + จัดการสินค้า โค้ดส่วนลด และผู้ดูแล |

ลูกค้า login ด้วย OTP โดยใช้อีเมลอะไรก็ได้ แล้วดูรหัสที่ Mailpit

Webhook secret สำหรับทดสอบคือ `whsec_test_inblackm_2026`

## การทดสอบ

```bash
make test
```

คำสั่งนี้รัน lint, typecheck, unit, integration, concurrency, contract และ E2E

| คำสั่ง | สิ่งที่ทดสอบ |
|---|---|
| `make test-unit` | ราคา, state machine, checksum |
| `make test-integration` | กฎใน PostgreSQL จริง, invariants ด้วย request พร้อมกันผ่าน proxy, contract กับ `openapi.yaml` |
| `make test-e2e` | Playwright: ซื้อ, จ่าย, เห็นสถานะสด, refund, คีย์บอร์ดล้วน, axe WCAG |
| `make chaos` | `docker kill` ของจริงระหว่างเติมเกม แล้วตรวจว่าไม่เติมซ้ำและไม่มีออเดอร์ค้าง |
| `make lighthouse` | Lighthouse Mobile หน้าสินค้า |
| `make perf` | seed 100,000 ออเดอร์แล้ววัด p95 |

ผลที่วัดได้บนเครื่องพัฒนา (Windows 11, Docker Desktop):

| รายการ | ผล | เกณฑ์ |
|---|---|---|
| ซื้อ Flash Sale พร้อมกัน 1,500 คน | ขายได้ 100 ชิ้นพอดี | ไม่เกิน 100 |
| p95 ของ `GET /admin/orders` ที่ 100,000 รายการ | ประมาณ 10 ms | < 200 ms |
| Lighthouse Mobile หน้าสินค้า (Performance / A11y / Best Practices / SEO) | Performance 97–100 และที่เหลือ 100 ทั้ง 3 รอบล่าสุด | ≥ 90 / ≥ 95 / ≥ 95 / 100 |
| ฟื้นตัวหลัง `docker kill` | ไม่เกิน 15 วินาที | ไม่ค้างเกิน 2 นาที |

คะแนน Performance ของ Lighthouse แกว่งตามภาระเครื่อง ระหว่างพัฒนาเคยมีรอบหนึ่งได้ 82 ตอนเครื่องเพิ่ง build เสร็จ

## ขอบเขตงาน

**เสร็จและทดสอบแล้ว**

- Business rules ทั้งหมดในข้อ 5 รวม invariants 5 ข้อ
- `mock-pay` ครบ 5 ปุ่ม และ `mock-game` ครบทุกโหมดความล้มเหลว
- API ตามข้อ 8.1, OTP, RBAC, CSRF, rate limit, reconciliation
- SSE ข้าม replica พร้อม `Last-Event-ID`
- หน้าเว็บตามข้อ 9.2, TH/EN, Light/Dark, ISR, JSON-LD, OG image
- แบบจาก Claude Design export เป็น HTML และ PDF ใน [design/](design/README.md)
- ตะกร้าสินค้า และปุ่มสลับโหมดสว่าง/มืดที่จำค่าได้

**ยังไม่ได้ทำหรือทำไม่ครบ**

- โบนัส: แอป Mobile, OpenTelemetry/Jaeger และ deploy จริง
- GitHub Actions เขียนแล้ว แต่ต้อง push ขึ้น GitHub ก่อนจึงจะรันจริง
- หน้าจอ superadmin แก้ได้เฉพาะชื่อและราคาสินค้า ส่วนโค้ดส่วนลดมีแค่ API เปิดปิด และยังไม่มี API สร้างโค้ดใหม่
- หลังบ้านมีเฉพาะภาษาไทย

**ข้อจำกัดที่รู้อยู่แล้ว**

- `restart: always` ไม่พา container กลับมาเองหลัง `docker kill` ต้องสั่ง `docker compose start <service>` เพราะ Docker ถือว่าเป็นการหยุดโดยผู้ใช้ ข้อมูลไม่เสียระหว่างนั้น
- Redis ล่มแล้ว rate limit จะนับแยกแต่ละ replica เพดานจริงจึงเป็นสองเท่าชั่วคราว
- Reconciliation เทียบกับ ledger ทั้งหมด จึงเหมาะกับไฟล์ settlement ที่ครอบคลุมทั้งช่วงที่มีข้อมูล
- `/metrics` ของ latency นับแยกต่อ replica เพราะยังไม่มี Prometheus กลาง
- รองรับ `max_uses_per_email` เฉพาะค่า 1 ตามโจทย์

## เอกสาร

- [ARCHITECTURE.md](ARCHITECTURE.md) แผนภาพระบบและการจัดการ failure
- [docs/adr/](docs/adr/) เหตุผลของการตัดสินใจสำคัญ
- [openapi.yaml](openapi.yaml) สเปก API
- [RUNBOOK.md](RUNBOOK.md) คู่มือ on-call
- [SEO.md](SEO.md) สิ่งที่ทำเรื่อง SEO
- [AI_LOG.md](AI_LOG.md) การใช้ AI
