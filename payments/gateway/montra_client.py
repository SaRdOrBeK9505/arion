import hashlib
import hmac
import re
import time
import json
import requests
from typing import Optional
from django.conf import settings


class MontraClientError(Exception):
    """MONTRA API xatoliklari uchun custom exception"""
    pass


_SIGNATURE_RE = re.compile(r"^[a-f0-9]{64}$")
_TIMESTAMP_MS_RE = re.compile(r"^\d{13}$")


class MontraClient:
    """MONTRA Payment Gateway klienti - HMAC-SHA256 Signature v1 autentifikatsiya"""

    BASE_URL = settings.MONTRA_BASE_URL

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ):
        self.api_key = api_key or settings.MONTRA_API_KEY
        self.secret_key = secret_key or settings.MONTRA_SECRET_KEY
        # MUHIM: webhook_secret — API so'rovlarini imzolashda ishlatiladigan
        # secret_key BILAN BOG'LIQ EMAS. MONTRA Dashboard'da har bir webhook
        # endpoint uchun alohida "Webhook Secret" beriladi. Ikkalasini
        # aralashtirib yubormang — https://docs.montratech.com/ru/webhooks/signature
        self.webhook_secret = webhook_secret or getattr(settings, "MONTRA_WEBHOOK_SECRET", None)

        if not self.api_key or not self.secret_key:
            raise MontraClientError("MONTRA_API_KEY va MONTRA_SECRET_KEY sozlanishi shart")

    def _sign(self, method: str, path: str, body: Optional[dict], idempotency_key: Optional[str]) -> tuple[dict, str]:
        """
        HMAC-SHA256 Signature v1 imzosini yaratish
        
        Signature format:
        t={timestamp},v1={signature}
        
        Canonical string:
        {timestamp}
        {method}
        {path}
        {idempotency_key}  (agar mavjud bo'lsa)
        {body_hash}
        """
        t = int(time.time())
        body_string = json.dumps(body, separators=(",", ":")) if body else ""
        body_hash = hashlib.sha256(body_string.encode()).hexdigest()

        parts = [str(t), method, path]
        if idempotency_key:
            parts.append(idempotency_key)
        parts.append(body_hash)
        canonical = "\n".join(parts)  # LF, \r YO'Q

        signature = hmac.new(
            self.secret_key.encode(), canonical.encode(), hashlib.sha256
        ).hexdigest()

        # DEBUG - imzo generatsiyasini log qilish
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"MONTRA SIGNATURE DEBUG:")
        logger.warning(f"  Timestamp: {t}")
        logger.warning(f"  Method: {method}")
        logger.warning(f"  Path: {path}")
        logger.warning(f"  Idempotency-Key: {idempotency_key}")
        logger.warning(f"  Body: {body_string}")
        logger.warning(f"  Body Hash: {body_hash}")
        logger.warning(f"  Canonical: {repr(canonical)}")
        logger.warning(f"  Secret Key (first 8 chars): {self.secret_key[:8]}...")
        logger.warning(f"  Signature: {signature}")

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
            "X-Signature": f"t={t},v1={signature}",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        return headers, body_string

    def create_invoice(
        self,
        *,
        external_id: str,
        amount: int,
        currency: str,
        description: str,
        success_url: str,
        fail_url: str,
        idempotency_key: str
    ) -> dict:
        """
        MONTRA'da invoice yaratish
        
        Args:
            external_id: Tashqi ID (bizda Payment.id)
            amount: Summa (minor birlikda, masalan 10000 = 100.00)
            currency: Valyuta kodi (UZS)
            description: To'lov tavsifi
            success_url: Muvaffaqiyatli to'lovdan keyin redirect URL
            fail_url: Muvaffaqiyatsiz to'lovdan keyin redirect URL
            idempotency_key: Idempotency key
            
        Returns:
            dict: {'invoice_id': str, 'payment_url': str, ...}
        """
        path = "/invoices"
        body = {
            "externalId": external_id,
            "amount": amount,
            "currency": currency,
            "description": description,
            "successUrl": success_url,
            "failUrl": fail_url,
        }

        headers, body_str = self._sign("POST", path, body, idempotency_key)

        try:
            response = requests.post(
                self.BASE_URL + path,
                headers=headers,
                data=body_str,
                timeout=30
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            # BUG TUZATILDI: requests.exceptions.HTTPError'da .status_code emas,
            # .response.status_code bo'ladi. Eskisi doim AttributeError berardi
            # va asl MONTRA xato xabari hech qachon logga chiqmasdi.
            status_code = e.response.status_code if e.response is not None else "?"
            error_data = e.response.json() if (e.response is not None and e.response.content) else {}
            raise MontraClientError(f"MONTRA API xatoligi: {status_code} - {error_data}")
        except requests.exceptions.RequestException as e:
            raise MontraClientError(f"MONTRA'ga ulanish xatoligi: {str(e)}")

    def get_invoice(self, invoice_id: str) -> dict:
        """
        Invoice ma'lumotlarini olish
        
        Args:
            invoice_id: Invoice ID
            
        Returns:
            dict: Invoice ma'lumotlari
        """
        path = f"/invoices/{invoice_id}"
        headers, _ = self._sign("GET", path, None, None)

        try:
            response = requests.get(
                self.BASE_URL + path,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "?"
            error_data = e.response.json() if (e.response is not None and e.response.content) else {}
            raise MontraClientError(f"MONTRA API xatoligi: {status_code} - {error_data}")
        except requests.exceptions.RequestException as e:
            raise MontraClientError(f"MONTRA'ga ulanish xatoligi: {str(e)}")

    @staticmethod
    def _parse_signature_header(header: str) -> Optional[tuple[str, str]]:
        """`t=...,v1=...` formatidagi headerni ajratib oladi (probellarga chidamli)."""
        t, v1 = None, None
        for part in header.split(","):
            part = part.strip()
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip()
            if key == "t":
                t = value.strip()
            elif key == "v1":
                v1 = value.strip()
        if not t or not v1:
            return None
        return t, v1

    def verify_webhook_signature(self, raw_body: bytes, signature_header: str, event: str) -> bool:
        """
        Webhook imzosini MONTRA rasmiy spetsifikatsiyasiga muvofiq tekshiradi.
        Manba: https://docs.montratech.com/ru/webhooks/signature

        MUHIM FARQLAR (avvalgi implementatsiyadagi xatolar tuzatildi):
        - Header nomi `X-Webhook-Signature` (`X-Signature` EMAS — u faqat
          chiquvchi API so'rovlarini imzolashda ishlatiladi).
        - Secret — Dashboard'dagi alohida "Webhook Secret", API so'rov
          imzolashdagi secret_key emas.
        - Timestamp MILLISEKUNDDA (13 xonali), sekundda emas.
        - Canonical string = timestamp + "\\n" + event + "\\n" + rawBody —
          JSON qayta serialize qilinmaydi, body_hash ishlatilmaydi, xom
          (raw) baytlar to'g'ridan-to'g'ri qo'shiladi.

        Args:
            raw_body: HTTP body'ning xom baytlari (request.data emas!
                Django/DRF JSON'ni qayta parse qilib yubormasdan oldin
                request.body orqali olingan bo'lishi shart — aks holda
                baytlar mos kelmay, imzo har doim noto'g'ri chiqadi).
            signature_header: `X-Webhook-Signature` header qiymati.
            event: `X-Webhook-Event` header qiymati (masalan "invoice.paid").

        Returns:
            bool: Imzo to'g'ri bo'lsa True.
        """
        if not self.webhook_secret:
            raise MontraClientError(
                "MONTRA_WEBHOOK_SECRET sozlanmagan. Bu API so'rov imzolash "
                "secret_key'idan BOSHQA qiymat — Dashboard → Settings → "
                "Webhooks'dan oling."
            )

        try:
            parsed = self._parse_signature_header(signature_header)
            if not parsed:
                return False
            timestamp, signature = parsed

            if not _SIGNATURE_RE.match(signature):
                return False
            if not _TIMESTAMP_MS_RE.match(timestamp):
                return False

            now_ms = int(time.time() * 1000)
            if abs(now_ms - int(timestamp)) > 300_000:  # ±5 daqiqa, ms
                return False

            if not event:
                return False

            canonical = timestamp.encode() + b"\n" + event.encode() + b"\n" + raw_body

            expected_signature = hmac.new(
                self.webhook_secret.encode(),
                canonical,
                hashlib.sha256,
            ).hexdigest()

            return hmac.compare_digest(signature, expected_signature)

        except Exception:
            return False
