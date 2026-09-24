import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from acctmgt.models import EmailVerificationOTP

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Prune old and expired 2FA / Password Reset OTP records to maintain database performance.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Delete OTP records older than this many days (default: 30 days).'
        )
        parser.add_argument(
            '--all-expired',
            action='store_true',
            help='Delete all expired OTP records regardless of how recently they were created.'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate the pruning operation and display how many records would be deleted without removing them.'
        )

    def handle(self, *args, **options):
        days = options['days']
        all_expired = options['all_expired']
        dry_run = options['dry_run']

        now = timezone.now()

        if all_expired:
            qs = EmailVerificationOTP.objects.filter(expires_at__lt=now)
            target_desc = "all expired OTP records"
        else:
            cutoff = now - timedelta(days=days)
            qs = EmailVerificationOTP.objects.filter(created_at__lt=cutoff)
            target_desc = f"OTP records older than {days} days (created before {cutoff.strftime('%Y-%m-%d %H:%M:%S')})"

        count = qs.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"[DRY-RUN] Found {count} OTP record(s) matching criteria: {target_desc}.")
            )
            self.stdout.write("No records were removed.")
            return

        if count == 0:
            self.stdout.write(self.style.SUCCESS(f"No stale OTP records found matching: {target_desc}."))
            return

        deleted_count = EmailVerificationOTP.prune_stale_otps(days=days, include_all_expired=all_expired)
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully pruned {deleted_count} stale OTP record(s) from the database."
            )
        )
        logger.info(f"[DB PRUNING] Successfully deleted {deleted_count} OTP records matching '{target_desc}'.")
