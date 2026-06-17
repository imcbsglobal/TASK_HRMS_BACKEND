from django.contrib import admin
from .models import ActivityLog


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action_type', 'module', 'created_at', 'ip_address')
    list_filter = ('action_type', 'module', 'created_at')
    search_fields = ('user__username', 'description', 'module')
    readonly_fields = ('created_at',)

