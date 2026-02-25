from django.contrib import admin
from .models import LeaveType, Allowance, Deduction

# Register your models here.

@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'payment_status', 'is_active', 'created_at']
    list_filter = ['category', 'payment_status', 'is_active']
    search_fields = ['name', 'description']


@admin.register(Allowance)
class AllowanceAdmin(admin.ModelAdmin):
    list_display = ['employee', 'allowance_name', 'year', 'month', 'amount', 'is_active', 'created_at']
    list_filter = ['year', 'month', 'is_active', 'allowance_name']
    search_fields = ['employee__first_name', 'employee__last_name', 'allowance_name', 'description']
    raw_id_fields = ['employee']


@admin.register(Deduction)
class DeductionAdmin(admin.ModelAdmin):
    list_display = ['employee', 'deduction_name', 'year', 'month', 'amount', 'is_active', 'created_at']
    list_filter = ['year', 'month', 'is_active', 'deduction_name']
    search_fields = ['employee__first_name', 'employee__last_name', 'deduction_name', 'description']
    raw_id_fields = ['employee']
