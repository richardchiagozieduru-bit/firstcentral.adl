from django.urls import path
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy
from . import views

app_name = 'acctmgt'

urlpatterns = [
    # Registration
    path('register/', views.RegisterView.as_view(), name='register'),
    
    # 2FA / Email Verification
    path('verify-otp/', views.VerifyOTPView.as_view(), name='verify_otp'),
    path('resend-otp/', views.ResendOTPView.as_view(), name='resend_otp'),

    # Password Reset (OTP-based)
    path('forgot-password/', views.ForgotPasswordView.as_view(), name='forgot_password'),
    path('reset-password/', views.ResetPasswordConfirmView.as_view(), name='reset_password'),
    path('resend-reset-otp/', views.ResendResetOTPView.as_view(), name='resend_reset_otp'),

    # Login/Logout & 2FA
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('verify-login-2fa/', views.Login2FAVerifyView.as_view(), name='verify_login_2fa'),
    path('resend-login-2fa/', views.ResendLogin2FAView.as_view(), name='resend_login_2fa'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
]


