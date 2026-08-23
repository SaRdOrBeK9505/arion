import uuid
from datetime import timedelta
from django.utils import timezone
from django.db.models import Count
from django.conf import settings
from sentry_sdk import capture_message
from .models import Company, CustomerCode, AccessSession, Payment, CodeVerificationLog
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
        amount: Summa (tiyinda)
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

    # 2. Summani tekshirish
    if amount < Payment.MIN_AMOUNT or amount > Payment.MAX_AMOUNT:
        raise InvalidAmountError(
            f"Summa {Payment.MIN_AMOUNT // 100:,} dan {Payment.MAX_AMOUNT // 100:,} gacha bo'lishi kerak"
        )

    # 3. company_name_snapshot ni aniqlash
    company = session.customer_code.company
    company_name_snapshot = company.name if company else ""
    description = company_name_snapshot if company_name_snapshot else "Kompaniyasiz to'lov"

    # 4. Payment yaratish (snapshot bilan)
    idempotency_key = str(uuid.uuid4())
    payment = Payment.objects.create(
        session=session,
        customer_code=session.customer_code,
        company=company,  # joriy holat, referens
        company_name_snapshot=company_name_snapshot,  # QOTIRILGAN qiymat
        customer_code_snapshot=session.customer_code.code,
        amount=amount,
        ip_address=ip_address,
        idempotency_key=idempotency_key,
    )

    # 5. MONTRA orqali invoice yaratish
    try:
        montra_client = MontraClient()
        invoice_response = montra_client.create_invoice(
            external_id=str(payment.id),
            amount=amount,
            currency="UZS",
            description=description,
            success_url=success_url,
            fail_url=fail_url,
            idempotency_key=idempotency_key,
        )

        # Gateway ma'lumotlarini saqlash
        payment.gateway_invoice_id = invoice_response.get('invoiceId') or invoice_response.get('id')
        payment.gateway_raw_response = invoice_response
        payment.save(update_fields=['gateway_invoice_id', 'gateway_raw_response'])

        return {
            'payment_url': invoice_response.get('paymentUrl') or invoice_response.get('url'),
            'payment_id': payment.id,
        }

    except MontraClientError as e:
        # Xatolik holatida payment statusini FAILED qilish
        payment.status = Payment.Status.FAILED
        payment.gateway_raw_response = {'error': str(e)}
        payment.save(update_fields=['status', 'gateway_raw_response'])
        raise


def handle_webhook(payload: dict, signature_header: str) -> None:
    """
    MONTRA webhook'ni qayta ishlash
    
    Args:
        payload: Webhook payload
        signature_header: X-Signature header
        
    Raises:
        WebhookSignatureError: Imzo noto'g'ri
        PaymentNotFoundError: To'lov topilmadi
    """
    # 1. Imzo tekshirish
    montra_client = MontraClient()
    if not montra_client.verify_webhook_signature(payload, signature_header):
        raise WebhookSignatureError("Webhook imzosi noto'g'ri")

    # 2. Payment'ni topish
    external_id = payload.get('data', {}).get('invoice', {}).get('externalId')
    if not external_id:
        raise PaymentNotFoundError("externalId topilmadi")

    try:
        payment = Payment.objects.get(id=external_id)
    except Payment.DoesNotExist:
        raise PaymentNotFoundError(f"To'lov topilmadi: {external_id}")

    # 3. Idempotency tekshirish - allaqachon PAID bo'lsa, hech narsa qilmaymiz
    if payment.status == Payment.Status.PAID:
        return

    # 4. Statusni yangilash
    invoice_data = payload.get('data', {}).get('invoice', {})
    invoice_status = invoice_data.get('status', '').lower()

    if invoice_status == 'paid':
        payment.status = Payment.Status.PAID
        payment.paid_at = timezone.now()
        payment.gateway_payment_id = invoice_data.get('paymentId')
        payment.gateway_raw_response = payload
        payment.save(update_fields=['status', 'paid_at', 'gateway_payment_id', 'gateway_raw_response'])

        # Telegram xabarnomasi (ixtiyoriy)
        from .tasks import send_telegram_notification
        send_telegram_notification.delay(payment.id)

    elif invoice_status == 'failed':
        payment.status = Payment.Status.FAILED
        payment.gateway_raw_response = payload
        payment.save(update_fields=['status', 'gateway_raw_response'])

    elif invoice_status == 'expired':
        payment.status = Payment.Status.EXPIRED
        payment.gateway_raw_response = payload
        payment.save(update_fields=['status', 'gateway_raw_response'])

    elif invoice_status == 'cancelled':
        payment.status = Payment.Status.CANCELLED
        payment.gateway_raw_response = payload
        payment.save(update_fields=['status', 'gateway_raw_response'])
