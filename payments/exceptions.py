"""Service layer exceptions"""


class PaymentServiceError(Exception):
    """Base exception for payment service errors"""
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
