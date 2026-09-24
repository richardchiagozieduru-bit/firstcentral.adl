from django.contrib import admin
from django.contrib.auth.models import User
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render
from django import forms
import calendar
from .models import Subscriber, UserSubscriberPermission, Feedback, UploadSession




@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    """
    Admin interface for Subscriber model (read-only since it's an external table)
    """
    list_display = ('subscriber_id', 'subscriber_name')
    list_filter = ('subscriber_name',)
    search_fields = ('subscriber_name', 'subscriber_id')
    readonly_fields = ('subscriber_id', 'subscriber_name')
    ordering = ('subscriber_name',)
    
    def has_add_permission(self, request):
        return False  # External table, no adding
    
    def has_delete_permission(self, request, obj=None):
        return False  # External table, no deleting
    
    def has_change_permission(self, request, obj=None):
        return False  # External table, no editing






@admin.register(UserSubscriberPermission)
class UserSubscriberPermissionAdmin(admin.ModelAdmin):
    """
    Admin interface for UserSubscriberPermission model
    """
    list_display = (
        'user', 'subscriber_info', 'permission_level', 
        'is_active', 'granted_at', 'granted_by'
    )
    list_filter = (
        'permission_level', 'is_active', 'granted_at', 'granted_by'
    )
    search_fields = ('user__username', 'user__email', 'subscriber_id')
    ordering = ('-granted_at',)
    date_hierarchy = 'granted_at'
    
    fieldsets = (
        ('Permission Assignment', {
            'fields': ('user', 'subscriber_id', 'permission_level', 'is_active')
        }),
        ('Metadata', {
            'fields': ('granted_by', 'granted_at'),
            'classes': ('collapse',)
        })
    )
    
    readonly_fields = ('granted_at',)
    
    def subscriber_info(self, obj):
        """Display subscriber information"""
        subscriber = obj.get_subscriber()
        if subscriber:
            return f"{subscriber.subscriber_name} (ID: {obj.subscriber_id})"
        return f"Subscriber ID: {obj.subscriber_id} (Not Found)"
    subscriber_info.short_description = 'Subscriber'
    
    def save_model(self, request, obj, form, change):
        """Set granted_by to current user if not set"""
        if not change and not obj.granted_by:
            obj.granted_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(UploadSession)
class UploadSessionAdmin(admin.ModelAdmin):
    """
    Admin interface for managing and monitoring upload sessions.
    Allows administrators to differentiate sessions by organization (subscriber),
    track status, and invalidate completed sessions to permit re-uploading.
    """
    list_display = (
        'id', 'subscriber_display', 'reporting_period_display', 'user_display',
        'status_badge', 'filename_display', 'total_records', 'uploaded_at'
    )
    list_filter = ('status', 'reporting_year', 'reporting_month', 'uploaded_at')
    search_fields = ('subscriber_id', 'filename', 'original_filename', 'user__username', 'user__email')
    ordering = ('-uploaded_at',)
    date_hierarchy = 'uploaded_at'
    actions = ['invalidate_and_allow_reupload']
    
    fieldsets = (
        ('Organization & Period', {
            'fields': ('subscriber_display', 'subscriber_id', 'reporting_month', 'reporting_year', 'user')
        }),
        ('Status & Lifecycle', {
            'fields': ('status', 'processing_stage', 'progress_percentage', 'current_message', 'error_message')
        }),
        ('Invalidation / Re-upload Control', {
            'fields': ('invalidated_at', 'invalidated_by'),
            'description': 'When invalidated, the monthly quota lock is released for this organization and reporting period.'
        }),
        ('File & Output Details', {
            'fields': ('filename', 'original_filename', 'file_size', 'split_option', 'individual_file_path', 'corporate_file_path'),
            'classes': ('collapse',)
        }),
        ('Processing Metrics', {
            'fields': ('total_records', 'individual_records', 'corporate_records', 'individual_credit_matched', 'corporate_credit_matched', 'unmatched_credit_records', 'processing_time'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('uploaded_at', 'processing_started_at', 'completed_at'),
            'classes': ('collapse',)
        }),
    )
    readonly_fields = (
        'subscriber_display', 'uploaded_at', 'processing_started_at', 
        'completed_at', 'invalidated_at', 'invalidated_by'
    )

    def subscriber_display(self, obj):
        sub_name = obj.get_subscriber_name()
        return format_html(
            '<strong style="color: #1B3D8C; font-size: 1.05em;">{}</strong> <span style="color: #6c757d; font-size: 0.85em;">(ID: {})</span>',
            sub_name, obj.subscriber_id
        )
    subscriber_display.short_description = 'Organization'
    subscriber_display.admin_order_field = 'subscriber_id'

    def reporting_period_display(self, obj):
        if obj.reporting_month and obj.reporting_year:
            try:
                m_name = calendar.month_name[obj.reporting_month]
                return format_html('<strong>{} {}</strong>', m_name, obj.reporting_year)
            except Exception:
                return f"{obj.reporting_month}/{obj.reporting_year}"
        return mark_safe('<span style="color: #999;">N/A</span>')
    reporting_period_display.short_description = 'Reporting Period'
    reporting_period_display.admin_order_field = 'reporting_year'

    def user_display(self, obj):
        if obj.user:
            return f"{obj.user.username}"
        return "System"
    user_display.short_description = 'Uploaded By'
    user_display.admin_order_field = 'user__username'

    def filename_display(self, obj):
        fname = obj.original_filename or obj.filename
        return fname[:35] + '...' if len(fname) > 35 else fname
    filename_display.short_description = 'File'

    def status_badge(self, obj):
        color_map = {
            'completed': ('#198754', '#e6f9f0', 'Completed'),
            'processing': ('#0d6efd', '#cfe2ff', 'Processing'),
            'uploading': ('#0d6efd', '#cfe2ff', 'Uploading'),
            'awaiting_verification': ('#fd7e14', '#fff3cd', 'Awaiting Review'),
            'finalizing': ('#0dcaf0', '#cff4fc', 'Finalizing'),
            'failed': ('#dc3545', '#f8d7da', 'Failed'),
            'cancelled': ('#6c757d', '#e2e3e5', 'Cancelled'),
            'invalidated': ('#d63384', '#f8d7da', 'Invalidated (Unlocked)'),
        }
        color, bg, label = color_map.get(obj.status, ('#495057', '#e9ecef', obj.get_status_display()))
        return format_html(
            '<span style="background-color: {}; color: {}; padding: 4px 10px; border-radius: 12px; font-weight: 600; font-size: 0.85em;">{}</span>',
            bg, color, label
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def invalidate_and_allow_reupload(self, request, queryset):
        """
        Admin action to invalidate selected completed upload sessions.
        Releases the monthly lock for the corresponding organization and reporting period.
        """
        from django.utils import timezone
        unlocked_records = []
        for session in queryset:
            sub_name = session.get_subscriber_name()
            period_str = f"{session.reporting_month}/{session.reporting_year}" if session.reporting_month else "N/A"
            session.status = 'invalidated'
            session.invalidated_at = timezone.now()
            session.invalidated_by = request.user
            session.save(update_fields=['status', 'invalidated_at', 'invalidated_by'])
            unlocked_records.append(f"{sub_name} ({period_str})")

        messages.success(
            request,
            f"Successfully invalidated {len(unlocked_records)} session(s): {', '.join(unlocked_records)}. "
            f"The respective organization(s) are now unlocked and permitted to re-upload for that period."
        )
    invalidate_and_allow_reupload.short_description = "Invalidate Session (Allow Re-upload for Period)"

    def get_search_results(self, request, queryset, search_term):
        """
        Enhances admin search so searching for a bank name (e.g. 'Zenith')
        matches upload sessions belonging to that subscriber.
        """
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        if search_term:
            try:
                matching_subs = Subscriber.objects.filter(subscriber_name__icontains=search_term)
                matching_ids = []
                for sub in matching_subs:
                    try:
                        matching_ids.append(int(float(sub.subscriber_id)))
                    except (ValueError, TypeError):
                        pass
                if matching_ids:
                    queryset |= self.model.objects.filter(subscriber_id__in=matching_ids)
            except Exception:
                pass
        return queryset, use_distinct


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    """
    Admin interface for viewing and managing user feedback & re-upload requests
    """
    list_display = (
        'created_at', 'subscriber_display', 'contact_email_display',
        'category_badge', 'rating_display', 'user', 'message_preview', 'is_reviewed'
    )
    list_filter = ('category', 'rating', 'is_reviewed', 'created_at')
    search_fields = ('message', 'contact_email', 'user__username', 'user__email')
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'
    readonly_fields = ('user', 'contact_email', 'rating', 'category', 'message', 'page_url', 'created_at')
    actions = ['mark_as_reviewed']
    
    fieldsets = (
        ('Submitter & Contact Information', {
            'fields': ('user', 'contact_email', 'page_url', 'created_at')
        }),
        ('Feedback Content', {
            'fields': ('category', 'rating', 'message')
        }),
        ('Admin Review & Resolution', {
            'fields': ('is_reviewed', 'reviewed_at', 'admin_notes'),
        })
    )

    def subscriber_display(self, obj):
        if obj.user:
            try:
                from acctmgt.models import UserProfile
                profile = UserProfile.objects.filter(user=obj.user, is_bound=True).first()
                if profile:
                    sub = profile.get_bound_subscriber()
                    if sub:
                        return format_html('<strong>{}</strong>', sub.subscriber_name)
            except Exception:
                pass
        return "N/A"
    subscriber_display.short_description = 'Organization'

    def contact_email_display(self, obj):
        email = obj.contact_email or (obj.user.email if obj.user else '')
        if email:
            return format_html('<a href="mailto:{}">{}</a>', email, email)
        return mark_safe('<span style="color: #999;">None</span>')
    contact_email_display.short_description = 'Contact Email'

    def category_badge(self, obj):
        if obj.category == 'reupload_request':
            return mark_safe(
                '<span style="background-color: #ffebe9; color: #cf222e; padding: 3px 8px; border-radius: 8px; font-weight: bold; border: 1px solid #ff8182;">Re-upload Request</span>'
            )
        return obj.get_category_display()
    category_badge.short_description = 'Category'
    
    def rating_display(self, obj):
        """Display rating as stars"""
        return '★' * obj.rating + '☆' * (5 - obj.rating)
    rating_display.short_description = 'Rating'
    
    def message_preview(self, obj):
        """Display truncated message"""
        return obj.message[:60] + '...' if len(obj.message) > 60 else obj.message
    message_preview.short_description = 'Message'
    
    def mark_as_reviewed(self, request, queryset):
        """Mark selected feedback as reviewed"""
        from django.utils import timezone
        updated = queryset.update(is_reviewed=True, reviewed_at=timezone.now())
        messages.success(request, f'Marked {updated} feedback entries as reviewed.')
    mark_as_reviewed.short_description = "Mark selected as reviewed"
    
    def has_add_permission(self, request):
        return False  # Feedback is submitted by users, not created in admin
    
    def has_delete_permission(self, request, obj=None):
        return True  # Allow deletion for cleanup

