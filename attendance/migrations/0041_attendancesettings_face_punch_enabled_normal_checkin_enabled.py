# Generated migration – adds face_punch_enabled and normal_checkin_enabled
# to AttendanceSettings so admins can independently toggle face-recognition
# punch and standard check-in / check-out per tenant.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0040_add_leave_type_obj_to_leaverequest'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendancesettings',
            name='face_punch_enabled',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'When True the face-recognition kiosk/auto-punch endpoints are active. '
                    'Set to False to disable face punch-in/out for this tenant.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='attendancesettings',
            name='normal_checkin_enabled',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'When True employees can use the standard check-in / check-out endpoints. '
                    'Set to False to disable normal (non-face) check-in/out.'
                ),
            ),
        ),
    ]
