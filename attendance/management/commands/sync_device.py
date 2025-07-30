from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from accounts.models import CustomUser, SystemConfiguration
from attendance.models import AttendanceDevice, AttendanceLog
from attendance.services import DeviceService, AttendanceService
from attendance.tasks import (
    sync_device_data,
    sync_all_devices,
    sync_employees_to_devices,
)
from attendance.utils import (
    DeviceManager,
    EmployeeDataManager,
    ValidationHelper,
    get_current_date,
    get_current_datetime,
)
from datetime import datetime, timedelta
import logging
import sys

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Synchronize attendance data with REALAND A-F011 devices"

    def add_arguments(self, parser):
        parser.add_argument("--device-id", type=str, help="Specific device ID to sync")

        parser.add_argument(
            "--all-devices", action="store_true", help="Sync all active devices"
        )

        parser.add_argument(
            "--sync-employees",
            action="store_true",
            help="Sync employee data to devices",
        )

        parser.add_argument(
            "--test-connection",
            action="store_true",
            help="Test device connections only",
        )

        parser.add_argument(
            "--force-sync",
            action="store_true",
            help="Force sync even if device was recently synced",
        )

        parser.add_argument(
            "--date-range", type=str, help="Date range for sync (YYYY-MM-DD:YYYY-MM-DD)"
        )

        parser.add_argument(
            "--async", action="store_true", help="Run sync as background task"
        )

        parser.add_argument("--verbose", action="store_true", help="Verbose output")

    def handle(self, *args, **options):
        self.verbosity = options.get("verbosity", 1)
        self.verbose = options.get("verbose", False)

        try:
            if options["test_connection"]:
                return self.test_device_connections()

            if options["sync_employees"]:
                return self.sync_employees_to_devices(options)

            if options["device_id"]:
                return self.sync_single_device(options["device_id"], options)

            if options["all_devices"]:
                return self.sync_all_devices(options)

            self.stdout.write(
                self.style.ERROR(
                    "Please specify --device-id, --all-devices, --sync-employees, or --test-connection"
                )
            )

        except Exception as e:
            logger.error(f"Device sync command failed: {str(e)}")
            raise CommandError(f"Command failed: {str(e)}")

    def sync_single_device(self, device_id, options):
        try:
            device = AttendanceDevice.objects.get(device_id=device_id)

            if not device.is_active:
                raise CommandError(f"Device {device_id} is not active")

            if not options["force_sync"]:
                if device.last_sync_time:
                    time_since_sync = get_current_datetime() - device.last_sync_time
                    min_sync_interval = SystemConfiguration.get_int_setting(
                        "MIN_DEVICE_SYNC_INTERVAL_MINUTES", 15
                    )

                    if time_since_sync.total_seconds() < (min_sync_interval * 60):
                        self.stdout.write(
                            self.style.WARNING(
                                f"Device {device_id} was synced {time_since_sync} ago. Use --force-sync to override."
                            )
                        )
                        return

            self.stdout.write(
                f"Starting sync for device: {device.device_name} ({device_id})"
            )

            if options["async"]:
                task = sync_device_data.delay(device.id)
                self.stdout.write(
                    self.style.SUCCESS(f"Sync task queued with ID: {task.id}")
                )
                return

            result = DeviceService.sync_device_data(device)

            if result["success"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✅ Device {device_id} synced successfully\n"
                        f"   📊 Logs synced: {result['logs_synced']}\n"
                        f"   🕐 Sync time: {result['sync_time']}"
                    )
                )

                if self.verbose:
                    self.display_device_details(device)

            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"❌ Device {device_id} sync failed: {result['error']}"
                    )
                )

        except AttendanceDevice.DoesNotExist:
            raise CommandError(f"Device with ID '{device_id}' not found")

    def sync_all_devices(self, options):
        active_devices = AttendanceDevice.active.all()

        if not active_devices.exists():
            self.stdout.write(self.style.WARNING("No active devices found"))
            return

        self.stdout.write(
            f"Starting sync for {active_devices.count()} active devices..."
        )

        if options["async"]:
            task = sync_all_devices.delay()
            self.stdout.write(
                self.style.SUCCESS(f"Bulk sync task queued with ID: {task.id}")
            )
            return

        successful_syncs = 0
        failed_syncs = 0
        total_logs = 0

        for device in active_devices:
            try:
                if not options["force_sync"]:
                    if device.last_sync_time:
                        time_since_sync = get_current_datetime() - device.last_sync_time
                        min_sync_interval = SystemConfiguration.get_int_setting(
                            "MIN_DEVICE_SYNC_INTERVAL_MINUTES", 15
                        )

                        if time_since_sync.total_seconds() < (min_sync_interval * 60):
                            if self.verbose:
                                self.stdout.write(
                                    f"⏭️  Skipping {device.device_id} (recently synced)"
                                )
                            continue

                self.stdout.write(
                    f"🔄 Syncing {device.device_name} ({device.device_id})..."
                )

                result = DeviceService.sync_device_data(device)

                if result["success"]:
                    successful_syncs += 1
                    total_logs += result["logs_synced"]

                    if self.verbose:
                        self.stdout.write(
                            f"   ✅ Success: {result['logs_synced']} logs"
                        )
                else:
                    failed_syncs += 1
                    self.stdout.write(f"   ❌ Failed: {result['error']}")

            except Exception as e:
                failed_syncs += 1
                self.stdout.write(f"   ❌ Error: {str(e)}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\n📊 SYNC SUMMARY:\n"
                f"   ✅ Successful: {successful_syncs}\n"
                f"   ❌ Failed: {failed_syncs}\n"
                f"   📋 Total logs: {total_logs}\n"
                f"   🕐 Completed at: {get_current_datetime()}"
            )
        )

    def sync_employees_to_devices(self, options):
        active_devices = AttendanceDevice.active.all()
        active_employees = CustomUser.active.all()

        if not active_devices.exists():
            self.stdout.write(self.style.WARNING("No active devices found"))
            return

        if not active_employees.exists():
            self.stdout.write(self.style.WARNING("No active employees found"))
            return

        self.stdout.write(
            f"Starting employee sync to {active_devices.count()} devices "
            f"for {active_employees.count()} employees..."
        )

        if options["async"]:
            task = sync_employees_to_devices.delay()
            self.stdout.write(
                self.style.SUCCESS(f"Employee sync task queued with ID: {task.id}")
            )
            return

        successful_devices = 0
        failed_devices = 0
        total_employees_synced = 0

        for device in active_devices:
            try:
                self.stdout.write(f"🔄 Syncing employees to {device.device_name}...")

                result = DeviceService.sync_employees_to_device(device)

                if result["success"]:
                    successful_devices += 1
                    total_employees_synced += result["employees_synced"]

                    if self.verbose:
                        self.stdout.write(
                            f"   ✅ Success: {result['employees_synced']}/{result['total_employees']} employees"
                        )
                else:
                    failed_devices += 1
                    self.stdout.write(f"   ❌ Failed: {result['error']}")

            except Exception as e:
                failed_devices += 1
                self.stdout.write(f"   ❌ Error: {str(e)}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\n📊 EMPLOYEE SYNC SUMMARY:\n"
                f"   ✅ Successful devices: {successful_devices}\n"
                f"   ❌ Failed devices: {failed_devices}\n"
                f"   👥 Total employees synced: {total_employees_synced}\n"
                f"   🕐 Completed at: {get_current_datetime()}"
            )
        )

    def test_device_connections(self):
        devices = AttendanceDevice.objects.all()

        if not devices.exists():
            self.stdout.write(self.style.WARNING("No devices configured"))
            return

        self.stdout.write(f"Testing connections for {devices.count()} devices...\n")

        online_devices = 0
        offline_devices = 0

        for device in devices:
            self.stdout.write(
                f"🔍 Testing {device.device_name} ({device.device_id})..."
            )

            try:
                is_connected, message = device.test_connection()

                if is_connected:
                    online_devices += 1
                    self.stdout.write(f"   ✅ ONLINE - {message}")

                    if device.status != "ACTIVE":
                        device.status = "ACTIVE"
                        device.save(update_fields=["status"])
                        self.stdout.write("   📝 Status updated to ACTIVE")

                else:
                    offline_devices += 1
                    self.stdout.write(f"   ❌ OFFLINE - {message}")

                    if device.status == "ACTIVE":
                        device.status = "ERROR"
                        device.save(update_fields=["status"])
                        self.stdout.write("   📝 Status updated to ERROR")

                if self.verbose:
                    self.display_device_details(device)

            except Exception as e:
                offline_devices += 1
                self.stdout.write(f"   ❌ ERROR - {str(e)}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\n📊 CONNECTION TEST SUMMARY:\n"
                f"   ✅ Online: {online_devices}\n"
                f"   ❌ Offline: {offline_devices}\n"
                f"   📊 Total: {devices.count()}\n"
                f"   🕐 Tested at: {get_current_datetime()}"
            )
        )

    def display_device_details(self, device):
        self.stdout.write(
            f"   📋 Device Details:\n"
            f"      🏷️  Name: {device.device_name}\n"
            f"      🆔 ID: {device.device_id}\n"
            f"      🌐 IP: {device.ip_address}:{device.port}\n"
            f"      📍 Location: {device.location}\n"
            f"      📊 Status: {device.status}\n"
            f"      🕐 Last Sync: {device.last_sync_time or 'Never'}\n"
        )

        recent_logs = AttendanceLog.objects.filter(
            device=device, created_at__gte=get_current_datetime() - timedelta(hours=24)
        ).count()

        self.stdout.write(f"      📋 Recent logs (24h): {recent_logs}")

    def get_version(self):
        return "1.0.0"
