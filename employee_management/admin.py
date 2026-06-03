from django.contrib import admin
from .models import SalaryIncrementHistory, EmployeeDocument


@admin.register(SalaryIncrementHistory)
class SalaryIncrementHistoryAdmin(admin.ModelAdmin):
    list_display = (
        'employee', 'increment_date', 'old_salary', 'new_salary',
        'increment_amount', 'increment_percentage',
        'increment_cycle_months', 'next_increment_date', 'created_by',
    )
    list_filter = ('increment_date', 'created_at')
    search_fields = (
        'employee__employee_id', 'employee__first_name',
        'employee__last_name', 'created_by__username',
    )
    readonly_fields = ('increment_amount', 'increment_percentage', 'created_at')


@admin.register(EmployeeDocument)
class EmployeeDocumentAdmin(admin.ModelAdmin):
    list_display = ('employee', 'title', 'document_type', 'uploaded_at')
    list_filter = ('document_type', 'uploaded_at')
    search_fields = ('employee__employee_id', 'employee__first_name', 'employee__last_name', 'title')
    readonly_fields = ('uploaded_at', 'updated_at')
