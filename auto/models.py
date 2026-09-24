from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import secrets


class Subscriber(models.Model):
    """
    Model to represent subscribers in the existing MSSQL Subscribers database.
    This connects to the Sheet1 table with SubscriberID and SubscriberName columns.
    """
    subscriber_id = models.AutoField(primary_key=True, db_column='SubscriberID')
    subscriber_name = models.CharField(max_length=255, db_column='SubscriberName')
    
    class Meta:
        db_table = 'Sheet1'
        managed = False  # Django won't manage this existing table
    
    def __str__(self):
        return self.subscriber_name or f"Subscriber {self.subscriber_id}"
    
    @classmethod
    def get_subscriber_choices(cls):
        """
        Return a list of tuples for form choices (id, name)
        """
        return [(sub.subscriber_id, sub.subscriber_name) for sub in cls.objects.all()]
    
    @classmethod
    def validate_subscriber(cls, subscriber_name, subscriber_id):
        """
        Validate if the subscriber name and ID match
        """
        try:
            subscriber = cls.objects.get(
                subscriber_name=subscriber_name,
                subscriber_id=subscriber_id
            )
            return subscriber
        except cls.DoesNotExist:
            return None


class UserSubscriberPermission(models.Model):
    """
    Model to manage user-subscriber relationships with permission levels.
    Supports both one-to-one and one-to-many user-subscriber scenarios.
    Uses integer field for subscriber_id - NO foreign key to avoid constraint issues.
    """
    PERMISSION_CHOICES = [
        ('read', 'Read Only'),
        ('write', 'Read/Write'),
        ('admin', 'Administrator'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='subscriber_permissions')
    subscriber_id = models.IntegerField(
        help_text="Reference to subscriber ID from external table",
        db_index=True  # Add index for performance
    )
    permission_level = models.CharField(max_length=20, choices=PERMISSION_CHOICES, default='read')
    granted_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='granted_permissions'
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, db_index=True)
    
    class Meta:
        db_table = 'user_subscriber_permissions'
        verbose_name = 'User Subscriber Permission'
        verbose_name_plural = 'User Subscriber Permissions'
        unique_together = ['user', 'subscriber_id']
        ordering = ['-granted_at']
        indexes = [
            models.Index(fields=['user', 'subscriber_id', 'is_active']),
            models.Index(fields=['subscriber_id', 'is_active']),
        ]
    
    def __str__(self):
        subscriber = self.get_subscriber()
        subscriber_name = subscriber.subscriber_name if subscriber else f"ID:{self.subscriber_id}"
        return f"{self.user.username} - {subscriber_name} ({self.permission_level})"
    
    def get_subscriber(self):
        """
        Get the related subscriber object manually (no FK relationship)
        """
        try:
            return Subscriber.objects.get(subscriber_id=self.subscriber_id)
        except Subscriber.DoesNotExist:
            return None
    
    @classmethod
    def get_user_subscribers(cls, user):
        """
        Get all subscribers a user has access to with their permission details
        Returns a queryset of permission objects
        """
        return cls.objects.filter(user=user, is_active=True).select_related('user')
    
    @classmethod
    def get_subscriber_choices_for_user(cls, user):
        """
        Get subscriber choices formatted for form dropdowns
        """
        user_subscribers = cls.get_user_subscribers(user)
        return [
            (sub['subscriber'].subscriber_id, sub['subscriber'].subscriber_name) 
            for sub in user_subscribers
        ]
    
    @classmethod
    def has_permission(cls, user, subscriber_id, required_permission='read'):
        """
        Check if user has required permission level for a subscriber
        Permission hierarchy: admin > write > read
        """
        permission_hierarchy = {'read': 1, 'write': 2, 'admin': 3}
        required_level = permission_hierarchy.get(required_permission, 1)
        
        try:
            user_permission = cls.objects.get(
                user=user, 
                subscriber_id=subscriber_id, 
                is_active=True
            )
            user_level = permission_hierarchy.get(user_permission.permission_level, 0)
            return user_level >= required_level
        except cls.DoesNotExist:
            return False
    
    @classmethod
    def get_users_for_subscriber(cls, subscriber_id):
        """
        Get all users who have access to a specific subscriber
        """
        return cls.objects.filter(
            subscriber_id=subscriber_id, 
            is_active=True
        ).select_related('user')


class SubscriberUtils:
    """
    Utility class for subscriber-related operations
    """
    
    @staticmethod
    def get_subscriber_by_id(subscriber_id):
        """
        Get subscriber by ID with error handling
        """
        try:
            return Subscriber.objects.get(subscriber_id=subscriber_id)
        except Subscriber.DoesNotExist:
            return None
    
    @staticmethod
    def get_user_context_info(user):
        """
        Get comprehensive context information for a user
        """
        if not user or not user.is_authenticated:
            return {}
        
        user_permissions = UserSubscriberPermission.get_user_subscribers(user)
        subscribers_info = []
        
        for perm in user_permissions:
            subscriber = perm.get_subscriber()
            if subscriber:
                subscribers_info.append({
                    'subscriber_id': subscriber.subscriber_id,
                    'subscriber_name': subscriber.subscriber_name,
                    'permission_level': perm.permission_level,
                    'granted_at': perm.granted_at
                })
        
        context = {
            'user': user,
            'subscribers': subscribers_info,
            'subscriber_count': len(subscribers_info)
        }
        
        return context


class UploadSession(models.Model):
    """
    Model to track file upload sessions for dashboard analytics and real-time progress tracking
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('uploading', 'Uploading'),
        ('processing', 'Processing'),
        ('awaiting_verification', 'Awaiting Verification'),
        ('finalizing', 'Finalizing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
        ('invalidated', 'Invalidated (Re-upload Allowed)'),
    ]
    
    STAGE_CHOICES = [
        ('pending', 'Pending'),
        ('upload_validation', 'File Upload & Validation'),
        ('data_mapping', 'Data Mapping & Preparation'),
        ('data_cleaning', 'Data Cleaning & Transformation'),
        ('credit_matching', 'Credit Matching & Classification'),
        ('awaiting_verification', 'Awaiting Human Verification'),
        ('post_verification', 'Finalization After Verification'),
        ('output_generation', 'Output Generation'),
        ('completed', 'Completed'),
    ]
    
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='upload_sessions'
    )
    subscriber_id = models.IntegerField(
        help_text="Reference to subscriber ID from external table",
        db_index=True
    )
    filename = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(help_text="File size in bytes")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending')
    
    # Progress tracking fields
    processing_stage = models.CharField(
        max_length=50, 
        choices=STAGE_CHOICES, 
        default='pending',
        help_text="Current processing stage for detailed progress tracking"
    )
    progress_percentage = models.IntegerField(
        default=0, 
        help_text="Overall progress percentage (0-100)"
    )
    current_message = models.TextField(
        blank=True, 
        help_text="Current processing message for real-time updates"
    )
    activity_log = models.JSONField(
        default=list, 
        blank=True,
        help_text="Timestamped log of processing activities for audit trail"
    )
    
    # Processing metrics
    individual_records = models.PositiveIntegerField(default=0)
    corporate_records = models.PositiveIntegerField(default=0)
    total_records = models.PositiveIntegerField(default=0)
    individual_credit_matched = models.PositiveIntegerField(default=0, help_text="Number of individual records with credit matches")
    corporate_credit_matched = models.PositiveIntegerField(default=0, help_text="Number of corporate records with credit matches")
    unmatched_credit_records = models.PositiveIntegerField(default=0, help_text="Number of credit records that could not be matched to any borrower")
    excluded_individual_records = models.PositiveIntegerField(default=0, help_text="Number of individual records excluded during processing")
    excluded_corporate_records = models.PositiveIntegerField(default=0, help_text="Number of corporate records excluded during processing")
    processing_time = models.FloatField(null=True, blank=True, help_text="Processing time in seconds")
    
    # Verification tracking fields
    has_verification_candidates = models.BooleanField(
        default=False,
        help_text="Whether this upload has records requiring human verification"
    )
    commercial_candidates_count = models.IntegerField(
        default=0,
        help_text="Number of potential commercial entities found in consumer records"
    )
    consumer_candidates_count = models.IntegerField(
        default=0,
        help_text="Number of potential consumer entities found in commercial records"
    )
    verification_completed_at = models.DateTimeField(
        null=True, 
        blank=True,
        help_text="Timestamp when human verification was completed"
    )
    verification_skipped = models.BooleanField(
        default=False,
        help_text="Whether verification was auto-skipped due to no candidates"
    )
    
    # Timestamps
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True, help_text="When session was invalidated to allow re-upload")
    invalidated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invalidated_sessions',
        help_text="Admin who invalidated session to allow re-upload"
    )
    
    # Error tracking
    error_message = models.TextField(blank=True, null=True)
    
    # File paths for downloads
    individual_file_path = models.CharField(max_length=500, blank=True, null=True)
    corporate_file_path = models.CharField(max_length=500, blank=True, null=True)
    individual_txt_path = models.CharField(max_length=500, blank=True, null=True)
    corporate_txt_path = models.CharField(max_length=500, blank=True, null=True)
    
    # Output format tracking (for smart format selection)
    individual_output_format = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        help_text="Output format for individual sheet: 'xlsx' or 'txt'"
    )
    corporate_output_format = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        help_text="Output format for corporate sheet: 'xlsx' or 'txt'"
    )
    
    # Original file archival for dispute resolution and compliance
    original_file_path = models.CharField(
        max_length=500, 
        blank=True, 
        null=True,
        help_text="Path to the original unmodified uploaded file for dispute resolution and audit purposes"
    )
    
    # Processing data storage (for async workflow and session recovery)
    processing_data = models.TextField(
        blank=True,
        null=True,
        help_text="JSON storage for processing data when using async workflow"
    )
    
    # Reporting period fields (user-selected date for output filename generation)
    reporting_month = models.IntegerField(
        null=True,
        blank=True,
        help_text="User-selected reporting month (1-12) for output filename generation"
    )
    reporting_year = models.IntegerField(
        null=True,
        blank=True,
        help_text="User-selected reporting year for output filename generation"
    )
    
    # Split option for merged files ('split' or 'no_split')
    split_option = models.CharField(
        max_length=20,
        default='split',
        blank=True,
        null=True,
        help_text="User decision for merged files: 'split' or 'no_split'"
    )
    
    # Password for encrypted files
    file_password = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Password for encrypted Excel files"
    )
    
    class Meta:
        db_table = 'upload_sessions'
        verbose_name = 'Upload Session'
        verbose_name_plural = 'Upload Sessions'
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['user', 'uploaded_at']),
            models.Index(fields=['subscriber_id', 'uploaded_at']),
            models.Index(fields=['status', 'uploaded_at']),
        ]
    
    def __str__(self):
        return f"{self.original_filename} - {self.user.username} ({self.status})"
    
    def get_subscriber(self):
        """Get the related subscriber object manually"""
        try:
            return Subscriber.objects.get(subscriber_id=self.subscriber_id)
        except Subscriber.DoesNotExist:
            return None

    def get_subscriber_name(self):
        """Get the related subscriber name"""
        sub = self.get_subscriber()
        return sub.subscriber_name if sub else f"ID: {self.subscriber_id}"
    
    def mark_processing_started(self):
        """Mark the upload as processing started"""
        self.status = 'processing'
        self.processing_stage = 'upload_validation'
        self.progress_percentage = 5
        self.processing_started_at = timezone.now()
        self.add_activity_log('Processing started')
        self.save(update_fields=['status', 'processing_stage', 'progress_percentage', 'processing_started_at'])
    
    def update_progress(self, stage, percentage, message=None):
        """
        Update processing progress with user-friendly message.
        Checks atomically if upload was cancelled and raises UploadCancelledException to halt execution.
        
        Args:
            stage: Processing stage from STAGE_CHOICES
            percentage: Progress percentage (0-100)
            message: Optional message describing current activity
        """
        # Atomic cancellation check: if user or admin cancelled, halt processing immediately
        if self.id:
            from .tasks import is_session_cancelled
            if is_session_cancelled(self.id):
                self.status = 'cancelled'
                from .exceptions import UploadCancelledException
                raise UploadCancelledException(f"Upload session {self.id} was cancelled by user", session_id=self.id)

        self.processing_stage = stage
        self.progress_percentage = percentage
        if message:
            self.current_message = message
            self.add_activity_log(message)
        
        # Update status based on stage
        if stage == 'awaiting_verification':
            self.status = 'awaiting_verification'
        elif stage == 'post_verification':
            self.status = 'finalizing'
        elif stage == 'completed':
            self.status = 'completed'
        elif stage != 'pending':
            self.status = 'processing'
        
        self.save(update_fields=['processing_stage', 'progress_percentage', 'current_message', 'status'])
    
    def add_activity_log(self, message):
        """
        Add a timestamped entry to the activity log
        
        Args:
            message: Activity message to log
        """
        from datetime import datetime
        if not isinstance(self.activity_log, list):
            self.activity_log = []
        
        log_entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'message': message,
            'stage': self.processing_stage,
            'progress': self.progress_percentage
        }
        self.activity_log.append(log_entry)
        self.save(update_fields=['activity_log'])
    
    def mark_awaiting_verification(self, commercial_count=0, consumer_count=0):
        """
        Mark the upload as awaiting human verification
        
        Args:
            commercial_count: Number of potential commercial entities in consumer records
            consumer_count: Number of potential consumer entities in commercial records
        """
        if self.id:
            from .tasks import is_session_cancelled
            if is_session_cancelled(self.id):
                return False

        self.status = 'awaiting_verification'
        self.processing_stage = 'awaiting_verification'
        self.progress_percentage = 80
        self.has_verification_candidates = True
        self.commercial_candidates_count = commercial_count
        self.consumer_candidates_count = consumer_count
        self.current_message = f'Verification required: {commercial_count} commercial candidates, {consumer_count} consumer candidates'
        self.add_activity_log(self.current_message)
        self.save(update_fields=[
            'status', 'processing_stage', 'progress_percentage', 
            'has_verification_candidates', 'commercial_candidates_count', 
            'consumer_candidates_count', 'current_message'
        ])
        return True
    
    def mark_verification_completed(self):
        """Mark that human verification has been completed"""
        if self.id:
            from .tasks import is_session_cancelled
            if is_session_cancelled(self.id):
                return False

        self.verification_completed_at = timezone.now()
        self.status = 'finalizing'
        self.processing_stage = 'post_verification'
        self.progress_percentage = 85
        self.current_message = 'Verification completed, finalizing processing'
        self.add_activity_log('Human verification completed')
        self.save(update_fields=[
            'verification_completed_at', 'status', 'processing_stage', 
            'progress_percentage', 'current_message'
        ])
        return True
    
    def mark_verification_skipped(self):
        """Mark that verification was auto-skipped (no candidates found)"""
        if self.id:
            from .tasks import is_session_cancelled
            if is_session_cancelled(self.id):
                return False

        self.verification_skipped = True
        self.verification_completed_at = timezone.now()
        self.status = 'finalizing'
        self.processing_stage = 'post_verification'
        self.progress_percentage = 85
        self.has_verification_candidates = False
        self.commercial_candidates_count = 0
        self.consumer_candidates_count = 0
        self.current_message = 'No verification candidates found, proceeding automatically'
        self.add_activity_log('Verification auto-skipped: no consumer/commercial candidates detected')
        self.save(update_fields=[
            'verification_skipped', 'verification_completed_at', 'status', 
            'processing_stage', 'progress_percentage', 'has_verification_candidates',
            'commercial_candidates_count', 'consumer_candidates_count', 'current_message'
        ])
        return True
    
    def mark_completed(self, individual_count=0, corporate_count=0, processing_time=None):
        """Mark the upload as completed with metrics. Returns False if cancelled, True otherwise."""
        if self.id:
            from .tasks import is_session_cancelled
            if is_session_cancelled(self.id):
                return False

        self.status = 'completed'
        self.processing_stage = 'completed'
        self.progress_percentage = 100
        self.completed_at = timezone.now()
        self.individual_records = individual_count
        self.corporate_records = corporate_count
        self.total_records = individual_count + corporate_count
        
        # Auto-calculate processing_time if not provided
        if processing_time:
            self.processing_time = processing_time
        elif self.processing_started_at:
            # Calculate from processing start to now
            duration = self.completed_at - self.processing_started_at
            self.processing_time = duration.total_seconds()
        
        self.current_message = 'Processing completed successfully'
        self.add_activity_log(f'Completed: {self.total_records} total records processed')
        self.save(update_fields=[
            'status', 'processing_stage', 'progress_percentage', 'completed_at', 
            'individual_records', 'corporate_records', 'total_records', 
            'processing_time', 'current_message'
        ])
        return True
    
    def mark_failed(self, error_message=None):
        """Mark the upload as failed with user-friendly error message"""
        if self.id:
            from .tasks import is_session_cancelled
            if is_session_cancelled(self.id):
                return False
        self.status = 'failed'
        self.progress_percentage = 0
        self.completed_at = timezone.now()
        if error_message:
            # Store the original technical error
            self.error_message = error_message
            
            # Generate user-friendly message for display
            try:
                from .error_messages import get_friendly_error
                friendly = get_friendly_error(error_message)
                self.current_message = f"{friendly['title']}: {friendly['message'][:150]}"
            except ImportError:
                # Fallback if error_messages module not available
                self.current_message = f'Processing failed: {error_message[:200]}'
            
            self.add_activity_log(f'ERROR: {error_message[:200]}')
        self.save(update_fields=['status', 'progress_percentage', 'completed_at', 'error_message', 'current_message'])
        return True
    
    def mark_cancelled(self, reason='Cancelled by user'):
        """Mark the upload as cancelled by user"""
        self.status = 'cancelled'
        self.progress_percentage = 0
        self.completed_at = timezone.now()
        self.current_message = reason
        self.add_activity_log(f'CANCELLED: {reason}')
        self.save(update_fields=['status', 'progress_percentage', 'completed_at', 'current_message'])
    
    @classmethod
    def get_user_stats(cls, user, days=None):
        """Get upload statistics for a user"""
        queryset = cls.objects.filter(user=user)
        
        if days:
            from datetime import timedelta
            start_date = timezone.now() - timedelta(days=days)
            queryset = queryset.filter(uploaded_at__gte=start_date)
        
        stats = {
            'total_uploads': queryset.count(),
            'successful_uploads': queryset.filter(status='completed').count(),
            'failed_uploads': queryset.filter(status='failed').count(),
            'pending_uploads': queryset.filter(status__in=['pending', 'processing']).count(),
            'total_records_processed': sum(queryset.filter(status='completed').values_list('total_records', flat=True)),
            'individual_records': sum(queryset.filter(status='completed').values_list('individual_records', flat=True)),
            'corporate_records': sum(queryset.filter(status='completed').values_list('corporate_records', flat=True)),
            'unmatched_credit_records': sum(queryset.filter(status='completed').values_list('unmatched_credit_records', flat=True)),
        }
        
        # Calculate success rate
        if stats['total_uploads'] > 0:
            stats['success_rate'] = (stats['successful_uploads'] / stats['total_uploads']) * 100
        else:
            stats['success_rate'] = 0
        
        return stats
    
    @classmethod
    def get_recent_uploads(cls, user, limit=10):
        """Get recent uploads for a user"""
        return cls.objects.filter(user=user).order_by('-uploaded_at')[:limit]


class Feedback(models.Model):
    """
    Model to store user feedback submitted via the in-app feedback modal.
    Allows users to rate their experience, categorize feedback, and provide details.
    """
    CATEGORY_CHOICES = [
        ('bug', 'Bug Report'),
        ('feature', 'Feature Request'),
        ('general', 'General Feedback'),
        ('reupload_request', 'Request Re-upload / Correction'),
    ]
    
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='feedback_submissions',
        help_text="User who submitted the feedback (if authenticated)"
    )
    contact_email = models.EmailField(
        max_length=254,
        blank=True,
        null=True,
        help_text="Contact email address for follow-up"
    )
    rating = models.IntegerField(
        default=0,
        blank=True,
        null=True,
        help_text="User satisfaction rating (optional)"
    )
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='general',
        help_text="Type of feedback"
    )
    message = models.TextField(
        help_text="Detailed feedback message from user"
    )
    page_url = models.CharField(
        max_length=500,
        blank=True,
        help_text="URL of page where feedback was submitted"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Optional: track if feedback has been reviewed/addressed
    is_reviewed = models.BooleanField(default=False)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True, help_text="Internal notes from admin review")
    
    class Meta:
        db_table = 'user_feedback'
        verbose_name = 'User Feedback'
        verbose_name_plural = 'User Feedback'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['category', 'created_at']),
            models.Index(fields=['rating', 'created_at']),
            models.Index(fields=['is_reviewed', 'created_at']),
        ]
    
    def __str__(self):
        username = self.user.username if self.user else 'Anonymous'
        rating_str = f" - {self.rating}★" if self.rating else ""
        return f"{self.get_category_display()} from {username}{rating_str} ({self.created_at.strftime('%Y-%m-%d')})"
    
    def mark_reviewed(self, notes=''):
        """Mark feedback as reviewed with optional notes"""
        self.is_reviewed = True
        self.reviewed_at = timezone.now()
        if notes:
            self.admin_notes = notes
        self.save(update_fields=['is_reviewed', 'reviewed_at', 'admin_notes'])