from django.urls import path
from .views import (
    verify_code_view,
    create_payment_view,
    payment_status_view,
    montra_webhook_view,
)

app_name = 'payments'

urlpatterns = [
    path('verify-code/', verify_code_view, name='verify_code'),
    path('create-payment/', create_payment_view, name='create_payment'),
    path('payments/<int:payment_id>/status/', payment_status_view, name='payment_status'),
    path('webhooks/montra/', montra_webhook_view, name='montra_webhook'),
]
