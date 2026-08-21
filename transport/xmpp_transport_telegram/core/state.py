from enum import Enum


class AuthStatus(str, Enum):
    WAITING_CODE = "waiting_code"
    WAITING_PASSWORD = "waiting_password"
    CONNECTED = "connected"
    FAILED = "failed"
