from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, Http404
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.views import View
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.db import transaction
import json
from datetime import datetime, timedelta

from .models import Department, Role, UserSession, AuditLog, SystemConfiguration, PasswordResetToken
from .forms import (
    CustomLoginForm, EmployeeRegistrationForm, EmployeeUpdateForm,
    CustomPasswordChangeForm, CustomPasswordResetForm, CustomSetPasswordForm,
    DepartmentForm, RoleForm, ProfileUpdateForm, BulkEmployeeUploadForm,
    UserSearchForm, SystemConfigurationForm
)
from .permissions import (
    SuperAdminRequiredMixin, HRAdminRequiredMixin, HRManagerRequiredMixin,
    DepartmentManagerRequiredMixin, PayrollManagerRequiredMixin,
    EmployeeAccessMixin, DepartmentAccessMixin, role_required,
    permission_required, employee_access_required, account_not_locked_required,
    password_change_required
)
from .utils import (
    log_user_activity, get_client_ip, get_user_agent, generate_secure_password,
    send_welcome_email, create_password_reset_token, send_password_reset_email,
    validate_employee_data, search_users, get_user_dashboard_data,
    UserUtilities, ExcelUtilities, SystemUtilities
)

User = get_user_model()


class BaseViewMixin:
    def get_client_context(self, request):
        return {
            'ip_address': get_client_ip(request),
            'user_agent': get_user_agent(request)
        }
    
    def log_activity(self, user, action, description, request, additional_data=None):
        log_user_activity(
            user=user,
            action=action,
            description=description,
            request=request,
            additional_data=additional_data or {}
        )


class LoginView(BaseViewMixin, View):
    template_name = 'accounts/login.html'
    form_class = CustomLoginForm

    def get(self, request):
        if request.user.is_authenticated:
            if request.user.must_change_password:
                return redirect('accounts:change_password')
            return redirect('accounts:dashboard')
        
        form = self.form_class()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = self.form_class(request, data=request.POST)
        
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            
            self.log_activity(
                user=user,
                action='LOGIN',
                description='User logged in successfully',
                request=request
            )
            
            remember_me = form.cleaned_data.get('remember_me')
            if remember_me:
                request.session.set_expiry(1209600)
            else:
                request.session.set_expiry(0)
            
            next_url = request.GET.get('next')
            if next_url:
                return redirect(next_url)
            
            if user.must_change_password:
                return redirect('accounts:change_password')
            
            return redirect('accounts:dashboard')
        
        return render(request, self.template_name, {'form': form})


@method_decorator([login_required, account_not_locked_required, password_change_required], name='dispatch')
class DashboardView(BaseViewMixin, LoginRequiredMixin, View):
    template_name = 'accounts/dashboard.html'

    def get(self, request):
        dashboard_data = get_user_dashboard_data(request.user)
        
        context = {
            'dashboard_data': dashboard_data,
            'user': request.user,
            'recent_activities': dashboard_data['recent_activities'][:5],
            'system_alerts': dashboard_data['system_alerts']
        }
        
        return render(request, self.template_name, context)


class LogoutView(BaseViewMixin, View):
    def post(self, request):
        if request.user.is_authenticated:
            self.log_activity(
                user=request.user,
                action='LOGOUT',
                description='User logged out',
                request=request
            )
        
        logout(request)
        messages.success(request, 'You have been logged out successfully.')
        return redirect('accounts:login')


@method_decorator([login_required, account_not_locked_required], name='dispatch')
class ChangePasswordView(BaseViewMixin, LoginRequiredMixin, View):
    template_name = 'accounts/change_password.html'
    form_class = CustomPasswordChangeForm

    def get(self, request):
        form = self.form_class(user=request.user)
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = self.form_class(user=request.user, data=request.POST)
        
        if form.is_valid():
            form.save()
            
            self.log_activity(
                user=request.user,
                action='PASSWORD_CHANGE',
                description='Password changed successfully',
                request=request
            )
            
            messages.success(request, 'Your password has been changed successfully.')
            return redirect('accounts:dashboard')
        
        return render(request, self.template_name, {'form': form})


class PasswordResetView(BaseViewMixin, View):
    template_name = 'accounts/password_reset.html'
    form_class = CustomPasswordResetForm

    def get(self, request):
        form = self.form_class()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = self.form_class(request.POST)
        
        if form.is_valid():
            try:
                email = form.cleaned_data['email']
                user = User.objects.get(email=email, is_active=True)
                
                token = create_password_reset_token(user, request)
                
                if send_password_reset_email(user, token, request):
                    messages.success(request, 'Password reset instructions have been sent to your email.')
                else:
                    messages.error(request, 'Failed to send password reset email. Please try again.')
                
            except User.DoesNotExist:
                messages.success(request, 'If an account with this email exists, password reset instructions have been sent.')
            
            return redirect('accounts:login')
        
        return render(request, self.template_name, {'form': form})


class PasswordResetConfirmView(BaseViewMixin, View):
    template_name = 'accounts/password_reset_confirm.html'
    form_class = CustomSetPasswordForm

    def get(self, request, token):
        try:
            reset_token = get_object_or_404(PasswordResetToken, token=token)
            
            if not reset_token.is_valid():
                messages.error(request, 'This password reset link is invalid or has expired.')
                return redirect('accounts:password_reset')
            
            form = self.form_class(user=reset_token.user)
            return render(request, self.template_name, {'form': form, 'token': token})
        
        except (PasswordResetToken.DoesNotExist, Http404):
            messages.error(request, 'Invalid password reset link.')
            return redirect('accounts:password_reset')

    def post(self, request, token):
        try:
            reset_token = get_object_or_404(PasswordResetToken, token=token)
            
            if not reset_token.is_valid():
                messages.error(request, 'This password reset link is invalid or has expired.')
                return redirect('accounts:password_reset')
            
            form = self.form_class(user=reset_token.user, data=request.POST)
            
            if form.is_valid():
                form.save()
                reset_token.use_token()
                
                self.log_activity(
                    user=reset_token.user,
                    action='PASSWORD_RESET',
                    description='Password reset completed',
                    request=request
                )
                
                messages.success(request, 'Your password has been reset successfully. You can now log in.')
                return redirect('accounts:login')
            
            return render(request, self.template_name, {'form': form, 'token': token})
        
        except (PasswordResetToken.DoesNotExist, Http404):
            messages.error(request, 'Invalid password reset link.')
            return redirect('accounts:password_reset')


@method_decorator([login_required, account_not_locked_required, password_change_required], name='dispatch')
class ProfileView(BaseViewMixin, LoginRequiredMixin, View):
    template_name = 'accounts/profile.html'

    def get(self, request):
        context = {
            'user': request.user,
            'department': request.user.department,
            'role': request.user.role,
            'manager': request.user.manager
        }
        return render(request, self.template_name, context)


@method_decorator([login_required, account_not_locked_required, password_change_required], name='dispatch')
class ProfileUpdateView(BaseViewMixin, LoginRequiredMixin, View):
    template_name = 'accounts/profile_update.html'
    form_class = ProfileUpdateForm

    def get(self, request):
        form = self.form_class(instance=request.user)
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = self.form_class(request.POST, instance=request.user)
        
        if form.is_valid():
            form.save()
            
            self.log_activity(
                user=request.user,
                action='PROFILE_UPDATE',
                description='Profile updated',
                request=request
            )
            
            messages.success(request, 'Your profile has been updated successfully.')
            return redirect('accounts:profile')
        
        return render(request, self.template_name, {'form': form})


@method_decorator([login_required, account_not_locked_required, password_change_required], name='dispatch')
class EmployeeListView(BaseViewMixin, HRManagerRequiredMixin, ListView):
    model = User
    template_name = 'accounts/employee_list.html'
    context_object_name = 'employees'
    paginate_by = 20

    def get_queryset(self):
        queryset = search_users(
            query=self.request.GET.get('search', ''),
            department_id=self.request.GET.get('department'),
            role_id=self.request.GET.get('role'),
            status=self.request.GET.get('status'),
            current_user=self.request.user
        )
        return queryset.select_related('department', 'role', 'manager')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_form'] = UserSearchForm(self.request.GET)
        context['departments'] = Department.active.all()
        context['roles'] = Role.active.all()
        return context


@method_decorator([login_required, account_not_locked_required, password_change_required], name='dispatch')
class EmployeeDetailView(BaseViewMixin, LoginRequiredMixin, DetailView):
    model = User
    template_name = 'accounts/employee_detail.html'
    context_object_name = 'employee'

    @method_decorator(employee_access_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return User.objects.select_related('department', 'role', 'manager')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = self.get_object()
        
        context['subordinates'] = employee.subordinates.filter(is_active=True).select_related('department', 'role')
        context['recent_activities'] = AuditLog.objects.filter(
            user=employee
        ).select_related('user').order_by('-timestamp')[:10]
        
        return context


@method_decorator([login_required, account_not_locked_required, password_change_required], name='dispatch')
class EmployeeCreateView(BaseViewMixin, HRAdminRequiredMixin, CreateView):
    model = User
    form_class = EmployeeRegistrationForm
    template_name = 'accounts/employee_create.html'
    success_url = reverse_lazy('accounts:employee_list')

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save(commit=False)
            user.created_by = self.request.user
            user.save()
            
            temp_password = generate_secure_password()
            user.set_password(temp_password)
            user.save()
            
            try:
                send_welcome_email(user, temp_password, self.request)
            except Exception as e:
                messages.warning(self.request, f'Employee created but welcome email failed: {str(e)}')
            
            self.log_activity(
                user=self.request.user,
                action='USER_CREATED',
                description=f'Created new employee: {user.employee_code}',
                request=self.request,
                additional_data={'new_user_id': user.id}
            )
            
            messages.success(self.request, f'Employee {user.employee_code} created successfully.')
            return super().form_valid(form)


@method_decorator([login_required, account_not_locked_required, password_change_required], name='dispatch')
class EmployeeUpdateView(BaseViewMixin, HRManagerRequiredMixin, UpdateView):
    model = User
    form_class = EmployeeUpdateForm
    template_name = 'accounts/employee_update.html'

    @method_decorator(employee_access_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return User.objects.select_related('department', 'role', 'manager')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['current_user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        self.log_activity(
            user=self.request.user,
            action='USER_UPDATED',
            description=f'Updated employee: {self.object.employee_code}',
            request=self.request,
            additional_data={'updated_user_id': self.object.id}
        )
        
        messages.success(self.request, 'Employee updated successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('accounts:employee_detail', kwargs={'pk': self.object.pk})


@method_decorator([login_required, account_not_locked_required, password_change_required], name='dispatch')
class DepartmentListView(BaseViewMixin, HRManagerRequiredMixin, ListView):
    model = Department
    template_name = 'accounts/department_list.html'
    context_object_name = 'departments'
    paginate_by = 20

    def get_queryset(self):
        access_mixin = DepartmentAccessMixin()
        return access_mixin.get_accessible_departments(self.request.user).select_related('manager', 'parent_department')


@method_decorator([login_required, account_not_locked_required, password_change_required], name='dispatch')
class DepartmentCreateView(BaseViewMixin, HRAdminRequiredMixin, CreateView):
    model = Department
    form_class = DepartmentForm
    template_name = 'accounts/department_create.html'
    success_url = reverse_lazy('accounts:department_list')

    def form_valid(self, form):
        department = form.save(commit=False)
        department.created_by = self.request.user
        department.save()
        
        self.log_activity(
            user=self.request.user,
            action='DEPARTMENT_CREATED',
            description=f'Created new department: {department.code}',
            request=self.request,
            additional_data={'department_id': department.id}
        )
        
        messages.success(self.request, f'Department {department.name} created successfully.')
        return super().form_valid(form)


class RoleListView(BaseViewMixin, SuperAdminRequiredMixin, ListView):
    model = Role
    template_name = 'accounts/role_list.html'
    context_object_name = 'roles'
    paginate_by = 20

    def get_queryset(self):
        return Role.active.all().order_by('display_name')


class RoleCreateView(BaseViewMixin, SuperAdminRequiredMixin, CreateView):
    model = Role
    form_class = RoleForm
    template_name = 'accounts/role_create.html'
    success_url = reverse_lazy('accounts:role_list')

    def form_valid(self, form):
        self.log_activity(
            user=self.request.user,
            action='ROLE_CREATED',
            description=f'Created new role: {form.instance.name}',
            request=self.request,
            additional_data={'role_id': form.instance.id}
        )
        
        messages.success(self.request, f'Role {form.instance.display_name} created successfully.')
        return super().form_valid(form)


class BulkEmployeeUploadView(BaseViewMixin, HRAdminRequiredMixin, View):
    template_name = 'accounts/bulk_upload.html'
    form_class = BulkEmployeeUploadForm

    def get(self, request):
        form = self.form_class()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = self.form_class(request.POST, request.FILES)
        
        if form.is_valid():
            excel_file = form.cleaned_data['excel_file']
            
            try:
                created_count, error_count, errors = ExcelUtilities.import_users_from_excel(
                    excel_file.read(),
                    request.user
                )
                
                self.log_activity(
                    user=request.user,
                    action='BULK_IMPORT',
                    description=f'Bulk import completed: {created_count} created, {error_count} errors',
                    request=request,
                    additional_data={
                        'created_count': created_count,
                        'error_count': error_count
                    }
                )
                
                if created_count > 0:
                    messages.success(request, f'Successfully imported {created_count} employees.')
                
                if error_count > 0:
                    messages.warning(request, f'{error_count} records had errors.')
                    for error in errors[:10]:
                        messages.error(request, error)
                
                return redirect('accounts:employee_list')
            
            except Exception as e:
                messages.error(request, f'Import failed: {str(e)}')
        
        return render(request, self.template_name, {'form': form})


class ExportEmployeesView(BaseViewMixin, HRManagerRequiredMixin, View):
    def get(self, request):
        queryset = search_users(
            query=request.GET.get('search', ''),
            department_id=request.GET.get('department'),
            role_id=request.GET.get('role'),
            status=request.GET.get('status'),
            current_user=request.user
        ).select_related('department', 'role', 'manager')
        
        excel_data = ExcelUtilities.export_users_to_excel(queryset)
        
        self.log_activity(
            user=request.user,
            action='DATA_EXPORT',
            description=f'Exported {queryset.count()} employee records',
            request=request,
            additional_data={'export_count': queryset.count()}
        )
        
        response = HttpResponse(
            excel_data,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="employees_export_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
        
        return response


class UserSessionsView(BaseViewMixin, LoginRequiredMixin, View):
    template_name = 'accounts/user_sessions.html'

    def get(self, request):
        if UserUtilities.check_user_permission(request.user, 'view_all_sessions'):
            sessions = UserSession.objects.filter(is_active=True).select_related('user').order_by('-login_time')
        else:
            sessions = UserSession.objects.filter(user=request.user, is_active=True).order_by('-login_time')
        
        paginator = Paginator(sessions, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        
        return render(request, self.template_name, {'page_obj': page_obj})


class TerminateSessionView(BaseViewMixin, LoginRequiredMixin, View):
    def post(self, request, session_id):
        try:
            session = get_object_or_404(UserSession, id=session_id)
            
            if not (UserUtilities.check_user_permission(request.user, 'manage_sessions') or 
                   session.user == request.user):
                raise PermissionDenied("You don't have permission to terminate this session.")
            
            session.terminate_session()
            
            self.log_activity(
                user=request.user,
                action='SESSION_TERMINATED',
                description=f'Terminated session for user: {session.user.employee_code}',
                request=request,
                additional_data={'terminated_session_id': str(session.id)}
            )
            
            messages.success(request, 'Session terminated successfully.')
        
        except Exception as e:
            messages.error(request, f'Failed to terminate session: {str(e)}')
        
        return redirect('accounts:user_sessions')


class AuditLogView(BaseViewMixin, SuperAdminRequiredMixin, ListView):
    model = AuditLog
    template_name = 'accounts/audit_log.html'
    context_object_name = 'audit_logs'
    paginate_by = 50

    def get_queryset(self):
        queryset = AuditLog.objects.select_related('user').order_by('-timestamp')
        
        user_filter = self.request.GET.get('user')
        if user_filter:
            queryset = queryset.filter(
                Q(user__employee_code__icontains=user_filter) |
                Q(user__first_name__icontains=user_filter) |
                Q(user__last_name__icontains=user_filter)
            )
        
        action_filter = self.request.GET.get('action')
        if action_filter:
            queryset = queryset.filter(action=action_filter)
        
        date_from = self.request.GET.get('date_from')
        if date_from:
            queryset = queryset.filter(timestamp__gte=date_from)
        
        date_to = self.request.GET.get('date_to')
        if date_to:
            queryset = queryset.filter(timestamp__lte=date_to)
        
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action_choices'] = AuditLog.ACTION_TYPES
        return context


class SystemSettingsView(BaseViewMixin, SuperAdminRequiredMixin, View):
    template_name = 'accounts/system_settings.html'
    form_class = SystemConfigurationForm

    def get(self, request):
        form = self.form_class()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = self.form_class(request.POST)
        
        if form.is_valid():
            with transaction.atomic():
                for key, value in form.cleaned_data.items():
                    SystemConfiguration.set_setting(key.upper(), str(value), user=request.user)
            
            self.log_activity(
                user=request.user,
                action='SYSTEM_SETTINGS_UPDATED',
                description='System settings updated',
                request=request
            )
            
            messages.success(request, 'System settings updated successfully.')
            return redirect('accounts:system_settings')
        
        return render(request, self.template_name, {'form': form})


class SystemStatsView(BaseViewMixin, SuperAdminRequiredMixin, View):
    template_name = 'accounts/system_stats.html'

    def get(self, request):
        stats = SystemUtilities.get_system_statistics()
        
        context = {
            'stats': stats,
            'recent_logins': AuditLog.objects.filter(
                action='LOGIN',
                timestamp__gte=timezone.now() - timedelta(days=7)
            ).count(),
            'failed_logins': AuditLog.objects.filter(
                action='LOGIN_FAILED',
                timestamp__gte=timezone.now() - timedelta(days=7)
            ).count(),
            'password_changes': AuditLog.objects.filter(
                action='PASSWORD_CHANGE',
                timestamp__gte=timezone.now() - timedelta(days=30)
            ).count()
        }
        
        return render(request, self.template_name, context)


@login_required
@account_not_locked_required
@password_change_required
@require_http_methods(["POST"])
def bulk_user_action(request):
    if not UserUtilities.check_user_permission(request.user, 'manage_employees'):
        return JsonResponse({'success': False, 'error': 'Permission denied'})
    
    try:
        data = json.loads(request.body)
        user_ids = data.get('user_ids', [])
        action = data.get('action')
        reason = data.get('reason', '')
        
        if not user_ids or not action:
            return JsonResponse({'success': False, 'error': 'Missing required parameters'})
        
        with transaction.atomic():
            users = User.objects.filter(id__in=user_ids)
            affected_count = 0
            
            for user in users:
                if action == 'activate':
                    user.status = 'ACTIVE'
                    user.is_active = True
                    affected_count += 1
                elif action == 'deactivate':
                    user.status = 'INACTIVE'
                    user.is_active = False
                    affected_count += 1
                elif action == 'suspend':
                    user.status = 'SUSPENDED'
                    affected_count += 1
                elif action == 'terminate':
                    user.status = 'TERMINATED'
                    user.termination_date = timezone.now().date()
                    affected_count += 1
                elif action == 'reset_password':
                    temp_password = generate_secure_password()
                    user.set_password(temp_password)
                    user.must_change_password = True
                    try:
                        send_welcome_email(user, temp_password, request)
                    except Exception:
                        pass
                    affected_count += 1
                elif action == 'unlock_account':
                    user.unlock_account()
                    affected_count += 1
                
                user.save()
        
        log_user_activity(
            user=request.user,
            action='BULK_ACTION',
            description=f'Bulk {action} performed on {affected_count} users',
            request=request,
            additional_data={
                'action': action,
                'affected_count': affected_count,
                'reason': reason
            }
        )
        
        return JsonResponse({
            'success': True,
            'message': f'{action.title()} applied to {affected_count} users successfully.'
        })
    
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@account_not_locked_required
@password_change_required
def search_employees_ajax(request):
    query = request.GET.get('q', '')
    
    if len(query) < 2:
        return JsonResponse({'results': []})
    
    users = search_users(query, current_user=request.user)[:10]
    
    results = []
    for user in users:
        results.append({
            'id': user.id,
            'text': f"{user.get_full_name()} ({user.employee_code})",
            'employee_code': user.employee_code,
            'department': user.department.name if user.department else '',
            'job_title': user.job_title or ''
        })
    
    return JsonResponse({'results': results})


@login_required
@account_not_locked_required
@password_change_required
def check_employee_code(request):
    employee_code = request.GET.get('employee_code', '').upper()
    
    if not employee_code:
        return JsonResponse({'available': False, 'message': 'Employee code is required'})
    
    exists = User.objects.filter(employee_code=employee_code).exists()
    
    return JsonResponse({
        'available': not exists,
        'message': 'Employee code already exists' if exists else 'Employee code is available'
    })


@login_required
@account_not_locked_required
@password_change_required
def send_notification(request):
    if not UserUtilities.check_user_permission(request.user, 'send_notifications'):
        return JsonResponse({'success': False, 'error': 'Permission denied'})
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            recipient_ids = data.get('recipient_ids', [])
            subject = data.get('subject', '')
            message = data.get('message', '')
            
            if not recipient_ids or not subject or not message:
                return JsonResponse({'success': False, 'error': 'Missing required fields'})
            
            recipients = User.objects.filter(id__in=recipient_ids, is_active=True)
            
            success, count = SystemUtilities.send_bulk_notification(recipients, subject, message, request.user)
            
            if success:
                return JsonResponse({
                    'success': True,
                    'message': f'Notification sent to {count} recipients successfully.'
                })
            else:
                return JsonResponse({'success': False, 'error': 'Failed to send notification'})
        
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
@account_not_locked_required
@password_change_required
def generate_report(request):
    if not UserUtilities.check_user_permission(request.user, 'generate_reports'):
        return JsonResponse({'success': False, 'error': 'Permission denied'})
    
    try:
        start_date = datetime.strptime(request.GET.get('start_date'), '%Y-%m-%d')
        end_date = datetime.strptime(request.GET.get('end_date'), '%Y-%m-%d')
        user_filter = request.GET.get('user_filter', '')
        
        report_data = SystemUtilities.generate_audit_report(start_date, end_date, user_filter)
        
        log_user_activity(
            user=request.user,
            action='REPORT_GENERATED',
            description=f'Audit report generated for {start_date.date()} to {end_date.date()}',
            request=request,
            additional_data={
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'user_filter': user_filter
            }
        )
        
        return JsonResponse({'success': True, 'data': report_data})
    
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def health_check(request):
    try:
        user_count = User.objects.count()
        active_sessions = UserSession.objects.filter(is_active=True).count()
        
        return JsonResponse({
            'status': 'healthy',
            'timestamp': timezone.now().isoformat(),
            'users': user_count,
            'active_sessions': active_sessions
        })
    
    except Exception as e:
        return JsonResponse({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': timezone.now().isoformat()
        }, status=500)


def handler404(request, exception):
    return render(request, 'accounts/404.html', status=404)


def handler500(request):
    return render(request, 'accounts/500.html', status=500)


def handler403(request, exception):
    return render(request, 'accounts/403.html', status=403)
