# Generated manually for the experience_certificate app.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('employee_management', '0030_employee_probation_end_date_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExperienceCertificate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('certificate_number', models.CharField(editable=False, max_length=30, unique=True)),
                ('employee_name', models.CharField(max_length=200)),
                ('employee_code', models.CharField(blank=True, max_length=30)),
                ('designation', models.CharField(max_length=150)),
                ('department', models.CharField(blank=True, max_length=150)),
                ('employment_type', models.CharField(blank=True, max_length=80)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('issue_date', models.DateField(default=django.utils.timezone.localdate)),
                ('conduct', models.CharField(blank=True, default='good', max_length=200)),
                ('responsibilities', models.TextField(blank=True)),
                ('remarks', models.TextField(blank=True)),
                ('signatory_name', models.CharField(blank=True, max_length=150)),
                ('signatory_designation', models.CharField(blank=True, default='HR Manager', max_length=150)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('issued', 'Issued'), ('revoked', 'Revoked')], default='draft', max_length=20)),
                ('issued_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('admin_owner', models.ForeignKey(blank=True, limit_choices_to={'role': 'ADMIN'}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='experience_certificates', to=settings.AUTH_USER_MODEL)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='experience_certificates', to='employee_management.employee')),
                ('issued_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='issued_experience_certificates', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-issue_date', '-created_at'],
            },
        ),
    ]
