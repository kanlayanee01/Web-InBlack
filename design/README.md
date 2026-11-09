# Design

แบบทำด้วย Claude Design ชื่อไฟล์ "InBlack Top-Up Design"

| รายการ | รายละเอียด |
|---|---|
| Design system | สี ระยะ ตัวอักษร แบบ Light และ Dark |
| Component | Status Badge, Button ทุก state, Input + error, Order Card, Timeline, Toast |
| หน้าจอ | หน้าแรก, สินค้า, Checkout, Tracking, My Orders, Admin แบบ Desktop และ Mobile พร้อม Loading, Empty, Error |
| Prototype | เลือกสินค้า → checkout → จ่ายเงิน → tracking |

แนวทาง: พื้นครีม `#F8F2D8` น้ำเงิน `#1400C3` ส้ม `#FF4E02` สไตล์กระจกฝ้า ใช้งานง่าย ไอคอนเส้น ไม่มีอิโมจิ

Tokens ในโค้ดอยู่ที่ [web/src/app/globals.css](../web/src/app/globals.css) และใช้ค่าเดียวกับในแบบ

**สถานะไฟล์ export:** รอวางไฟล์ standalone HTML และ PDF ในโฟลเดอร์นี้
