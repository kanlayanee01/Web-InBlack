"""OTP login, admin login, sessions and guest tracking tokens."""

import asyncio
import hashlib
import hmac
import secrets
import time
from uuid import UUID

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

from app.problem import Problem
from app.services import mail
from app.settings import Settings

OTP_TTL_MINUTES = 5
OTP_MAX_FAILURES = 5
OTP_LOCK_MINUTES = 15
CUSTOMER_SESSION_DAYS = 30
ADMIN_SESSION_HOURS = 8
TRACKING_TOKEN_SECONDS = 7 * 24 * 3600

_hasher = PasswordHasher()
# Unknown emails are verified against this, so they take as long as real accounts.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def _keyed(secret: str, purpose: str, value: str) -> str:
    return hmac.new(secret.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def make_tracking_token(secret: str, order_id: str, now: int | None = None) -> str:
    expires = (int(time.time()) if now is None else now) + TRACKING_TOKEN_SECONDS
    return f"{expires}.{_keyed(secret, 'track', f'{order_id}:{expires}')}"


def tracking_token_valid(secret: str, order_id: str, token: str) -> bool:
    expires, _, signature = token.partition(".")
    if not expires.isascii() or not expires.isdigit() or int(expires) < time.time():
        return False
    expected = _keyed(secret, "track", f"{order_id}:{expires}")
    return hmac.compare_digest(expected.encode(), signature.encode())


async def request_otp(pool: asyncpg.Pool, settings: Settings, email: str) -> None:
    """Always looks the same to the caller, whether or not a code was actually sent."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    code_hash = _keyed(settings.session_secret, "otp", f"{email}:{code}")
    # Failure counts survive a re-send, otherwise requesting a new code would reset the lockout.
    issued = await pool.fetchval(
        """
        INSERT INTO otp_codes AS otp (email, code_hash, expires_at)
        VALUES ($1, $2, now() + make_interval(mins => $3))
        ON CONFLICT (email) DO UPDATE SET
            code_hash = EXCLUDED.code_hash,
            expires_at = EXCLUDED.expires_at,
            consumed_at = NULL,
            created_at = now(),
            failed_attempts = CASE WHEN otp.locked_until <= now() THEN 0
                                   ELSE otp.failed_attempts END,
            locked_until = CASE WHEN otp.locked_until <= now() THEN NULL
                                ELSE otp.locked_until END
        WHERE otp.locked_until IS NULL OR otp.locked_until <= now()
        RETURNING email
        """,
        email,
        code_hash,
        OTP_TTL_MINUTES,
    )
    if issued is not None:
        body = f"รหัส OTP ของคุณคือ {code}\nรหัสนี้หมดอายุใน {OTP_TTL_MINUTES} นาที"
        await mail.send(settings, email, f"InBlack OTP {code}", body)


async def verify_otp(
    pool: asyncpg.Pool, settings: Settings, email: str, code: str
) -> UUID:
    """Returns the customer id. Failed attempts commit even though the request is refused."""
    invalid = Problem(400, "OTP_INVALID", "The code is wrong or has expired")
    expected = _keyed(settings.session_secret, "otp", f"{email}:{code}")
    error: Problem | None = None
    customer_id: UUID | None = None
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT *, COALESCE(locked_until > now(), false) AS locked,
                   ceil(extract(epoch FROM locked_until - now()))::integer AS lock_seconds,
                   expires_at <= now() AS expired
            FROM otp_codes WHERE email = $1 FOR UPDATE
            """,
            email,
        )
        if row is None:
            error = invalid
        elif row["locked"]:
            error = Problem(
                429,
                "OTP_LOCKED",
                "Too many wrong codes, please try again later",
                {"Retry-After": str(row["lock_seconds"])},
            )
        elif row["consumed_at"] is not None or row["expired"]:
            error = invalid
        elif not hmac.compare_digest(row["code_hash"].encode(), expected.encode()):
            await conn.execute(
                """
                UPDATE otp_codes SET failed_attempts = failed_attempts + 1,
                    locked_until = CASE WHEN failed_attempts + 1 >= $2
                                        THEN now() + make_interval(mins => $3) END
                WHERE email = $1
                """,
                email,
                OTP_MAX_FAILURES,
                OTP_LOCK_MINUTES,
            )
            error = invalid
        else:
            await conn.execute(
                "UPDATE otp_codes SET consumed_at = now(), failed_attempts = 0 WHERE email = $1",
                email,
            )
            customer_id = await conn.fetchval(
                """
                INSERT INTO customers (email) VALUES ($1)
                ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
                RETURNING id
                """,
                email,
            )
    if error is not None:
        raise error
    assert customer_id is not None
    return customer_id


async def verify_admin_password(
    pool: asyncpg.Pool, email: str, password: str
) -> asyncpg.Record:
    admin = await pool.fetchrow(
        "SELECT * FROM admin_users WHERE email = $1 AND is_active", email
    )
    stored = admin["password_hash"] if admin is not None else _DUMMY_HASH
    try:
        await asyncio.to_thread(_hasher.verify, stored, password)
    except (Argon2Error, InvalidHashError) as error:
        raise Problem(401, "INVALID_CREDENTIALS", "Email or password is wrong") from error
    if admin is None:
        raise Problem(401, "INVALID_CREDENTIALS", "Email or password is wrong")
    return admin


def hash_password(password: str) -> str:
    return _hasher.hash(password)


async def create_session(
    pool: asyncpg.Pool, kind: str, subject_id: UUID, lifetime_seconds: int
) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    await pool.execute(
        """
        INSERT INTO sessions (token_hash, kind, customer_id, admin_id, csrf_token, expires_at)
        VALUES ($1, $2, $3, $4, $5, now() + make_interval(secs => $6::integer))
        """,
        hash_token(token),
        kind,
        subject_id if kind == "customer" else None,
        subject_id if kind == "admin" else None,
        csrf_token,
        lifetime_seconds,
    )
    return token, csrf_token


async def delete_session(pool: asyncpg.Pool, token: str) -> None:
    await pool.execute("DELETE FROM sessions WHERE token_hash = $1", hash_token(token))
