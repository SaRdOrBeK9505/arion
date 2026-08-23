"""Service layer exceptions"""


class PaymentServiceError(Exception):
    """Base exception for payment service errors"""
    pass


class CodeNotFoundError(PaymentServiceError):
    """Kod topilmadi yoki faol emas"""
    pass


class SessionExpiredError(PaymentServiceError):
    """Sessiya muddati o'tgan"""
    pass


class InvalidAmountError(PaymentServiceError):
    """Summa noto'g'ri"""
    pass


class WebhookSignatureError(PaymentServiceError):
    """Webhook imzosi noto'g'ri"""
    pass


class PaymentNotFoundError(PaymentServiceError):
    """To'lov topilmadi"""
    pass
