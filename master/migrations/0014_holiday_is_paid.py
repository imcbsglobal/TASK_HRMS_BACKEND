from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('master', '0013_section'),
    ]

    operations = [
        migrations.AddField(
            model_name='holiday',
            name='is_paid',
            field=models.BooleanField(
                default=True,
                help_text='Paid holiday = no salary deduction; Unpaid = salary deducted for that day',
            ),
        ),
    ]
