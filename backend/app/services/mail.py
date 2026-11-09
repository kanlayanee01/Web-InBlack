import asyncio
import smtplib
from email.message import EmailMessage

from app.settings import Settings

SMTP_TIMEOUT_SECONDS = 5


def _deliver(settings: Settings, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.send_message(message)


async def send(settings: Settings, to: str, subject: str, body: str) -> None:
    await asyncio.to_thread(_deliver, settings, to, subject, body)


def baht(satang: int) -> str:
    return f"{satang // 100:,}.{satang % 100:02d}"
