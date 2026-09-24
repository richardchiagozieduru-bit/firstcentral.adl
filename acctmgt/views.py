import time
import logging
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib.auth import login as auth_login, authenticate
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib import messages
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, FormView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.conf import settings
from django.db.models import Q
from django.utils.decorators import method_decorator

from .forms import CustomUserCreationForm, CustomAuthenticationForm
from .models import UserProfile, EmailVerificationOTP
from .utils import (
    get_client_ip,
    mask_email,
    send_verification_otp_email,
    send_password_reset_otp_email,
    send_login_2fa_otp_email,
)
from .rate_limiter import check_request_rate_limit

logger = logging.getLogger(__name__)


class CustomLoginView(LoginView):
    """Custom login view with enhanced styling and error handling."""
    form_class = CustomAuthenticationForm
    template_name = 'acctmgt/auth_unified.html'
    redirect_authenticated_user = True
    
    def get(self, request, *args, **kwargs):
        # Guarantee a completely clean sign-in page by purging all stale application/upload messages.
        # Only keep password reset confirmation messages or genuine auth notices.
        storage = messages.get_messages(request)
        kept_messages = []
        auth_keywords = ['password', 'reset code', 'successfully reset', 'provisioned credentials', 'account disabled', 'session expired']
        excluded_keywords = ['reporting period', 'upload session', 'processing file', 'quota']

        for msg in storage:
            msg_text = str(msg).lower()
            if any(k in msg_text for k in auth_keywords) and not any(e in msg_text for e in excluded_keywords):
                kept_messages.append(msg)
        storage.used = True
        for km in kept_messages:
            messages.add_message(request, km.level, km.message, extra_tags=km.extra_tags)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Pre-fill username if provided in GET parameter or POST data
        context['username_prefill'] = self.request.GET.get('username', '') or self.request.POST.get('username', '')
        return context

    def form_valid(self, form):
        """
        Authenticate user credentials.
        - Superusers bypass 2FA and log in directly.
        - All other users receive a 6-digit OTP to their registered email address.
        """
        user = form.get_user()

        # Superusers are exempt from 2FA: log in immediately
        if user.is_superuser:
            auth_login(self.request, user)
            self.request.session['session_login_time'] = time.time()
            return redirect(self.get_success_url())

        # Non-superusers require 2FA OTP verification
        if not user.email:
            messages.error(
                self.request,
                'No registered email address found for this account. Please contact First Central Administrator.'
            )
            context = self.get_context_data(form=form)
            context['show_section'] = 'login'
            context['username_prefill'] = user.username
            return self.render_to_response(context)

        # Store pending authentication user ID in session
        self.request.session['pending_2fa_user_id'] = user.id

        # Generate & send login 2FA OTP
        otp_obj = EmailVerificationOTP.generate_otp_for_user(
            user=user,
            otp_type='login_2fa',
            ip_address=get_client_ip(self.request)
        )
        success, err = send_login_2fa_otp_email(user, otp_obj.otp_code, request=self.request)
        if not success:
            logger.error(f"[LOGIN 2FA] Failed to send login OTP: {err}")

        return redirect('acctmgt:verify_login_2fa')

    def form_invalid(self, form):
        username = form.cleaned_data.get('username', '') or self.request.POST.get('username', '').strip()
        password = form.cleaned_data.get('password', '') or self.request.POST.get('password', '')
        
        # Check if account exists but is deactivated
        if username:
            user = User.objects.filter(username=username).first()
            if user and not user.is_active and user.check_password(password):
                messages.error(
                    self.request,
                    'This account is inactive or has been disabled. Please contact First Central Administrator.'
                )
                context = self.get_context_data(form=form)
                context['show_section'] = 'login'
                context['username_prefill'] = username
                return self.render_to_response(context)

        messages.error(self.request, 'Invalid username or password. Please try again.')
        context = self.get_context_data(form=form)
        context['show_section'] = 'login'  # Keep user on login section on failed auth
        context['username_prefill'] = username
        return self.render_to_response(context)
    
    def get_success_url(self):
        # Staff and superusers have unrestricted administrative access
        if self.request.user.is_staff or self.request.user.is_superuser:
            return reverse_lazy('auto:upload')

        # Multi-subscriber users don't need binding - go directly to upload page
        if self.request.user.groups.filter(name='multi_subscriber').exists():
            return reverse_lazy('auto:upload')
        
        # Check if user is already bound to a subscriber
        user_profile = UserProfile.get_or_create_profile(self.request.user)
        
        if user_profile and user_profile.is_bound:
            # User is already bound, redirect directly to dashboard
            return reverse_lazy('auto:dashboard')
        else:
            # User is not bound to any organization - safety guard
            from django.contrib.auth import logout
            logout(self.request)
            messages.error(
                self.request,
                'Your account has not yet been assigned to an organization. Please contact First Central Administrator.'
            )
            return reverse_lazy('acctmgt:login')


class CustomLogoutView(LogoutView):
    """
    Custom logout view that guarantees all pending/stale messages are purged,
    ensuring a completely clean sign-in card without lingering banners.
    """
    next_page = '/acctmgt/login/?section=login'

    def dispatch(self, request, *args, **kwargs):
        try:
            storage = messages.get_messages(request)
            for _ in storage:
                pass
            storage.used = True
        except Exception:
            pass
        return super().dispatch(request, *args, **kwargs)


class Login2FAVerifyView(View):
    """
    Two-Factor Authentication (2FA) verification view required for sign-in.
    Verifies the 6-digit security code sent to the user's registered email.
    Superusers bypass this view completely.
    """
    def get(self, request):
        user_id = request.session.get('pending_2fa_user_id')
        if not user_id:
            return redirect('/acctmgt/login/?section=login')

        user = User.objects.filter(id=user_id).first()
        if not user:
            return redirect('/acctmgt/login/?section=login')

        return render(request, 'acctmgt/verify_login_2fa.html', {
            'masked_email': mask_email(user.email),
            'username': user.username
        })

    def post(self, request):
        user_id = request.session.get('pending_2fa_user_id')
        if not user_id:
            messages.error(request, 'Sign-in session expired. Please enter your credentials again.')
            return redirect('/acctmgt/login/?section=login')

        user = User.objects.filter(id=user_id).first()
        if not user:
            return redirect('/acctmgt/login/?section=login')

        otp_code = request.POST.get('otp_code', '').strip()
        if not otp_code:
            messages.error(request, 'Please enter the 6-digit security code.')
            return render(request, 'acctmgt/verify_login_2fa.html', {
                'masked_email': mask_email(user.email),
                'username': user.username
            })

        # Fetch the most recent active login_2fa OTP
        otp_obj = EmailVerificationOTP.objects.filter(
            user=user,
            otp_type='login_2fa',
            is_verified=False
        ).order_by('-created_at').first()

        if not otp_obj or otp_obj.is_expired():
            messages.error(request, 'Your verification code has expired. Please request a new code.')
            return render(request, 'acctmgt/verify_login_2fa.html', {
                'masked_email': mask_email(user.email),
                'username': user.username
            })

        if not otp_obj.is_valid(otp_code):
            otp_obj.increment_attempts()
            messages.error(request, 'Invalid verification code. Please check your email and try again.')
            return render(request, 'acctmgt/verify_login_2fa.html', {
                'masked_email': mask_email(user.email),
                'username': user.username
            })

        # OTP is valid — mark verified and authenticate session
        otp_obj.mark_verified()
        auth_login(request, user)
        request.session['session_login_time'] = time.time()

        if 'pending_2fa_user_id' in request.session:
            del request.session['pending_2fa_user_id']

        # Redirect user based on role and organization binding
        if user.is_staff or user.is_superuser:
            return redirect('auto:upload')

        if user.groups.filter(name='multi_subscriber').exists():
            return redirect('auto:upload')

        user_profile = UserProfile.get_or_create_profile(user)
        if user_profile and user_profile.is_bound:
            return redirect('auto:dashboard')
        else:
            from django.contrib.auth import logout
            logout(request)
            messages.error(
                request,
                'Your account has not yet been assigned to an organization. Please contact First Central Administrator.'
            )
            return redirect('/acctmgt/login/?section=login')


class ResendLogin2FAView(View):
    """
    AJAX endpoint to resend a fresh 6-digit login 2FA verification code.
    Enforces a strict 60-second cooldown period.
    """
    def post(self, request):
        user_id = request.session.get('pending_2fa_user_id')
        if not user_id:
            return JsonResponse({'success': False, 'error': 'Sign-in session expired. Please sign in again.'}, status=400)

        user = User.objects.filter(id=user_id).first()
        if not user:
            return JsonResponse({'success': False, 'error': 'User not found.'}, status=400)

        # Cooldown enforcement
        latest_otp = EmailVerificationOTP.objects.filter(
            user=user,
            otp_type='login_2fa'
        ).order_by('-created_at').first()

        if latest_otp:
            elapsed = (timezone.now() - latest_otp.created_at).total_seconds()
            cooldown = getattr(settings, 'OTP_RESEND_COOLDOWN_SECONDS', 60)
            if elapsed < cooldown:
                remaining = int(cooldown - elapsed)
                return JsonResponse({
                    'success': False,
                    'error': f'Please wait {remaining} seconds before requesting a new code.',
                    'remaining_seconds': remaining
                }, status=429)

        # Generate fresh OTP & email
        otp_obj = EmailVerificationOTP.generate_otp_for_user(
            user=user,
            otp_type='login_2fa',
            ip_address=get_client_ip(request)
        )
        success, err = send_login_2fa_otp_email(user, otp_obj.otp_code, request=request)
        if not success:
            return JsonResponse({'success': False, 'error': f'Failed to send code: {err}'}, status=500)

        return JsonResponse({'success': True})


class RegisterView(View):
    """
    Decommissioned registration view.
    Account registration is now managed exclusively by the Administrator.
    """
    def get(self, request, *args, **kwargs):
        messages.info(request, "Account registration is managed by First Central Administrator. Please sign in with your provisioned credentials.")
        return redirect('/acctmgt/login/?section=login')

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)


class VerifyOTPView(View):
    """
    Decommissioned verification view.
    Registration OTP verification is no longer used since accounts are pre-verified by Admin.
    """
    def get(self, request, *args, **kwargs):
        messages.info(request, "Account verification is no longer required. Please sign in with your provisioned credentials.")
        return redirect('/acctmgt/login/?section=login')

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)


class ResendOTPView(View):
    """Decommissioned resend view."""
    def post(self, request, *args, **kwargs):
        return redirect('/acctmgt/login/?section=login')


class ForgotPasswordView(View):
    """
    View to handle initial password reset requests by username or email.
    Generates a password reset OTP and dispatches it via email.
    """
    def post(self, request):
        # Rate limit: Max 5 reset requests per IP per 15 minutes
        allowed, retry_after = check_request_rate_limit(request, 'forgot_password', max_requests=5, window_seconds=900)
        if not allowed:
            wait_min = max(1, retry_after // 60)
            messages.error(request, f"Too many password reset requests from your device. Please wait {wait_min} minute(s).")
            return redirect('/acctmgt/login/?section=forgot')

        identity = request.POST.get('identity', '').strip()
        if not identity:
            messages.error(request, 'Please enter your username or email address.')
            return redirect('/acctmgt/login/?section=forgot')

        user = User.objects.filter(Q(username__iexact=identity) | Q(email__iexact=identity)).first()
        if not user:
            messages.error(request, f"No account found matching '{identity}'. Please verify and try again.")
            return redirect('/acctmgt/login/?section=forgot')

        if not user.email:
            messages.error(request, 'This account has no email address associated with it. Please contact an administrator.')
            return redirect('/acctmgt/login/?section=forgot')

        # Generate password reset OTP
        client_ip = get_client_ip(request)
        otp = EmailVerificationOTP.generate_otp_for_user(user, otp_type='password_reset', ip_address=client_ip)

        # Dispatch email
        email_sent, error = send_password_reset_otp_email(user, otp.otp_code, request)
        request.session['password_reset_user_id'] = user.id

        if email_sent:
            messages.success(request, f"A 6-digit password reset code was sent to {mask_email(user.email)}.")
        else:
            messages.warning(request, "A reset code was generated, but there was an issue delivering the email. Please check connection or contact support.")

        return redirect(f"{reverse('acctmgt:reset_password')}?user_id={user.id}")


class ResetPasswordConfirmView(View):
    """
    View to confirm password reset with the 6-digit OTP and set a new password.
    """
    template_name = 'acctmgt/reset_password.html'

    def _get_target_user(self, request):
        user_id = request.POST.get('user_id') or request.GET.get('user_id') or request.session.get('password_reset_user_id')
        if not user_id:
            return None
        try:
            return User.objects.get(pk=user_id)
        except (User.DoesNotExist, ValueError):
            return None

    def _get_cooldown_remaining(self, user):
        cooldown_total = getattr(settings, 'OTP_RESEND_COOLDOWN_SECONDS', 60)
        latest_otp = EmailVerificationOTP.objects.filter(user=user, otp_type='password_reset').order_by('-created_at').first()
        if not latest_otp:
            return 0
        elapsed = (timezone.now() - latest_otp.created_at).total_seconds()
        return max(0, int(cooldown_total - elapsed))

    def get(self, request):
        user = self._get_target_user(request)
        if not user:
            messages.warning(request, 'Please enter your username or email to request a password reset.')
            return redirect('/acctmgt/login/?section=forgot')

        # Strictly filter messages: only show the notification that a reset code was sent
        storage = messages.get_messages(request)
        filtered_messages = []
        for message in storage:
            msg_text = str(message).lower()
            if 'password reset code' in msg_text or 'reset code was sent' in msg_text or 'fresh code was sent' in msg_text:
                filtered_messages.append(message)

        cooldown_remaining = self._get_cooldown_remaining(user)
        context = {
            'user_id': user.id,
            'masked_email': mask_email(user.email),
            'cooldown_remaining': cooldown_remaining,
            'filtered_messages': filtered_messages,
        }
        return render(request, self.template_name, context)



    def post(self, request):
        user = self._get_target_user(request)
        if not user:
            messages.error(request, 'Session expired. Please request a new password reset.')
            return redirect('/acctmgt/login/?section=forgot')

        # Rate limit: Max 10 reset confirmation attempts per IP per 10 minutes
        allowed, retry_after = check_request_rate_limit(request, 'reset_password', max_requests=10, window_seconds=600)
        if not allowed:
            wait_min = max(1, retry_after // 60)
            context = {
                'user_id': user.id,
                'masked_email': mask_email(user.email),
                'cooldown_remaining': self._get_cooldown_remaining(user),
                'error_message': f"Too many password reset attempts from your device. Please wait {wait_min} minute(s)."
            }
            return render(request, self.template_name, context)

        otp_code = request.POST.get('otp_code', '').strip()
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')
        cooldown_remaining = self._get_cooldown_remaining(user)

        context = {
            'user_id': user.id,
            'masked_email': mask_email(user.email),
            'cooldown_remaining': cooldown_remaining,
        }

        if not otp_code or len(otp_code) != 6:
            context['error_message'] = 'Please enter all 6 digits of the reset code.'
            return render(request, self.template_name, context)

        if not new_password or not confirm_password:
            context['error_message'] = 'Please enter and confirm your new password.'
            return render(request, self.template_name, context)

        if new_password != confirm_password:
            context['error_message'] = 'Passwords do not match. Please ensure both passwords are identical.'
            return render(request, self.template_name, context)

        if len(new_password) < 8:
            context['error_message'] = 'Password must be at least 8 characters long.'
            return render(request, self.template_name, context)

        # Retrieve active password_reset OTP
        otp = EmailVerificationOTP.objects.filter(user=user, otp_type='password_reset', is_verified=False).order_by('-created_at').first()

        if not otp:
            context['error_message'] = 'No active reset code found. Please request a new code.'
            return render(request, self.template_name, context)

        if otp.is_expired():
            context['error_message'] = 'Your reset code has expired. Please request a new code.'
            return render(request, self.template_name, context)

        max_attempts = getattr(settings, 'OTP_MAX_ATTEMPTS', 5)
        if otp.attempts >= max_attempts:
            context['error_message'] = 'Maximum verification attempts exceeded. Please request a new code.'
            return render(request, self.template_name, context)

        # Validate code
        if not otp.is_valid(otp_code):
            otp.increment_attempts()
            remaining = max(0, max_attempts - otp.attempts)
            context['error_message'] = f"Incorrect reset code. {remaining} attempt(s) remaining."
            return render(request, self.template_name, context)

        # Success: Mark OTP verified, set new password, activate account if needed
        otp.mark_verified()
        user.set_password(new_password)
        user.is_active = True
        user.save()

        # Clear session reset state
        request.session.pop('password_reset_user_id', None)

        messages.success(request, 'Your password has been successfully reset! Please sign in with your new credentials.')
        return redirect(f"{reverse('acctmgt:login')}?username={user.username}&section=login")


class ResendResetOTPView(View):
    """
    View to resend a new password reset OTP code with rate limiting cooldown.
    """
    def post(self, request):
        user_id = request.POST.get('user_id') or request.session.get('password_reset_user_id')
        if not user_id:
            messages.error(request, 'Unable to identify account. Please request a password reset.')
            return redirect('/acctmgt/login/?section=forgot')

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            messages.error(request, 'Account not found.')
            return redirect('/acctmgt/login/?section=forgot')

        # Rate limit: Max 5 resend reset requests per IP per 15 minutes
        allowed, retry_after = check_request_rate_limit(request, 'resend_reset_otp', max_requests=5, window_seconds=900)
        if not allowed:
            wait_min = max(1, retry_after // 60)
            messages.error(request, f"Too many reset code requests from your device. Please wait {wait_min} minute(s).")
            return redirect(f"{reverse('acctmgt:reset_password')}?user_id={user.id}")

        # Check cooldown
        cooldown_total = getattr(settings, 'OTP_RESEND_COOLDOWN_SECONDS', 60)
        latest_otp = EmailVerificationOTP.objects.filter(user=user, otp_type='password_reset').order_by('-created_at').first()
        if latest_otp:
            elapsed = (timezone.now() - latest_otp.created_at).total_seconds()
            if elapsed < cooldown_total:
                remaining = int(cooldown_total - elapsed)
                messages.warning(request, f"Please wait {remaining} seconds before requesting another code.")
                return redirect(f"{reverse('acctmgt:reset_password')}?user_id={user.id}")

        # Generate and send fresh reset OTP
        client_ip = get_client_ip(request)
        new_otp = EmailVerificationOTP.generate_otp_for_user(user, otp_type='password_reset', ip_address=client_ip)
        email_sent, error = send_password_reset_otp_email(user, new_otp.otp_code, request)

        if email_sent:
            messages.success(request, f"A fresh password reset code was sent to {mask_email(user.email)}.")
        else:
            messages.error(request, "Failed to deliver email. Please check your connection or contact support.")

        return redirect(f"{reverse('acctmgt:reset_password')}?user_id={user.id}")

