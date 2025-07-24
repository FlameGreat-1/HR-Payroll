The user wants me to organize all the endpoint tests together in a comprehensive format that they can add to their README.md file. I need to create a well-structured, complete testing guide that includes all endpoints with proper curl commands, expected responses, and organized sections.

Action: DirectAnswer

# **HR Payroll System - API Endpoints Testing Guide**

## **Prerequisites**
- Server running: `python manage.py runserver`
- Default superuser credentials: `ADMIN001` / `?.X_aka8Ru7C`

---

## **1. HEALTH CHECK (No Authentication Required)**

### Health Status
```bash
curl -X GET http://127.0.0.1:8000/accounts/health/
```
**Expected Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-07-24T14:30:00.000Z",
  "users": 1,
  "active_sessions": 0
}
```

---

## **2. AUTHENTICATION ENDPOINTS**

### Get Login Form
```bash
curl -X GET http://127.0.0.1:8000/accounts/login/
```
**Expected:** HTML login form

### Login (Save Session)
```bash
curl -X POST http://127.0.0.1:8000/accounts/login/ \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "employee_code=ADMIN001&password=?.X_aka8Ru7C" \
  -c cookies.txt -L
```
**Expected:** Redirect to dashboard with session cookies saved

### Logout
```bash
curl -X POST http://127.0.0.1:8000/accounts/logout/ \
  -b cookies.txt -L
```
**Expected:** Redirect to login page

---

## **3. PASSWORD MANAGEMENT**

### Get Password Change Form
```bash
curl -X GET http://127.0.0.1:8000/accounts/password/change/ \
  -b cookies.txt
```

### Get Password Reset Form
```bash
curl -X GET http://127.0.0.1:8000/accounts/password/reset/
```

### Password Reset Confirm (with token)
```bash
curl -X GET http://127.0.0.1:8000/accounts/password/reset/sample-token/ \
  -b cookies.txt
```

---

## **4. USER PROFILE MANAGEMENT**

### View Profile
```bash
curl -X GET http://127.0.0.1:8000/accounts/profile/ \
  -b cookies.txt
```

### Get Profile Update Form
```bash
curl -X GET http://127.0.0.1:8000/accounts/profile/update/ \
  -b cookies.txt
```

---

## **5. EMPLOYEE MANAGEMENT**

### List All Employees
```bash
curl -X GET http://127.0.0.1:8000/accounts/employees/ \
  -b cookies.txt
```

### Search Employees
```bash
curl -X GET "http://127.0.0.1:8000/accounts/employees/?search=admin&department=1&status=ACTIVE" \
  -b cookies.txt
```

### View Employee Details
```bash
curl -X GET http://127.0.0.1:8000/accounts/employees/1/ \
  -b cookies.txt
```

### Get Employee Creation Form
```bash
curl -X GET http://127.0.0.1:8000/accounts/employees/create/ \
  -b cookies.txt
```

### Get Employee Update Form
```bash
curl -X GET http://127.0.0.1:8000/accounts/employees/1/update/ \
  -b cookies.txt
```

### Get Bulk Upload Form
```bash
curl -X GET http://127.0.0.1:8000/accounts/employees/bulk-upload/ \
  -b cookies.txt
```

### Export Employees (Excel Download)
```bash
curl -X GET http://127.0.0.1:8000/accounts/employees/export/ \
  -b cookies.txt \
  -o employees_export.xlsx
```

---

## **6. DEPARTMENT MANAGEMENT**

### List Departments
```bash
curl -X GET http://127.0.0.1:8000/accounts/departments/ \
  -b cookies.txt
```

### Get Department Creation Form
```bash
curl -X GET http://127.0.0.1:8000/accounts/departments/create/ \
  -b cookies.txt
```

---

## **7. ROLE MANAGEMENT (SuperAdmin Only)**

### List Roles
```bash
curl -X GET http://127.0.0.1:8000/accounts/roles/ \
  -b cookies.txt
```

### Get Role Creation Form
```bash
curl -X GET http://127.0.0.1:8000/accounts/roles/create/ \
  -b cookies.txt
```

---

## **8. SESSION MANAGEMENT**

### View User Sessions
```bash
curl -X GET http://127.0.0.1:8000/accounts/sessions/ \
  -b cookies.txt
```

### Terminate Session
```bash
curl -X POST http://127.0.0.1:8000/accounts/sessions/1/terminate/ \
  -b cookies.txt
```

---

## **9. SYSTEM ADMINISTRATION (SuperAdmin Only)**

### View Audit Logs
```bash
curl -X GET http://127.0.0.1:8000/accounts/audit-logs/ \
  -b cookies.txt
```

### Filter Audit Logs
```bash
curl -X GET "http://127.0.0.1:8000/accounts/audit-logs/?user=admin&action=LOGIN&date_from=2025-01-01&date_to=2025-07-24" \
  -b cookies.txt
```

### System Settings
```bash
curl -X GET http://127.0.0.1:8000/accounts/settings/ \
  -b cookies.txt
```

### System Statistics
```bash
curl -X GET http://127.0.0.1:8000/accounts/statistics/ \
  -b cookies.txt
```

---

## **10. AJAX ENDPOINTS (Return JSON)**

### Search Employees (Autocomplete)
```bash
curl -X GET "http://127.0.0.1:8000/accounts/ajax/search-employees/?q=admin" \
  -b cookies.txt
```
**Expected Response:**
```json
{
  "results": [
    {
      "id": 1,
      "text": "System Administrator (ADMIN001)",
      "employee_code": "ADMIN001",
      "department": "Human Resources",
      "job_title": "System Administrator"
    }
  ]
}
```

### Check Employee Code Availability
```bash
curl -X GET "http://127.0.0.1:8000/accounts/ajax/check-employee-code/?employee_code=TEST001" \
  -b cookies.txt
```
**Expected Response:**
```json
{
  "available": true,
  "message": "Employee code is available"
}
```

### Bulk User Action
```bash
curl -X POST http://127.0.0.1:8000/accounts/ajax/bulk-action/ \
  -H "Content-Type: application/json" \
  -H "X-CSRFToken: your-csrf-token" \
  -d '{"user_ids":[1],"action":"activate","reason":"Testing bulk action"}' \
  -b cookies.txt
```
**Expected Response:**
```json
{
  "success": true,
  "message": "Activate applied to 1 users successfully."
}
```

### Send Notification
```bash
curl -X POST http://127.0.0.1:8000/accounts/ajax/send-notification/ \
  -H "Content-Type: application/json" \
  -H "X-CSRFToken: your-csrf-token" \
  -d '{"recipient_ids":[1],"subject":"Test Notification","message":"This is a test message"}' \
  -b cookies.txt
```

### Generate Report
```bash
curl -X GET "http://127.0.0.1:8000/accounts/ajax/generate-report/?start_date=2025-01-01&end_date=2025-07-24&user_filter=admin" \
  -b cookies.txt
```

---

## **11. ERROR HANDLING**

### Test 404 Error
```bash
curl -X GET http://127.0.0.1:8000/accounts/nonexistent-page/ \
  -b cookies.txt
```

### Test 403 Error (Access Denied)
```bash
# Try accessing SuperAdmin endpoint without proper role
curl -X GET http://127.0.0.1:8000/accounts/roles/ \
  -b cookies_regular_user.txt
```

---

## **12. COMPLETE TESTING SCRIPT**

### Save this as `test_endpoints.sh`:
```bash
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
```

### Run the script:
```bash
chmod +x test_endpoints.sh
./test_endpoints.sh
```

---

## **Notes:**
- All endpoints require authentication except `/health/` and `/login/`
- AJAX endpoints return JSON responses
- Web endpoints return HTML pages
- Some endpoints require specific roles (SuperAdmin, HRAdmin, etc.)
- CSRF tokens are required for POST requests from web forms
- Session cookies are automatically handled when using `-c` and `-b` flags