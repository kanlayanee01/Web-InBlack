# ADR-0002: Idempotency ของ webhook และ fulfillment

**สถานะ:** ใช้งาน

## ปัญหา

Provider ส่ง webhook ซ้ำได้ รวมถึงส่งพร้อมกัน ส่วน `mock-game` ไม่กันซ้ำให้ และอาจตอบ timeout ทั้งที่เติมสำเร็จแล้ว

## Webhook

`webhook_events.event_id` เป็น primary key และการ insert อยู่ใน transaction เดียวกับการเปลี่ยนสถานะ

```sql
INSERT INTO webhook_events (...) ON CONFLICT (event_id) DO NOTHING RETURNING event_id
```

ตัวซ้ำที่มาพร้อมกันจะรอจนตัวแรก commit แล้วเจอ conflict จึงตอบ 200 ทันที ถ้าตัวแรก rollback แถวนี้ก็หายไปด้วย การ retry ของ provider จึงไม่ถูกมองเป็นตัวซ้ำโดยผิดพลาด ซึ่งเป็นเหตุผลที่ไม่ใช้ Redis SETNX เพราะจะเหลือ key ค้างแม้งานล้มเหลว

ลายเซ็นตรวจจาก raw body ก่อน parse JSON และปฏิเสธถ้า timestamp ต่างเกิน 300 วินาที

## Idempotency-Key ของ API

key กับงานที่มันปกป้อง commit พร้อมกัน ตัวซ้ำรอที่ primary key แล้วอ่าน `response_body` ที่เก็บเป็น bytes จึงได้คำตอบเหมือนกันทุกไบต์ คำตอบปฏิเสธเชิงธุรกิจก็ถูกเก็บด้วย

## Fulfillment

ไม่มีทางทำให้ "ยิง POST แล้วบันทึกผล" เป็น atomic ข้าม service ได้ จึงออกแบบให้ทุกจังหวะที่ตายรู้ว่าต้องทำอะไรต่อ

1. COMMIT สถานะ `SUBMITTING` **ก่อน** ยิง POST
2. แถวที่ไม่ใช่ `PENDING` อาจเคยยิงไปแล้ว ต้อง `GET /topups/{reference}` ก่อนเสมอ
3. ยิง POST ซ้ำเฉพาะเมื่อ GET ยืนยันว่าไม่ได้เติม ถ้า GET ตอบไม่ได้ ห้ามยิง

| worker ตายตอนไหน | รอบถัดไปเจอ | ทำอะไร |
|---|---|---|
| ก่อน commit `SUBMITTING` | `PENDING` | ยิงได้เลย เพราะยังไม่เคยส่ง |
| หลัง commit ก่อนยิง | `SUBMITTING` | GET ไม่เจอ จึงยิง |
| หลังยิงก่อนบันทึกผล | `SUBMITTING` | GET เจอ `SUCCEEDED` จึงไม่ยิง |

`reference` คงที่ต่อรายการสินค้าและไม่สร้างใหม่แม้ support สั่งเติมซ้ำ

`mock-game` บันทึก request ตั้งแต่ตอนรับ ดังนั้น GET ระหว่าง POST ที่ช้าจะได้ `PROCESSING` ไม่ใช่ "ไม่พบ"

advisory lock ของ PostgreSQL กันไม่ให้สอง worker ทำออเดอร์เดียวกัน lock ผูกกับ connection จึงหลุดเองถ้า worker ตาย

## ผลลัพธ์

สคริปต์ `scripts/chaos.py` ฆ่า worker และ mock-game ระหว่างเติมเกมจริง `/stats` ไม่มี reference ใดเกิน 1

ข้อแลกเปลี่ยนคือถ้าเช็กผลไม่ได้จนหมดรอบ ออเดอร์จะไป `NEEDS_REVIEW` ซึ่งเลือกให้คนตรวจแทนการเสี่ยงเติมซ้ำ
