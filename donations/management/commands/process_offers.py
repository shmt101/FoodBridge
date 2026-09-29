from django.core.management.base import BaseCommand

from donations import workflow


class Command(BaseCommand):
    help = ("Expire overdue listings, time out stale offers (moving on to the next-nearest partner) "
            "and send expiry warnings. Page loads already do this lazily; run this from cron "
            "(e.g. every 5 minutes) if you want it to happen even when nobody is on the site.")

    def handle(self, *args, **options):
        stats = workflow.process_timeouts()
        self.stdout.write(self.style.SUCCESS(
            f"Expired {stats['expired']} listing(s), timed out {stats['offers_timed_out']} offer(s), "
            f"sent {stats['warned']} expiry warning(s)."))
