"""Verify SQLite database setup"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'kattappa.settings')
django.setup()

from django.conf import settings
from django.db import connection

print("=" * 60)
print("SQLITE DATABASE SETUP VERIFICATION")
print("=" * 60)

print(f"\nDatabase Engine: {settings.DATABASES['default']['ENGINE']}")
print(f"Database File: {settings.DATABASES['default']['NAME']}")

cursor = connection.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'quotations_%'")
tables = cursor.fetchall()

print(f"\nTotal Quotations Tables: {len(tables)}")
for table in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
    count = cursor.fetchone()[0]
    print(f"  {table[0]}: {count} records")

print("\n" + "=" * 60)
print("[OK] SQLite Database Setup Complete!")
print("=" * 60)

