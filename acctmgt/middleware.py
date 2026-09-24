from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages
from django.utils.deprecation import MiddlewareMixin


class SubscriberSessionMiddleware(MiddlewareMixin):
    """
    Middleware to enforce subscriber session validation for protected views.
    Ensures users have selected and validated a subscriber before accessing
    data cleaning functionality.
    """
    
    # URLs that don't require subscriber validation
    EXEMPT_URLS = [
        '/acctmgt/login/',
        '/acctmgt/logout/',
        '/acctmgt/verify-login-2fa/',
        '/acctmgt/resend-login-2fa/',
        '/acctmgt/reset-password/',
        '/acctmgt/forgot-password/',
        '/acctmgt/resend-reset-otp/',
        '/check-upload-quota/',
        '/admin/',
        '/adl/static/',
        '/static/',
        '/media/',
    ]
    
    def process_request(self, request):
        """
        Check if the user needs subscriber binding before accessing protected views
        """
        # Skip middleware for exempt URLs
        if any(request.path.startswith(url) for url in self.EXEMPT_URLS):
            # Clear any accumulated messages when accessing admin to prevent stacking
            if request.path.startswith('/admin/'):
                storage = messages.get_messages(request)
                # Iterate through messages to mark them as used/cleared
                for _ in storage:
                    pass
            return None
        
        # Skip middleware for unauthenticated users (they'll be handled by LoginRequiredMixin)
        if not request.user.is_authenticated:
            return None
        
        # Enforce 24-hour absolute security session timeout for non-superusers
        if not request.user.is_superuser:
            import time
            from django.contrib.auth import logout
            login_time = request.session.get('session_login_time')
            if login_time:
                if (time.time() - login_time) > 86400:  # 24 hours
                    logout(request)
                    messages.info(
                        request,
                        'Your session has reached the 24-hour security limit. Please sign in again.'
                    )
                    return redirect('/acctmgt/login/?section=login')
            else:
                # Stamp login time if missing
                request.session['session_login_time'] = time.time()
        
        # Staff and superusers have full administrative access and do not require organization binding
        if request.user.is_staff or request.user.is_superuser:
            return None
        
        # Check if user is bound to a subscriber (new binding system)
        from .models import UserProfile
        from django.contrib.auth import logout
        try:
            # Multi-subscriber users don't need binding - they select subscriber on upload
            if request.user.groups.filter(name='multi_subscriber').exists():
                return None
            
            user_profile = UserProfile.get_or_create_profile(request.user)

            # Guard against None profile (safety check)
            if user_profile is None:
                return redirect('/acctmgt/login/')
            
            if not user_profile.is_bound:
                # User is authenticated but not bound to any subscriber
                logout(request)
                messages.error(
                    request,
                    'Your account has not yet been assigned to an organization. Please contact First Central Administrator.'
                )
                return redirect('/acctmgt/login/')
            
            # Get bound subscriber information
            bound_subscriber = user_profile.get_bound_subscriber()
            if not bound_subscriber:
                # Bound but subscriber doesn't exist - data integrity issue
                logout(request)
                messages.error(
                    request,
                    'Unable to retrieve your organization information. Please contact First Central Administrator.'
                )
                return redirect('/acctmgt/login/')
            
            # Add subscriber info to request and session for access across all views & async tasks
            request.subscriber_id = bound_subscriber.subscriber_id
            request.subscriber_name = bound_subscriber.subscriber_name
            request.session['subscriber_id'] = bound_subscriber.subscriber_id
            request.session['subscriber_name'] = bound_subscriber.subscriber_name
            
        except Exception:
            # Handle any database or model errors gracefully — do NOT redirect here
            # to avoid creating a redirect loop on errors
            return None
        
        return None
    
    def process_response(self, request, response):
        """
        Add subscriber context to response headers for debugging (optional)
        """
        if hasattr(request, 'subscriber_id') and hasattr(request, 'subscriber_name'):
            response['X-Subscriber-ID'] = str(request.subscriber_id)
            response['X-Subscriber-Name'] = request.subscriber_name
        
        return response


class SubscriberDataFilterMixin:
    """
    Mixin to automatically filter data by subscriber in views.
    Use this mixin in views that need to filter data by the current subscriber.
    """
    
    def get_subscriber_id(self):
        """
        Get the current subscriber ID from the request
        """
        return getattr(self.request, 'subscriber_id', None)
    
    def get_subscriber_name(self):
        """
        Get the current subscriber name from the request
        """
        return getattr(self.request, 'subscriber_name', None)
    
    def filter_queryset_by_subscriber(self, queryset, subscriber_field='subscriber_id'):
        """
        Filter a queryset by the current subscriber
        
        Args:
            queryset: The queryset to filter
            subscriber_field: The field name to filter by (default: 'subscriber_id')
        
        Returns:
            Filtered queryset
        """
        subscriber_id = self.get_subscriber_id()
        if subscriber_id:
            filter_kwargs = {subscriber_field: subscriber_id}
            return queryset.filter(**filter_kwargs)
        return queryset.none()  # Return empty queryset if no subscriber
    
    def get_context_data(self, **kwargs):
        """
        Add subscriber information to template context
        """
        context = super().get_context_data(**kwargs)
        context['current_subscriber_id'] = self.get_subscriber_id()
        context['current_subscriber_name'] = self.get_subscriber_name()
        return context


def get_current_subscriber(request):
    """
    Utility function to get current subscriber information from request
    
    Returns:
        dict: {'id': subscriber_id, 'name': subscriber_name} or None
    """
    subscriber_id = getattr(request, 'subscriber_id', None)
    subscriber_name = getattr(request, 'subscriber_name', None)
    
    if subscriber_id and subscriber_name:
        return {
            'id': subscriber_id,
            'name': subscriber_name
        }
    return None