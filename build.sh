#!/usr/bin/env bash
set -o errexit

echo "Installing dependencies..."
pip install -r requirements.txt

echo "Creating migrations..."
python manage.py makemigrations accounts
python manage.py makemigrations core
python manage.py makemigrations employees
python manage.py makemigrations attendance
python manage.py makemigrations payroll
python manage.py makemigrations expenses
python manage.py makemigrations reports

echo "Collecting static files..."
python manage.py collectstatic --no-input

echo "Running database migrations..."
python manage.py migrate

echo "Creating superuser if needed..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
import os
User = get_user_model()
if not User.objects.filter(is_superuser=True).exists():
    User.objects.create_superuser(
        employee_code='admin',
        email=os.environ.get('ADMIN_EMAIL', 'admin@company.com'),
        password=os.environ.get('ADMIN_PASSWORD', 'admin123'),
        first_name='Admin',
        last_name='User'
    )
    print('Superuser created successfully')
else:
    print('Superuser already exists')
"

echo "Build completed successfully!"
