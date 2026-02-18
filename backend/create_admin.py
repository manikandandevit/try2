#!/usr/bin/env python
"""Create admin superuser if it doesn't exist."""
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'kattappa.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

# Create superuser if it doesn't exist
if not User.objects.filter(username="admin").exists():
    User.objects.create_superuser(
        username="admin",
        email="admin@gmail.com",
        password="admin123"
    )
    print("✅ Admin superuser created successfully!")
    print("   Username: admin")
    print("   Password: admin123")
else:
    print("ℹ️  Admin superuser already exists.")

