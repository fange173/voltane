"""当前测试进程的临时管理员凭据。"""
from secrets import token_urlsafe

ADMIN_LOGIN = ("admin", token_urlsafe(24))
