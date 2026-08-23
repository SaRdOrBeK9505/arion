import hashlib
import hmac
import time
import json
import requests
from typing import Optional
from django.conf import settings


class MontraClientError(Exception):
    """MONTRA API xatoliklari uchun custom exception"""
    pass


class MontraClient:
    """MONTRA Payment Gateway klienti - HMAC-SHA256 Signature v1 autentifikatsiya"""

    BASE_URL = settings.MONTRA_BASE_URL

    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        self.api_key = api_key or settings.MONTRA_API_KEY
        self.secret_key = secret_key or settings.MONTRA_SECRET_KEY

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
            error_data = e.response.json() if e.response.content else {}
            raise MontraClientError(f"MONTRA API xatoligi: {e.status_code} - {error_data}")
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
            error_data = e.response.json() if e.response.content else {}
            raise MontraClientError(f"MONTRA API xatoligi: {e.status_code} - {error_data}")
        except requests.exceptions.RequestException as e:
            raise MontraClientError(f"MONTRA'ga ulanish xatoligi: {str(e)}")

    def verify_webhook_signature(self, payload: dict, signature_header: str) -> bool:
        """
        Webhook imzosini tekshirish
        
        Args:
            payload: Webhook payload (dict)
            signature_header: X-Signature header qiymati
            
        Returns:
            bool: Imzo to'g'ri bo'lsa True
        """
        try:
            # Signature headerdan timestamp va signature'ni ajratib olish
            # Format: t={timestamp},v1={signature}
            parts = signature_header.split(',')
            timestamp = None
            signature = None

            for part in parts:
                if part.startswith('t='):
                    timestamp = part[2:]
                elif part.startswith('v1='):
                    signature = part[3:]

            if not timestamp or not signature:
                return False

            # Timestamp tekshirish (5 daqiqa ichida bo'lishi kerak)
            current_time = int(time.time())
            if abs(current_time - int(timestamp)) > 300:  # 5 minutes
                return False

            # Body string yaratish
            body_string = json.dumps(payload, separators=(",", ":"))
            body_hash = hashlib.sha256(body_string.encode()).hexdigest()

            # Canonical string yaratish
            canonical = f"{timestamp}\n{body_hash}"

            # Signature hisoblash
            expected_signature = hmac.new(
                self.secret_key.encode(),
                canonical.encode(),
                hashlib.sha256
            ).hexdigest()

            # Constant-time comparison
            return hmac.compare_digest(signature, expected_signature)

        except Exception:
            return False
