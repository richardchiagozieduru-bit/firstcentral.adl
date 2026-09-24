from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html, mark_safe
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from .models import UserProfile, UnbindingAuditLog
from .forms import AdminUserCreationForm


from django import forms
from auto.models import Subscriber


class UserProfileForm(forms.ModelForm):
    """
    Form for UserProfile in admin with interactive organization dropdown.
    Allows administrators to assign or update the bound organization directly.
    """
    organization_select = forms.ChoiceField(
        label='Assign / Reassign Organization',
        required=False,
        help_text='Select an organization from the list to bind or update this user.'
    )

    class Meta:
        model = UserProfile
        fields = ('organization_select', 'is_bound', 'bound_subscriber_id', 'binding_method', 'binding_token', 'bound_at', 'bound_by_ip')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [('', '--------- Select Organization ---------')]
        try:
            for sub in Subscriber.objects.all().order_by('subscriber_name'):
                choices.append((str(sub.subscriber_id), f"{sub.subscriber_name} (ID: {sub.subscriber_id})"))
        except Exception:
            pass
        self.fields['organization_select'].choices = choices
        if self.instance and self.instance.bound_subscriber_id:
            self.fields['organization_select'].initial = str(self.instance.bound_subscriber_id)

    def clean(self):
        cleaned_data = super().clean()
        org_sel = cleaned_data.get('organization_select')
        if org_sel:
            try:
                sub_id = int(float(org_sel))
                cleaned_data['bound_subscriber_id'] = sub_id
                cleaned_data['is_bound'] = True
                cleaned_data['binding_method'] = 'admin'
                if not self.instance.bound_at:
                    cleaned_data['bound_at'] = timezone.now()
            except (ValueError, TypeError):
                pass
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        org_sel = self.cleaned_data.get('organization_select')
        if org_sel:
            try:
                sub_id = int(float(org_sel))
                instance.bound_subscriber_id = sub_id
                instance.is_bound = True
                instance.binding_method = 'admin'
                if not instance.bound_at:
                    instance.bound_at = timezone.now()
            except (ValueError, TypeError):
                pass
        if commit:
            instance.save()
        return instance


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    form = UserProfileForm
    can_delete = False
    verbose_name_plural = 'User Profile'
    fk_name = 'user'
    readonly_fields = ('get_subscriber_display', 'binding_token', 'bound_at', 'bound_by_ip')
    
    fieldsets = (
        ('Subscriber Binding', {
            'fields': (
                'get_subscriber_display', 'organization_select', 'is_bound', 'bound_subscriber_id', 'binding_method',
                'binding_token', 'bound_at', 'bound_by_ip'
            ),
            'description': 'Permanent binding between user and organization / subscriber'
        }),
    )

    def get_subscriber_display(self, obj):
        if obj and obj.bound_subscriber_id:
            sub = obj.get_bound_subscriber()
            if sub:
                return format_html('<strong style="color: #1B3D8C; font-size: 1.05em;">{} (ID: {})</strong>', sub.subscriber_name, obj.bound_subscriber_id)
            return f"ID: {obj.bound_subscriber_id}"
        return mark_safe('<span style="color: red;">Not Bound</span>')
    get_subscriber_display.short_description = 'Bound Organization'


class CustomUserAdmin(UserAdmin):
    add_form = AdminUserCreationForm
    add_fieldsets = (
        ('Account Credentials', {
            'classes': ('wide',),
            'fields': ('username', 'email', 'password1', 'password2'),
            'description': 'Enter the organization credentials. Account will be active immediately.'
        }),
        ('Organization Assignment (Required)', {
            'classes': ('wide',),
            'fields': ('subscriber',),
            'description': 'Select the organization this user represents. The account will be strictly bound to this organization.'
        }),
        ('Personal Details (Optional)', {
            'classes': ('wide',),
            'fields': ('first_name', 'last_name'),
        }),
    )
    inlines = (UserProfileInline, )
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'get_bound_subscriber')
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'profile__is_bound')
    search_fields = ('username', 'email', 'first_name', 'last_name', 'profile__bound_subscriber_id')
    actions = ['unbind_selected_users', 'show_binding_details']

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Ensure profile binding is saved when creating a user via AdminUserCreationForm
        if 'subscriber' in getattr(form, 'cleaned_data', {}):
            subscriber = form.cleaned_data['subscriber']
            if subscriber:
                sub_id = subscriber.subscriber_id if hasattr(subscriber, 'subscriber_id') else int(subscriber)
                profile, _ = UserProfile.objects.get_or_create(user=obj)
                profile.bound_subscriber_id = sub_id
                profile.is_bound = True
                profile.binding_method = 'admin'
                if not profile.bound_at:
                    profile.bound_at = timezone.now()
                profile.save()
    
    def get_bound_subscriber(self, obj):
        try:
            if hasattr(obj, 'profile') and obj.profile.is_bound:
                subscriber = obj.profile.get_bound_subscriber()
                if subscriber:
                    return format_html(
                        '<span style="color: green;">{}</span>',
                        subscriber.subscriber_name
                    )
                return format_html(
                    '<span style="color: orange;">ID:{}</span>',
                    obj.profile.bound_subscriber_id
                )
            return mark_safe('<span style="color: red;">Not Bound</span>')
        except Exception:
            return mark_safe('<span style="color: red;">Error</span>')
    
    get_bound_subscriber.short_description = 'Bound Subscriber'
    get_bound_subscriber.admin_order_field = 'profile__bound_subscriber_id'
    
    def get_inline_instances(self, request, obj=None):
        if not obj:
            return []
        return super().get_inline_instances(request, obj)
    
    def unbind_selected_users(self, request, queryset):
        """
        Admin action to unbind selected users from their subscribers
        """
        bound_users = []
        unbound_users = []
        
        for user in queryset:
            try:
                profile = UserProfile.get_or_create_profile(user)
                if profile.is_bound:
                    subscriber = profile.get_bound_subscriber()
                    subscriber_name = subscriber.subscriber_name if subscriber else f"ID:{profile.bound_subscriber_id}"
                    
                    # Perform unbinding with admin user tracking
                    success = profile.unbind_subscriber(reason=f'admin_unbind_by_{request.user.username}')
                    
                    if success:
                        # Update the audit log to include who performed the unbinding
                        latest_log = UnbindingAuditLog.objects.filter(user=user).order_by('-unbound_at').first()
                        if latest_log:
                            latest_log.unbound_by = request.user
                            latest_log.save()
                        
                        bound_users.append(f"{user.username} (from {subscriber_name})")
                    else:
                        messages.error(request, f'Failed to unbind user {user.username}')
                else:
                    unbound_users.append(user.username)
            except Exception as e:
                messages.error(request, f'Error unbinding {user.username}: {str(e)}')
        
        if bound_users:
            messages.success(
                request, 
                f'Successfully unbound {len(bound_users)} users: {", ".join(bound_users)}'
            )
        
        if unbound_users:
            messages.warning(
                request,
                f'{len(unbound_users)} users were already unbound: {", ".join(unbound_users)}'
            )
    
    unbind_selected_users.short_description = "Unbind selected users from their subscribers"
    
    def show_binding_details(self, request, queryset):
        """
        Admin action to show detailed binding information for selected users
        """
        details = []
        
        for user in queryset:
            try:
                profile = UserProfile.get_or_create_profile(user)
                if profile.is_bound:
                    subscriber = profile.get_bound_subscriber()
                    subscriber_name = subscriber.subscriber_name if subscriber else f"ID:{profile.bound_subscriber_id}"
                    
                    bound_date = profile.bound_at.strftime('%Y-%m-%d %H:%M:%S') if profile.bound_at else 'Unknown'
                    method = profile.get_binding_method_display()
                    token = profile.binding_token or 'N/A'
                    
                    details.append(
                        f"{user.username}: Bound to {subscriber_name} on {bound_date} via {method} (Token: {token})"
                    )
                else:
                    details.append(f"{user.username}: Not bound to any subscriber")
            except Exception as e:
                details.append(f"{user.username}: Error retrieving binding info - {str(e)}")
        
        if details:
            message = "Binding Details:\n" + "\n".join(details)
            messages.info(request, message)
        else:
            messages.warning(request, "No binding details found for selected users")
    
    show_binding_details.short_description = "Show binding details for selected users"


@admin.register(UnbindingAuditLog)
class UnbindingAuditLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'previous_subscriber_id', 'unbinding_reason', 'unbound_at', 'unbound_by')
    list_filter = ('unbound_at', 'unbinding_reason')
    search_fields = ('user__username', 'previous_subscriber_id', 'unbinding_reason')
    readonly_fields = ('user', 'previous_subscriber_id', 'previous_binding_token', 
                      'previous_bound_at', 'unbinding_reason', 'unbound_at', 'unbound_by')
    
    fieldsets = (
        ('User Information', {
            'fields': ('user',)
        }),
        ('Previous Binding', {
            'fields': ('previous_subscriber_id', 'previous_binding_token', 'previous_bound_at')
        }),
        ('Unbinding Details', {
            'fields': ('unbinding_reason', 'unbound_at', 'unbound_by')
        }),
    )
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """
    Dedicated admin interface for UserProfile to manage bindings
    """
    form = UserProfileForm
    list_display = ('user', 'is_bound', 'get_subscriber_name', 'binding_method', 'bound_at')
    list_filter = ('is_bound', 'binding_method', 'bound_at')
    search_fields = ('user__username', 'user__email', 'bound_subscriber_id')
    readonly_fields = ('binding_token', 'bound_at', 'bound_by_ip')
    actions = ['unbind_selected_profiles']
    
    fieldsets = (
        ('User Information', {
            'fields': ('user',)
        }),
        ('Binding Status', {
            'fields': ('organization_select', 'is_bound', 'bound_subscriber_id', 'binding_method')
        }),
        ('Binding Details', {
            'fields': ('binding_token', 'bound_at', 'bound_by_ip'),
            'classes': ('collapse',)
        }),
    )
    
    def get_subscriber_name(self, obj):
        if obj.is_bound:
            subscriber = obj.get_bound_subscriber()
            if subscriber:
                return format_html(
                    '<span style="color: green;">{}</span>',
                    subscriber.subscriber_name
                )
            return format_html(
                '<span style="color: orange;">ID:{}</span>',
                obj.bound_subscriber_id
            )
        return mark_safe('<span style="color: red;">Not Bound</span>')
    
    get_subscriber_name.short_description = 'Subscriber'
    get_subscriber_name.admin_order_field = 'bound_subscriber_id'
    
    def unbind_selected_profiles(self, request, queryset):
        """
        Admin action to unbind selected user profiles
        """
        unbound_count = 0
        already_unbound = 0
        
        for profile in queryset:
            if profile.is_bound:
                subscriber = profile.get_bound_subscriber()
                subscriber_name = subscriber.subscriber_name if subscriber else f"ID:{profile.bound_subscriber_id}"
                
                success = profile.unbind_subscriber(reason=f'admin_unbind_by_{request.user.username}')
                
                if success:
                    # Update the audit log to include who performed the unbinding
                    latest_log = UnbindingAuditLog.objects.filter(user=profile.user).order_by('-unbound_at').first()
                    if latest_log:
                        latest_log.unbound_by = request.user
                        latest_log.save()
                    
                    unbound_count += 1
            else:
                already_unbound += 1
        
        if unbound_count > 0:
            messages.success(request, f'Successfully unbound {unbound_count} users.')
        
        if already_unbound > 0:
            messages.warning(request, f'{already_unbound} users were already unbound.')
    
    unbind_selected_profiles.short_description = "Unbind selected user profiles"


from .models import UserProfile, UnbindingAuditLog, EmailVerificationOTP


@admin.register(EmailVerificationOTP)
class EmailVerificationOTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'otp_code', 'otp_type', 'is_verified', 'attempts', 'created_at', 'expires_at', 'status_display')
    list_filter = ('otp_type', 'is_verified', 'created_at')
    search_fields = ('user__username', 'user__email', 'otp_code')
    readonly_fields = ('user', 'otp_code', 'otp_type', 'created_at', 'expires_at', 'attempts', 'ip_address')
    ordering = ('-created_at',)

    def status_display(self, obj):
        if obj.is_verified:
            return mark_safe('<span style="color: green; font-weight: bold;">Verified</span>')
        elif obj.is_expired():
            return mark_safe('<span style="color: red;">Expired</span>')
        return mark_safe('<span style="color: orange; font-weight: bold;">Active</span>')
    status_display.short_description = 'Status'



# Unregister the default UserAdmin and register our custom one
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)

