class AudioError(Exception):
    def __init__(self, code: str, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def document(self) -> dict:
        return {"code": self.code, "message": str(self), "details": self.details}
