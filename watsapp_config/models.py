from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class WhatsAppConfig(models.Model):
    PROVIDER_CHOICES = [
        ('ultramsg', 'UltraMsg'),
        ('waapi',    'WaAPI'),
        ('twilio',   'Twilio'),
        ('meta',     'Meta Cloud API'),
        ('wablas',   'Wablas'),
        ('custom',   'Custom / Other'),
    ]

    provider     = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default='ultramsg')
    instance_id  = models.CharField(max_length=255, blank=True)
    api_token    = models.CharField(max_length=500, blank=True)
    phone_number = models.CharField(max_length=50,  blank=True)
    webhook_url  = models.CharField(max_length=500, blank=True)
    is_active    = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'WhatsApp Config'

    def __str__(self):
        return f"{self.provider} ({'active' if self.is_active else 'inactive'})"


class WhatsAppAdminNumber(models.Model):
    name     = models.CharField(max_length=100)
    phone    = models.CharField(max_length=20)
    role     = models.CharField(max_length=100, default='HR Manager')
    purposes = models.JSONField(default=list)   # list of purpose keys
    active   = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.phone})"


class WhatsAppNotificationPurpose(models.Model):
    key             = models.CharField(max_length=50, unique=True)
    label           = models.CharField(max_length=100)
    icon            = models.CharField(max_length=10, blank=True)
    desc            = models.CharField(max_length=255, blank=True)
    enabled         = models.BooleanField(default=False)
    send_to_employee = models.BooleanField(default=True)
    send_to_admin   = models.BooleanField(default=False)

    def __str__(self):
        return self.label