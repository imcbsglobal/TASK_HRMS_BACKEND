from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('login', '0019_remove_user_company_setup_completed'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompanySettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('tagline', models.CharField(blank=True, default='', max_length=255)),
                ('email', models.EmailField(blank=True, default='', max_length=254)),
                ('phone', models.CharField(blank=True, default='', max_length=50)),
                ('website', models.URLField(blank=True, default='')),
                ('address', models.TextField(blank=True, default='')),
                ('logo', models.TextField(blank=True, default='')),
                ('primaryColor', models.CharField(default='#6d3ef6', max_length=20)),
                ('currency', models.CharField(default='USD', max_length=10)),
                ('timezone', models.CharField(default='UTC', max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.OneToOneField(limit_choices_to={'role': 'ADMIN'}, on_delete=django.db.models.deletion.CASCADE, related_name='company_settings', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Company Settings',
                'verbose_name_plural': 'Company Settings',
            },
        ),
    ]
