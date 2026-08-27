from django.contrib import admin
from import_export import resources
from import_export.admin import ImportExportMixin
from .models import Payment, WebhookEvent


class PaymentResource(resources.ModelResource):
    class Meta:
        model = Payment
        fields = [
            'id', 'created_at', 'company_name_snapshot', 'customer_code_snapshot',
            'amount', 'currency', 'status', 'paid_at', 'ip_address'
        ]
        export_order = [
            'id', 'created_at', 'company_name_snapshot', 'customer_code_snapshot',
            'amount', 'currency', 'status', 'paid_at', 'ip_address'
        ]


@admin.register(Payment)
class PaymentAdmin(ImportExportMixin, admin.ModelAdmin):
    resource_class = PaymentResource
    list_display = [
        "created_at", "company_name_display", "customer_code_snapshot",
        "amount_display", "status", "mode_display", "paid_at",
    ]
    list_filter = ["status", "is_test", "created_at", "paid_at"]
    search_fields = ["company_name_snapshot", "customer_code_snapshot", "gateway_invoice_id"]
    readonly_fields = [f.name for f in Payment._meta.fields]  # to'liq faqat-o'qish
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False  # to'lovlar faqat API orqali yaratiladi, admin panelda emas

    def has_change_permission(self, request, obj=None):
        return False  # to'lov yozuvlarini tahrirlab bo'lmaydi

    def has_delete_permission(self, request, obj=None):
        return False  # to'lov yozuvlarini o'chirib bo'lmaydi

    def company_name_display(self, obj):
        name = obj.company_name_snapshot or "— Kompaniyasiz —"
        if len(name) > 20:
            return name[:20] + "..."
        return name
    company_name_display.short_description = "Kompaniya"

    def amount_display(self, obj):
        return f"{obj.amount // 100:,} {obj.currency}".replace(",", " ")
    amount_display.short_description = "Summa"

    def mode_display(self, obj):
        return "🧪 TEST" if obj.is_test else "LIVE"
    mode_display.short_description = "Rejim"


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ["event", "webhook_id", "received_at"]
    list_filter = ["event", "received_at"]
    search_fields = ["webhook_id"]
    readonly_fields = ["webhook_id", "event", "received_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False