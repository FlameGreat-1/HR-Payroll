from django.db.models.signals import post_save, pre_save, post_delete
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import transaction
from django.conf import settings
from .models import Department, Role, AuditLog, UserSession, SystemConfiguration
from .utils import log_user_activity, get_client_ip, get_user_agent, create_user_session
import logging
import hashlib
from datetime import timedelta

User = get_user_model()
logger = logging.getLogger(__name__)


class SignalHandlerMixin:
    @staticmethod
    def get_request_ip(request=None):
        return get_client_ip(request) if request else '127.0.0.1'
    
    @staticmethod
    def log_audit_action(user, action, description, request=None, additional_data=None):
        log_user_activity(
            user=user,
            action=action,
            description=description,
            request=request,
            additional_data=additional_data or {}
        )
    
    @staticmethod
    def queue_notification_email(recipients, subject, message, priority='normal'):
        try:
            from django.core.mail import send_mail
            if settings.DEBUG:
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=recipients,
                    fail_silently=True
                )
            else:
                pass
        except Exception as e:
            logger.error(f"Failed to queue email notification: {e}")


class UserSignalHandler(SignalHandlerMixin):
    @staticmethod
    def handle_user_creation(instance, request_ip='127.0.0.1'):
        try:
            if instance.created_by:
                UserSignalHandler.log_audit_action(
                    user=instance.created_by,
                    action='USER_CREATED',
                    description=f'Created new user: {instance.employee_code} - {instance.get_full_name()}',
                    additional_data={
                        'new_user_id': instance.id,
                        'employee_code': instance.employee_code,
                        'department': instance.department.name if instance.department else None,
                        'role': instance.role.name if instance.role else None
                    }
                )
            
            UserSignalHandler.send_manager_notification(instance)
            UserSignalHandler.send_hr_admin_notification(instance)
            
        except Exception as e:
            logger.error(f"Error handling user creation for {instance.employee_code}: {e}")
    
    @staticmethod
    def send_manager_notification(instance):
        if instance.department and instance.department.manager:
            try:
                manager = instance.department.manager
                subject = f'New Employee Added to {instance.department.name}'
                message = f'''A new employee has been added to your department:
                
Name: {instance.get_full_name()}
Employee Code: {instance.employee_code}
Email: {instance.email}
Job Title: {instance.job_title or 'Not specified'}
Hire Date: {instance.hire_date or 'Not specified'}

Please ensure proper onboarding and orientation.'''
                
                UserSignalHandler.queue_notification_email(
                    recipients=[manager.email],
                    subject=subject,
                    message=message
                )
            except Exception as e:
                logger.error(f"Failed to send manager notification: {e}")
    
    @staticmethod
    def send_hr_admin_notification(instance):
        if instance.role and instance.role.name in ['HR_ADMIN', 'SUPER_ADMIN']:
            try:
                hr_admins = User.objects.filter(
                    role__name__in=['HR_ADMIN', 'SUPER_ADMIN'],
                    is_active=True
                ).exclude(id=instance.id).values_list('email', flat=True)
                
                if hr_admins:
                    subject = 'New HR Admin User Created'
                    message = f'''A new HR Admin user has been created:
                    
Name: {instance.get_full_name()}
Employee Code: {instance.employee_code}
Email: {instance.email}
Role: {instance.role.display_name}

Please review the user permissions and access levels.'''
                    
                    UserSignalHandler.queue_notification_email(
                        recipients=list(hr_admins),
                        subject=subject,
                        message=message,
                        priority='high'
                    )
            except Exception as e:
                logger.error(f"Failed to send HR admin notification: {e}")
    
    @staticmethod
    def handle_user_update(instance):
        try:
            changes = UserSignalHandler.detect_user_changes(instance)
            if changes:
                UserSignalHandler.log_audit_action(
                    user=instance,
                    action='PROFILE_UPDATE',
                    description=f'Profile updated: {"; ".join(changes)}',
                    additional_data={
                        'changes': changes,
                        'employee_code': instance.employee_code
                    }
                )
                
                if instance.status in ['SUSPENDED', 'TERMINATED']:
                    UserSignalHandler.handle_user_deactivation(instance)
                    
        except Exception as e:
            logger.error(f"Error handling user update for {instance.employee_code}: {e}")
    
    @staticmethod
    def detect_user_changes(instance):
        changes = []
        try:
            if hasattr(instance, '_original_values'):
                original = instance._original_values
                
                if original.get('status') != instance.status:
                    changes.append(f'Status: {original.get("status")} → {instance.status}')
                
                if original.get('department_id') != getattr(instance.department, 'id', None):
                    old_dept = original.get('department_name', 'None')
                    new_dept = instance.department.name if instance.department else 'None'
                    changes.append(f'Department: {old_dept} → {new_dept}')
                
                if original.get('role_id') != getattr(instance.role, 'id', None):
                    old_role = original.get('role_name', 'None')
                    new_role = instance.role.display_name if instance.role else 'None'
                    changes.append(f'Role: {old_role} → {new_role}')
                
                if original.get('manager_id') != getattr(instance.manager, 'id', None):
                    old_manager = original.get('manager_name', 'None')
                    new_manager = instance.manager.get_full_name() if instance.manager else 'None'
                    changes.append(f'Manager: {old_manager} → {new_manager}')
                
                if original.get('job_title') != instance.job_title:
                    changes.append(f'Job Title: {original.get("job_title") or "None"} → {instance.job_title or "None"}')
        
        except Exception as e:
            logger.error(f"Error detecting user changes: {e}")
        
        return changes
    
    @staticmethod
    def handle_user_deactivation(instance):
        try:
            from .utils import terminate_user_sessions
            terminate_user_sessions(instance)
            
            if instance.status == 'TERMINATED':
                subject = 'Account Terminated'
                message = f'''Your employment account has been terminated.

Employee Code: {instance.employee_code}
Termination Date: {timezone.now().date()}

Please contact HR for any questions.'''
                
                UserSignalHandler.queue_notification_email(
                    recipients=[instance.email],
                    subject=subject,
                    message=message,
                    priority='high'
                )
        except Exception as e:
            logger.error(f"Error handling user deactivation: {e}")


@receiver(pre_save, sender=User)
def user_pre_save_handler(sender, instance, **kwargs):
    try:
        if instance.pk:
            try:
                original = User.objects.select_related('department', 'role', 'manager').get(pk=instance.pk)
                instance._original_values = {
                    'status': original.status,
                    'department_id': original.department.id if original.department else None,
                    'department_name': original.department.name if original.department else None,
                    'role_id': original.role.id if original.role else None,
                    'role_name': original.role.display_name if original.role else None,
                    'manager_id': original.manager.id if original.manager else None,
                    'manager_name': original.manager.get_full_name() if original.manager else None,
                    'job_title': original.job_title,
                    'password_hash': original.password
                }
                
                if original.password != instance.password and instance.password:
                    instance.password_changed_at = timezone.now()
                    instance.must_change_password = False
                    instance.failed_login_attempts = 0
                    instance.account_locked_until = None
                    
            except User.DoesNotExist:
                pass
        
        if instance.employee_code:
            instance.employee_code = instance.employee_code.upper()
            instance.username = instance.employee_code
            
    except Exception as e:
        logger.error(f"Error in user_pre_save_handler: {e}")


@receiver(post_save, sender=User)
def user_post_save_handler(sender, instance, created, **kwargs):
    try:
        if created:
            UserSignalHandler.handle_user_creation(instance)
        else:
            UserSignalHandler.handle_user_update(instance)
    except Exception as e:
        logger.error(f"Error in user_post_save_handler: {e}")


@receiver(post_delete, sender=User)
def user_post_delete_handler(sender, instance, **kwargs):
    try:
        with transaction.atomic():
            SignalHandlerMixin.log_audit_action(
                user=None,
                action='USER_DELETED',
                description=f'User deleted: {instance.employee_code} - {instance.get_full_name()}',
                additional_data={
                    'deleted_user_id': instance.id,
                    'employee_code': instance.employee_code,
                    'email': instance.email,
                    'department': instance.department.name if instance.department else None,
                    'role': instance.role.name if instance.role else None
                }
            )
            
            User.objects.filter(manager=instance).update(manager=None)
            Department.objects.filter(manager=instance).update(manager=None)
            
    except Exception as e:
        logger.error(f"Error in user_post_delete_handler: {e}")


@receiver(post_save, sender=Department)
def department_post_save_handler(sender, instance, created, **kwargs):
    try:
        action = 'DEPARTMENT_CREATED' if created else 'DEPARTMENT_UPDATED'
        
        SignalHandlerMixin.log_audit_action(
            user=instance.created_by,
            action=action,
            description=f'{action.replace("_", " ").title()}: {instance.code} - {instance.name}',
            additional_data={
                'department_id': instance.id,
                'department_code': instance.code,
                'department_name': instance.name,
                'manager': instance.manager.get_full_name() if instance.manager else None
            }
        )
    except Exception as e:
        logger.error(f"Error in department_post_save_handler: {e}")


@receiver(post_delete, sender=Department)
def department_post_delete_handler(sender, instance, **kwargs):
    try:
        with transaction.atomic():
            SignalHandlerMixin.log_audit_action(
                user=None,
                action='DEPARTMENT_DELETED',
                description=f'Department deleted: {instance.code} - {instance.name}',
                additional_data={
                    'deleted_department_id': instance.id,
                    'department_code': instance.code,
                    'department_name': instance.name
                }
            )
            
            User.objects.filter(department=instance).update(department=None)
            
    except Exception as e:
        logger.error(f"Error in department_post_delete_handler: {e}")


@receiver(post_save, sender=Role)
def role_post_save_handler(sender, instance, created, **kwargs):
    try:
        action = 'ROLE_CREATED' if created else 'ROLE_UPDATED'
        
        SignalHandlerMixin.log_audit_action(
            user=None,
            action=action,
            description=f'{action.replace("_", " ").title()}: {instance.name} - {instance.display_name}',
            additional_data={
                'role_id': instance.id,
                'role_name': instance.name,
                'display_name': instance.display_name,
                'permissions': list(instance.permissions.values_list('codename', flat=True))
            }
        )
        
        if not created:
            affected_users = User.objects.filter(role=instance, is_active=True)
            for user in affected_users:
                SignalHandlerMixin.log_audit_action(
                    user=user,
                    action='PERMISSION_CHANGE',
                    description=f'Permissions changed due to role update: {instance.display_name}',
                    additional_data={
                        'role_id': instance.id,
                        'role_name': instance.name
                    }
                )
    except Exception as e:
        logger.error(f"Error in role_post_save_handler: {e}")


@receiver(post_delete, sender=Role)
def role_post_delete_handler(sender, instance, **kwargs):
    try:
        with transaction.atomic():
            SignalHandlerMixin.log_audit_action(
                user=None,
                action='ROLE_DELETED',
                description=f'Role deleted: {instance.name} - {instance.display_name}',
                additional_data={
                    'deleted_role_id': instance.id,
                    'role_name': instance.name,
                    'display_name': instance.display_name
                }
            )
            
            User.objects.filter(role=instance).update(role=None)
            
    except Exception as e:
        logger.error(f"Error in role_post_delete_handler: {e}")


class AuthenticationSignalHandler(SignalHandlerMixin):
    @staticmethod
    def handle_successful_login(user, request):
        try:
            user_session = create_user_session(user, request)
            
            AuthenticationSignalHandler.log_audit_action(
                user=user,
                action='LOGIN',
                description=f'User logged in successfully from {get_client_ip(request)}',
                request=request,
                additional_data={
                    'session_id': str(user_session.id),
                    'user_agent': get_user_agent(request),
                    'login_method': 'web'
                }
            )
            
            user.last_login = timezone.now()
            user.save(update_fields=['last_login'])
            
            AuthenticationSignalHandler.check_password_status(user, request)
            AuthenticationSignalHandler.manage_concurrent_sessions(user, user_session, request)
            AuthenticationSignalHandler.send_admin_login_alert(user, request)
            
        except Exception as e:
            logger.error(f"Error handling successful login for {user.employee_code}: {e}")
    
    @staticmethod
    def check_password_status(user, request):
        try:
            if user.must_change_password:
                AuthenticationSignalHandler.log_audit_action(
                    user=user,
                    action='PASSWORD_CHANGE_REQUIRED',
                    description='User must change password on next login',
                    request=request
                )
            
            if user.is_password_expired():
                AuthenticationSignalHandler.log_audit_action(
                    user=user,
                    action='PASSWORD_EXPIRED',
                    description='User password has expired',
                    request=request
                )
        except Exception as e:
            logger.error(f"Error checking password status: {e}")
    
    @staticmethod
    def manage_concurrent_sessions(user, current_session, request):
        try:
            max_sessions = int(SystemConfiguration.get_setting('MAX_CONCURRENT_SESSIONS', '3'))
            
            active_sessions = UserSession.objects.filter(
                user=user,
                is_active=True
            ).exclude(id=current_session.id).order_by('login_time')
            
            if active_sessions.count() >= max_sessions:
                sessions_to_terminate = active_sessions[:active_sessions.count() - max_sessions + 1]
                terminated_count = 0
                
                for session in sessions_to_terminate:
                    session.terminate_session()
                    terminated_count += 1
                
                AuthenticationSignalHandler.log_audit_action(
                    user=user,
                    action='SESSION_LIMIT_EXCEEDED',
                    description=f'Terminated {terminated_count} old sessions due to concurrent session limit',
                    request=request,
                    additional_data={
                        'terminated_sessions': terminated_count,
                        'max_sessions': max_sessions
                    }
                )
        except Exception as e:
            logger.error(f"Error managing concurrent sessions: {e}")
    
    @staticmethod
    def send_admin_login_alert(user, request):
        try:
            if user.role and user.role.name in ['SUPER_ADMIN', 'HR_ADMIN']:
                admin_emails = User.objects.filter(
                    role__name__in=['SUPER_ADMIN', 'HR_ADMIN'],
                    is_active=True
                ).exclude(id=user.id).values_list('email', flat=True)
                
                if admin_emails:
                    subject = 'Admin User Login Alert'
                    message = f'''An admin user has logged into the system:

User: {user.get_full_name()} ({user.employee_code})
Role: {user.role.display_name}
Login Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}
IP Address: {get_client_ip(request)}

This is an automated security notification.'''
                    
                    AuthenticationSignalHandler.queue_notification_email(
                        recipients=list(admin_emails),
                        subject=subject,
                        message=message,
                        priority='high'
                    )
        except Exception as e:
            logger.error(f"Error sending admin login alert: {e}")
    
    @staticmethod
    def handle_logout(user, request):
        try:
            session_key = request.session.session_key
            if session_key:
                session_hash = hashlib.sha256(session_key.encode()).hexdigest()
                try:
                    user_session = UserSession.objects.get(
                        user=user,
                        session_key_hash=session_hash,
                        is_active=True
                    )
                    user_session.terminate_session()
                except UserSession.DoesNotExist:
                    pass
            
            AuthenticationSignalHandler.log_audit_action(
                user=user,
                action='LOGOUT',
                description=f'User logged out from {get_client_ip(request)}',
                request=request,
                additional_data={
                    'logout_method': 'manual',
                    'user_agent': get_user_agent(request)
                }
            )
        except Exception as e:
            logger.error(f"Error handling logout: {e}")
    
    @staticmethod
    def handle_failed_login(credentials, request):
        try:
            employee_code = credentials.get('username', '')
            ip_address = get_client_ip(request)
            
            try:
                user = User.objects.get(employee_code=employee_code.upper())
                
                AuthenticationSignalHandler.log_audit_action(
                    user=user,
                    action='LOGIN_FAILED',
                    description=f'Failed login attempt from {ip_address}',
                    request=request,
                    additional_data={
                        'attempted_username': employee_code,
                        'user_agent': get_user_agent(request),
                        'failure_reason': 'invalid_credentials'
                    }
                )
                
                AuthenticationSignalHandler.check_failed_login_threshold(user, ip_address)
                
            except User.DoesNotExist:
                AuditLog.log_action(
                    user=None,
                    action='LOGIN_FAILED',
                    description=f'Failed login attempt with invalid username: {employee_code} from {ip_address}',
                    ip_address=ip_address,
                    user_agent=get_user_agent(request),
                    additional_data={
                        'attempted_username': employee_code,
                        'failure_reason': 'user_not_found'
                    }
                )
        except Exception as e:
            logger.error(f"Error handling failed login: {e}")
    
    @staticmethod
    def check_failed_login_threshold(user, ip_address):
        try:
            threshold = int(SystemConfiguration.get_setting('FAILED_LOGIN_ALERT_THRESHOLD', '4'))
            
            if user.failed_login_attempts >= threshold:
                admin_emails = User.objects.filter(
                    role__name__in=['SUPER_ADMIN', 'HR_ADMIN'],
                    is_active=True
                ).values_list('email', flat=True)
                
                if admin_emails:
                    subject = 'Multiple Failed Login Attempts Alert'
                    message = f'''Multiple failed login attempts detected:

User: {user.get_full_name()} ({user.employee_code})
Failed Attempts: {user.failed_login_attempts + 1}
IP Address: {ip_address}
Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}

The account will be locked after 5 failed attempts.'''
                    
                    AuthenticationSignalHandler.queue_notification_email(
                        recipients=list(admin_emails),
                        subject=subject,
                        message=message,
                        priority='high'
                    )
        except Exception as e:
            logger.error(f"Error checking failed login threshold: {e}")


class SecurityMonitoringHandler(SignalHandlerMixin):
    @staticmethod
    def monitor_security_events(instance):
        try:
            if instance.action in ['LOGIN_FAILED', 'ACCOUNT_LOCK', 'PERMISSION_CHANGE']:
                threshold = int(SystemConfiguration.get_setting('SECURITY_ALERT_THRESHOLD', '10'))
                
                recent_events = AuditLog.objects.filter(
                    action__in=['LOGIN_FAILED', 'ACCOUNT_LOCK', 'PERMISSION_CHANGE'],
                    timestamp__gte=timezone.now() - timedelta(hours=1)
                ).count()
                
                if recent_events >= threshold:
                    SecurityMonitoringHandler.send_security_alert(instance, recent_events, threshold)
                    
        except Exception as e:
            logger.error(f"Error monitoring security events: {e}")
    
    @staticmethod
    def send_security_alert(instance, event_count, threshold):
        try:
            admin_emails = User.objects.filter(
                role__name__in=['SUPER_ADMIN', 'HR_ADMIN'],
                is_active=True
            ).values_list('email', flat=True)
            
            if admin_emails:
                subject = 'Security Alert: High Activity Detected'
                message = f'''High security-related activity detected in the last hour:

Total Events: {event_count}
Threshold: {threshold}
Latest Event: {instance.get_action_display()}
Time: {instance.timestamp.strftime('%Y-%m-%d %H:%M:%S')}

Please review the audit logs for potential security issues.'''
                
                SecurityMonitoringHandler.queue_notification_email(
                    recipients=list(admin_emails),
                    subject=subject,
                    message=message,
                    priority='critical'
                )
        except Exception as e:
            logger.error(f"Error sending security alert: {e}")


@receiver(user_logged_in)
def user_logged_in_handler(sender, request, user, **kwargs):
    AuthenticationSignalHandler.handle_successful_login(user, request)


@receiver(user_logged_out)
def user_logged_out_handler(sender, request, user, **kwargs):
    if user:
        AuthenticationSignalHandler.handle_logout(user, request)


@receiver(user_login_failed)
def user_login_failed_handler(sender, credentials, request, **kwargs):
    AuthenticationSignalHandler.handle_failed_login(credentials, request)


@receiver(post_save, sender=SystemConfiguration)
def system_configuration_post_save_handler(sender, instance, created, **kwargs):
    try:
        action = 'SYSTEM_CONFIG_CREATED' if created else 'SYSTEM_CONFIG_UPDATED'
        
        SignalHandlerMixin.log_audit_action(
            user=instance.updated_by,
            action=action,
            description=f'System configuration {action.lower().replace("_", " ")}: {instance.key}',
            additional_data={
                'config_key': instance.key,
                'config_value': instance.value,
                'description': instance.description
            }
        )
        
        security_keys = [
            'PASSWORD_EXPIRY_DAYS', 'MAX_LOGIN_ATTEMPTS', 'SESSION_TIMEOUT',
            'MAX_CONCURRENT_SESSIONS', 'SECURITY_ALERT_THRESHOLD'
        ]
        
        if instance.key in security_keys:
            admin_emails = User.objects.filter(
                role__name__in=['SUPER_ADMIN', 'HR_ADMIN'],
                is_active=True
            ).values_list('email', flat=True)
            
            if admin_emails:
                subject = 'Security Configuration Changed'
                message = f'''A security-related system configuration has been changed:

Setting: {instance.key}
New Value: {instance.value}
Changed By: {instance.updated_by.get_full_name() if instance.updated_by else 'System'}
Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}

Please review this change for security implications.'''
                
                SignalHandlerMixin.queue_notification_email(
                    recipients=list(admin_emails),
                    subject=subject,
                    message=message,
                    priority='high'
                )
    except Exception as e:
        logger.error(f"Error in system_configuration_post_save_handler: {e}")


@receiver(post_save, sender=AuditLog)
def audit_log_post_save_handler(sender, instance, created, **kwargs):
    if created:
        SecurityMonitoringHandler.monitor_security_events(instance)


def cleanup_old_audit_logs():
    try:
        retention_days = int(SystemConfiguration.get_setting('AUDIT_LOG_RETENTION_DAYS', '365'))
        cutoff_date = timezone.now() - timedelta(days=retention_days)
        
        old_logs = AuditLog.objects.filter(timestamp__lt=cutoff_date)
        deleted_count = old_logs.count()
        
        if deleted_count > 0:
            old_logs.delete()
            
            AuditLog.log_action(
                user=None,
                action='SYSTEM_MAINTENANCE',
                description=f'Cleaned up {deleted_count} old audit log entries',
                ip_address='127.0.0.1',
                additional_data={
                    'deleted_count': deleted_count,
                    'retention_days': retention_days
                }
            )
        
        return deleted_count
    except Exception as e:
        logger.error(f"Error in cleanup_old_audit_logs: {e}")
        return 0


def cleanup_expired_sessions():
    try:
        from .utils import cleanup_expired_sessions as utils_cleanup
        return utils_cleanup()
    except Exception as e:
        logger.error(f"Error in cleanup_expired_sessions: {e}")
        return 0


def send_password_expiry_reminders():
    try:
        from .utils import SystemUtilities
        return SystemUtilities.send_password_expiry_notifications()
    except Exception as e:
        logger.error(f"Error in send_password_expiry_reminders: {e}")
        return 0


def generate_daily_security_report():
    try:
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        
        start_time = timezone.datetime.combine(yesterday, timezone.datetime.min.time())
        end_time = timezone.datetime.combine(today, timezone.datetime.min.time())
        
        daily_stats = {
            'date': yesterday.strftime('%Y-%m-%d'),
            'total_logins': AuditLog.objects.filter(
                action='LOGIN',
                timestamp__gte=start_time,
                timestamp__lt=end_time
            ).count(),
            'failed_logins': AuditLog.objects.filter(
                action='LOGIN_FAILED',
                timestamp__gte=start_time,
                timestamp__lt=end_time
            ).count(),
            'account_locks': AuditLog.objects.filter(
                action='ACCOUNT_LOCK',
                timestamp__gte=start_time,
                timestamp__lt=end_time
            ).count(),
            'password_changes': AuditLog.objects.filter(
                action='PASSWORD_CHANGE',
                timestamp__gte=start_time,
                timestamp__lt=end_time
            ).count(),
            'new_users': AuditLog.objects.filter(
                action='USER_CREATED',
                timestamp__gte=start_time,
                timestamp__lt=end_time
            ).count(),
            'active_sessions': UserSession.objects.filter(is_active=True).count()
        }
        
        admin_emails = User.objects.filter(
            role__name__in=['SUPER_ADMIN', 'HR_ADMIN'],
            is_active=True
        ).values_list('email', flat=True)
        
        if admin_emails:
            subject = f'Daily Security Report - {yesterday.strftime("%Y-%m-%d")}'
            message = f'''Daily Security Report for {yesterday.strftime("%Y-%m-%d")}:

Total Logins: {daily_stats['total_logins']}
Failed Logins: {daily_stats['failed_logins']}
Account Locks: {daily_stats['account_locks']}
Password Changes: {daily_stats['password_changes']}
New Users: {daily_stats['new_users']}
Active Sessions: {daily_stats['active_sessions']}

Please review any unusual activity in the audit logs.'''
            
            SignalHandlerMixin.queue_notification_email(
                recipients=list(admin_emails),
                subject=subject,
                message=message
            )
        
        AuditLog.log_action(
            user=None,
            action='SECURITY_REPORT_GENERATED',
            description=f'Daily security report generated for {yesterday.strftime("%Y-%m-%d")}',
            ip_address='127.0.0.1',
            additional_data=daily_stats
        )
        
        return daily_stats
    except Exception as e:
        logger.error(f"Error generating daily security report: {e}")
        return {}
