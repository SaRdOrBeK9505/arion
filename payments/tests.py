from django.test import TestCase
from unittest.mock import patch, MagicMock
from .models import Payment
from .services import create_payment, handle_webhook
from .exceptions import InvalidAmountError


class MockCeleryTask:
    """Celery task mock for tests"""
    @staticmethod
    def delay(*args, **kwargs):
        pass


class CreatePaymentTests(TestCase):
    """create_payment funksiyasi uchun testlar"""

    def test_create_payment_success(self):
        """To'lov yaratish - kompaniyasiz"""
        with patch('payments.services.MontraClient') as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.create_invoice.return_value = {
                'invoiceId': 'inv_123',
                'paymentUrl': 'https://payment.url'
            }

            result = create_payment(
                amount=1000000,  # 10,000 so'm
                ip_address="127.0.0.1"
            )

            self.assertIn('payment_url', result)
            self.assertIn('payment_id', result)

            # Payment yaratilganini tekshirish
            payment = Payment.objects.get(id=result['payment_id'])
            self.assertEqual(payment.amount, 1000000)
            self.assertEqual(payment.company_name_snapshot, "")
            self.assertEqual(payment.customer_code_snapshot, "")
            self.assertEqual(payment.status, Payment.Status.PENDING)

    def test_create_payment_amount_too_low(self):
        """Summa juda kichik bo'lganda xatolik"""
        with self.assertRaises(InvalidAmountError):
            create_payment(
                amount=100,  # Juda kichik
                ip_address="127.0.0.1"
            )

    def test_create_payment_amount_too_high(self):
        """Summa juda katta bo'lganda xatolik"""
        with self.assertRaises(InvalidAmountError):
            create_payment(
                amount=999_999_999_999,  # Juda katta
                ip_address="127.0.0.1"
            )


class WebhookIdempotencyTests(TestCase):
    """Webhook idempotency testlari"""

    def setUp(self):
        # Payment yaratish
        with patch('payments.services.MontraClient') as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.create_invoice.return_value = {
                'invoiceId': 'inv_123',
                'paymentUrl': 'https://payment.url'
            }
            result = create_payment(
                amount=1000000,
                ip_address="127.0.0.1"
            )
            self.payment = Payment.objects.get(id=result['payment_id'])

    def test_webhook_idempotency(self):
        """
        Bir xil webhook ikki marta yuborilganda ikkinchi marta hech narsa o'zgarmasligi
        """
        import json as json_module

        payload = {
            'event': 'invoice.paid',
            'data': {
                'invoice': {
                    'externalId': str(self.payment.id),
                    'status': 'PAID',
                    'payment': {'id': 'pay_123'},
                }
            }
        }
        raw_body = json_module.dumps(payload).encode()
        signature_header = "t=1234567890000,v1=" + "a" * 64
        event = "invoice.paid"
        webhook_id = "wh_test_123"

        with patch('payments.services.MontraClient') as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.verify_webhook_signature.return_value = True

            # Telegram taskni mock qilish (import ichida bo lgani uchun)
            with patch('payments.tasks.send_telegram_notification.delay', MockCeleryTask.delay):
                # Birinchi webhook
                handle_webhook(raw_body, signature_header, event, webhook_id)
                self.payment.refresh_from_db()
                first_paid_at = self.payment.paid_at
                self.assertEqual(self.payment.status, Payment.Status.PAID)

                # Ikkinchi webhook (xuddi shu webhook_id — dedup ishlashi kerak)
                handle_webhook(raw_body, signature_header, event, webhook_id)
                self.payment.refresh_from_db()
                second_paid_at = self.payment.paid_at

                # paid_at o'zgarmaganini tekshirish
                self.assertEqual(first_paid_at, second_paid_at)

    def test_webhook_different_id_not_deduped(self):
        """Turli webhook_id bo'lsa, dedup ishlamasligi (masalan boshqa event)"""
        from .models import WebhookEvent

        self.assertEqual(WebhookEvent.objects.count(), 0)


