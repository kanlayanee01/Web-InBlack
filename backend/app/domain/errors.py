class DomainError(Exception):
    """A business rule was violated. `code` is the machine-readable API error code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
