"""ETL engine errors."""


class MaxRetriesError(RuntimeError):
    """Raised when the fix loop exhausts all attempts."""

    def __init__(self, retry_count: int, last_error: str):
        self.retry_count = retry_count
        self.last_error = last_error
        super().__init__(
            f"Validation still failing after {retry_count} fix attempts: {last_error}"
        )
