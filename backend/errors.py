class AppError(Exception):
    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class AuthzError(AppError):
    def __init__(self, message='Forbidden', status=403):
        super().__init__(message, status=status, code='FORBIDDEN')


class ValidationError(AppError):
    def __init__(self, message, status=400):
        super().__init__(message, status=status, code='VALIDATION_ERROR')
