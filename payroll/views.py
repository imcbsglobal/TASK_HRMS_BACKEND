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
from master.models import Allowance, Deduction, PayrollPolicy
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


# ─────────────────────────────────────────────────────────────────────────────
# POLICY VIOLATION CHECKER
# ─────────────────────────────────────────────────────────────────────────────

def _check_policy_violations(employee, year, month, policy_data, admin):
    """
    Compare an employee's attendance for the given month against policy_data.
    Returns a list of violation dicts:
      [{ violation_type, description, count, deduction_amount }, ...]
    Returns an empty list when the employee is within all policy limits.
    """
    from login.models import User
    from attendance.models import LateArrivalRequest, EarlyDepartureRequest

    violations = []

    # ── Resolve auth user from employee email ─────────────────────────────────
    auth_user = User.objects.filter(email=employee.email).first()
    if not auth_user:
        return violations

    basic_salary = Decimal(str(employee.salary))
    calendar_days = calendar.monthrange(year, month)[1]
    per_day = (basic_salary / Decimal(str(calendar_days))).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    per_half_day = (per_day / Decimal('2')).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    per_half_hour = (per_day / Decimal('16')).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )

    def _policy_fine_amount(policy, billable_count):
        fine = policy.get('fine', {}) or {}
        value = Decimal(str(fine.get('value') or '0'))
        if value <= 0 or billable_count <= 0:
            return None

        if fine.get('type') == 'percentage':
            per_occurrence = (basic_salary * value / Decimal('100')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
        else:
            per_occurrence = value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        return (per_occurrence * Decimal(str(billable_count))).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

    # ── Late Arrival violations ───────────────────────────────────────────────
    la_policy = policy_data.get('attendance', {}).get('lateArrival', {})
    if la_policy.get('enabled', True):
        forgiven = int(la_policy.get('forgivenLatesPerMonth', 3))
        habitual_threshold = 4  # 4th+ late in a month = habitual

        late_qs = LateArrivalRequest.objects.filter(
            user=auth_user,
            date__year=year,
            date__month=month,
            admin_owner=admin,
        )
        late_qs = late_qs.exclude(status__in=['waived', 'rejected', 'cancelled'])
        late_count = late_qs.count()
        billable = max(0, late_count - forgiven)

        if billable > 0:
            tiers = la_policy.get('tiers', [])
            habitual_action = la_policy.get('habitualLate', 'full_day_cut')

            if late_count >= habitual_threshold:
                action = habitual_action
                desc = f"Habitual late arrival ({late_count} times, {forgiven} forgiven)"
            elif tiers:
                tier = tiers[min(billable - 1, len(tiers) - 1)]
                action = tier.get('action', 'warn_only')
                desc = f"Late arrival ({late_count} times, {forgiven} forgiven, {billable} billable)"
            else:
                action = 'warn_only'
                desc = f"Late arrival ({late_count} times)"

            deduction_amount = _policy_fine_amount(la_policy, billable)
            if deduction_amount is None:
                deduction_amount = Decimal('0')
                if action == 'half_day_cut':
                    deduction_amount = per_half_day * Decimal(str(billable))
                elif action == 'full_day_cut':
                    deduction_amount = per_day * Decimal(str(billable))
                elif action == 'half_hour_cut':
                    deduction_amount = per_half_hour * Decimal(str(billable))
            # warn_only / no_action → 0

            violations.append({
                'violation_type':   'late_arrival',
                'description':      desc,
                'count':            late_count,
                'billable_count':   billable,
                'action':           action,
                'fine':             la_policy.get('fine', {}),
                'deduction_amount': float(deduction_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
            })

    # ── Early Departure violations ────────────────────────────────────────────
    ed_policy = policy_data.get('attendance', {}).get('earlyDeparture', {})
    if ed_policy.get('enabled', True):
        forgiven_early = int(ed_policy.get('forgivenEarlyPerMonth', 2))

        early_qs = EarlyDepartureRequest.objects.filter(
            user=auth_user,
            date__year=year,
            date__month=month,
            admin_owner=admin,
        )
        early_qs = early_qs.exclude(status__in=['waived', 'rejected', 'cancelled'])
        early_count = early_qs.count()
        billable_early = max(0, early_count - forgiven_early)

        if billable_early > 0:
            ed_tiers = ed_policy.get('tiers', [])
            if ed_tiers:
                tier = ed_tiers[min(billable_early - 1, len(ed_tiers) - 1)]
                action = tier.get('action', 'warn_only')
            else:
                action = ed_policy.get('unapprovedEarlyLeave', 'half_hour_cut')

            deduction_amount = _policy_fine_amount(ed_policy, billable_early)
            if deduction_amount is None:
                deduction_amount = Decimal('0')
                if action == 'half_day_cut':
                    deduction_amount = per_half_day * Decimal(str(billable_early))
                elif action == 'full_day_cut':
                    deduction_amount = per_day * Decimal(str(billable_early))
                elif action in ('half_hour_cut', 'full_absence_marked'):
                    deduction_amount = per_half_hour * Decimal(str(billable_early))

            violations.append({
                'violation_type':   'early_departure',
                'description':      f"Early departure ({early_count} times, {forgiven_early} forgiven, {billable_early} billable)",
                'count':            early_count,
                'billable_count':   billable_early,
                'action':           action,
                'fine':             ed_policy.get('fine', {}),
                'deduction_amount': float(deduction_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
            })

    # ── Missed punch violations ───────────────────────────────────────────────
    missed_qs = Attendance.objects.filter(
        user=auth_user,
        date__year=year,
        date__month=month,
        check_in_time__isnull=False,
        check_out_time__isnull=True,
        admin_owner=admin,
    )
    missed_qs = missed_qs.exclude(check_out_waived=True)
    missed_count = missed_qs.count()
    if missed_count > 0:
        deduction_amount = per_half_hour * Decimal(str(missed_count))
        violations.append({
            'violation_type':   'missed_punch',
            'description':      f"Missed check-out punch ({missed_count} times)",
            'count':            missed_count,
            'billable_count':   missed_count,
            'action':           'warn_only',
            'deduction_amount': float(deduction_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
        })

    return violations


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


def _policy_violation_label(violation_type):
    labels = {
        'late_arrival': 'Late Arrival',
        'early_departure': 'Early Departure',
        'missed_punch': 'Missed Punch',
        'all': 'All Violations',
    }
    return labels.get(violation_type or 'all', str(violation_type).replace('_', ' ').title())


def _policy_decision_name(kind, violation_type, month, year):
    month_name = dict(Payroll.MONTH_CHOICES).get(month, str(month))
    label = _policy_violation_label(violation_type)
    return f"Policy {kind} - {label} - {month_name} {year}"


def _policy_decision_qs(employee, year, month, admin_owner=None):
    qs = Deduction.objects.filter(
        employee=employee,
        year=year,
        month=month,
        deduction_name__startswith='Policy ',
    )
    if admin_owner:
        qs = qs.filter(admin_owner=admin_owner)
    return qs


def _build_payroll_dict(employee, year, month, admin_owner=None):
    """
    Central helper for payroll calculation.
    """
    calendar_days = calendar.monthrange(year, month)[1]
    
    from master.models import Holiday
    holidays_qs = Holiday.objects.filter(date__year=year, date__month=month, is_active=True)
    if admin_owner:
        holidays_qs = holidays_qs.filter(admin_owner=admin_owner)
    holiday_count = holidays_qs.count()
    
    total_days = max(1, calendar_days - holiday_count)
    
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

    db_policy_deductions = db_deductions.filter(deduction_name__startswith='Policy Deduction')
    db_other_deductions = db_deductions.exclude(deduction_name__startswith='Policy Deduction')

    db_allowances_total = db_allowances.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    total_manual_deductions = db_other_deductions.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    policy_deductions_total = (
        db_policy_deductions.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    policy_obj = PayrollPolicy.objects.filter(admin_owner=admin_owner).first() if admin_owner else None
    policy_data = policy_obj.policy_data if policy_obj else {}
    policy_violations = _check_policy_violations(employee, year, month, policy_data, admin_owner)

    # ── Auto-compute PF & Overtime ────────────────────────────────────────────
    pf_amount,  pf_detail  = _calc_pf_amount(employee)
    ot_amount,  ot_detail  = _calc_overtime_amount(employee, total_days)

    auto_allowances_total = pf_amount + ot_amount

    # ── Totals ────────────────────────────────────────────────────────────────
    total_allowances = Decimal(str(db_allowances_total)) + auto_allowances_total
    total_deductions = att_deduction + Decimal(str(total_manual_deductions)) + policy_deductions_total

    net_salary = (
        Decimal(str(basic_salary)) + total_allowances - total_deductions
    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return {
        'basic_salary':            str(basic_salary),
        'total_allowances':        str(total_allowances),
        'total_deductions':        str(total_deductions),
        'attendance_deduction':    str(att_deduction),
        'manual_deductions_total': str(total_manual_deductions),
        'policy_deductions_total': str(policy_deductions_total),
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
            {
                'id': d.id,
                'name': d.deduction_name,
                'amount': str(d.amount),
                'description': d.description,
                'source': 'policy' if d.deduction_name.startswith('Policy Deduction') else 'manual',
            }
            for d in list(db_other_deductions) + list(db_policy_deductions)
        ],
        'policy_violations': policy_violations,
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
            'policy_deductions_total': data['policy_deductions_total'],
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

    # ─────────────────────────────────────────────────────────────────────────
    # POLICY VIOLATION ENDPOINTS
    # ─────────────────────────────────────────────────────────────────────────

    @action(detail=False, methods=['get'], url_path='policy-violations')
    def policy_violations(self, request):
        """
        GET /api/payroll/policy-violations/?year=YYYY&month=MM

        Checks every employee's attendance data for the given month against the
        saved PayrollPolicy and returns a list of employees who have exceeded
        the configured limits (late arrivals, early departures, leave days).

        Each violation item includes:
          - employee info
          - violation details (what was exceeded, by how much)
          - suggested deduction amount
          - whether a deduction has already been applied this month
        """
        year  = request.query_params.get('year')
        month = request.query_params.get('month')

        if not year or not month:
            return Response(
                {'error': 'year and month query params are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            year, month = int(year), int(month)
        except ValueError:
            return Response({'error': 'Invalid year or month'}, status=status.HTTP_400_BAD_REQUEST)

        user  = request.user
        admin = _get_admin_owner(user)

        # Load the payroll policy for this tenant
        from master.models import PayrollPolicy
        policy_obj = None
        if admin:
            policy_obj = PayrollPolicy.objects.filter(admin_owner=admin).first()

        policy_data = policy_obj.policy_data if policy_obj else {}

        # Fetch all employees for this tenant
        emp_qs = Employee.objects.all()
        if user.role != 'SUPER_ADMIN':
            if admin is None:
                return Response([], status=status.HTTP_200_OK)
            emp_qs = emp_qs.filter(admin_owner=admin)

        violations = []

        for employee in emp_qs.select_related('department'):
            emp_violations = _check_policy_violations(employee, year, month, policy_data, admin)
            if emp_violations:
                decision_names = set(
                    _policy_decision_qs(employee, year, month, admin)
                    .values_list('deduction_name', flat=True)
                )
                for violation in emp_violations:
                    violation_type = violation.get('violation_type') or 'all'
                    deduction_name = _policy_decision_name('Deduction', violation_type, month, year)
                    waiver_name = _policy_decision_name('Waiver', violation_type, month, year)
                    if deduction_name in decision_names:
                        violation['decision_status'] = 'approved_deduct'
                    elif waiver_name in decision_names:
                        violation['decision_status'] = 'waived'
                    else:
                        violation['decision_status'] = 'pending'

                violations.append({
                    'employee': {
                        'id':          employee.id,
                        'employee_id': employee.employee_id,
                        'name':        f"{employee.first_name} {employee.last_name}",
                        'position':    employee.position,
                        'department':  employee.department.name if employee.department else '',
                        'salary':      str(employee.salary),
                    },
                    'violations':       emp_violations,
                    'total_deduction':  str(sum(v['deduction_amount'] for v in emp_violations)),
                    'already_applied':  any(v.get('decision_status') == 'approved_deduct' for v in emp_violations),
                    'already_waived':   any(v.get('decision_status') == 'waived' for v in emp_violations),
                    'year':             year,
                    'month':            month,
                })

        return Response(violations, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='apply-policy-deduction')
    def apply_policy_deduction(self, request):
        """
        POST /api/payroll/apply-policy-deduction/

        Body:
          {
            "employee_id": <int>,
            "year": <int>,
            "month": <int>,
            "deduction_amount": <decimal>,
            "reason": "<string>"   // optional, defaults to auto-generated
          }

        Creates a Deduction record for the employee for the given month.
        Returns the created deduction.
        """
        employee_id      = request.data.get('employee_id')
        year             = request.data.get('year')
        month            = request.data.get('month')
        deduction_amount = request.data.get('deduction_amount')
        reason           = request.data.get('reason', '')
        violation_type   = request.data.get('violation_type') or 'all'

        if not all([employee_id, year, month, deduction_amount]):
            return Response(
                {'error': 'employee_id, year, month, and deduction_amount are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            year, month = int(year), int(month)
            deduction_amount = Decimal(str(deduction_amount))
        except (ValueError, Exception):
            return Response({'error': 'Invalid data types'}, status=status.HTTP_400_BAD_REQUEST)

        user  = request.user
        admin = _get_admin_owner(user)

        emp_qs = Employee.objects.all()
        if user.role != 'SUPER_ADMIN':
            if admin is None:
                return Response({'error': 'No admin tenant found'}, status=status.HTTP_403_FORBIDDEN)
            emp_qs = emp_qs.filter(admin_owner=admin)

        try:
            employee = emp_qs.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)

        deduction_name = _policy_decision_name('Deduction', violation_type, month, year)
        waiver_name    = _policy_decision_name('Waiver',    violation_type, month, year)
        existing_qs    = _policy_decision_qs(employee, year, month, admin).filter(
            deduction_name__in=[deduction_name, waiver_name]
        )

        if existing_qs.exists():
            existing = existing_qs.first()
            if existing.deduction_name == deduction_name:
                # Already deducted — idempotent: return 200
                return Response({
                    'id':             existing.id,
                    'employee_id':    employee.id,
                    'employee_name':  f"{employee.first_name} {employee.last_name}",
                    'deduction_name': existing.deduction_name,
                    'amount':         str(existing.amount),
                    'year':           existing.year,
                    'month':          existing.month,
                    'description':    existing.description,
                    'message':        'Policy deduction was already applied for this period.',
                    'already_exists': True,
                }, status=status.HTTP_200_OK)
            else:
                # A waiver exists — cannot also deduct
                return Response(
                    {'error': 'This penalty has already been waived for this employee and period. Cancel the waiver before applying a deduction.'},
                    status=status.HTTP_409_CONFLICT,
                )

        month_name    = dict(Payroll.MONTH_CHOICES).get(month, str(month))
        description   = reason or f"Approved policy deduction for {_policy_violation_label(violation_type)} in {month_name} {year}"

        deduction = Deduction.objects.create(
            employee=employee,
            deduction_name=deduction_name,
            year=year,
            month=month,
            amount=deduction_amount,
            description=description,
            is_active=True,
            admin_owner=admin,
        )

        return Response({
            'id':             deduction.id,
            'employee_id':    employee.id,
            'employee_name':  f"{employee.first_name} {employee.last_name}",
            'deduction_name': deduction.deduction_name,
            'amount':         str(deduction.amount),
            'year':           deduction.year,
            'month':          deduction.month,
            'description':    deduction.description,
            'message':        f"Policy deduction of ₹{deduction_amount} applied successfully.",
        }, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='waive-policy-deduction')
    def waive_policy_deduction(self, request):
        """
        POST /api/payroll/waive-policy-deduction/

        Records a per-violation waiver. The row is inactive with zero amount,
        so payroll totals are not affected.
        """
        employee_id    = request.data.get('employee_id')
        year           = request.data.get('year')
        month          = request.data.get('month')
        reason         = request.data.get('reason', '')
        violation_type = request.data.get('violation_type') or 'all'

        if not all([employee_id, year, month]):
            return Response(
                {'error': 'employee_id, year, and month are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            year, month = int(year), int(month)
        except ValueError:
            return Response({'error': 'Invalid data types'}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        admin = _get_admin_owner(user)

        emp_qs = Employee.objects.all()
        if user.role != 'SUPER_ADMIN':
            if admin is None:
                return Response({'error': 'No admin tenant found'}, status=status.HTTP_403_FORBIDDEN)
            emp_qs = emp_qs.filter(admin_owner=admin)

        try:
            employee = emp_qs.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)

        waiver_name    = _policy_decision_name('Waiver',    violation_type, month, year)
        deduction_name = _policy_decision_name('Deduction', violation_type, month, year)
        existing_qs    = _policy_decision_qs(employee, year, month, admin).filter(
            deduction_name__in=[waiver_name, deduction_name]
        )

        if existing_qs.exists():
            existing = existing_qs.first()
            if existing.deduction_name == waiver_name:
                # Already waived — idempotent: return 200
                return Response({
                    'id':             existing.id,
                    'employee_id':    employee.id,
                    'employee_name':  f"{employee.first_name} {employee.last_name}",
                    'deduction_name': existing.deduction_name,
                    'amount':         str(existing.amount),
                    'year':           existing.year,
                    'month':          existing.month,
                    'description':    existing.description,
                    'message':        'This penalty was already waived for this period.',
                    'already_exists': True,
                }, status=status.HTTP_200_OK)
            else:
                # A deduction already exists — cannot also waive
                return Response(
                    {'error': 'A salary deduction has already been applied for this employee and period. Cancel the deduction before issuing a waiver.'},
                    status=status.HTTP_409_CONFLICT,
                )

        month_name = dict(Payroll.MONTH_CHOICES).get(month, str(month))
        description = reason or f"Waived policy deduction for {_policy_violation_label(violation_type)} in {month_name} {year}"

        waiver = Deduction.objects.create(
            employee=employee,
            deduction_name=waiver_name,
            year=year,
            month=month,
            amount=Decimal('0.00'),
            description=description,
            is_active=False,
            admin_owner=admin,
        )

        return Response({
            'id': waiver.id,
            'employee_id': employee.id,
            'employee_name': f"{employee.first_name} {employee.last_name}",
            'deduction_name': waiver.deduction_name,
            'amount': str(waiver.amount),
            'year': waiver.year,
            'month': waiver.month,
            'description': waiver.description,
            'message': 'Policy penalty waived successfully.',
        }, status=status.HTTP_201_CREATED)
