import json
import uuid
from datetime import timedelta
from django.utils import timezone
from django.db.models import Count
from django.conf import settings
from sentry_sdk import capture_message
from .models import Company, CustomerCode, AccessSession, Payment, CodeVerificationLog, WebhookEvent
from .gateway.montra_client import MontraClient, MontraClientError
from .exceptions import (
    CodeNotFoundError,
    SessionExpiredError,
    InvalidAmountError,
    WebhookSignatureError,
    PaymentNotFoundError,
)


def verify_code(code: str, ip_address: str) -> AccessSession:
    """
    Kodni tekshirish va AccessSession yaratish
    
    Args:
        code: 6 xonali kod
        ip_address: Mijoz IP adresi
        
    Returns:
        AccessSession: Yaratilgan sessiya
        
    Raises:
        CodeNotFoundError: Kod topilmadi yoki faol emas
    """
    try:
        customer_code = CustomerCode.objects.get(code=code, is_active=True)
        was_successful = True
    except CustomerCode.DoesNotExist:
        was_successful = False
        # Log yozish
        CodeVerificationLog.objects.create(
            code_attempted=code,
            ip_address=ip_address,
            was_successful=False,
            matched_code=None
        )
        raise CodeNotFoundError("Kod topilmadi yoki faol emas")

    # Muvaffaqiyatli urinish logi
    CodeVerificationLog.objects.create(
        code_attempted=code,
        ip_address=ip_address,
        was_successful=True,
        matched_code=customer_code
    )

    # Global brute-force tekshiruvi (5 daqiqada 50+ muvaffaqiyatsiz urinish)
    five_minutes_ago = timezone.now() - timedelta(minutes=5)
    failed_attempts = CodeVerificationLog.objects.filter(
        created_at__gte=five_minutes_ago,
        was_successful=False
    ).count()

    if failed_attempts >= 50:
        capture_message(
            f"Potentsial brute-force hujumi: 5 daqiqada {failed_attempts} muvaffaqiyatsiz urinish",
            level="warning"
        )

    # Sessiya yaratish
    return AccessSession.create_for(customer_code, ip_address)


def create_payment(
    session_id: uuid.UUID,
    amount: int,
    ip_address: str,
    success_url: str = "https://arion-export.uz/payment/success",
    fail_url: str = "https://arion-export.uz/payment/fail"
) -> dict:
    """
    To'lov yaratish va MONTRA invoice yaratish

    Args:
        session_id: AccessSession ID
        amount: Summa (SO'MDA - mijoz kiritgan qiymat)
        ip_address: Mijoz IP adresi
        success_url: Muvaffaqiyatli to'lov URL
        fail_url: Muvaffaqiyatsiz to'lov URL

    Returns:
        dict: {'payment_url': str, 'payment_id': int}

    Raises:
        SessionExpiredError: Sessiya muddati o'tgan
        InvalidAmountError: Summa noto'g'ri
    """
    # 1. Sessiyani olish va tekshirish
    try:
        session = AccessSession.objects.get(id=session_id)
    except AccessSession.DoesNotExist:
        raise SessionExpiredError("Sessiya topilmadi")

    if not session.is_valid():
        raise SessionExpiredError("Sessiya muddati o'tgan")

    # 2. Summani tekshirish (so'mda)
    MIN_AMOUNT_SOM = 1000
    MAX_AMOUNT_SOM = 500_000_000

    if amount < MIN_AMOUNT_SOM or amount > MAX_AMOUNT_SOM:
        raise InvalidAmountError(
            f"Summa {MIN_AMOUNT_SOM:,} dan {MAX_AMOUNT_SOM:,} gacha bo'lishi kerak (so'mda)"
        )

    # 3. MONTRA ga yuborish uchun tiyinga o'tkazish
    amount_in_tiyin = amount * 100

    # 4. company_name_snapshot ni aniqlash
    company = session.customer_code.company
    company_name_snapshot = company.name if company else ""
    description = company_name_snapshot if company_name_snapshot else "Kompaniyasiz to'lov"

    # 5. Payment yaratish (snapshot bilan, tiyinda saqlaymiz)
    idempotency_key = str(uuid.uuid4())
    payment = Payment.objects.create(
        session=session,
        customer_code=session.customer_code,
        company=company,  # joriy holat, referens
        company_name_snapshot=company_name_snapshot,  # QOTIRILGAN qiymat
        customer_code_snapshot=session.customer_code.code,
        amount=amount_in_tiyin,  # Tiyinda saqlash
        ip_address=ip_address,
        idempotency_key=idempotency_key,
    )

    # 6. MONTRA orqali invoice yaratish (tiyinda yuboramiz)
    try:
        montra_client = MontraClient()
        invoice_response = montra_client.create_invoice(
            external_id=str(payment.id),
            amount=amount_in_tiyin,  # Tiyinda yuborish
            currency="UZS",
            description=description,
            success_url=success_url,
            fail_url=fail_url,
            idempotency_key=idempotency_key,
        )

        # Gateway ma'lumotlarini saqlash
        data = invoice_response.get('data', {})
        payment.gateway_invoice_id = data.get('id') or invoice_response.get('invoiceId') or invoice_response.get('id')
        payment.gateway_raw_response = invoice_response
        payment.save(update_fields=['gateway_invoice_id', 'gateway_raw_response'])

        return {
            'payment_url': data.get('paymentUrl') or data.get('url') or invoice_response.get('paymentUrl'),
            'payment_id': payment.id,
        }

    except MontraClientError as e:
        # Xatolik holatida payment statusini FAILED qilish
        payment.status = Payment.Status.FAILED
        payment.gateway_raw_response = {'error': str(e)}
        payment.save(update_fields=['status', 'gateway_raw_response'])
        raise


def handle_webhook(raw_body: bytes, signature_header: str, event: str, webhook_id: str) -> None:
    """
    MONTRA webhook'ni qayta ishlash

    Args:
        raw_body: HTTP body'ning XOM baytlari (request.body — request.data EMAS,
            chunki imzo tekshiruvi aynan tarmoqdan kelgan baytlarga bog'liq).
        signature_header: `X-Webhook-Signature` header qiymati.
        event: `X-Webhook-Event` header qiymati (masalan "invoice.paid").
        webhook_id: `X-Webhook-Id` header qiymati — dedup uchun.

    Raises:
        WebhookSignatureError: Imzo noto'g'ri
        PaymentNotFoundError: To'lov topilmadi
    """
    if not event or not webhook_id:
        raise WebhookSignatureError("X-Webhook-Event yoki X-Webhook-Id header yo'q")

    # 1. Imzo tekshirish (rasmiy MONTRA spetsifikatsiyasiga muvofiq)
    montra_client = MontraClient()
    if not montra_client.verify_webhook_signature(raw_body, signature_header, event):
        raise WebhookSignatureError("Webhook imzosi noto'g'ri")

    # 2. Dedup — MONTRA bir xil webhookni qayta yuborishi (retry) mumkin.
    # get_or_create atomik: bir xil webhook_id ikkinchi marta kelsa,
    # created=False bo'ladi va qayta ishlanmaydi.
    _, created = WebhookEvent.objects.get_or_create(webhook_id=webhook_id, defaults={"event": event})
    if not created:
        return

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, TypeError):
        raise PaymentNotFoundError("Webhook body JSON emas")

    if payload.get("event") != event:
        raise WebhookSignatureError("event header va body mos kelmadi")

    data = payload.get("data", {})
    invoice_data = data.get("invoice", {})
    external_id = invoice_data.get("externalId")
    if not external_id:
        # invoice bilan bog'liq bo'lmagan eventlar (masalan settlement.*, fiscal.*)
        # bizning oqimimizda kerak emas — jim o'tkazib yuboramiz.
        return

    try:
        payment = Payment.objects.get(id=external_id)
    except (Payment.DoesNotExist, ValueError):
        raise PaymentNotFoundError(f"To'lov topilmadi: {external_id}")

    # 3. Idempotency — allaqachon PAID bo'lsa, hech narsa qilmaymiz
    if payment.status == Payment.Status.PAID:
        return

    # 4. Event nomi bo'yicha yo'naltirish (MONTRA rasmiy event ro'yxati —
    # https://docs.montratech.com/ru/webhooks#события)
    if event == "invoice.paid":
        payment.status = Payment.Status.PAID
        payment.paid_at = timezone.now()
        payment.gateway_payment_id = invoice_data.get("payment", {}).get("id")
        payment.gateway_raw_response = payload
        payment.save(update_fields=["status", "paid_at", "gateway_payment_id", "gateway_raw_response"])

        from .tasks import send_telegram_notification
        send_telegram_notification.delay(payment.id)

    elif event == "invoice.failed":
        payment.status = Payment.Status.FAILED
        payment.gateway_raw_response = payload
        payment.save(update_fields=["status", "gateway_raw_response"])

    elif event == "invoice.expired":
        payment.status = Payment.Status.EXPIRED
        payment.gateway_raw_response = payload
        payment.save(update_fields=["status", "gateway_raw_response"])

    elif event == "invoice.cancelled":
        payment.status = Payment.Status.CANCELLED
        payment.gateway_raw_response = payload
        payment.save(update_fields=["status", "gateway_raw_response"])

    # `invoice.created`, `invoice.processing`, `payment.authorized`,
    # `payment.captured` (DMS) va boshqa eventlar hozircha bizning oddiy
    # bir bosqichli oqimimizda kerak emas — faqat WebhookEvent'ga
    # yozilgani yetarli (dedup jadvalidan tashqarida qo'shimcha ish yo'q).
