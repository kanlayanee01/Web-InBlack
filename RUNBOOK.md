# Runbook สำหรับ On-call

คำสั่งทั้งหมดรันที่ root ของ repo สำหรับ SQL ใช้

```bash
docker compose exec postgres psql -U inblack -d inblack
```

## ตรวจสุขภาพเบื้องต้น

```bash
curl -i http://localhost:8088/readyz
```

```bash
curl -s http://localhost:8088/metrics | grep -E 'inblack_(queue_depth|dead_letter|orders)'
```

```bash
docker compose ps -a
```

ถ้า container หายไปหลังถูก kill ให้สั่ง `docker compose start <service>` เพราะ Docker ไม่ restart ให้เองในกรณีนี้

ติดตามออเดอร์ข้าม service ได้ด้วย `request_id` ใน log

```bash
docker compose logs --no-log-prefix api-1 api-2 worker mock-game | grep <request_id>
```

## DLQ มีงานค้าง

**อาการ:** `inblack_dead_letter_jobs` มากกว่า 0 หรือช่อง Dead-letter ใน dashboard ไม่เป็นศูนย์

```sql
SELECT id, kind, payload, attempts, last_error, updated_at
FROM jobs WHERE status = 'dead' ORDER BY updated_at DESC;
```

| `kind` | ความหมาย | ทำอย่างไร |
|---|---|---|
| `fulfill_order` | เติมเกมไม่สำเร็จจนหมดรอบ | ดูด้านล่าง |
| `refund_provider` | ส่งคำขอ refund ให้ provider ไม่สำเร็จ | แก้สาเหตุแล้วส่งงานกลับเข้าคิว |
| `send_receipt` | ส่งใบเสร็จไม่สำเร็จ | ความสำคัญต่ำ ส่งกลับเข้าคิวเมื่อระบบอีเมลกลับมา |

**`fulfill_order`:** เปิดออเดอร์ใน `/admin/orders/<id>` ดูประวัติการเติมเกม

- สถานะ `FAILED` ยืนยันแล้วว่าไม่ได้เติม เลือกกด **เติมเกมอีกครั้ง** หรือให้ finance คืนเงิน
- สถานะ `NEEDS_REVIEW` เช็กผลจริงก่อนเสมอ

```bash
curl -s http://localhost:8091/topups/<ORDER_ID>-1
```

ปุ่ม **เติมเกมอีกครั้ง** ปลอดภัยเสมอ เพราะระบบจะ `GET` ก่อน ถ้าเติมไปแล้วจะไม่เติมซ้ำ

**งานประเภทอื่น** ส่งกลับเข้าคิวได้ด้วย SQL นี้

```sql
UPDATE jobs SET status = 'queued', attempts = 0, run_at = now(), last_error = NULL
WHERE id = <job_id> AND status = 'dead';
```

**ห้าม** แก้ `fulfillments.status` เป็น `PENDING` ด้วยมือ เพราะจะข้ามการเช็กก่อนยิงและทำให้เติมซ้ำได้

## Reconciliation ไม่ตรง

Finance อัปโหลดไฟล์ที่ `/admin/reconciliation` ระบบแยกเป็น 4 ประเภท

| ประเภท | ความหมาย | ทำอย่างไร |
|---|---|---|
| `missing_in_system` | Provider มีแต่เราไม่มี | webhook อาจไม่ถึง ตรวจ `webhook_events` แล้วขอให้ provider ส่งใหม่ |
| `missing_at_provider` | เรามีแต่ provider ไม่มี | ถ้าเป็น refund ค้าง ดู DLQ ถ้าเป็น payment ให้ติดต่อ provider ทันที เพราะเราอาจเติมของโดยไม่ได้เงิน |
| `amount_mismatch` | ยอดไม่ตรง | เทียบ `ledger_entries` กับ provider ออเดอร์มักอยู่ `NEEDS_REVIEW` แล้ว |
| `status_mismatch` | ประเภทหรือสถานะไม่ตรง | ดู timeline ของออเดอร์ มักเกิดจาก webhook ตกหล่น |

```sql
SELECT category, event_id, order_id, system_satang, provider_satang, detail
FROM reconciliation_items
WHERE run_id = (SELECT id FROM reconciliation_runs ORDER BY created_at DESC LIMIT 1)
ORDER BY category;
```

**ห้าม** แก้ `ledger_entries` ตารางนี้เป็น append-only และ trigger จะปฏิเสธ การแก้ไขต้องทำด้วยรายการใหม่หรือ refund ผ่าน API เพื่อให้มี audit

## mock-game ล่มทั้งวัน

**ผลกระทบ:** ลูกค้ายังซื้อและจ่ายได้ ออเดอร์ค้างไม่เกิน 2 นาทีแล้วไป `NEEDS_REVIEW` เพราะเช็กผลไม่ได้ และงานเข้า DLQ ระบบจะไม่ยิงซ้ำ

1. **แจ้งทีมที่เกี่ยวข้อง** และเปิดสถานะบนเว็บว่าการเติมล่าช้า
2. **พิจารณาปิดการขาย** ถ้าจะล่มนาน ให้ superadmin เรียก `PATCH /api/v1/admin/products/{sku}` ด้วย `{"is_active": false}` (ยังไม่มีปุ่มนี้ในหน้าจอ)
3. **เมื่อระบบกลับมา** ตรวจว่าตอบได้ด้วย `curl http://localhost:8091/healthz`
4. **เติมออเดอร์ที่ค้าง** รายการนี้คือออเดอร์ที่จ่ายครบและรอเติม

```sql
SELECT id FROM orders
WHERE status = 'NEEDS_REVIEW' AND paid_satang = total_satang AND refunded_satang = 0;
```

5. กด **เติมเกมอีกครั้ง** ทีละออเดอร์ หรือเรียก `POST /api/v1/admin/orders/{id}/retry-fulfillment` เป็นชุด ควรเว้นจังหวะเพื่อไม่ให้ปลายทางที่เพิ่งฟื้นล้มซ้ำ
6. ตรวจซ้ำ `/stats` ต้องไม่มี reference ใดเกิน 1

ถ้าลูกค้าขอคืนเงินระหว่างนี้ ต้องยืนยันด้วย `GET /topups/<reference>` ก่อนว่ายังไม่ได้เติม

## ออเดอร์ค้าง `IN_PROGRESS`

ไม่ควรเกิดเกิน 2 นาที ถ้าเจอให้ตรวจว่า `worker` รันอยู่ เมื่อ worker กลับมา งานจะทำต่อเองภายในประมาณ 15 วินาที
