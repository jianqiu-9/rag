"""对外错误使用固定文本，避免泄露数据库连接串、密钥和模型原始响应。"""


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
