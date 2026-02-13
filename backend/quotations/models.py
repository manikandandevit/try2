"""
Models for quotations app.
"""
from django.db import models
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password, is_password_usable
import json


def generate_quotation_number() -> str:
    """
    Generate a company-based sequential quotation number.

    Final format (as per latest requirement):
    - Quotation prefix from Company admin (field: quotation_prefix)
    - PLUS an auto-incrementing integer from quotation_numberfield

    Example (company_name = "Syngrid", quotation_prefix="SynQ", quotation_numberfield=100):
    - Next quotation:  SynQ101
    - Then:            SynQ102, SynQ103, ...

    Behaviour:
    - Does NOT use SQL AUTO_INCREMENT / database sequence.
    - Uses Company.quotation_prefix as the fixed prefix.
    - Uses the larger of:
        * Company.quotation_numberfield
        * Max numeric suffix already used with this prefix in Quotation.quotation_number
      then returns next = current + 1.
    - Also updates Company.quotation_numberfield to this new value
      so it is permanent across admin/user1/user2.
    - Shared across all users in the same company.
    """
    # Import here to avoid circular issues at import time
    from .models import Quotation, Company  # type: ignore

    company = Company.get_company()

    # 1) Determine prefix
    # Prefer explicit quotation_prefix from admin; fallback to first 3 letters logic if empty.
    raw_prefix = (company.quotation_prefix or "").strip()
    if raw_prefix:
        prefix = raw_prefix
    else:
        base_name = (company.company_name or company.brand_name or "").strip()
        letters_only = "".join(ch for ch in base_name if ch.isalpha())[:3]
        if not letters_only:
            letters_only = "com"
        elif len(letters_only) < 3:
            letters_only = (letters_only + "xxx")[:3]
        prefix = f"{letters_only.lower()}q"

    # 2) Find max number already used with this prefix in existing quotations
    existing_numbers = (
        Quotation.objects.filter(quotation_number__startswith=prefix)
        .exclude(quotation_number__isnull=True)
        .exclude(quotation_number="")
        .values_list("quotation_number", flat=True)
    )

    max_seq = 0
    for num in existing_numbers:
        if not isinstance(num, str):
            continue
        if not num.startswith(prefix):
            continue
        numeric_part = num[len(prefix):]
        try:
            seq = int(numeric_part)
            if seq > max_seq:
                max_seq = seq
        except (ValueError, TypeError):
            # Ignore any non-standard values
            continue

    # 3) Also consider the value stored on the Company itself
    company_seq = company.quotation_numberfield or 0
    current_seq = max(max_seq, company_seq)

    # 4) Next number = current + 1
    next_seq = current_seq + 1

    # 5) Persist the new last-used number back to Company so it's permanent
    company.quotation_prefix = prefix  # ensure stored prefix matches what we used
    company.quotation_numberfield = next_seq
    company.save(update_fields=["quotation_prefix", "quotation_numberfield"])

    # 6) Final quotation number: prefix + integer (no zero padding)
    return f"{prefix}{next_seq}"


class Quotation(models.Model):
    """Model to store quotation data."""
    # User-facing quotation number (auto-generated, sequential, unique)
    quotation_number = models.CharField(
        max_length=50,
        unique=True,
        blank=False,
        null=False,
        editable=False,
        db_index=True,
        help_text="Public quotation number (auto-generated, sequential)",
    )
    quotation_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def save(self, *args, **kwargs):
        """
        On create, generate a sequential quotation_number if missing.
        
        CRITICAL: Once a quotation has a quotation_number, it NEVER changes.
        This ensures one quotation = one permanent number for its entire lifetime.

        Uses a custom generator to ensure unique, sequential quotation numbers
        based on company prefix and incrementing counter.
        """
        # Only generate if quotation_number is completely missing
        # If it already exists, NEVER change it - it's permanent
        if not self.quotation_number:
            # Try until we get a unique number (extremely unlikely to loop)
            while True:
                new_number = generate_quotation_number()
                if not Quotation.objects.filter(quotation_number=new_number).exists():
                    self.quotation_number = new_number
                    break
        # If quotation_number already exists, preserve it - do NOT regenerate
        super().save(*args, **kwargs)
    
    def __str__(self):
        # Prefer the public quotation number instead of internal PK
        return f"Quotation {self.quotation_number} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"
    
    def get_services(self):
        """Get services from quotation data."""
        return self.quotation_data.get('services', [])
    
    def get_subtotal(self):
        """Get subtotal from quotation data."""
        return self.quotation_data.get('subtotal', 0)
    
    def get_grand_total(self):
        """Get grand total from quotation data."""
        return self.quotation_data.get('grand_total', 0)


class Company(models.Model):
    """Model to store single company login credentials and images."""
    company_name = models.CharField(max_length=255, blank=True, null=True, help_text="Company Name")
    brand_name = models.CharField(max_length=255, blank=True, null=True, help_text="Brand Name")
    email = models.EmailField(help_text="Company Email")
    password = models.CharField(max_length=255, help_text="Company Password")
    tagline = models.CharField(max_length=255, blank=True, null=True, help_text="Company Tagline")
    phone_number = models.CharField(max_length=20, blank=True, null=True, help_text="Company Phone Number")
    address = models.TextField(blank=True, null=True, help_text="Company Address")
    sendemail = models.EmailField(blank=True, null=True, help_text="Send Email Address")
    sendpassword = models.CharField(max_length=255, blank=True, null=True, help_text="Send Email Password")
    sendnumber = models.CharField(max_length=20, blank=True, null=True, help_text="Send WhatsApp Number")
    openrouter_api_key = models.CharField(max_length=500, blank=True, null=True, help_text="OpenRouter API Key")
    openrouter_model = models.CharField(max_length=255, blank=True, null=True, help_text="OpenRouter Model:1", default='google/gemini-flash-1.5:free')
    openrouter_model_2 = models.CharField(max_length=255, blank=True, null=True, help_text="OpenRouter Model:2")
    openrouter_model_3 = models.CharField(max_length=255, blank=True, null=True, help_text="OpenRouter Model:3")
    # Quotation number generator settings (visible in Company admin)
    quotation_prefix = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Custom quotation prefix (e.g. 'synq'). If empty, first 3 letters of company name are used.",
    )
    quotation_numberfield = models.PositiveIntegerField(
        default=0,
        help_text="Last used quotation running number (for reference).",
    )
    login_logo = models.ImageField(upload_to='company_login/', blank=True, null=True, help_text="Login Logo")
    login_image = models.ImageField(upload_to='company_login/', blank=True, null=True, help_text="Login Image")
    quotation_logo = models.ImageField(upload_to='company_quotation/', blank=True, null=True, help_text="Quotation Logo")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Company Details'
        verbose_name_plural = 'Company Details'
        ordering = ['-created_at']
    
    def save(self, *args, **kwargs):
        """Ensure only one company instance exists."""
        # If this is a new instance and one already exists, update the existing one instead
        if not self.pk and Company.objects.exists():
            # Get the existing company and update it
            existing_company = Company.objects.first()
            existing_company.company_name = self.company_name
            existing_company.brand_name = self.brand_name
            existing_company.email = self.email
            existing_company.password = self.password
            existing_company.tagline = self.tagline
            existing_company.phone_number = self.phone_number
            existing_company.address = self.address
            existing_company.sendemail = self.sendemail
            existing_company.sendpassword = self.sendpassword
            existing_company.sendnumber = self.sendnumber
            existing_company.openrouter_api_key = self.openrouter_api_key
            existing_company.openrouter_model = self.openrouter_model
            existing_company.openrouter_model_2 = self.openrouter_model_2
            existing_company.openrouter_model_3 = self.openrouter_model_3
            existing_company.login_logo = self.login_logo
            existing_company.login_image = self.login_image
            existing_company.quotation_logo = self.quotation_logo
            existing_company.save()
            return
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.email
    
    @classmethod
    def get_company(cls):
        """Get the single company instance, create if doesn't exist."""
        company, created = cls.objects.get_or_create(
            pk=1,
            defaults={
                'email': '',
                'password': '',
                'sendemail': '',
                'sendpassword': '',
                'openrouter_model': 'google/gemini-flash-1.5:free'
            }
        )
        return company


class Client(models.Model):
    """Model to store customer information."""
    customer_name = models.CharField(max_length=255, help_text="Customer Name")
    company_name = models.CharField(max_length=255, help_text="Company Name", blank=True, null=True)
    phone_number = models.CharField(max_length=20, help_text="Phone Number", blank=True, null=True)
    email = models.EmailField(help_text="Customer Email")
    address = models.TextField(help_text="Customer Address", blank=True, null=True)
    is_active = models.BooleanField(default=True, help_text="Is this customer active/enabled in dashboard toggle")
    created_by_type = models.CharField(max_length=20, blank=True, null=True, help_text="Type of creator: 'company' or 'user'")
    created_by_user_id = models.IntegerField(blank=True, null=True, help_text="ID of user who created this customer (if created by user)")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Customer'
        verbose_name_plural = 'Customers'
    
    def __str__(self):
        return f"{self.customer_name} ({self.email})"


class User(models.Model):
    """Model to store user information with authentication."""
    email = models.EmailField(unique=True, help_text="User Email")
    password = models.CharField(max_length=255, help_text="Hashed Password")
    name = models.CharField(max_length=255, help_text="Full Name")
    phone = models.CharField(max_length=20, help_text="Phone Number")
    is_active = models.BooleanField(default=True, help_text="Active Status")
    created_by_type = models.CharField(max_length=20, blank=True, null=True, help_text="Type of creator: 'company' or 'user'")
    created_by_user_id = models.IntegerField(blank=True, null=True, help_text="ID of user who created this user (if created by user)")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'User'
        verbose_name_plural = 'Users'
    
    def __str__(self):
        return f"{self.name} ({self.email})"
    
    def set_password(self, raw_password):
        """Hash and set password."""
        self.password = make_password(raw_password)
    
    def check_password(self, raw_password):
        """Check if provided password matches."""
        return check_password(raw_password, self.password)
    
    def save(self, *args, **kwargs):
        """Override save to hash password if it's not already hashed."""
        # Check if password is already hashed
        # Django password hashes typically start with algorithm identifiers like 'pbkdf2_sha256$'
        if self.password:
            # Check if password is already hashed (hashed passwords start with algorithm identifiers)
            # is_password_usable returns True if password is already hashed
            if not is_password_usable(self.password):
                # Password is not hashed (raw password), hash it
                print(f"Auto-hashing password for user: {self.email}")
                self.password = make_password(self.password)
        super().save(*args, **kwargs)


class RefreshToken(models.Model):
    """Model to store refresh tokens securely for JWT authentication."""
    token = models.CharField(max_length=500, unique=True, db_index=True, help_text="Refresh Token")
    user_email = models.EmailField(db_index=True, help_text="User Email")
    user_type = models.CharField(max_length=20, help_text="User type: 'user' or 'company'")
    user_id = models.IntegerField(null=True, blank=True, help_text="User ID if user_type is 'user'")
    is_active = models.BooleanField(default=True, help_text="Token active status")
    expires_at = models.DateTimeField(help_text="Token expiration time")
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True, help_text="Last time token was used")
    ip_address = models.GenericIPAddressField(null=True, blank=True, help_text="IP address of token creation")
    user_agent = models.CharField(max_length=500, blank=True, null=True, help_text="User agent of token creation")
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Refresh Token'
        verbose_name_plural = 'Refresh Tokens'
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['user_email', 'is_active']),
            models.Index(fields=['expires_at']),
        ]
    
    def __str__(self):
        return f"RefreshToken for {self.user_email} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"
    
    def is_expired(self):
        """Check if token is expired."""
        return timezone.now() > self.expires_at
    
    def revoke(self):
        """Revoke the token."""
        self.is_active = False
        self.save()


class QuotationSend(models.Model):
    """Model to track quotation sends via email and WhatsApp."""
    SEND_TYPE_CHOICES = [
        ('email', 'Email'),
        ('whatsapp', 'WhatsApp'),
    ]
    
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name='sends', help_text="Quotation that was sent")
    send_type = models.CharField(max_length=10, choices=SEND_TYPE_CHOICES, help_text="Type of send: email or whatsapp")
    recipient_email = models.EmailField(blank=True, null=True, help_text="Recipient email (for email sends)")
    recipient_phone = models.CharField(max_length=20, blank=True, null=True, help_text="Recipient phone (for WhatsApp sends)")
    user_id = models.IntegerField(null=True, blank=True, help_text="User ID who sent this quotation (null for admin/company sends)")
    sent_at = models.DateTimeField(default=timezone.now, help_text="When the quotation was sent")
    created_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        ordering = ['-sent_at']
        verbose_name = 'Quotation Send'
        verbose_name_plural = 'Quotation Sends'
        indexes = [
            models.Index(fields=['quotation', 'send_type'], name='qsend_quotation_type_idx'),
            models.Index(fields=['sent_at'], name='qsend_sent_at_idx'),
            models.Index(fields=['user_id'], name='qsend_user_id_idx'),
        ]
    
    def __str__(self):
        return f"{self.get_send_type_display()} send for Quotation {self.quotation.id} - {self.sent_at.strftime('%Y-%m-%d %H:%M')}"


class QuotGenerator(models.Model):
    """
    Simple model to configure quotation number generation.

    - prefix: free text prefix (e.g. 'synq')
    - numberfield: last used number or starting number
    """
    prefix = models.CharField(max_length=50, help_text="Prefix for quotation numbers (e.g. 'synq')")
    numberfield = models.PositiveIntegerField(default=0, help_text="Current/last used number")
    
    class Meta:
        verbose_name = "Quotation Number Generator"
        verbose_name_plural = "Quotation Number Generators"
    
    def __str__(self):
        return f"{self.prefix} - {self.numberfield}"