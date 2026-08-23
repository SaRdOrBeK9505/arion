import secrets
import uuid
from datetime import timedelta
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Company(models.Model):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=32, blank=True)
    telegram_contact = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True, help_text=_("Owner uchun ichki izoh"))
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CustomerCode(models.Model):
    code = models.CharField(max_length=6, unique=True, db_index=True)
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,   # kompaniya o'chirilsa ham kod qolaveradi, faqat bog'lanish uziladi
        null=True,
        blank=True,
        related_name="codes",
    )
    label = models.CharField(
        max_length=255, blank=True,
        help_text=_("Owner uchun eslatma, masalan mijoz ismi/tashkiloti")
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["code"])]

    def __str__(self):
        company_part = self.company.name if self.company else _("Kompaniyasiz")
        return f"{self.code} ({company_part})"

    @staticmethod
    def generate_unique_code() -> str:
        """Kriptografik jihatdan xavfsiz, kollizisiz 6 xonali kod generatsiya qilida."""
        while True:
            code = f"{secrets.randbelow(1_000_000):06d}"
            if not CustomerCode.objects.filter(code=code).exists():
                return code


class AccessSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer_code = models.ForeignKey(CustomerCode, on_delete=models.CASCADE)
    ip_address = models.GenericIPAddressField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    DEFAULT_TTL_MINUTES = 20

    def is_valid(self) -> bool:
        return self.expires_at > timezone.now()

    @classmethod
    def create_for(cls, customer_code: "CustomerCode", ip_address: str) -> "AccessSession":
        return cls.objects.create(
            customer_code=customer_code,
            ip_address=ip_address,
            expires_at=timezone.now() + timedelta(minutes=cls.DEFAULT_TTL_MINUTES),
        )


class CodeVerificationLog(models.Model):
    code_attempted = models.CharField(max_length=6)
    ip_address = models.GenericIPAddressField()
    was_successful = models.BooleanField()
    matched_code = models.ForeignKey(CustomerCode, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["ip_address", "created_at"])]
        verbose_name = _("Kod tekshirish logi")
        verbose_name_plural = _("Kod tekshirish loglari")


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Kutilmoqda")
        PAID = "paid", _("To'landi")
        FAILED = "failed", _("Muvaffaqiyatsiz")
        EXPIRED = "expired", _("Muddati o'tdi")
        CANCELLED = "cancelled", _("Bekor qilindi")

    # --- Bog'lanishlar (faqat referens/filtrlash uchun, hisobotda ishlatilmaydi) ---
    session = models.ForeignKey(AccessSession, on_delete=models.PROTECT, related_name="payments")
    customer_code = models.ForeignKey(CustomerCode, on_delete=models.PROTECT, related_name="payments")
    company = models.ForeignKey(
        Company, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payments",
        help_text=_("Referens uchun. Hisobotlarda company_name_snapshot ishlatilsin!"),
    )

    # --- SNAPSHOT maydonlar (o'zgarmas, to'lov paytidagi holat) ---
    company_name_snapshot = models.CharField(
        max_length=255, blank=True,
        help_text=_("To'lov paytida kod biriktirilgan kompaniya nomi. Bo'sh = kompaniyasiz to'lov."),
    )
    customer_code_snapshot = models.CharField(max_length=6)

    # --- To'lov ma'lumotlari ---
    amount = models.PositiveBigIntegerField(help_text=_("Minor birlikda (tiyin)"))
    currency = models.CharField(max_length=3, default="UZS")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # --- Gateway integratsiyasi ---
    gateway_invoice_id = models.CharField(max_length=128, blank=True, null=True, db_index=True)
    gateway_payment_id = models.CharField(max_length=128, blank=True, null=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    gateway_raw_response = models.JSONField(null=True, blank=True)  # debugging/audit uchun

    # --- Meta ---
    ip_address = models.GenericIPAddressField()
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    MIN_AMOUNT = 10_000_00   # 10,000 so'm (tiyinda)
    MAX_AMOUNT = 500_000_000_00

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["gateway_invoice_id"]),
        ]
        verbose_name = _("To'lov")
        verbose_name_plural = _("To'lovlar")

    def __str__(self):
        return f"{self.customer_code_snapshot} - {self.amount // 100:,} {self.currency}".replace(",", " ")

    def save(self, *args, **kwargs):
        # Snapshot yozilishini kafolatlaydi — birinchi saqlashda,
        # keyingi update'larda o'zgartirilmasligi kerak (create() da to'ldiriladi).
        super().save(*args, **kwargs)
