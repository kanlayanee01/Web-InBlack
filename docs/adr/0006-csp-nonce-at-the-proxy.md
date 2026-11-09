# ADR-0006: เติม CSP nonce ที่ proxy เพื่อคงหน้า static

**สถานะ:** ใช้งาน

## ปัญหา

โจทย์ต้องการ CSP ที่ไม่มี `unsafe-inline` สำหรับ script และต้องการหน้าแคตตาล็อกเป็น SSG/ISR สองข้อนี้ขัดกันใน Next.js เพราะ Next ใส่ inline script เองทุกหน้า และวิธีมาตรฐานคือสร้าง nonce ใน middleware ซึ่งทำให้ทุกหน้ากลายเป็น dynamic

## ทางเลือก

| ทางเลือก | ปัญหา |
|---|---|
| ยอมใส่ `unsafe-inline` | ผิดโจทย์ |
| nonce ใน middleware ของ Next | เสีย SSG/ISR |
| Hash ของ inline script | hash เปลี่ยนทุกครั้งที่ ISR สร้างหน้าใหม่ |
| **ให้ nginx เติม nonce ตอนส่งออก** | ต้องส่ง HTML แบบไม่บีบอัดระหว่าง Next กับ nginx |

## การตัดสินใจ

```nginx
sub_filter '<script' '<script nonce="$request_id"';
add_header Content-Security-Policy "... script-src 'self' 'nonce-$request_id' ...";
```

`$request_id` เป็นค่าสุ่ม 128 บิตต่อ request Next ตอบ HTML แบบไม่บีบอัด แล้ว nginx บีบอัดให้ไคลเอนต์แทน

ไม่เสี่ยงที่ผู้ใช้จะได้ nonce ไปกับข้อความของตัวเอง เพราะ React escape `<` ในทุกข้อความ และ JSON-LD ถูกแทน `<` ด้วย `<` ข้อความ `<script` จึงเกิดได้เฉพาะแท็กจริงของ Next

## ผลลัพธ์

ทุก `<script>` มี nonce, หน้ายังเป็น ISR (`x-nextjs-cache: HIT`) และ Playwright ไม่พบ CSP violation ตลอด flow ซื้อจนคืนเงิน
