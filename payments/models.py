import secrets
import uuid
from datetime import timedelta
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Kutilmoqda")
        PAID = "paid", _("To'landi")
        FAILED = "failed", _("Muvaffaqiyatsiz")
        EXPIRED = "expired", _("Muddati o'tdi")
        CANCELLED = "cancelled", _("Bekor qilindi")

    # --- SNAPSHOT maydonlar (o'zgarmas, to'lov paytidagi holat) ---
    company_name_snapshot = models.CharField(
        max_length=255, blank=True,
        help_text=_("To'lov paytida kod biriktirilgan kompaniya nomi. Bo'sh = kompaniyasiz to'lov."),
    )
    customer_code_snapshot = models.CharField(max_length=6, blank=True)

    # --- To'lov ma'lumotlari ---
    amount = models.PositiveBigIntegerField(help_text=_("Minor birlikda (tiyin)"))
    currency = models.CharField(max_length=3, default="UZS")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    is_test = models.BooleanField(
        default=False,
        help_text=_(
            "To'lov yaratilgan paytdagi MONTRA_MODE=TEST bo'lsa True. "
            "Snapshot maydon — keyinchalik o'zgartirilmaydi (Telegram "
            "xabarnomasida 'test' belgisi shu asosda ko'rsatiladi)."
        ),
    )

    # --- Gateway integratsiyasi ---
    gateway_invoice_id = models.CharField(max_length=128, blank=True, null=True, db_index=True)
    gateway_payment_id = models.CharField(max_length=128, blank=True, null=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    gateway_raw_response = models.JSONField(null=True, blank=True)  # debugging/audit uchun

    # --- Meta ---
    ip_address = models.GenericIPAddressField()
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    MIN_AMOUNT = 100_000   # 1,000 so'm (tiyinda)
    MAX_AMOUNT = 50_000_000_000  # 500,000,000 so'm (tiyinda)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["gateway_invoice_id"]),
        ]
        verbose_name = _("To'lov")
        verbose_name_plural = _("To'lovlar")

    def __str__(self):
        return f"{self.amount // 100:,} {self.currency}".replace(",", " ")

    # Snapshot maydonlar — yaratilgandan keyin o'zgartirib bo'lmaydi (moliyaviy audit uchun)
    _SNAPSHOT_FIELDS = ("company_name_snapshot", "customer_code_snapshot", "is_test")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)


class WebhookEvent(models.Model):
    """
    MONTRA'dan kelgan har bir webhookni `X-Webhook-Id` bo'yicha bir marta
    qayta ishlash uchun. MONTRA tarmoq muammosi bo'lsa bir xil webhookni
    qayta yuborishi mumkin (retry) — bu jadval shu holatlarda ikkilanishning
    oldini oladi (masalan Telegram xabarnomasi ikki marta yuborilmasin).
    """
    webhook_id = models.CharField(max_length=128, unique=True, db_index=True)
    event = models.CharField(max_length=64)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Webhook logi")
        verbose_name_plural = _("Webhook loglari")

    def __str__(self):
        return f"{self.event} ({self.webhook_id})"