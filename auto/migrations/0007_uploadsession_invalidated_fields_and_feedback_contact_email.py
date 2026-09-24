from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('auto', '0006_uploadsession_file_password_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='uploadsession',
            name='invalidated_at',
            field=models.DateTimeField(blank=True, help_text='When session was invalidated to allow re-upload', null=True),
        ),
        migrations.AddField(
            model_name='uploadsession',
            name='invalidated_by',
            field=models.ForeignKey(blank=True, help_text='Admin who invalidated session to allow re-upload', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invalidated_sessions', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='uploadsession',
            name='status',
            field=models.CharField(choices=[('pending', 'Pending'), ('uploading', 'Uploading'), ('processing', 'Processing'), ('awaiting_verification', 'Awaiting Verification'), ('finalizing', 'Finalizing'), ('completed', 'Completed'), ('failed', 'Failed'), ('cancelled', 'Cancelled'), ('invalidated', 'Invalidated (Re-upload Allowed)')], default='pending', max_length=30),
        ),
        migrations.AddField(
            model_name='feedback',
            name='contact_email',
            field=models.EmailField(blank=True, help_text='Contact email address for follow-up', max_length=254, null=True),
        ),
        migrations.AlterField(
            model_name='feedback',
            name='category',
            field=models.CharField(choices=[('bug', 'Bug Report'), ('feature', 'Feature Request'), ('general', 'General Feedback'), ('reupload_request', 'Request Re-upload / Correction')], default='general', help_text='Type of feedback', max_length=20),
        ),
    ]
