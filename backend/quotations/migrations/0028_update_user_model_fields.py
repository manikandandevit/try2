# Generated migration to update User model fields
from django.db import migrations, models


def migrate_user_data(apps, schema_editor):
    """Migrate first_name and last_name to name field."""
    User = apps.get_model('quotations', 'User')
    for user in User.objects.all():
        # Combine first_name and last_name into name
        name_parts = []
        if user.first_name:
            name_parts.append(user.first_name)
        if user.last_name:
            name_parts.append(user.last_name)
        user.name = ' '.join(name_parts) if name_parts else user.email
        user.save()


def reverse_migrate_user_data(apps, schema_editor):
    """Reverse migration: split name back to first_name and last_name."""
    User = apps.get_model('quotations', 'User')
    for user in User.objects.all():
        if user.name:
            name_parts = user.name.split(' ', 1)
            user.first_name = name_parts[0] if len(name_parts) > 0 else ''
            user.last_name = name_parts[1] if len(name_parts) > 1 else ''
        else:
            user.first_name = ''
            user.last_name = ''
        user.save()


class Migration(migrations.Migration):

    dependencies = [
        ('quotations', '0027_remove_company_currency_symbol'),
    ]

    operations = [
        # Step 1: Add new fields (name and phone)
        migrations.AddField(
            model_name='user',
            name='name',
            field=models.CharField(default='', help_text='Full Name', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='user',
            name='phone',
            field=models.CharField(default='', help_text='Phone Number', max_length=20, blank=True),
            preserve_default=True,
        ),
        # Step 2: Migrate data from first_name/last_name to name
        migrations.RunPython(migrate_user_data, reverse_migrate_user_data),
        # Step 3: Remove old fields
        migrations.RemoveField(
            model_name='user',
            name='first_name',
        ),
        migrations.RemoveField(
            model_name='user',
            name='last_name',
        ),
        migrations.RemoveField(
            model_name='user',
            name='is_admin',
        ),
        migrations.RemoveField(
            model_name='user',
            name='permissions',
        ),
    ]

