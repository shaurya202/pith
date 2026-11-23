"""Errors we can show to the person using Pith, in plain language."""


class UserError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status
