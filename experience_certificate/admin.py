from django.contrib import admin

from .models import ExperienceCertificate


@admin.register(ExperienceCertificate)
class ExperienceCertificateAdmin(admin.ModelAdmin):
    list_display = (
        'certificate_number',
        'employee_name',
        'employee_code',
        'designation',
        'end_date',
        'status',
    )
    list_filter = ('status', 'issue_date', 'admin_owner')
    search_fields = ('certificate_number', 'employee_name', 'employee_code', 'designation')
    readonly_fields = ('certificate_number', 'issued_at', 'created_at', 'updated_at')
