from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
from django_ratelimit.decorators import ratelimit
from django.conf import settings
from .serializers import (
    VerifyCodeRequestSerializer,
    VerifyCodeResponseSerializer,
    CreatePaymentRequestSerializer,
    CreatePaymentResponseSerializer,
    PaymentStatusSerializer,
    WebhookSerializer,
)
from .services import verify_code, create_payment, handle_webhook
from .exceptions import PaymentServiceError


@extend_schema(
    request=VerifyCodeRequestSerializer,
    responses={200: VerifyCodeResponseSerializer},
    description="Kodni tekshirish va sessiya yaratish",
)
@api_view(['POST'])
@ratelimit(key='ip', rate='5/m', method='POST')
@ratelimit(key='ip', rate='20/h', method='POST')
def verify_code_view(request):
    """
    Kodni tekshirish
    
    POST /api/verify-code/
    {
        "code": "123456"
    }
    """
    serializer = VerifyCodeRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        session = verify_code(
            code=serializer.validated_data['code'],
            ip_address=get_client_ip(request),
        )

        response_data = {
            'session_id': session.id,
            'expires_at': session.expires_at,
            'company_name': session.customer_code.company.name if session.customer_code.company else "",
        }

        return Response(VerifyCodeResponseSerializer(response_data).data)

    except PaymentServiceError as e:
        raise ValidationError(str(e))


@extend_schema(
    request=CreatePaymentRequestSerializer,
    responses={200: CreatePaymentResponseSerializer},
    description="To'lov yaratish va MONTRA invoice olish",
)
@api_view(['POST'])
@ratelimit(key='ip', rate='10/m', method='POST')
@ratelimit(key='ip', rate='60/h', method='POST')
def create_payment_view(request):
    """
    To'lov yaratish

    POST /api/create-payment/
    {
        "session_id": "uuid",
        "amount": 1000000
    }

    MUHIM: bu endpointda rate-limit bo'lishi SHART. Aks holda bitta
    session_id (masalan brauzer tarixi, log fayl orqali oshkor bo'lib
    qolsa) bilan cheksiz marta MONTRA'da invoice yaratish mumkin bo'lardi
    (spam-invoice / MONTRA API limitiga tegish xavfi).
    """
    serializer = CreatePaymentRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        result = create_payment(
            session_id=serializer.validated_data['session_id'],
            amount=serializer.validated_data['amount'],
            ip_address=get_client_ip(request),
        )
        return Response(CreatePaymentResponseSerializer(result).data)

    except PaymentServiceError as e:
        raise ValidationError(str(e))


@extend_schema(
    responses={200: PaymentStatusSerializer},
    description="To'lov holatini olish (polling uchun)",
    parameters=[
        OpenApiParameter(name='id', type=OpenApiTypes.INT, location='path', description='To\'lov ID'),
    ],
)
@api_view(['GET'])
def payment_status_view(request, payment_id):
    """
    To'lov holatini olish
    
    GET /api/payments/{id}/status/
    """
    from .models import Payment

    try:
        payment = Payment.objects.get(id=payment_id)
        return Response(PaymentStatusSerializer(payment).data)

    except Payment.DoesNotExist:
        return Response(
            {'error': 'To\'lov topilmadi'},
            status=status.HTTP_404_NOT_FOUND
        )


@extend_schema(
    request=WebhookSerializer,
    responses={200: None},
    description="MONTRA webhook endpoint",
)
@api_view(['POST'])
def montra_webhook_view(request):
    """
    MONTRA webhook endpoint

    POST /api/webhooks/montra/

    MUHIM: imzo tekshiruvi XOM (raw) baytlarga bog'liq bo'lgani uchun
    request.body dan foydalanamiz, request.data DAN EMAS — DRF request.data
    JSON'ni allaqachon Python dict'ga aylantirib bo'ladi va original
    baytlar yo'qoladi, natijada imzo hech qachon mos kelmaydi.
    """
    raw_body = request.body  # request.data'dan OLDIN o'qilishi shart
    signature_header = request.headers.get('X-Webhook-Signature', '')
    event = request.headers.get('X-Webhook-Event', '')
    webhook_id = request.headers.get('X-Webhook-Id', '')

    try:
        handle_webhook(raw_body, signature_header, event, webhook_id)
        return Response({'status': 'ok'})

    except PaymentServiceError as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )


def get_client_ip(request):
    """
    Mijoz IP adresini olish.

    MUHIM: `X-Forwarded-For` headerini faqat SO'ROV BIZNING ISHONCHLI
    PROKSIMIZDAN (Nginx) kelgan bo'lsagina hisobga olamiz. Aks holda mijoz
    o'zi bu headerni qo'lda qo'yib, IP-based rate-limitni (kod brute-force
    himoyasini) osongina chetlab o'tishi mumkin edi — avvalgi versiyada
    aynan shu muammo bor edi.

    Nginx tomonda quyidagicha sozlanishi SHART (append emas, overwrite):
        proxy_set_header X-Forwarded-For $remote_addr;
    """
    remote_addr = request.META.get('REMOTE_ADDR')
    trusted_proxies = getattr(settings, 'TRUSTED_PROXY_IPS', [])

    if remote_addr in trusted_proxies:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()

    return remote_addr
