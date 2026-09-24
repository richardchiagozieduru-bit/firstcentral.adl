# Generated manually to clean up obsolete SubscriberToken model

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('auto', '0007_uploadsession_invalidated_fields_and_feedback_contact_email'),
    ]

    operations = [
        migrations.DeleteModel(
            name='SubscriberToken',
        ),
    ]
