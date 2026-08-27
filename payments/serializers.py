from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field
from .models import Payment


class CreatePaymentRequestSerializer(serializers.Serializer):
    amount = serializers.IntegerField(
        min_value=1000,  # Minimal 1,000 so'm
        max_value=500_000_000,  # Maksimal 500,000,000 so'm
        help_text="Summa (so'mda)"
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
