from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from auto.models import Subscriber

class UserProfile(models.Model):
    """
    Extended user profile to store permanent subscriber binding.
    Implements one-time token binding where users are permanently
    associated with a subscriber after initial token validation.
    """
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='profile'
    )
    
    # Permanent subscriber binding
    bound_subscriber_id = models.IntegerField(
        null=True, 
        blank=True,
        help_text="Permanently bound subscriber ID after one-time token validation",
        db_index=True
    )
    
    # Binding metadata
    binding_token = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        help_text="Token used for initial binding (for audit purposes)"
    )
    
    bound_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when user was bound to subscriber"
    )
    
    bound_by_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP address from which binding was performed"
    )
    
    # Status flags
    is_bound = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether user is permanently bound to a subscriber"
    )
    
    binding_method = models.CharField(
        max_length=20,
        choices=[
            ('token', 'Token Validation'),
            ('admin', 'Admin Assignment'),
            ('migration', 'Data Migration')
        ],
        default='token',
        help_text="How the binding was established"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'user_profiles'
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'
        indexes = [
            models.Index(fields=['bound_subscriber_id', 'is_bound']),
            models.Index(fields=['user', 'is_bound']),
        ]
    
    def __str__(self):
        if self.is_bound:
            subscriber = self.get_bound_subscriber()
            subscriber_name = subscriber.subscriber_name if subscriber else f"ID:{self.bound_subscriber_id}"
            return f"{self.user.username} → {subscriber_name}"
        return f"{self.user.username} (Unbound)"
    
    def get_bound_subscriber(self):
        """
        Get the bound subscriber object.
        Returns None if not bound or subscriber doesn't exist.
        """
        if not self.is_bound or not self.bound_subscriber_id:
            return None
        
        try:
            sub_id = int(float(str(self.bound_subscriber_id)))
            return Subscriber.objects.get(subscriber_id=sub_id)
        except (ValueError, TypeError, Subscriber.DoesNotExist):
            return None
    
    def bind_to_subscriber(self, subscriber_id, token_string=None, ip_address=None, method='admin'):
        """
        Permanently bind user to a subscriber.
        
        Args:
            subscriber_id: ID of the subscriber to bind to
            token_string: Token used for binding (optional)
            ip_address: IP address of the binding request
            method: How the binding was established
        
        Returns:
            bool: True if binding was successful
        """
        if self.is_bound:
            return False  # Already bound
        
        # Verify subscriber exists
        try:
            sub_id = int(float(str(subscriber_id)))
            subscriber = Subscriber.objects.get(subscriber_id=sub_id)
        except (ValueError, TypeError, Subscriber.DoesNotExist):
            return False
        
        # Perform binding
        self.bound_subscriber_id = sub_id
        self.binding_token = token_string
        self.bound_at = timezone.now()
        self.bound_by_ip = ip_address
        self.is_bound = True
        self.binding_method = method
        self.save()
        
        return True

    
    def unbind_subscriber(self, reason='admin_action'):
        """
        Remove subscriber binding (admin function).
        
        Args:
            reason: Reason for unbinding
        
        Returns:
            bool: True if unbinding was successful
        """
        if not self.is_bound:
            return False
        
        # Create audit log before unbinding
        UnbindingAuditLog.objects.create(
            user=self.user,
            previous_subscriber_id=self.bound_subscriber_id,
            previous_binding_token=self.binding_token,
            previous_bound_at=self.bound_at,
            unbinding_reason=reason,
            unbound_at=timezone.now()
        )
        
        # Clear binding
        self.bound_subscriber_id = None
        self.binding_token = None
        self.bound_at = None
        self.bound_by_ip = None
        self.is_bound = False
        self.save()
        
        return True
    
    @classmethod
    def get_or_create_profile(cls, user):
        """
        Get or create user profile for a given user.
        
        Args:
            user: Django User instance
        
        Returns:
            UserProfile instance or None if unauthenticated
        """
        if not user or (not getattr(user, 'is_authenticated', False) and not getattr(user, 'pk', None)):
            return None

        profile, created = cls.objects.get_or_create(
            user=user,
            defaults={
                'is_bound': False
            }
        )
        return profile

    
    @classmethod
    def get_bound_users_for_subscriber(cls, subscriber_id):
        """
        Get all users bound to a specific subscriber.
        
        Args:
            subscriber_id: ID of the subscriber
        
        Returns:
            QuerySet of UserProfile instances
        """
        return cls.objects.filter(
            bound_subscriber_id=subscriber_id,
            is_bound=True
        ).select_related('user')


class UnbindingAuditLog(models.Model):
    """
    Audit log for tracking when users are unbound from subscribers.
    Important for security and compliance.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='unbinding_logs'
    )
    
    # Previous binding information
    previous_subscriber_id = models.IntegerField(
        help_text="Subscriber ID user was previously bound to"
    )
    
    previous_binding_token = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        help_text="Token that was used for original binding"
    )
    
    previous_bound_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the original binding occurred"
    )
    
    # Unbinding information
    unbinding_reason = models.CharField(
        max_length=100,
        help_text="Reason for unbinding"
    )
    
    unbound_at = models.DateTimeField(
        help_text="When the unbinding occurred"
    )
    
    unbound_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='performed_unbindings',
        help_text="Admin user who performed the unbinding"
    )
    
    class Meta:
        db_table = 'unbinding_audit_logs'
        verbose_name = 'Unbinding Audit Log'
        verbose_name_plural = 'Unbinding Audit Logs'
        ordering = ['-unbound_at']
    
    def __str__(self):
        return f"{self.user.username} unbound from subscriber {self.previous_subscriber_id} at {self.unbound_at}"


class EmailVerificationOTP(models.Model):
    """
    Model for Two-Factor Authentication (2FA) / Email verification OTP tokens.
    Used to verify user email address during account registration and password resets.
    """
    OTP_TYPE_CHOICES = [
        ('registration', 'Registration Verification'),
        ('password_reset', 'Password Reset'),
        ('login_2fa', 'Login 2FA Verification'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='verification_otps'
    )
    otp_code = models.CharField(
        max_length=6,
        db_index=True,
        help_text="6-digit verification code"
    )
    otp_type = models.CharField(
        max_length=20,
        choices=OTP_TYPE_CHOICES,
        default='registration',
        db_index=True,
        help_text="Purpose of the OTP token"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        help_text="Timestamp when this OTP expires"
    )
    is_verified = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if this OTP has been successfully verified"
    )
    attempts = models.IntegerField(
        default=0,
        help_text="Number of failed validation attempts"
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP address where OTP was requested"
    )

    class Meta:
        db_table = 'email_verification_otps'
        verbose_name = 'Email Verification OTP'
        verbose_name_plural = 'Email Verification OTPs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'otp_type', 'is_verified', 'expires_at']),
        ]

    def __str__(self):
        status = "Verified" if self.is_verified else ("Expired" if self.is_expired() else "Active")
        return f"OTP ({self.get_otp_type_display()}) for {self.user.username} - {status}"

    def is_expired(self):
        """Check if OTP has expired."""
        return timezone.now() > self.expires_at

    def is_valid(self, code):
        """Check if code matches and OTP is active."""
        from django.conf import settings
        max_attempts = getattr(settings, 'OTP_MAX_ATTEMPTS', 5)
        if self.is_verified or self.is_expired() or self.attempts >= max_attempts:
            return False
        return self.otp_code.strip() == str(code).strip()

    def increment_attempts(self):
        """Increment failed attempts count."""
        self.attempts += 1
        self.save(update_fields=['attempts'])

    def mark_verified(self):
        """Mark OTP as used and verified."""
        self.is_verified = True
        self.save(update_fields=['is_verified'])

    @classmethod
    def generate_otp_for_user(cls, user, otp_type='registration', ip_address=None, expiry_minutes=None):
        """
        Generate a secure 6-digit OTP for a user, invalidating prior active OTPs of the same type.
        """
        import secrets
        from datetime import timedelta
        from django.conf import settings

        if expiry_minutes is None:
            expiry_minutes = getattr(settings, 'OTP_EXPIRY_MINUTES', 10)

        # Generate cryptographically secure 6-digit numeric string
        otp_code = f"{secrets.randbelow(1000000):06d}"
        expires_at = timezone.now() + timedelta(minutes=expiry_minutes)

        # Deactivate previous unverified OTPs of the same type for clean state
        cls.objects.filter(user=user, otp_type=otp_type, is_verified=False).update(is_verified=True)

        return cls.objects.create(
            user=user,
            otp_code=otp_code,
            otp_type=otp_type,
            expires_at=expires_at,
            ip_address=ip_address
        )

    @classmethod
    def prune_stale_otps(cls, days=30, include_all_expired=False):
        """
        Prune old / expired OTP records to keep the database table lean.
        
        Args:
            days (int): Delete records created older than N days.
            include_all_expired (bool): If True, delete all records whose expires_at < now,
                                       regardless of creation date.
        Returns:
            int: Number of deleted OTP records.
        """
        from datetime import timedelta
        now = timezone.now()

        if include_all_expired:
            qs = cls.objects.filter(expires_at__lt=now)
        else:
            cutoff = now - timedelta(days=days)
            qs = cls.objects.filter(created_at__lt=cutoff)

        count, _ = qs.delete()
        return count



