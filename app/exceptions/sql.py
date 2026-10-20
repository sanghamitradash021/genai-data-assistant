"""Exceptions raised by the SQL validation/execution tools."""


class SQLValidationError(ValueError):
    """retryable=False for security rejections (writes, forbidden functions, system schemas): the agent must
    not ask the model to "repair" those, or it may swap in an unrelated query."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable
