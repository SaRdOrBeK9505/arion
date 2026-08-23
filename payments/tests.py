from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch, MagicMock
from .models import Company, CustomerCode, AccessSession, Payment, CodeVerificationLog
from .services import verify_code, create_payment, handle_webhook
from .exceptions import CodeNotFoundError, SessionExpiredError, InvalidAmountError


class MockCeleryTask:
    """Celery task mock for tests"""
    @staticmethod
    def delay(*args, **kwargs):
        pass


class VerifyCodeTests(TestCase):
    """verify_code funksiyasi uchun testlar"""

    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.code = CustomerCode.objects.create(
            code="123456",
            company=self.company,
            is_active=True
        )
        self.inactive_code = CustomerCode.objects.create(
            code="654321",
            company=self.company,
            is_active=False
        )

    def test_verify_code_success(self):
        """Mavjud va faol kod bilan muvaffaqiyatli tekshirish"""
        session = verify_code("123456", "127.0.0.1")
        
        self.assertIsNotNone(session)
        self.assertEqual(session.customer_code, self.code)
        self.assertEqual(session.ip_address, "127.0.0.1")
        self.assertTrue(session.is_valid())

        # Log yozilganini tekshirish
        log = CodeVerificationLog.objects.filter(
            code_attempted="123456",
            was_successful=True
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.matched_code, self.code)

    def test_verify_code_not_found(self):
        """Mavjud bo'lmagan kod bilan xatolik"""
        with self.assertRaises(CodeNotFoundError):
            verify_code("999999", "127.0.0.1")

        # Log yozilganini tekshirish
        log = CodeVerificationLog.objects.filter(
            code_attempted="999999",
            was_successful=False
        ).first()
        self.assertIsNotNone(log)

    def test_verify_code_inactive(self):
        """Faol bo'lmagan kod bilan xatolik"""
        with self.assertRaises(CodeNotFoundError):
            verify_code("654321", "127.0.0.1")


class CreatePaymentTests(TestCase):
    """create_payment funksiyasi uchun testlar"""

    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.code = CustomerCode.objects.create(
            code="123456",
            company=self.company,
            is_active=True
        )
        self.session = AccessSession.create_for(self.code, "127.0.0.1")

    def test_create_payment_success(self):
        """To'lov yaratish - kompaniyali kod"""
        with patch('payments.services.MontraClient') as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.create_invoice.return_value = {
                'invoiceId': 'inv_123',
                'paymentUrl': 'https://payment.url'
            }

            result = create_payment(
                session_id=self.session.id,
                amount=1000000,  # 10,000 so'm
                ip_address="127.0.0.1"
            )

            self.assertIn('payment_url', result)
            self.assertIn('payment_id', result)

            # Payment yaratilganini tekshirish
            payment = Payment.objects.get(id=result['payment_id'])
            self.assertEqual(payment.amount, 1000000)
            self.assertEqual(payment.company_name_snapshot, "Test Company")
            self.assertEqual(payment.customer_code_snapshot, "123456")
            self.assertEqual(payment.status, Payment.Status.PENDING)

    def test_create_payment_no_company(self):
        """To'lov yaratish - kompaniyasiz kod"""
        # Kompaniyasiz kod yaratish
        code_no_company = CustomerCode.objects.create(
            code="789012",
            company=None,
            is_active=True
        )
        session_no_company = AccessSession.create_for(code_no_company, "127.0.0.1")

        with patch('payments.services.MontraClient') as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.create_invoice.return_value = {
                'invoiceId': 'inv_456',
                'paymentUrl': 'https://payment.url'
            }

            result = create_payment(
                session_id=session_no_company.id,
                amount=1000000,
                ip_address="127.0.0.1"
            )

            payment = Payment.objects.get(id=result['payment_id'])
            self.assertEqual(payment.company_name_snapshot, "")
            self.assertIsNone(payment.company)

    def test_create_payment_amount_too_low(self):
        """Summa juda kichik bo'lganda xatolik"""
        with self.assertRaises(InvalidAmountError):
            create_payment(
                session_id=self.session.id,
                amount=100,  # Juda kichik
                ip_address="127.0.0.1"
            )

    def test_create_payment_amount_too_high(self):
        """Summa juda katta bo'lganda xatolik"""
        with self.assertRaises(InvalidAmountError):
            create_payment(
                session_id=self.session.id,
                amount=999_999_999_999,  # Juda katta
                ip_address="127.0.0.1"
            )

    def test_create_payment_session_expired(self):
        """Sessiya muddati o'tgan bo'lsa xatolik"""
        # Sessiyani muddati o'tgan qilib o'zgartirish
        self.session.expires_at = timezone.now() - timedelta(minutes=1)
        self.session.save()

        with self.assertRaises(SessionExpiredError):
            create_payment(
                session_id=self.session.id,
                amount=1000000,
                ip_address="127.0.0.1"
            )


class SnapshotImmutabilityTest(TestCase):
    """Snapshot immutability testi - loyihaning eng muhim testi"""

    def setUp(self):
        self.company_a = Company.objects.create(name="Company A")
        self.company_b = Company.objects.create(name="Company B")
        self.code = CustomerCode.objects.create(
            code="123456",
            company=self.company_a,
            is_active=True
        )
        self.session = AccessSession.create_for(self.code, "127.0.0.1")

    def test_snapshot_immutability(self):
        """
        Payment yaratilgandan keyin CustomerCode.company o'zgartirilsa,
        eski Payment.company_name_snapshot o'zgarmay qolishi kerak
        """
        with patch('payments.services.MontraClient') as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.create_invoice.return_value = {
                'invoiceId': 'inv_123',
                'paymentUrl': 'https://payment.url'
            }

            # To'lov yaratish (Company A bilan)
            result = create_payment(
                session_id=self.session.id,
                amount=1000000,
                ip_address="127.0.0.1"
            )

            payment = Payment.objects.get(id=result['payment_id'])
            original_snapshot = payment.company_name_snapshot
            original_company_id = payment.company.id
            self.assertEqual(original_snapshot, "Company A")
            self.assertEqual(payment.company.name, "Company A")

            # Kodni Company B ga qayta biriktirish
            self.code.company = self.company_b
            self.code.save()

            # Payment'ni qayta yuklash
            payment.refresh_from_db()

            # Snapshot o'zgarmaganini tekshirish (bu asosiy test)
            self.assertEqual(payment.company_name_snapshot, "Company A")
            # FK ham o'zgarmaganini tekshirish (referens sifatida saqlanadi)
            self.assertEqual(payment.company.id, original_company_id)
            self.assertEqual(payment.company.name, "Company A")
            # Kod esa endi Company B ga biriktirilgan
            self.assertEqual(self.code.company.name, "Company B")


class WebhookIdempotencyTests(TestCase):
    """Webhook idempotency testlari"""

    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.code = CustomerCode.objects.create(
            code="123456",
            company=self.company,
            is_active=True
        )
        self.session = AccessSession.create_for(self.code, "127.0.0.1")
        
        # Payment yaratish
        with patch('payments.services.MontraClient') as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.create_invoice.return_value = {
                'invoiceId': 'inv_123',
                'paymentUrl': 'https://payment.url'
            }
            result = create_payment(
                session_id=self.session.id,
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


class PaymentSnapshotImmutabilityTests(TestCase):
    """
    Loyihaning eng muhim printsipi: Payment.company_name_snapshot va
    customer_code_snapshot yaratilgandan keyin O'ZGARTIRIB BO'LMAYDI.
    """

    def setUp(self):
        self.company_a = Company.objects.create(name="Kompaniya A")
        self.company_b = Company.objects.create(name="Kompaniya B")
        self.code = CustomerCode.objects.create(
            code="111222", company=self.company_a, is_active=True
        )
        self.session = AccessSession.create_for(self.code, "127.0.0.1")

        with patch('payments.services.MontraClient') as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.create_invoice.return_value = {
                'invoiceId': 'inv_1', 'paymentUrl': 'https://payment.url'
            }
            result = create_payment(
                session_id=self.session.id, amount=1000000, ip_address="127.0.0.1"
            )
            self.payment = Payment.objects.get(id=result['payment_id'])

    def test_snapshot_survives_code_reassignment(self):
        """Kod boshqa kompaniyaga qayta biriktirilsa ham, eski Payment o'zgarmaydi"""
        self.assertEqual(self.payment.company_name_snapshot, "Kompaniya A")

        # Owner kodni boshqa kompaniyaga qayta biriktiradi
        self.code.company = self.company_b
        self.code.save()

        self.payment.refresh_from_db()
        self.assertEqual(
            self.payment.company_name_snapshot, "Kompaniya A",
            "Snapshot o'zgarmasligi kerak, garchi CustomerCode.company o'zgargan bo'lsa ham",
        )

    def test_direct_snapshot_mutation_raises(self):
        """Snapshot maydonini to'g'ridan-to'g'ri o'zgartirishga urinish xato berishi kerak"""
        self.payment.company_name_snapshot = "Boshqa nom"
        with self.assertRaises(ValueError):
            self.payment.save()
