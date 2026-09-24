import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def get_client_ip(request):
    """Extract client IP address from HTTP request."""
    if not request:
        return None
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def mask_email(email):
    """
    Mask an email address for privacy display.
    Example: richard.d@firstcentral.com -> r***d@firstcentral.com
    """
    if not email or '@' not in email:
        return email or ''
    
    local_part, domain = email.split('@', 1)
    if len(local_part) <= 2:
        masked_local = local_part[0] + '***'
    else:
        masked_local = local_part[0] + '***' + local_part[-1]
    
    return f"{masked_local}@{domain}"


def send_verification_otp_email(user, otp_code, request=None):
    """
    Send a 2FA verification email containing the 6-digit OTP code.
    Returns (success: bool, error: str or None)
    """
    if not user.email:
        logger.warning(f"[2FA] Cannot send OTP: User {user.username} has no email address.")
        return False, "No email address associated with account."

    expiry_minutes = getattr(settings, 'OTP_EXPIRY_MINUTES', 10)
    company_name = "First Central Credit Bureau"
    subject = f"Your Verification Code: {otp_code} - {company_name}"
    
    context = {
        'user': user,
        'username': user.username,
        'otp_code': otp_code,
        'expiry_minutes': expiry_minutes,
        'company_name': company_name,
        'support_email': getattr(settings, 'FEEDBACK_EMAIL_RECIPIENT', 'support@firstcentralcreditbureau.com')
    }

    try:
        html_content = render_to_string('acctmgt/emails/verify_otp.html', context)
        try:
            text_content = render_to_string('acctmgt/emails/verify_otp.txt', context)
        except Exception:
            text_content = strip_tags(html_content)

        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@firstcentralcreditbureau.com')
        recipient_list = [user.email]

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=recipient_list
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)

        logger.info(f"[2FA] Verification OTP successfully sent to {mask_email(user.email)} for user '{user.username}'.")
        return True, None

    except Exception as e:
        logger.error(f"[2FA ERROR] Failed to send OTP email to {user.email}: {str(e)}", exc_info=True)
        return False, str(e)


def send_password_reset_otp_email(user, otp_code, request=None):
    """
    Send a Password Reset email containing the 6-digit OTP code.
    Returns (success: bool, error: str or None)
    """
    if not user.email:
        logger.warning(f"[PASSWORD RESET] Cannot send OTP: User {user.username} has no email address.")
        return False, "No email address associated with account."

    expiry_minutes = getattr(settings, 'OTP_EXPIRY_MINUTES', 10)
    company_name = "First Central Credit Bureau"
    subject = f"Password Reset Code: {otp_code} - {company_name}"
    
    context = {
        'user': user,
        'username': user.username,
        'otp_code': otp_code,
        'expiry_minutes': expiry_minutes,
        'company_name': company_name,
        'support_email': getattr(settings, 'FEEDBACK_EMAIL_RECIPIENT', 'support@firstcentralcreditbureau.com')
    }

    try:
        html_content = render_to_string('acctmgt/emails/password_reset_otp.html', context)
        try:
            text_content = render_to_string('acctmgt/emails/password_reset_otp.txt', context)
        except Exception:
            text_content = strip_tags(html_content)

        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@firstcentralcreditbureau.com')
        recipient_list = [user.email]

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=recipient_list
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)

        logger.info(f"[PASSWORD RESET] Reset OTP sent to {mask_email(user.email)} for user '{user.username}'.")
        return True, None

    except Exception as e:
        logger.error(f"[PASSWORD RESET ERROR] Failed to send reset OTP to {user.email}: {str(e)}", exc_info=True)
        return False, str(e)


def send_login_2fa_otp_email(user, otp_code, request=None):
    """
    Send a Two-Factor Authentication (2FA) sign-in security code to the user's registered email.
    Returns (success: bool, error: str or None)
    """
    if not user.email:
        logger.warning(f"[LOGIN 2FA] Cannot send OTP: User {user.username} has no email address.")
        return False, "No email address associated with account."

    expiry_minutes = getattr(settings, 'OTP_EXPIRY_MINUTES', 10)
    company_name = "First Central Credit Bureau"
    subject = f"Sign-In Security Code: {otp_code} - {company_name}"
    
    context = {
        'user': user,
        'username': user.username,
        'otp_code': otp_code,
        'expiry_minutes': expiry_minutes,
        'company_name': company_name,
        'support_email': getattr(settings, 'FEEDBACK_EMAIL_RECIPIENT', 'support@firstcentralcreditbureau.com')
    }

    try:
        html_content = render_to_string('acctmgt/emails/login_2fa_otp.html', context)
        try:
            text_content = render_to_string('acctmgt/emails/login_2fa_otp.txt', context)
        except Exception:
            text_content = strip_tags(html_content)

        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@firstcentralcreditbureau.com')
        recipient_list = [user.email]

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=recipient_list
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)

        logger.info(f"[LOGIN 2FA] Sign-in OTP successfully sent to {mask_email(user.email)} for user '{user.username}'.")
        return True, None

    except Exception as e:
        logger.error(f"[LOGIN 2FA ERROR] Failed to send login OTP to {user.email}: {str(e)}", exc_info=True)
        return False, str(e)


