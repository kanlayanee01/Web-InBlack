"""PII masking for logs and for admin users who may not see full customer details."""


def mask_email(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator:
        return "***"
    return f"{local[:1]}***@{domain}"


def mask_game_uid(uid: str) -> str:
    if len(uid) <= 4:
        return "****"
    return f"{uid[:2]}***{uid[-2:]}"
