from django.contrib.auth import get_user_model
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Q, Count
from django.db import models
from .models import AuditLog, PasswordResetToken, UserSession, SystemConfiguration, Department
import secrets
import string
import hashlib
import re
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging

User = get_user_model()
logger = logging.getLogger(__name__)


def generate_employee_code(department_code: str = None) -> str:
    if department_code:
        prefix = department_code[:3].upper()
    else:
        prefix = "EMP"

    last_employee = (
        User.objects.filter(employee_code__startswith=prefix)
        .order_by("-employee_code")
        .first()
    )

    if last_employee:
        try:
            last_number = int(last_employee.employee_code[len(prefix):])
            new_number = last_number + 1
        except (ValueError, IndexError):
            new_number = 1
    else:
        new_number = 1

    return f"{prefix}{new_number:04d}"


def generate_secure_password(length: int = 12) -> str:
    min_length = int(SystemConfiguration.get_setting('MIN_PASSWORD_LENGTH', '8'))
    if length < min_length:
        length = min_length

    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits
    special_chars = "!@#$%^&*()_+-=[]{}|;:,.<>?"

    password = [
        secrets.choice(lowercase),
        secrets.choice(uppercase),
        secrets.choice(digits),
        secrets.choice(special_chars),
    ]

    all_chars = lowercase + uppercase + digits + special_chars
    for _ in range(length - 4):
        password.append(secrets.choice(all_chars))

    secrets.SystemRandom().shuffle(password)
    return "".join(password)


def validate_password_strength(password: str) -> Tuple[bool, List[str]]:
    errors = []
    min_length = int(SystemConfiguration.get_setting('MIN_PASSWORD_LENGTH', '8'))

    if len(password) < min_length:
        errors.append(f"Password must be at least {min_length} characters long")

    if not re.search(r"[A-Z]", password):
        errors.append("Password must contain at least one uppercase letter")

    if not re.search(r"[a-z]", password):
        errors.append("Password must contain at least one lowercase letter")

    if not re.search(r"\d", password):
        errors.append("Password must contain at least one number")

    if not re.search(r'[!@#$%^&*(),.?":{}|<>_+\-=\[\]\\;\'\/]', password):
        errors.append("Password must contain at least one special character")

    common_passwords = [
        "password", "123456", "123456789", "qwerty", "abc123", 
        "password123", "admin", "letmein", "welcome", "monkey"
    ]

    if password.lower() in common_passwords:
        errors.append("Password is too common")

    return len(errors) == 0, errors


def hash_sensitive_data(data: str) -> str:
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", data.encode(), salt.encode(), 100000)
    return f"{salt}:{hashed.hex()}"


def verify_hashed_data(data: str, hashed_data: str) -> bool:
    try:
        salt, stored_hash = hashed_data.split(":")
        hashed = hashlib.pbkdf2_hmac("sha256", data.encode(), salt.encode(), 100000)
        return hashed.hex() == stored_hash
    except ValueError:
        return False


def get_client_ip(request) -> str:
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "127.0.0.1")
    return ip


def get_user_agent(request) -> str:
    return request.META.get("HTTP_USER_AGENT", "Unknown")


def log_user_activity(user, action: str, description: str, request, additional_data: Dict = None):
    try:
        ip_address = get_client_ip(request) if request else "127.0.0.1"
        user_agent = get_user_agent(request) if request else "System"
        
        AuditLog.log_action(
            user=user,
            action=action,
            description=description,
            ip_address=ip_address,
            user_agent=user_agent,
            additional_data=additional_data or {},
        )
    except Exception as e:
        logger.error(f"Failed to log user activity: {e}")


def create_user_session(user, request) -> UserSession:
    session_key = request.session.session_key
    if not session_key:
        request.session.create()
        session_key = request.session.session_key

    user_session = UserSession()
    user_session.user = user
    user_session._session_key = session_key
    user_session.ip_address = get_client_ip(request)
    user_session.user_agent = get_user_agent(request)
    user_session.save()

    user.last_login_ip = get_client_ip(request)
    user.save(update_fields=["last_login_ip"])

    return user_session


def terminate_user_sessions(user, exclude_session_key: str = None):
    sessions = UserSession.objects.filter(user=user, is_active=True)
    if exclude_session_key:
        exclude_hash = hashlib.sha256(exclude_session_key.encode()).hexdigest()
        sessions = sessions.exclude(session_key_hash=exclude_hash)

    for session in sessions:
        session.terminate_session()


def cleanup_expired_sessions():
    timeout_minutes = int(SystemConfiguration.get_setting("SESSION_TIMEOUT", "30"))
    cutoff_time = timezone.now() - timedelta(minutes=timeout_minutes)

    expired_sessions = UserSession.objects.filter(
        is_active=True, last_activity__lt=cutoff_time
    )

    for session in expired_sessions:
        session.terminate_session()

    return expired_sessions.count()


def create_password_reset_token(user, request) -> PasswordResetToken:
    existing_tokens = PasswordResetToken.objects.filter(
        user=user, is_used=False, expires_at__gt=timezone.now()
    )
    existing_tokens.update(is_used=True, used_at=timezone.now())

    token = PasswordResetToken.objects.create(
        user=user, ip_address=get_client_ip(request)
    )

    return token


def send_password_reset_email(user, token: PasswordResetToken, request):
    try:
        reset_url = request.build_absolute_uri(f"/accounts/reset-password/{token.token}/")

        context = {
            "user": user,
            "reset_url": reset_url,
            "company_name": SystemConfiguration.get_setting("COMPANY_NAME", "HR System"),
            "expires_in_hours": int(SystemConfiguration.get_setting("PASSWORD_RESET_EXPIRY_HOURS", "24")),
        }

        html_message = render_to_string("accounts/emails/password_reset.html", context)
        plain_message = strip_tags(html_message)

        email = EmailMultiAlternatives(
            subject="Password Reset Request",
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        email.attach_alternative(html_message, "text/html")
        email.send()

        log_user_activity(
            user=user,
            action="PASSWORD_RESET_REQUEST",
            description=f"Password reset email sent to {user.email}",
            request=request,
        )

        return True
    except Exception as e:
        logger.error(f"Failed to send password reset email: {e}")
        return False


def send_welcome_email(user, temporary_password: str, request):
    try:
        login_url = request.build_absolute_uri("/accounts/login/")

        context = {
            "user": user,
            "temporary_password": temporary_password,
            "login_url": login_url,
            "company_name": SystemConfiguration.get_setting("COMPANY_NAME", "HR System"),
        }

        html_message = render_to_string("accounts/emails/welcome.html", context)
        plain_message = strip_tags(html_message)

        email = EmailMultiAlternatives(
            subject="Welcome to HR System",
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        email.attach_alternative(html_message, "text/html")
        email.send()

        return True
    except Exception as e:
        logger.error(f"Failed to send welcome email: {e}")
        return False


def validate_employee_data(data: Dict) -> Tuple[bool, Dict[str, List[str]]]:
    errors = {}

    required_fields = ["employee_code", "first_name", "last_name", "email"]
    for field in required_fields:
        if not data.get(field):
            errors.setdefault(field, []).append(
                f"{field.replace('_', ' ').title()} is required"
            )

    employee_code = data.get("employee_code", "").upper()
    if employee_code:
        if not re.match(r"^[A-Z0-9]{3,20}$", employee_code):
            errors.setdefault("employee_code", []).append(
                "Employee code must be 3-20 characters, alphanumeric uppercase only"
            )

        if User.objects.filter(employee_code=employee_code).exists():
            errors.setdefault("employee_code", []).append("Employee code already exists")

    email = data.get("email")
    if email:
        email_regex = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
        if not email_regex.match(email):
            errors.setdefault("email", []).append("Invalid email format")

        if User.objects.filter(email=email).exists():
            errors.setdefault("email", []).append("Email already exists")

    phone = data.get("phone_number")
    if phone:
        phone_regex = re.compile(r"^\+?[1-9]\d{1,14}$")
        if not phone_regex.match(phone):
            errors.setdefault("phone_number", []).append("Invalid phone number format")

    date_of_birth = data.get("date_of_birth")
    if date_of_birth:
        try:
            if isinstance(date_of_birth, str):
                dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
            else:
                dob = date_of_birth

            today = timezone.now().date()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            
            min_age = int(SystemConfiguration.get_setting('MIN_EMPLOYEE_AGE', '18'))
            max_age = int(SystemConfiguration.get_setting('MAX_EMPLOYEE_AGE', '65'))

            if age < min_age:
                errors.setdefault("date_of_birth", []).append(f"Employee must be at least {min_age} years old")
            elif age > max_age:
                errors.setdefault("date_of_birth", []).append("Please verify the date of birth")
        except (ValueError, TypeError):
            errors.setdefault("date_of_birth", []).append("Invalid date format")

    hire_date = data.get("hire_date")
    if hire_date:
        try:
            if isinstance(hire_date, str):
                hire_dt = datetime.strptime(hire_date, "%Y-%m-%d").date()
            else:
                hire_dt = hire_date

            if hire_dt > timezone.now().date():
                errors.setdefault("hire_date", []).append("Hire date cannot be in the future")
        except (ValueError, TypeError):
            errors.setdefault("hire_date", []).append("Invalid date format")

    return len(errors) == 0, errors


def search_users(query: str, department_id: int = None, role_id: int = None, status: str = None, current_user=None) -> List[User]:
    queryset = User.objects.select_related('department', 'role', 'manager').filter(is_active=True)

    if current_user and not current_user.is_superuser:
        from .permissions import EmployeeAccessMixin
        access_mixin = EmployeeAccessMixin()
        accessible_employees = access_mixin.get_accessible_employees(current_user)
        queryset = queryset.filter(id__in=accessible_employees.values_list("id", flat=True))

    if query:
        queryset = queryset.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(employee_code__icontains=query)
            | Q(email__icontains=query)
            | Q(job_title__icontains=query)
        )

    if department_id:
        queryset = queryset.filter(department_id=department_id)

    if role_id:
        queryset = queryset.filter(role_id=role_id)

    if status:
        queryset = queryset.filter(status=status)

    return queryset.order_by("employee_code")


def get_user_dashboard_data(user) -> Dict:
    data = {
        "user": user,
        "total_employees": 0,
        "active_employees": 0,
        "departments": 0,
        "pending_approvals": 0,
        "recent_activities": [],
        "system_alerts": [],
    }

    if user.is_superuser or (user.role and user.role.name in ["SUPER_ADMIN", "HR_ADMIN"]):
        data["total_employees"] = User.objects.count()
        data["active_employees"] = User.objects.filter(is_active=True, status="ACTIVE").count()
        data["departments"] = Department.objects.filter(is_active=True).count()

    elif user.role and user.role.name == "DEPARTMENT_MANAGER":
        if user.department:
            dept_employees = user.department.get_all_employees()
            data["total_employees"] = dept_employees.count()
            data["active_employees"] = dept_employees.filter(status="ACTIVE").count()

    data["recent_activities"] = AuditLog.objects.filter(user=user).order_by("-timestamp")[:10]

    if user.must_change_password:
        data["system_alerts"].append({
            "type": "warning",
            "message": "You must change your password before continuing.",
        })

    if user.is_password_expired():
        data["system_alerts"].append({
            "type": "danger",
            "message": "Your password has expired. Please change it immediately.",
        })

    return data

class UserUtilities:
    @staticmethod
    def get_user_permissions_list(user) -> List[str]:
        if user.is_superuser:
            return ["all_permissions"]

        if not user.role:
            return ["view_own_profile", "edit_own_profile"]

        role_permissions = {
            "SUPER_ADMIN": ["all_permissions"],
            "HR_ADMIN": [
                "manage_employees", "manage_departments", "manage_roles",
                "view_all_attendance", "manage_payroll", "view_all_reports",
                "manage_system_settings", "view_audit_logs",
            ],
            "HR_MANAGER": [
                "manage_employees", "view_departments", "view_all_attendance",
                "view_payroll_reports", "view_hr_reports", "approve_leave_requests",
            ],
            "DEPARTMENT_MANAGER": [
                "view_department_employees", "view_department_attendance",
                "approve_department_leave", "view_department_reports",
            ],
            "PAYROLL_MANAGER": [
                "manage_payroll", "view_all_employees", "generate_payslips",
                "view_payroll_reports", "manage_salary_components",
            ],
            "ACCOUNTANT": [
                "view_payroll", "manage_expenses", "view_financial_reports",
                "export_financial_data",
            ],
            "AUDITOR": [
                "view_all_data", "view_audit_logs", "generate_audit_reports",
                "export_audit_data",
            ],
            "EMPLOYEE": ["view_own_profile", "edit_own_profile", "view_own_payslip"],
        }

        return role_permissions.get(user.role.name, ["view_own_profile", "edit_own_profile"])

    @staticmethod
    def check_user_permission(user, permission: str) -> bool:
        user_permissions = UserUtilities.get_user_permissions_list(user)
        return "all_permissions" in user_permissions or permission in user_permissions

    @staticmethod
    def get_navigation_menu(user) -> List[Dict]:
        menu_items = []

        if UserUtilities.check_user_permission(user, "view_own_profile"):
            menu_items.append({
                "name": "Dashboard",
                "url": "accounts:dashboard",
                "icon": "fas fa-tachometer-alt",
                "active": True,
            })

        if (UserUtilities.check_user_permission(user, "manage_employees") or 
            UserUtilities.check_user_permission(user, "view_department_employees")):
            menu_items.append({
                "name": "Employees",
                "url": "employees:list",
                "icon": "fas fa-users",
                "submenu": [
                    {"name": "All Employees", "url": "employees:list"},
                    {"name": "Add Employee", "url": "employees:create"},
                    {"name": "Departments", "url": "employees:departments"},
                ],
            })

        if (UserUtilities.check_user_permission(user, "view_all_attendance") or 
            UserUtilities.check_user_permission(user, "view_department_attendance")):
            menu_items.append({
                "name": "Attendance",
                "url": "attendance:list",
                "icon": "fas fa-clock",
                "submenu": [
                    {"name": "View Attendance", "url": "attendance:list"},
                    {"name": "Import Attendance", "url": "attendance:import"},
                    {"name": "Reports", "url": "attendance:reports"},
                ],
            })

        if (UserUtilities.check_user_permission(user, "manage_payroll") or 
            UserUtilities.check_user_permission(user, "view_payroll_reports")):
            menu_items.append({
                "name": "Payroll",
                "url": "payroll:list",
                "icon": "fas fa-money-bill-wave",
                "submenu": [
                    {"name": "Generate Payroll", "url": "payroll:generate"},
                    {"name": "Payslips", "url": "payroll:payslips"},
                    {"name": "Salary Components", "url": "payroll:components"},
                ],
            })

        if UserUtilities.check_user_permission(user, "manage_expenses"):
            menu_items.append({
                "name": "Expenses",
                "url": "expenses:list",
                "icon": "fas fa-receipt",
                "submenu": [
                    {"name": "All Expenses", "url": "expenses:list"},
                    {"name": "Submit Expense", "url": "expenses:create"},
                    {"name": "Approvals", "url": "expenses:approvals"},
                ],
            })

        if UserUtilities.check_user_permission(user, "view_all_reports"):
            menu_items.append({
                "name": "Reports",
                "url": "reports:dashboard",
                "icon": "fas fa-chart-bar",
                "submenu": [
                    {"name": "Employee Reports", "url": "reports:employees"},
                    {"name": "Attendance Reports", "url": "reports:attendance"},
                    {"name": "Payroll Reports", "url": "reports:payroll"},
                    {"name": "Financial Reports", "url": "reports:financial"},
                ],
            })

        if UserUtilities.check_user_permission(user, "manage_system_settings"):
            menu_items.append({
                "name": "Settings",
                "url": "accounts:settings",
                "icon": "fas fa-cog",
                "submenu": [
                    {"name": "System Settings", "url": "accounts:system_settings"},
                    {"name": "User Roles", "url": "accounts:roles"},
                    {"name": "Audit Logs", "url": "accounts:audit_logs"},
                ],
            })

        return menu_items


class ExcelUtilities:
    @staticmethod
    def export_users_to_excel(users_queryset, filename: str = None) -> bytes:
        import pandas as pd
        import io

        if not filename:
            filename = f"employees_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

        data = []
        for user in users_queryset.select_related('department', 'role', 'manager'):
            data.append({
                "Employee Code": user.employee_code,
                "Full Name": user.get_full_name(),
                "Email": user.email,
                "Phone": user.phone_number,
                "Department": user.department.name if user.department else "",
                "Role": user.role.display_name if user.role else "",
                "Job Title": user.job_title or "",
                "Hire Date": user.hire_date.strftime("%Y-%m-%d") if user.hire_date else "",
                "Status": user.get_status_display(),
                "Manager": user.manager.get_full_name() if user.manager else "",
                "Address": f"{user.address_line1 or ''} {user.address_line2 or ''}".strip(),
                "City": user.city or "",
                "State": user.state or "",
                "Country": user.country or "",
                "Emergency Contact": user.emergency_contact_name or "",
                "Emergency Phone": user.emergency_contact_phone or "",
                "Created Date": user.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            })

        df = pd.DataFrame(data)
        output = io.BytesIO()
        
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Employees", index=False)

        output.seek(0)
        return output.getvalue()

    @staticmethod
    def import_users_from_excel(file_content, created_by_user) -> Tuple[int, int, List[str]]:
        import pandas as pd
        import io

        try:
            df = pd.read_excel(io.BytesIO(file_content))

            required_columns = ["employee_code", "first_name", "last_name", "email"]
            missing_columns = [col for col in required_columns if col not in df.columns]

            if missing_columns:
                return 0, 0, [f"Missing required columns: {', '.join(missing_columns)}"]

            created_count = 0
            error_count = 0
            errors = []

            for index, row in df.iterrows():
                try:
                    row_data = row.to_dict()

                    is_valid, validation_errors = validate_employee_data(row_data)
                    if not is_valid:
                        error_count += 1
                        error_messages = []
                        for field, field_errors in validation_errors.items():
                            error_messages.extend(field_errors)
                        errors.append(f"Row {index + 2}: {'; '.join(error_messages)}")
                        continue

                    user_data = {
                        "employee_code": str(row_data["employee_code"]).upper(),
                        "first_name": str(row_data["first_name"]),
                        "last_name": str(row_data["last_name"]),
                        "email": str(row_data["email"]),
                        "phone_number": str(row_data.get("phone_number", "")),
                        "job_title": str(row_data.get("job_title", "")),
                        "created_by": created_by_user,
                    }

                    if pd.notna(row_data.get("middle_name")):
                        user_data["middle_name"] = str(row_data["middle_name"])

                    if pd.notna(row_data.get("date_of_birth")):
                        user_data["date_of_birth"] = pd.to_datetime(row_data["date_of_birth"]).date()

                    if pd.notna(row_data.get("hire_date")):
                        user_data["hire_date"] = pd.to_datetime(row_data["hire_date"]).date()

                    if pd.notna(row_data.get("gender")):
                        gender = str(row_data["gender"]).upper()
                        if gender in ["M", "F", "O"]:
                            user_data["gender"] = gender

                    temp_password = generate_secure_password()

                    user = User.objects.create_user(
                        username=user_data["employee_code"],
                        password=temp_password,
                        **user_data,
                    )

                    created_count += 1

                except Exception as e:
                    error_count += 1
                    errors.append(f"Row {index + 2}: {str(e)}")

            return created_count, error_count, errors

        except Exception as e:
            return 0, 0, [f"File processing error: {str(e)}"]

class SystemUtilities:
    @staticmethod
    def cleanup_expired_tokens():
        expired_tokens = PasswordResetToken.objects.filter(
            expires_at__lt=timezone.now(), is_used=False
        )
        count = expired_tokens.count()
        expired_tokens.update(is_used=True, used_at=timezone.now())
        return count

    @staticmethod
    def get_system_statistics() -> Dict:
        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True, status="ACTIVE").count()
        inactive_users = total_users - active_users

        recent_logins = AuditLog.objects.filter(
            action="LOGIN", timestamp__gte=timezone.now() - timedelta(days=30)
        ).count()

        failed_logins = AuditLog.objects.filter(
            action="LOGIN",
            description__icontains="failed",
            timestamp__gte=timezone.now() - timedelta(days=7),
        ).count()

        active_sessions = UserSession.objects.filter(is_active=True).count()

        return {
            "total_users": total_users,
            "active_users": active_users,
            "inactive_users": inactive_users,
            "recent_logins": recent_logins,
            "failed_logins": failed_logins,
            "active_sessions": active_sessions,
            "departments": Department.objects.filter(is_active=True).count(),
            "roles": User.objects.values("role").distinct().count(),
        }

    @staticmethod
    def send_bulk_notification(users_queryset, subject: str, message: str, sender_user):
        try:
            recipient_emails = list(users_queryset.values_list("email", flat=True))
            
            for email in recipient_emails:
                email_obj = EmailMultiAlternatives(
                    subject=subject,
                    body=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[email],
                )
                email_obj.send()

            log_user_activity(
                user=sender_user,
                action="BULK_NOTIFICATION",
                description=f"Sent notification to {len(recipient_emails)} users",
                request=None,
                additional_data={
                    "subject": subject,
                    "recipient_count": len(recipient_emails),
                },
            )

            return True, len(recipient_emails)
        except Exception as e:
            logger.error(f"Failed to send bulk notification: {e}")
            return False, 0

    @staticmethod
    def validate_file_upload(file, allowed_extensions: List[str], max_size_mb: int = 5) -> Tuple[bool, str]:
        if not file:
            return False, "No file provided"

        file_extension = file.name.split(".")[-1].lower()
        if file_extension not in allowed_extensions:
            return False, f"File type not allowed. Allowed types: {', '.join(allowed_extensions)}"

        max_size_bytes = max_size_mb * 1024 * 1024
        if file.size > max_size_bytes:
            return False, f"File size exceeds {max_size_mb}MB limit"

        return True, "File is valid"

    @staticmethod
    def generate_audit_report(start_date: datetime, end_date: datetime, user_filter: str = None) -> Dict:
        queryset = AuditLog.objects.filter(timestamp__gte=start_date, timestamp__lte=end_date)

        if user_filter:
            queryset = queryset.filter(
                Q(user__employee_code__icontains=user_filter)
                | Q(user__first_name__icontains=user_filter)
                | Q(user__last_name__icontains=user_filter)
            )

        total_activities = queryset.count()

        activity_breakdown = {}
        for action_choice in AuditLog.ACTION_TYPES:
            action_code = action_choice[0]
            action_name = action_choice[1]
            count = queryset.filter(action=action_code).count()
            if count > 0:
                activity_breakdown[action_name] = count

        top_users = (
            queryset.values("user__employee_code", "user__first_name", "user__last_name")
            .annotate(activity_count=Count("id"))
            .order_by("-activity_count")[:10]
        )

        return {
            "total_activities": total_activities,
            "date_range": {
                "start": start_date.strftime("%Y-%m-%d"),
                "end": end_date.strftime("%Y-%m-%d"),
            },
            "activity_breakdown": activity_breakdown,
            "top_users": list(top_users),
            "generated_at": timezone.now().isoformat(),
        }

    @staticmethod
    def mask_sensitive_data(data: str, mask_char: str = "*", visible_chars: int = 4) -> str:
        if not data or len(data) <= visible_chars:
            return mask_char * len(data) if data else ""

        return data[:visible_chars] + mask_char * (len(data) - visible_chars)

    @staticmethod
    def get_password_expiry_users(days_before_expiry: int = None) -> List[User]:
        if days_before_expiry is None:
            days_before_expiry = int(SystemConfiguration.get_setting('PASSWORD_EXPIRY_WARNING_DAYS', '7'))
            
        expiry_days = int(SystemConfiguration.get_setting("PASSWORD_EXPIRY_DAYS", "90"))
        cutoff_date = timezone.now() - timedelta(days=expiry_days - days_before_expiry)

        return User.objects.filter(
            is_active=True,
            password_changed_at__lte=cutoff_date,
            password_changed_at__isnull=False,
        )

    @staticmethod
    def send_password_expiry_notifications():
        users_to_notify = SystemUtilities.get_password_expiry_users()
        notification_count = 0

        for user in users_to_notify:
            try:
                warning_days = int(SystemConfiguration.get_setting('PASSWORD_EXPIRY_WARNING_DAYS', '7'))
                context = {
                    "user": user,
                    "days_until_expiry": warning_days,
                    "change_password_url": "/accounts/change-password/",
                    "company_name": SystemConfiguration.get_setting("COMPANY_NAME", "HR System"),
                }

                html_message = render_to_string("accounts/emails/password_expiry_warning.html", context)
                plain_message = strip_tags(html_message)

                email = EmailMultiAlternatives(
                    subject="Password Expiry Warning",
                    body=plain_message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[user.email],
                )
                email.attach_alternative(html_message, "text/html")
                email.send()
                notification_count += 1

            except Exception as e:
                logger.error(f"Failed to send password expiry notification to {user.email}: {e}")

        return notification_count
