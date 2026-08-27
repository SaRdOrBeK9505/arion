# ARION Export — To'lov Tizimi

Django/DRF backend loyihasi - to'lov tizimi uchun backend.

## Admin Panel

Admin panel **Jazzmin** theme bilan sozlangan va uch tilda qo'llab-quvvatlanadi:
- 🇺🇿 O'zbek
- 🇷🇺 Русский
- 🇬🇧 English

Admin panel oddiy insonlar ham bemalol boshqara oladigan qilib sozlangan:
- Zamonaviy va chiroyli dizayn
- Oson navigatsiya (To'lovlar)
- Tezkor linklar (To'lov hisoboti)
- CSV/Excel export qobiliyati
- Tilda o'tish imkoniyati

## Loyiha konteksti

`arion-export.uz` — statik mahsulot katalogiga ega frontend sayt (Next.js/oddiy HTML, backend bilan bog'liq emas). Mahsulotlar bazada saqlanmaydi.

### Ishlash tartibi

1. Mijoz saytda to'lov sahifasiga o'tadi.
2. Mijoz o'zi xohlagan miqdorda summani kiritadi.
3. To'lov MONTRA Payment Gateway orqali amalga oshiriladi.
4. Owner (admin) Django admin panel orqali to'lovlar tarixini ko'radi.

## Texnologiyalik stek

- **Backend:** Django 5.x + Django REST Framework
- **DB:** PostgreSQL (development uchun SQLite)
- **Cache/Rate-limit:** Redis + `django-ratelimit`
- **Async vazifalar:** Celery + Redis
- **To'lov gateway:** MONTRA Payment Gateway (HMAC-SHA256 Signature v1)
- **Admin panel:** Django Admin + Jazzmin theme (zamonaviy, owner-friendly)
- **API hujjatlashtirish:** drf-spectacular
- **Xatoliklarni kuzatish:** Sentry
- **Tillar:** O'zbek, Rus, Ingliz (i18n)

## O'rnatish

### 1. Virtual environment yaratish

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
# yoki
source .venv/bin/activate  # Linux/Mac
```

### 2. Dependencies o'rnatish

```bash
pip install -r requirements.txt
```

### 3. Environment sozlash

`.env.example` faylini `.env` ga nusxalang va to'ldiring:

```bash
cp .env.example .env
```

`.env` faylida quyidagilarni sozlang:

```env
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database (ixtiyoriy - PostgreSQL uchun)
DB_NAME=arion_export
DB_USER=postgres
DB_PASSWORD=your-db-password
DB_HOST=localhost
DB_PORT=5432

# Redis
REDIS_URL=redis://localhost:6379/0

# MONTRA Payment Gateway
MONTRA_API_KEY_TEST=your-test-api-key
MONTRA_SECRET_KEY_TEST=your-test-secret-key
MONTRA_MODE=TEST

# Telegram (ixtiyoriy)
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-admin-chat-id

# Sentry (ixtiyoriy)
SENTRY_DSN=your-sentry-dsn
```

### 4. Migratsiyalarni yurgazish

```bash
python manage.py makemigrations
python manage.py migrate
```

### 5. Superuser yaratish

```bash
python manage.py createsuperuser
```

### 6. Serverni ishga tushirish

```bash
python manage.py runserver
```

Admin panel: http://localhost:8000/admin/

API docs: http://localhost:8000/api/docs/

## API Endpointlar

| Endpoint | Method | Vazifa |
|---|---|---|
| `/api/create-payment/` | POST | `amount` → gateway invoice + `paymentUrl` |
| `/api/payments/<id>/status/` | GET | Frontend polling uchun |
| `/api/webhooks/montra/` | POST | MONTRA'dan kelgan webhook |

## Testlar

```bash
python manage.py test payments.tests
```

Testlar quyidagilarni qamrab oladi:
- `create_payment`: kompaniyasiz to'lov, MIN/MAX chegaralar
- Webhook idempotency: bir xil webhook ikki marta yuborilganda

## Celery (ixtiyoriy)

Celery worker uchun:

```bash
celery -A arion_export worker -l info
```

Celery beat uchun (agar kerak bo'lsa):

```bash
celery -A arion_export beat -l info
```

## Deploy

Gunicorn + Nginx + systemd pattern:

```bash
gunicorn arion_export.wsgi:application --bind 0.0.0.0:8000
```

## Xavfsizlik

- Barcha API'lar faqat HTTPS
- Rate limiting: IP bo'yicha 5 urinish/daqiqa, 20 urinish/soat
- Webhook signature verification majburiy
- `SECRET_KEY`, `MONTRA_SECRET_KEY` kabi maxfiy ma'lumotlar faqat `.env` da
