from django.core.management.base import BaseCommand

from devices.discovery import scan


class Command(BaseCommand):
    help = "Discover Spooky Eyes boards on the LAN via mDNS and add/update them."

    def add_arguments(self, parser):
        parser.add_argument("--seconds", type=float, default=5.0)

    def handle(self, *args, seconds, **options):
        devices, errors = scan(seconds)
        for d in devices:
            self.stdout.write(self.style.SUCCESS(f"{d.name} ({d.device_id}) at {d.host}:{d.port}"))
        for e in errors:
            self.stderr.write(e)
        if not devices and not errors:
            self.stdout.write("No boards found.")
