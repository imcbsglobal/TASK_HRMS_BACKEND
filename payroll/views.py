from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Sum
from django.utils import timezone
from datetime import datetime
import calendar
from decimal import Decimal, ROUND_HALF_UP

from .models import Payroll
from .serializers import PayrollSerializer, PayrollDetailSerializer, PayrollCalculateSerializer
from employee_management.models import Employee
from master.models import Allowance, Deduction
from attendance.models import Attendance, LeaveRequest


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_admin_owner(user):
    """
    Return the ADMIN who owns the current request's tenant scope.
    """
    if user.role == 'ADMIN':
        return user
    if user.role == 'USER':
        return user.admin_owner
    return None  # SUPER_ADMIN


def _get_leave_type_breakdown(employee, year, month):
    """
    Get detailed breakdown of leave types for the employee in the given month.
    """
    from login.models import User
    from datetime import date

    leave_breakdown = {
        'sick_leave':      {'count': 0, 'days': 0, 'label': 'Sick Leave'},
        'casual_leave':    {'count': 0, 'days': 0, 'label': 'Casual Leave'},
        'annual_leave':    {'count': 0, 'days': 0, 'label': 'Annual Leave'},
        'maternity_leave': {'count': 0, 'days': 0, 'label': 'Maternity Leave'},
        'paternity_leave': {'count': 0, 'days': 0, 'label': 'Paternity Leave'},
        'unpaid_leave':    {'count': 0, 'days': 0, 'label': 'Unpaid Leave'},
        'other_leave':     {'count': 0, 'days': 0, 'label': 'Other Leave'},
    }

    try:
        user = User.objects.filter(email=employee.email).first()
        if not user:
            return leave_breakdown

        first_day = date(year, month, 1)
        last_day  = date(year, month, calendar.monthrange(year, month)[1])

        leave_requests = LeaveRequest.objects.filter(
            user=user,
            status='approved',
            start_date__lte=last_day,
            end_date__gte=first_day,
        )

        for leave_req in leave_requests:
            actual_start   = max(leave_req.start_date, first_day)
            actual_end     = min(leave_req.end_date,   last_day)
            days_in_month  = (actual_end - actual_start).days + 1
            leave_type_key = f"{leave_req.leave_type}_leave"
            if leave_type_key in leave_breakdown:
                leave_breakdown[leave_type_key]['count'] += 1
                leave_breakdown[leave_type_key]['days']  += days_in_month

    except Exception:
        pass

    return leave_breakdown


def _get_attendance_summary(employee, year, month, total_days):
    """
    Match Employee → auth User by email, then count every attendance status
    """
    summary = {
        'present_days':   0,
        'absent_days':    0,
        'late_days':      0,
        'half_days':      0,
        'leave_days':     0,
        'working_days':   0,
        'paid_days':      0,
        'leave_breakdown': {},
    }
    try:
        from login.models import User
        user = User.objects.filter(email=employee.email).first()
        if user:
            att_qs = Attendance.objects.filter(user=user, date__year=year, date__month=month)
            summary['present_days'] = att_qs.filter(status='present').count()
            summary['absent_days']  = att_qs.filter(status='absent').count()
            summary['late_days']    = att_qs.filter(status='late').count()
            summary['half_days']    = att_qs.filter(status='half_day').count()
            summary['leave_days']   = att_qs.filter(status='leave').count()
            summary['working_days'] = summary['present_days'] + summary['late_days'] + summary['half_days']
            summary['paid_days']    = summary['present_days'] + summary['late_days'] + summary['leave_days']
            summary['leave_breakdown'] = _get_leave_type_breakdown(employee, year, month)
        else:
            summary['present_days']    = total_days
            summary['working_days']    = total_days
            summary['paid_days']       = total_days
            summary['leave_breakdown'] = _get_leave_type_breakdown(employee, year, month)
    except Exception:
        summary['present_days']    = total_days
        summary['working_days']    = total_days
        summary['paid_days']       = total_days
        summary['leave_breakdown'] = {}
    return summary


def _calc_att_deduction(basic_salary, total_days, absent_days, half_days):
    basic   = Decimal(str(basic_salary))
    divisor = Decimal(str(total_days)) if total_days else Decimal('1')
    per_day = (basic / divisor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    deduct  = (
        (Decimal(str(absent_days)) + Decimal(str(half_days)) * Decimal('0.5')) * per_day
    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return per_day, deduct


def _calc_pf_amount(employee):
    if not employee.pf_enabled:
        return Decimal('0'), None

    basic   = Decimal(str(employee.salary))
    contrib = Decimal(str(employee.employee_pf_contribution))

    if employee.pf_contribution_type == 'fixed':
        amount = contrib
        label  = f"Fixed ₹{contrib}"
    else:
        amount = (basic * contrib / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        label  = f"{contrib}% of Basic"

    return amount, {
        'source':           'auto_pf',
        'name':             'Provident Fund (Employee)',
        'amount':           str(amount),
        'description':      f"Auto-computed: {label}",
        'pf_number':        employee.pf_number or '',
        'contribution_type': employee.pf_contribution_type,
        'rate':             str(contrib),
    }


def _calc_overtime_amount(employee, total_days_in_month):
    if not employee.overtime_enabled:
        return Decimal('0'), None

    basic    = Decimal(str(employee.salary))
    rate     = Decimal(str(employee.overtime_rate))
    max_hrs  = Decimal(str(employee.max_overtime_hours_per_month))

    if max_hrs <= 0:
        return Decimal('0'), None

    if employee.overtime_rate_type == 'fixed':
        amount = (rate * max_hrs).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        label  = f"₹{rate}/hr × {max_hrs} hrs"
    else:
        hours_in_month = Decimal(str(total_days_in_month)) * Decimal('8')
        hourly_rate    = basic / hours_in_month
        amount         = (hourly_rate * rate * max_hrs).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        label          = f"{rate}x multiplier × {max_hrs} hrs"

    return amount, {
        'source':      'auto_overtime',
        'name':        'Overtime',
        'amount':      str(amount),
        'description': f"Auto-computed: {label}",
        'rate_type':   employee.overtime_rate_type,
        'rate':        str(rate),
        'max_hours':   str(max_hrs),
    }


def _build_payroll_dict(employee, year, month, admin_owner=None):
    """
    Central helper for payroll calculation.
    """
    total_days   = calendar.monthrange(year, month)[1]
    basic_salary = employee.salary

    # ── Attendance ────────────────────────────────────────────────────────────
    att = _get_attendance_summary(employee, year, month, total_days)
    per_day, att_deduction = _calc_att_deduction(
        basic_salary, total_days, att['absent_days'], att['half_days']
    )

    # ── Manual allowances & deductions from DB (tenant-scoped) ────────────────
    db_allowances = Allowance.objects.filter(
        employee=employee, year=year, month=month, is_active=True
    )
    db_deductions = Deduction.objects.filter(
        employee=employee, year=year, month=month, is_active=True
    )
    
    if admin_owner:
        db_allowances = db_allowances.filter(admin_owner=admin_owner)
        db_deductions = db_deductions.filter(admin_owner=admin_owner)

    db_allowances_total = db_allowances.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    total_manual_deductions = db_deductions.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    # ── Auto-compute PF & Overtime ────────────────────────────────────────────
    pf_amount,  pf_detail  = _calc_pf_amount(employee)
    ot_amount,  ot_detail  = _calc_overtime_amount(employee, total_days)

    auto_allowances_total = pf_amount + ot_amount

    # ── Totals ────────────────────────────────────────────────────────────────
    total_allowances = Decimal(str(db_allowances_total)) + auto_allowances_total
    total_deductions = att_deduction + Decimal(str(total_manual_deductions))

    net_salary = (
        Decimal(str(basic_salary)) + total_allowances - total_deductions
    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return {
        'basic_salary':            str(basic_salary),
        'total_allowances':        str(total_allowances),
        'total_deductions':        str(total_deductions),
        'attendance_deduction':    str(att_deduction),
        'manual_deductions_total': str(total_manual_deductions),
        'net_salary':              str(net_salary),
        'total_days_in_month':     total_days,
        'total_working_days':      att['working_days'],
        'db_allowances_total':     str(db_allowances_total),
        'pf_amount':               str(pf_amount),
        'overtime_amount':         str(ot_amount),
        'auto_allowances_total':   str(auto_allowances_total),
        'attendance': {
            'present_days':         att['present_days'],
            'absent_days':          att['absent_days'],
            'late_days':            att['late_days'],
            'half_days':            att['half_days'],
            'leave_days':           att['leave_days'],
            'working_days':         att['working_days'],
            'paid_days':            att['paid_days'],
            'per_day_salary':       str(per_day),
            'attendance_deduction': str(att_deduction),
            'leave_breakdown':      att.get('leave_breakdown', {}),
        },
        'allowances': [
            {
                'id': a.id, 'name': a.allowance_name, 'amount': str(a.amount), 
                'description': a.description, 'source': 'manual'
            } for a in db_allowances
        ] + ([pf_detail] if pf_detail else []) + ([ot_detail] if ot_detail else []),
        'deductions': [
            {'id': d.id, 'name': d.deduction_name, 'amount': str(d.amount), 'description': d.description}
            for d in db_deductions
        ],
        'employee_settings': {
            'pf_enabled':                   employee.pf_enabled,
            'pf_number':                    employee.pf_number,
            'pf_contribution_type':         employee.pf_contribution_type,
            'employee_pf_contribution':     str(employee.employee_pf_contribution),
            'employer_pf_contribution':     str(employee.employer_pf_contribution),
            'overtime_enabled':             employee.overtime_enabled,
            'overtime_rate_type':           employee.overtime_rate_type,
            'overtime_rate':                str(employee.overtime_rate),
            'max_overtime_hours_per_month': str(employee.max_overtime_hours_per_month),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────

class PayrollViewSet(viewsets.ModelViewSet):
    """ViewSet for Payroll CRUD operations"""
    queryset           = Payroll.objects.all()
    serializer_class   = PayrollSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'SUPER_ADMIN':
            qs = Payroll.objects.select_related('employee', 'employee__department', 'processed_by').all()
        else:
            admin = _get_admin_owner(user)
            if admin is None:
                return Payroll.objects.none()
            qs = Payroll.objects.select_related('employee', 'employee__department', 'processed_by').filter(admin_owner=admin)
        
        p = self.request.query_params
        if p.get('employee'): qs = qs.filter(employee_id=p['employee'])
        if p.get('year'):     qs = qs.filter(year=p['year'])
        if p.get('month'):    qs = qs.filter(month=p['month'])
        if p.get('status'):   qs = qs.filter(status=p['status'])
        return qs

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return PayrollDetailSerializer
        return PayrollSerializer

    def perform_create(self, serializer):
        admin = _get_admin_owner(self.request.user)
        serializer.save(admin_owner=admin)

    @action(detail=False, methods=['post'], url_path='calculate')
    def calculate_payroll(self, request):
        ser = PayrollCalculateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        employee_id = ser.validated_data['employee_id']
        year        = ser.validated_data['year']
        month       = ser.validated_data['month']

        try:
            # Enforce tenant scope for employee lookup
            user = request.user
            emp_qs = Employee.objects.all()
            if user.role != 'SUPER_ADMIN':
                admin = _get_admin_owner(user)
                emp_qs = emp_qs.filter(admin_owner=admin)
            
            employee = emp_qs.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)

        admin = _get_admin_owner(request.user)
        data = _build_payroll_dict(employee, year, month, admin_owner=admin)
        data.update({
            'employee_id':   employee.id,
            'employee_name': f"{employee.first_name} {employee.last_name}",
            'employee_code': employee.employee_id,
            'year':          year,
            'month':         month,
            'month_name':    dict(Payroll.MONTH_CHOICES).get(month, ''),
        })
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='employee-data')
    def employee_data(self, request):
        employee_id = request.query_params.get('employee_id')
        year        = request.query_params.get('year')
        month       = request.query_params.get('month')

        if not all([employee_id, year, month]):
            return Response({'error': 'employee_id, year, and month are required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            user = request.user
            emp_qs = Employee.objects.all()
            if user.role != 'SUPER_ADMIN':
                admin = _get_admin_owner(user)
                emp_qs = emp_qs.filter(admin_owner=admin)
            employee = emp_qs.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)
            
        try:
            year, month = int(year), int(month)
        except ValueError:
            return Response({'error': 'Invalid year or month'}, status=status.HTTP_400_BAD_REQUEST)

        admin = _get_admin_owner(request.user)
        data = _build_payroll_dict(employee, year, month, admin_owner=admin)
        data['employee'] = {
            'id':          employee.id,
            'employee_id': employee.employee_id,
            'name':        f"{employee.first_name} {employee.last_name}",
            'email':       employee.email,
            'position':    employee.position,
            'department':  employee.department.name if employee.department else '',
            'phone':       employee.phone,
        }
        data['payroll'] = {
            'year': year, 'month': month, 'month_name': dict(Payroll.MONTH_CHOICES).get(month, ''),
            'basic_salary': data['basic_salary'], 'total_allowances': data['total_allowances'],
            'total_deductions': data['total_deductions'], 'attendance_deduction': data['attendance_deduction'],
            'manual_deductions_total': data['manual_deductions_total'], 'net_salary': data['net_salary'],
            'total_days_in_month': data['total_days_in_month'], 'total_working_days': data['total_working_days'],
            'pf_amount': data['pf_amount'], 'overtime_amount': data['overtime_amount'],
            'db_allowances_total': data['db_allowances_total'], 'auto_allowances_total': data['auto_allowances_total'],
        }
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='process')
    def process_payroll(self, request, pk=None):
        payroll = self.get_object()
        if payroll.status in ('processed', 'paid'):
            return Response({'error': 'Payroll is already processed or paid'}, status=status.HTTP_400_BAD_REQUEST)
        payroll.status       = 'processed'
        payroll.processed_by = request.user
        payroll.processed_at = timezone.now()
        payroll.save()
        return Response(PayrollDetailSerializer(payroll).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='mark-paid')
    def mark_paid(self, request, pk=None):
        payroll = self.get_object()
        if payroll.status == 'paid':
            return Response({'error': 'Payroll is already marked as paid'}, status=status.HTTP_400_BAD_REQUEST)
        payroll.status            = 'paid'
        payroll.payment_date      = request.data.get('payment_date') or payroll.payment_date
        payroll.payment_reference = request.data.get('payment_reference', '')
        payroll.save()
        return Response(PayrollDetailSerializer(payroll).data, status=status.HTTP_200_OK)