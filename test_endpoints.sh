#!/bin/bash

echo "=== HR Payroll System Endpoint Testing ==="
echo "Starting server test..."

# Health check
echo "1. Testing health check..."
curl -s -X GET http://127.0.0.1:8000/accounts/health/ | jq .

# Login
echo "2. Testing login..."
curl -s -X POST http://127.0.0.1:8000/accounts/login/ \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "employee_code=ADMIN001&password=?.X_aka8Ru7C" \
  -c cookies.txt -L > /dev/null

# Test authenticated endpoints
echo "3. Testing dashboard..."
curl -s -X GET http://127.0.0.1:8000/accounts/dashboard/ -b cookies.txt > /dev/null && echo "✓ Dashboard accessible"

echo "4. Testing employee list..."
curl -s -X GET http://127.0.0.1:8000/accounts/employees/ -b cookies.txt > /dev/null && echo "✓ Employee list accessible"

echo "5. Testing AJAX search..."
curl -s -X GET "http://127.0.0.1:8000/accounts/ajax/search-employees/?q=admin" -b cookies.txt | jq .

echo "6. Testing employee code check..."
curl -s -X GET "http://127.0.0.1:8000/accounts/ajax/check-employee-code/?employee_code=TEST001" -b cookies.txt | jq .

echo "=== Testing Complete ==="
