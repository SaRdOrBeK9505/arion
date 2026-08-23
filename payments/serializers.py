from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field
from .models import Payment


class VerifyCodeRequestSerializer(serializers.Serializer):
    code = serializers.CharField(
        max_length=6,
        help_text="6 xonali kod"
    )


class VerifyCodeResponseSerializer(serializers.Serializer):
    session_id = serializers.UUIDField(help_text="Sessiya ID")
    expires_at = serializers.DateTimeField(help_text="Sessiya muddati")
    company_name = serializers.CharField(
        allow_blank=True,
        help_text="Kompaniya nomi (agar biriktirilgan bo'lsa)"
    )


class CreatePaymentRequestSerializer(serializers.Serializer):
    session_id = serializers.UUIDField(help_text="Sessiya ID")
    amount = serializers.IntegerField(
        min_value=1000000,  # 10,000 so'm
        max_value=50000000000,  # 500,000,000 so'm
        help_text="Summa (tiyinda)"
    )


class CreatePaymentResponseSerializer(serializers.Serializer):
    payment_url = serializers.URLField(help_text="To'lov URL")
    payment_id = serializers.IntegerField(help_text="To'lov ID")


class PaymentStatusSerializer(serializers.ModelSerializer):
    amount_display = serializers.SerializerMethodField()
    company_name_display = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            'id', 'company_name_snapshot', 'customer_code_snapshot',
            'amount', 'currency', 'status', 'created_at', 'paid_at',
            'amount_display', 'company_name_display'
        ]
        read_only_fields = fields

    @extend_schema_field(str)
    def get_amount_display(self, obj):
        return f"{obj.amount // 100:,} {obj.currency}".replace(",", " ")

    @extend_schema_field(str)
    def get_company_name_display(self, obj):
        return obj.company_name_snapshot or "— Kompaniyasiz —"


class WebhookSerializer(serializers.Serializer):
    """MONTRA webhook payload serializer"""
    data = serializers.DictField(help_text="Webhook ma'lumotlari")
