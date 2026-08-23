from django.contrib import admin
from django.db.models import Sum, Count
from import_export import resources
from import_export.admin import ImportExportMixin
from .models import Company, CustomerCode, AccessSession, Payment, CodeVerificationLog


class CompanyFilter(admin.SimpleListFilter):
    title = "Kompaniya biriktirilganmi"
    parameter_name = "has_company"

    def lookups(self, request, model_admin):
        return [("yes", "Biriktirilgan"), ("no", "Kompaniyasiz")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(company__isnull=False)
        if self.value() == "no":
            return queryset.filter(company__isnull=True)
        return queryset


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "phone", "is_active", "active_codes_count", "total_payments_amount", "created_at"]
    list_filter = ["is_active", "created_at"]
    search_fields = ["name", "phone", "telegram_contact"]
    readonly_fields = ["created_at", "updated_at"]

    def active_codes_count(self, obj):
        return obj.codes.filter(is_active=True).count()
    active_codes_count.short_description = "Faol kodlar soni"

    def total_payments_amount(self, obj):
        total = obj.payments.filter(status=Payment.Status.PAID).aggregate(
            total=Sum('amount')
        )['total'] or 0
        return f"{total // 100:,} UZS".replace(",", " ")
    total_payments_amount.short_description = "Jami to'lovlar summasi"


@admin.register(CustomerCode)
class CustomerCodeAdmin(admin.ModelAdmin):
    list_display = ["code", "company_display", "label", "is_active", "created_at"]
    list_filter = [CompanyFilter, "is_active", "created_at"]
    search_fields = ["code", "label", "company__name"]
    readonly_fields = ["created_at", "updated_at"]

    def company_display(self, obj):
        return obj.company.name if obj.company else "— Kompaniyasiz —"
    company_display.short_description = "Kompaniya"

    def save_model(self, request, obj, form, change):
        if not change and not obj.code:
            # Yangi kod yaratilayotganda va code bo'sh bo'lsa, avtomatik generatsiya qilish
            obj.code = CustomerCode.generate_unique_code()
        super().save_model(request, obj, form, change)


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
        "amount_display", "status", "paid_at",
    ]
    list_filter = ["status", "created_at", "paid_at"]
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


@admin.register(CodeVerificationLog)
class CodeVerificationLogAdmin(admin.ModelAdmin):
    list_display = ["code_attempted", "ip_address", "was_successful", "matched_code", "created_at"]
    list_filter = ["was_successful", "created_at"]
    search_fields = ["code_attempted", "ip_address"]
    readonly_fields = ["created_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccessSession)
class AccessSessionAdmin(admin.ModelAdmin):
    list_display = ["id", "customer_code", "ip_address", "created_at", "expires_at", "is_valid"]
    list_filter = ["created_at", "expires_at"]
    search_fields = ["id", "customer_code__code", "ip_address"]
    readonly_fields = ["id", "created_at"]

    def is_valid(self, obj):
        return obj.is_valid()
    is_valid.boolean = True
    is_valid.short_description = "Yaroqli"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
