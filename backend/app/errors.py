class AppError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class DatabaseError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status=502, code="database")
