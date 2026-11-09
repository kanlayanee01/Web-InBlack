from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt

from app.domain.pricing import PaymentMethod

MAX_QTY = 99
_EMAIL_PATTERN = r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$"
_PRINTABLE_ASCII = r"^[\x20-\x7e]+$"

Email = Annotated[
    str,
    Field(max_length=254, pattern=_EMAIL_PATTERN),
    AfterValidator(str.lower),
]


class ItemInput(BaseModel):
    sku: str = Field(min_length=1, max_length=64, pattern=_PRINTABLE_ASCII)
    qty: StrictInt = Field(ge=1, le=MAX_QTY)


class QuoteRequest(BaseModel):
    items: list[ItemInput] = Field(min_length=1, max_length=20)
    payment_method: PaymentMethod
    voucher_code: str | None = Field(default=None, max_length=32, pattern=_PRINTABLE_ASCII)


class CreateOrderRequest(QuoteRequest):
    email: Email
    game_uid: str = Field(pattern=r"^[A-Za-z0-9_-]{4,32}$")


class PaymentWebhook(BaseModel):
    model_config = ConfigDict(strict=False)

    event_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    type: Literal["payment.succeeded", "payment.failed", "payment.refunded"]
    order_id: str = Field(min_length=1, max_length=64, pattern=_PRINTABLE_ASCII)
    amount_satang: StrictInt = Field(ge=1, le=10**12)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    occurred_at: datetime
