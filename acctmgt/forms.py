import logging
from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
from auto.models import Subscriber
from .models import UserProfile

logger = logging.getLogger(__name__)


class CustomAuthenticationForm(AuthenticationForm):

    """
    Custom authentication form with enhanced styling and validation
    """
    username = forms.CharField(
        max_length=254,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your username',
            'autofocus': True
        })
    )
    
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your password'
        })
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add Bootstrap classes to all fields
        for field_name, field in self.fields.items():
            field.widget.attrs.update({'class': 'form-control'})


from django.core.validators import RegexValidator

username_with_spaces_validator = RegexValidator(
    regex=r'^[\w\s.@+-]+$',
    message='Enter a valid username. This value may contain letters, numbers, spaces, and @/./+/-/_ characters.'
)

# Patch the User model field validators at runtime so full_clean() accepts spaces
try:
    User._meta.get_field('username').validators = [username_with_spaces_validator]
except Exception:
    pass


class CustomUserCreationForm(UserCreationForm):
    """
    Custom user creation form with styling, space-supported usernames, and email collection
    """
    username = forms.CharField(
        max_length=150,
        required=True,
        validators=[username_with_spaces_validator],
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your username'
        }),
        help_text='Required. 150 characters or fewer. Letters, digits, spaces, and @/./+/-/_ only.'
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email address'
        }),
        help_text='Reports will be sent to this email after processing.'
    )
    
    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ensure model field validators accept spaces for this instance
        try:
            self.instance._meta.get_field('username').validators = [username_with_spaces_validator]
        except Exception:
            pass
            
        # Add Bootstrap classes to all fields
        for field_name, field in self.fields.items():
            field.widget.attrs.update({
                'class': 'form-control',
            })
        
        # Update placeholders
        self.fields['username'].widget.attrs.update({
            'placeholder': 'Enter your username'
        })
        self.fields['password1'].widget.attrs.update({
            'placeholder': 'Enter your password'
        })
        self.fields['password2'].widget.attrs.update({
            'placeholder': 'Confirm your password'
        })
    
    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        if not username:
            raise ValidationError('Username cannot be empty.')
        username_with_spaces_validator(username)
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError('A user with that username already exists.')
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise ValidationError('An account with this email already exists.')
        return email

    def _post_clean(self):
        super()._post_clean()
        # Ensure default model-level UnicodeUsernameValidator does not inject errors for spaces
        if 'username' in self._errors:
            self._errors['username'] = [
                err for err in self._errors['username']
                if 'only letters, numbers, and @/./+/-/_' not in str(err)
            ]
            if not self._errors['username']:
                del self._errors['username']






class AdminUserCreationForm(UserCreationForm):
    """
    Admin form for provisioning organization users in Django Admin.
    Strictly creates regular organization users bound directly to the selected subscriber.
    """
    username = forms.CharField(
        max_length=150,
        required=True,
        validators=[username_with_spaces_validator],
        widget=forms.TextInput(attrs={'class': 'vTextField', 'placeholder': 'Enter username'}),
        help_text='Required. 150 characters or fewer.'
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'vTextField', 'placeholder': 'organization@bank.com'}),
        help_text='Required. Reports and notifications will be sent to this email.'
    )
    first_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    subscriber = forms.ChoiceField(
        label='Organization / Subscriber',
        choices=[],
        required=True,
        help_text='Select the organization this user represents. The account will be permanently bound to this organization.'
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'first_name', 'last_name', 'subscriber', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Populate subscriber choices dynamically from MSSQL Subscriber model
        choices = [('', '--------- Select Organization ---------')]
        try:
            for sub in Subscriber.objects.all().order_by('subscriber_name'):
                choices.append((str(sub.subscriber_id), f"{sub.subscriber_name} (ID: {sub.subscriber_id})"))
        except Exception as e:
            logger.warning(f"Could not load subscriber choices for AdminUserCreationForm: {e}")
        self.fields['subscriber'].choices = choices

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        if not username:
            raise ValidationError('Username cannot be empty.')
        username_with_spaces_validator(username)
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError('A user with that username already exists.')
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip()
        if not email:
            raise ValidationError('Email is required.')
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('A user with this email address already exists.')
        return email

    def clean_subscriber(self):
        subscriber_id_str = self.cleaned_data.get('subscriber')
        if not subscriber_id_str:
            raise ValidationError('Please select an organization for this user.')
        try:
            sub_id = int(float(subscriber_id_str))
            subscriber = Subscriber.objects.get(subscriber_id=sub_id)
            return subscriber
        except (ValueError, Subscriber.DoesNotExist):
            raise ValidationError('Selected organization does not exist.')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data.get('first_name', '')
        user.last_name = self.cleaned_data.get('last_name', '')
        # Strictly regular organization user: NOT staff, NOT superuser, active immediately
        user.is_staff = False
        user.is_superuser = False
        user.is_active = True

        subscriber = self.cleaned_data.get('subscriber')

        def _save_profile():
            if subscriber and user.pk:
                sub_id = subscriber.subscriber_id if hasattr(subscriber, 'subscriber_id') else int(subscriber)
                profile, _ = UserProfile.objects.get_or_create(user=user)
                profile.bound_subscriber_id = sub_id
                profile.is_bound = True
                profile.binding_method = 'admin'
                profile.bound_at = timezone.now()
                profile.save()

        self.save_m2m = _save_profile

        if commit:
            user.save()
            _save_profile()
        return user



