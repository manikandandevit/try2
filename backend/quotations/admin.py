"""
Admin configuration for quotations app.
"""
from django.contrib import admin
from django import forms
from django.shortcuts import redirect
from django.urls import reverse
from .models import Quotation, Company, Client, User, QuotationSend, QuotGenerator


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ['quotation_number', 'id', 'created_at', 'updated_at', 'get_grand_total_display']
    readonly_fields = ['quotation_number', 'created_at', 'updated_at']
    search_fields = ['quotation_number']
    list_filter = ['created_at', 'updated_at']
    
    def get_grand_total_display(self, obj):
        return f"₹{obj.get_grand_total():,.2f}"
    get_grand_total_display.short_description = 'Grand Total'


class CompanyAdminForm(forms.ModelForm):
    """Custom form for Company admin with custom field labels."""
    class Meta:
        model = Company
        fields = '__all__'
        labels = {
            'openrouter_model': 'Openrouter model:1',
            'openrouter_model_2': 'Openrouter model:2',
            'openrouter_model_3': 'Openrouter model:3',
        }


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    form = CompanyAdminForm
    list_display = ['email', 'sendemail', 'created_at', 'updated_at']
    list_filter = ['created_at', 'updated_at']
    search_fields = ['email', 'sendemail']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Company Information', {
            'fields': ('company_name', 'brand_name', 'tagline', 'phone_number', 'address')
        }),
        ('Quotation Number Settings', {
            'fields': ('quotation_prefix', 'quotation_numberfield'),
            'description': 'Configure quotation number prefix and track last used number.'
        }),
        ('Login Credentials', {
            'fields': ('email', 'password')
        }),
        ('Send Email Credentials', {
            'fields': ('sendemail', 'sendpassword'),
            'description': 'Email credentials for sending quotations via email'
        }),
        ('Send WhatsApp Number', {
            'fields': ('sendnumber',),
            'description': 'WhatsApp number for sending quotations via WhatsApp'
        }),
        ('OpenRouter API Settings', {
            'fields': ('openrouter_api_key', 'openrouter_model', 'openrouter_model_2', 'openrouter_model_3'),
            'description': 'OpenRouter API configuration for AI features'
        }),
        ('Images', {
            'fields': ('login_logo', 'login_image', 'quotation_logo'),
            'description': 'Login images and quotation header logo'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        """Limit queryset to only one company - the first one."""
        qs = super().get_queryset(request)
        # Get only the first company if any exists
        if qs.exists():
            first_company = qs.first()
            return qs.filter(pk=first_company.pk)
        return qs
    
    def changelist_view(self, request, extra_context=None):
        """Redirect to change view if company exists, otherwise show add form."""
        # If a company exists, redirect to its change page (edit form)
        company = Company.objects.first()
        if company:
            return redirect(reverse('admin:quotations_company_change', args=[company.pk]))
        # If no company exists, show the changelist (which will show add button)
        return super().changelist_view(request, extra_context)
    
    def has_add_permission(self, request):
        """Allow adding company details only if no company exists - single company only."""
        if Company.objects.exists():
            return False
        return super().has_add_permission(request)
    
    def has_delete_permission(self, request, obj=None):
        """Prevent deleting the company - must always exist."""
        return False


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ['customer_name', 'company_name', 'phone_number', 'email', 'created_at']
    list_filter = ['created_at', 'updated_at']
    search_fields = ['customer_name', 'company_name', 'email', 'phone_number']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Customer Information', {
            'fields': ('customer_name', 'company_name', 'phone_number', 'email', 'address')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['email', 'name', 'phone', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at', 'updated_at']
    search_fields = ['email', 'name', 'phone']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('User Information', {
            'fields': ('email', 'name', 'phone', 'is_active')
        }),
        ('Authentication', {
            'fields': ('password',),
            'description': 'Password is hashed automatically. Leave blank to keep current password when editing.'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(QuotationSend)
class QuotationSendAdmin(admin.ModelAdmin):
    list_display = ['id', 'quotation', 'send_type', 'recipient_email', 'recipient_phone', 'sent_at']
    list_filter = ['send_type', 'sent_at', 'created_at']
    search_fields = ['recipient_email', 'recipient_phone', 'quotation__id']
    readonly_fields = ['created_at']
    date_hierarchy = 'sent_at'
    fieldsets = (
        ('Send Information', {
            'fields': ('quotation', 'send_type', 'recipient_email', 'recipient_phone', 'sent_at')
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )


@admin.register(QuotGenerator)
class QuotGeneratorAdmin(admin.ModelAdmin):
    list_display = ['prefix', 'numberfield']
    search_fields = ['prefix']
