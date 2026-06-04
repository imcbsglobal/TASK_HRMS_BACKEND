from django.contrib import admin
from .models import BreakRecord


@admin.register(BreakRecord)
class BreakRecordAdmin(admin.ModelAdmin):
    list_display = ('user', 'attendance', 'break_start', 'break_end', 'duration_minutes', 'admin_owner')
    list_filter = ('admin_owner', 'break_start')
    search_fields = ('user__username', 'user__email')


from .models import WFHRequest

@admin.register(WFHRequest)
class WFHRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'status', 'reviewed_by', 'reviewed_at', 'admin_owner')
    list_filter = ('status', 'admin_owner', 'date')
    search_fields = ('user__username', 'user__email', 'reason')
    readonly_fields = ('created_at', 'updated_at')
