import requests
from celery import shared_task
from django.conf import settings
from .models import Payment


@shared_task(bind=True, max_retries=3)
def send_telegram_notification(self, payment_id: int):
    """
    To'lov muvaffaqiyatli bo'lganda ownerga Telegram xabari yuborish

    Args:
        payment_id: To'lov ID
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        # Telegram sozlanmagan bo'lsa, hech narsa qilmaymiz
        return

    try:
        payment = Payment.objects.get(id=payment_id)

        if payment.status != Payment.Status.PAID:
            return

        # Xabar matni
        company_name = payment.company_name_snapshot or "Kompaniyasiz"
        amount_formatted = f"{payment.amount // 100:,} {payment.currency}".replace(",", " ")

        # TEST/LIVE belgisi: agar to'lov TEST kartasi (MONTRA test rejimi)
        # orqali qilingan bo'lsa, sarlavha va alohida qator qo'shiladi.
        # LIVE (haqiqiy) to'lovda bu qator umuman bo'lmaydi — o'rni bo'sh
        # qoladi (talab qilingandek), qo'shimcha belgi chiqarilmaydi.
        title = "🧪 *TEST to'lov*" if payment.is_test else "✅ *Yangi to'lov*"
        test_line = "🧪 *Rejim:* TEST karta\n" if payment.is_test else ""

        message = (
            f"{title}\n\n"
            f"{test_line}"
            f"🏢 *Kompaniya:* {company_name}\n"
            f"🔑 *Kod:* {payment.customer_code_snapshot}\n"
            f"💰 *Summa:* {amount_formatted}\n"
            f"🌐 *IP:* {payment.ip_address}\n"
            f"📅 *Vaqt:* {payment.paid_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"🆔 *To\'lov ID:* {payment.id}"
        )

        # Telegram API'ga so'rov yuborish
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': settings.TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown',
        }

        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()

        return {'status': 'sent', 'payment_id': payment_id}

    except Payment.DoesNotExist:
        return {'status': 'error', 'error': 'Payment not found', 'payment_id': payment_id}

    except requests.exceptions.RequestException as e:
        # Xatolik bo'lsa, retry qilish
        raise self.retry(exc=e, countdown=60 * (self.request.retries + 1))

    except Exception as e:
        return {'status': 'error', 'error': str(e), 'payment_id': payment_id}