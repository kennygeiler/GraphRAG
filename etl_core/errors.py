"""ETL engine errors."""


class MaxRetriesError(RuntimeError):
    """Raised when the fix loop exhausts all attempts."""

    def __init__(self, retry_count: int, last_error: str):
        self.retry_count = retry_count
        self.last_error = last_error
        super().__init__(
            f"Schema/rules repair exhausted after {retry_count} attempt(s) "
            f"(Pydantic or deterministic rules; audit not reached): {last_error}"
        )
