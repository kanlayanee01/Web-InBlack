# Design

แบบทำด้วย Claude Design ชื่อ "InBlack Top-Up Design" ไฟล์ export อยู่ในโฟลเดอร์นี้ เพราะลิงก์แชร์ของ Claude Design เปิดได้เฉพาะคนในองค์กรเดียวกัน

| ไฟล์ | รายละเอียด |
|---|---|
| [inblack-design.html](inblack-design.html) | Standalone HTML รวมทุกอย่างไว้ในไฟล์เดียว เปิดได้โดยไม่ต้องต่ออินเทอร์เน็ต |
| [inblack-design.pdf](inblack-design.pdf) | PDF 9 หน้า |

## เนื้อหาในแบบ

| ส่วน | รายละเอียด |
|---|---|
| Design system | สี ระยะ ตัวอักษร แบบ Light และ Dark |
| Component | Status Badge ทุกสถานะ, Button ทุก state, Input + error, Order Card, Timeline, Toast |
| หน้าจอ | หน้าแรก, สินค้า, Checkout, Tracking, My Orders, Admin แบบ Desktop และ Mobile พร้อม Loading, Empty, Error และตัวอย่าง Dark |
| Prototype | เลือกสินค้า → checkout → จ่ายเงิน → tracking (ในไฟล์ HTML) |

แนวทาง: พื้นครีม `#F8F2D8` น้ำเงิน `#1400C3` ส้ม `#FF4E02` สไตล์กระจกฝ้า ใช้งานง่าย ไอคอนเส้น ไม่มีอิโมจิ มีตะกร้าสินค้าและปุ่มสลับโหมดสว่าง/มืด

Tokens ในโค้ดอยู่ที่ [web/src/app/globals.css](../web/src/app/globals.css) และใช้ค่าเดียวกับในแบบ จุดที่โค้ดต่างจากแบบและเหตุผล ดูที่ [AI_LOG.md](../AI_LOG.md)
