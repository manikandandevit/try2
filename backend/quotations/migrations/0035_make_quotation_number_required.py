# Generated manually to backfill quotation numbers and make field required

from django.db import migrations, models


def backfill_quotation_numbers(apps, schema_editor):
    """
    Backfill quotation_number for any existing quotations that have null values.
    Assigns numbers based on creation date - oldest first gets first number.
    """
    Quotation = apps.get_model('quotations', 'Quotation')
    Company = apps.get_model('quotations', 'Company')
    
    # Get all quotations ordered by created_at (oldest first)
    # This ensures first created quotation gets first number
    all_quotations = Quotation.objects.all().order_by('created_at')
    
    if all_quotations.exists():
        # Get company to determine prefix
        company = Company.objects.first()
        if not company:
            # Create default company if it doesn't exist
            company = Company.objects.create(
                pk=1,
                email='',
                password='',
                quotation_numberfield=0
            )
        
        # Determine prefix
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
        
        # Find max number already used (for quotations that already have numbers)
        existing_numbers = Quotation.objects.exclude(
            quotation_number__isnull=True
        ).exclude(
            quotation_number=""
        ).values_list("quotation_number", flat=True)
        
        max_seq = 0
        for num in existing_numbers:
            if isinstance(num, str) and num.startswith(prefix):
                numeric_part = num[len(prefix):]
                try:
                    seq = int(numeric_part)
                    if seq > max_seq:
                        max_seq = seq
                except (ValueError, TypeError):
                    continue
        
        # Also consider company's stored number
        company_seq = company.quotation_numberfield or 0
        starting_seq = max(max_seq, company_seq)
        
        # Get quotations without numbers, ordered by creation date (oldest first)
        quotations_without_number = all_quotations.filter(
            quotation_number__isnull=True
        ).order_by('created_at')
        
        # Assign sequential numbers based on creation order
        # First created quotation gets the next number after starting_seq
        current_seq = starting_seq
        for quotation in quotations_without_number:
            current_seq += 1
            quotation.quotation_number = f"{prefix}{current_seq}"
            quotation.save(update_fields=['quotation_number'])
        
        # Update company's quotation_numberfield to the last used number
        if current_seq > company_seq:
            company.quotation_numberfield = current_seq
            company.quotation_prefix = prefix
            company.save(update_fields=['quotation_prefix', 'quotation_numberfield'])


def reverse_backfill(apps, schema_editor):
    """
    Reverse migration - set quotation_number to null (not recommended but needed for rollback)
    """
    # This is intentionally left empty as we don't want to lose quotation numbers
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('quotations', '0034_company_quotation_numberfield_and_more'),
    ]

    operations = [
        # First, backfill any null quotation numbers
        migrations.RunPython(backfill_quotation_numbers, reverse_backfill),
        # Then, make the field non-nullable
        migrations.AlterField(
            model_name='quotation',
            name='quotation_number',
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text='Public quotation number (auto-generated, sequential)',
                max_length=50,
                unique=True
            ),
        ),
    ]

