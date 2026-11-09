# Design

โฟลเดอร์นี้สำหรับไฟล์ export จาก Claude Design (standalone HTML และ PDF) ตามข้อ 9.1 ของโจทย์

**สถานะปัจจุบัน: ยังไม่มีไฟล์จาก Claude Design**

สิ่งที่มีในโค้ดแล้ว

- Design tokens สี ระยะ และตัวอักษร พร้อม Light/Dark ใน [web/src/app/globals.css](../web/src/app/globals.css)
- Component: Status Badge ทุกสถานะ, Button ทุก state, Input + error, Order Card, Timeline
- สถานะ Loading, Empty, Error ในหน้าที่โหลดข้อมูลฝั่ง client
