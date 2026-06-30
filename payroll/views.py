from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Sum
from django.utils import timezone
from datetime import datetime
import calendar
import re
from decimal import Decimal, ROUND_HALF_UP

from .models import Payroll
from .serializers import PayrollSerializer, PayrollDetailSerializer, PayrollCalculateSerializer
from employee_management.models import Employee
from master.models import Allowance, Deduction, PayrollPolicy
from attendance.models import Attendance, LeaveRequest
from activitylog.utils import ActivityLogMixin, log_activity


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _is_admin(user):
    return (
        user.role in ('SUPER_ADMIN', 'ADMIN') or
        getattr(user, 'is_admin_user', False)
    )

def _get_admin_owner(user):
    """
    Return the ADMIN who owns the current request's tenant scope.
    """
    if user.role == 'ADMIN' or getattr(user, 'is_admin_user', False):
        return user if user.role == 'ADMIN' else user.admin_owner
    if user.role == 'USER':
        return user.admin_owner
    return None  # SUPER_ADMIN


# ─────────────────────────────────────────────────────────────────────────────
# POLICY VIOLATION CHECKER
# ─────────────────────────────────────────────────────────────────────────────

def _get_duty_times(employee, admin):
    """
    Return (duty_start_time, duty_end_time) for an employee.
    Priority: employee-specific duty times → global AttendanceSettings.
    Both returned as datetime.time objects (or None if not configured).
    """
    from attendance.models import AttendanceSettings
    start = getattr(employee, 'duty_start_time', None)
    end   = getattr(employee, 'duty_end_time', None)
    if not start or not end:
        settings_obj = AttendanceSettings.objects.filter(admin_owner=admin).order_by('-id').first()
        if settings_obj:
            start = start or settings_obj.office_start_time
            end   = end   or settings_obj.office_end_time
    return start, end


def _minutes_late(actual_time, duty_start, grace_minutes=0):
    """
    How many minutes after (duty_start + grace) did the employee arrive?
    actual_time and duty_start are datetime.time objects.
    Returns 0 if within grace. Capped at 720 min (12h).
    Handles night-shift: if arrival < duty_start on same base date,
    adds 24h to arrival (assumes arrival is next day).
    """
    from datetime import datetime as _dt
    base = _dt(2000, 1, 1)
    actual_dt = base.replace(hour=actual_time.hour, minute=actual_time.minute, second=0)
    limit_dt  = base.replace(hour=duty_start.hour,  minute=duty_start.minute, second=0)
    delta = (actual_dt - limit_dt).total_seconds() / 60
    if delta < -360:  # night-shift: arrival is next day
        delta += 1440
    return min(max(0, delta - grace_minutes), 720)


def _minutes_early(actual_time, duty_end, buffer_minutes=0):
    """
    How many minutes before (duty_end - buffer) did the employee leave?
    Returns 0 if within buffer. Capped at 720 min (12h) to prevent
    unreasonable values from bad data.
    Handles night-shift crossing: if duty_end < departure on same base date,
    adds 24h to duty_end (assumes departure is on duty_end's day).
    """
    from datetime import datetime as _dt
    base = _dt(2000, 1, 1)
    actual_dt = base.replace(hour=actual_time.hour, minute=actual_time.minute, second=0)
    limit_dt  = base.replace(hour=duty_end.hour,    minute=duty_end.minute,    second=0)
    delta = (limit_dt - actual_dt).total_seconds() / 60  # positive = left early
    if delta < -360:  # night-shift wrap: duty_end is next day
        delta += 1440
    return min(max(0, delta - buffer_minutes), 720)


def _tier_action_for_minutes(tiers, minutes):
    """
    Given a sorted list of tiers [{fromMin, toMin, action}] and actual minutes,
    return the matching tier's action (or 'warn_only' if no tier matches).
    """
    for tier in tiers:
        from_min = tier.get('fromMin') or 0
        to_min   = tier.get('toMin')   # None = open-ended
        if minutes >= from_min and (to_min is None or minutes < to_min):
            return tier.get('action', 'warn_only')
    return 'warn_only'


def _check_policy_violations(employee, year, month, policy_data, admin, total_days=None):
    """
    Compare an employee's attendance for the given month against policy_data.

    Uses employee-specific duty_start_time / duty_end_time (falls back to
    global AttendanceSettings) to calculate the actual minutes late / early
    for each request, then maps those minutes to the correct policy tier.

    per_day is computed from the employee's monthly salary divided by
    total_days (working days after holidays).  If total_days is not supplied
    it is recalculated here so the function stays self-contained.

    Deduction mapping (per billable occurrence):
      half_day_cut        → per_day / 2
      full_day_cut        → per_day
      full_absence_marked → per_day          (treat as one full absent day)
      half_hour_cut       → per_day / total_duty_hours_per_day / 2
      warn_only / no_action → 0

    Returns a list of violation dicts:
      [{ violation_type, description, count, billable_count,
         action, fine, deduction_amount, per_day_salary, requests }, ...]
    Returns an empty list when the employee is within all policy limits.
    """
    from login.models import User
    from attendance.models import LateArrivalRequest, EarlyDepartureRequest

    violations = []

    # ── Resolve auth user from employee email ─────────────────────────────────
    auth_user = User.objects.filter(email=employee.email).first()
    if not auth_user:
        return violations

    duty_start, duty_end = _get_duty_times(employee, admin)

    # ── Per-day salary — must match _build_payroll_dict ───────────────────────
    basic_salary = Decimal(str(employee.salary))
    if total_days is None:
        # Recalculate working days — only Sunday holidays reduce the divisor
        calendar_days_count = calendar.monthrange(year, month)[1]
        hol_bk = _get_holiday_breakdown(year, month, admin)
        total_days = max(1, calendar_days_count - hol_bk['sunday_count'])

    per_day = (basic_salary / Decimal(str(total_days))).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    per_half_day = (per_day / Decimal('2')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)    # Half-hour cut: 30-min proportion of the actual duty-hour window
    # e.g. 9-hr shift → per_day / 9hrs / 2 = per_day / 18
    # Fallback to 8 h if no duty times configured.
    if duty_start and duty_end:
        from datetime import datetime as _dt
        _base = _dt(2000, 1, 1)
        duty_minutes = (
            _base.replace(hour=duty_end.hour,   minute=duty_end.minute)
            - _base.replace(hour=duty_start.hour, minute=duty_start.minute)
        ).total_seconds() / 60
        duty_hours = max(Decimal(str(duty_minutes / 60)), Decimal('1'))
    else:
        duty_hours = Decimal('8')

    # 30 min / total duty minutes as a fraction of per_day
    per_half_hour = (per_day * Decimal('30') / (duty_hours * Decimal('60'))).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )

    def _policy_fine_amount(policy, billable_count):
        fine  = policy.get('fine', {}) or {}
        value = Decimal(str(fine.get('value') or '0'))
        if value <= 0 or billable_count <= 0:
            return None
        if fine.get('type') == 'percentage':
            per_occ = (basic_salary * value / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            per_occ = value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return (per_occ * Decimal(str(billable_count))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def _deduction_for_action(action, count):
        """
        Map a tier action to a rupee deduction per billable occurrence.
        All amounts derive from per_day so they are consistent with the
        rest of the payroll calculation.
        """
        cnt = Decimal(str(count))
        if action == 'half_day_cut':
            return (per_half_day * cnt).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if action in ('full_day_cut', 'full_absence_marked'):
            return (per_day * cnt).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if action == 'half_hour_cut':
            return (per_half_hour * cnt).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        # warn_only, no_action, or unknown → no monetary deduction
        return Decimal('0')

    # ── Late Arrival violations ───────────────────────────────────────────────
    la_policy = policy_data.get('attendance', {}).get('lateArrival', {})
    if la_policy.get('enabled', True):
        forgiven          = int(la_policy.get('forgivenLatesPerMonth', 3))
        grace_min         = int(la_policy.get('gracePeriodMin', 0))
        habitual_threshold = int(la_policy.get('habitualThreshold', 4))
        tiers             = la_policy.get('tiers', [])
        habitual_action   = la_policy.get('habitualLate', 'full_day_cut')

        late_qs = LateArrivalRequest.objects.filter(
            user=auth_user,
            date__year=year,
            date__month=month,
            admin_owner=admin,
        ).exclude(status__in=['rejected', 'cancelled']).order_by('date')

        late_requests = list(late_qs)
        late_count    = len(late_requests)
        billable      = max(0, late_count - forgiven)

        # Annotate each request with minutes_late and tier action
        annotated_late = []
        for req in late_requests:
            mins = 0
            if duty_start and req.expected_arrival_time:
                mins = _minutes_late(req.expected_arrival_time, duty_start, grace_min)
            act = _tier_action_for_minutes(tiers, mins) if tiers else 'warn_only'
            annotated_late.append({
                'id':             req.id,
                'date':           str(req.date),
                'arrival_time':   str(req.expected_arrival_time),
                'minutes_late':   round(mins),
                'status':         req.status,
                'reason':         req.reason,
                'tier_action':    act,
                'is_waived':      req.status == 'waived',
            })

        if billable > 0:
            # For the overall month deduction, use the worst (most severe) tier
            # among the billable requests (the ones beyond the forgiven count)
            billable_reqs = [r for r in annotated_late if not r['is_waived']]
            # Sort by minutes so we pick deductions for the most-late ones
            billable_reqs_sorted = sorted(billable_reqs, key=lambda x: x['minutes_late'], reverse=True)
            billable_for_deduct  = billable_reqs_sorted[:billable]

            if late_count >= habitual_threshold:
                action = habitual_action
                desc   = f"Habitual late arrival ({late_count} times, {forgiven} forgiven, {billable} billable)"
            elif tiers:
                # Use the tier of the worst billable request
                worst_mins = billable_for_deduct[0]['minutes_late'] if billable_for_deduct else 0
                action = _tier_action_for_minutes(tiers, worst_mins)
                desc   = f"Late arrival ({late_count} times, {forgiven} forgiven, {billable} billable)"
            else:
                action = 'warn_only'
                desc   = f"Late arrival ({late_count} times)"

            deduction_amount = _policy_fine_amount(la_policy, billable)
            if deduction_amount is None:
                deduction_amount = _deduction_for_action(action, billable)

            violations.append({
                'violation_type':     'late_arrival',
                'description':        desc,
                'count':              late_count,
                'billable_count':     billable,
                'forgiven':           forgiven,
                'action':             action,
                'fine':               la_policy.get('fine', {}),
                'duty_start':         str(duty_start) if duty_start else None,
                'grace_minutes':      grace_min,
                'per_day_salary':     float(per_day),
                'per_half_day_salary': float(per_half_day),
                'per_half_hour_salary': float(per_half_hour),
                'total_days':         total_days,
                'deduction_amount':   float(deduction_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'requests':           annotated_late,
            })

    # ── Early Departure violations ────────────────────────────────────────────
    ed_policy = policy_data.get('attendance', {}).get('earlyDeparture', {})
    if ed_policy.get('enabled', True):
        forgiven_early = int(ed_policy.get('forgivenEarlyPerMonth', 2))
        buffer_min     = int(ed_policy.get('earlyBufferMin', 0))
        ed_tiers       = ed_policy.get('tiers', [])
        default_action = ed_policy.get('unapprovedEarlyLeave', 'half_hour_cut')

        early_qs = EarlyDepartureRequest.objects.filter(
            user=auth_user,
            date__year=year,
            date__month=month,
            admin_owner=admin,
        ).exclude(status__in=['rejected', 'cancelled']).order_by('date')

        early_requests = list(early_qs)
        early_count    = len(early_requests)
        billable_early = max(0, early_count - forgiven_early)

        annotated_early = []
        for req in early_requests:
            mins = 0
            if duty_end and req.expected_departure_time:
                mins = _minutes_early(req.expected_departure_time, duty_end, buffer_min)
            act = _tier_action_for_minutes(ed_tiers, mins) if ed_tiers else default_action
            annotated_early.append({
                'id':               req.id,
                'date':             str(req.date),
                'departure_time':   str(req.expected_departure_time),
                'minutes_early':    round(mins),
                'status':           req.status,
                'reason':           req.reason,
                'tier_action':      act,
                'is_waived':        req.status == 'waived',
            })

        if billable_early > 0:
            billable_early_reqs  = [r for r in annotated_early if not r['is_waived']]
            early_sorted         = sorted(billable_early_reqs, key=lambda x: x['minutes_early'], reverse=True)
            billable_for_deduct  = early_sorted[:billable_early]

            if ed_tiers:
                worst_mins = billable_for_deduct[0]['minutes_early'] if billable_for_deduct else 0
                action = _tier_action_for_minutes(ed_tiers, worst_mins)
            else:
                action = default_action

            desc = (
                f"Early departure ({early_count} times, {forgiven_early} forgiven, "
                f"{billable_early} billable)"
            )

            deduction_amount = _policy_fine_amount(ed_policy, billable_early)
            if deduction_amount is None:
                deduction_amount = _deduction_for_action(action, billable_early)

            violations.append({
                'violation_type':     'early_departure',
                'description':        desc,
                'count':              early_count,
                'billable_count':     billable_early,
                'forgiven':           forgiven_early,
                'action':             action,
                'fine':               ed_policy.get('fine', {}),
                'duty_end':           str(duty_end) if duty_end else None,
                'buffer_minutes':     buffer_min,
                'per_day_salary':     float(per_day),
                'per_half_day_salary': float(per_half_day),
                'per_half_hour_salary': float(per_half_hour),
                'total_days':         total_days,
                'deduction_amount':   float(deduction_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'requests':           annotated_early,
            })

    # ── Break Deduction violations ───────────────────────────────────────────
    bd_policy = policy_data.get('attendance', {}).get('breakDeduction', {})
    if bd_policy.get('enabled', True):
        allowed_break_min = int(bd_policy.get('allowedBreakMinutes', 30))
        bd_tiers = bd_policy.get('tiers', [])
        required_hours = duty_hours

        # Attendance records with break data for this month
        att_qs = Attendance.objects.filter(
            user=auth_user,
            date__year=year,
            date__month=month,
            total_break_minutes__gt=0,
        ).order_by('date')

        annotated_break = []
        for att in att_qs:
            net_hours = Decimal(str(att.net_working_hours or 0))
            # No penalty if the employee met the required working hours
            if net_hours >= required_hours:
                continue
            excess_min = max(0, att.total_break_minutes - allowed_break_min)
            act = _tier_action_for_minutes(bd_tiers, excess_min) if bd_tiers else 'warn_only'
            annotated_break.append({
                'id': att.id,
                'date': str(att.date),
                'total_break_minutes': att.total_break_minutes,
                'net_working_hours': float(net_hours),
                'required_hours': float(required_hours),
                'excess_minutes': round(excess_min),
                'tier_action': act,
            })

        billable_break_days = len(annotated_break)

        if billable_break_days > 0:
            worst_excess = max(r['excess_minutes'] for r in annotated_break)

            action = _tier_action_for_minutes(bd_tiers, worst_excess) if bd_tiers else 'warn_only'

            desc = (
                f"Break time exceeded — net hours below required "
                f"({billable_break_days} day(s), allowed break {allowed_break_min} min/day)"
            )

            deduction_amount = _policy_fine_amount(bd_policy, billable_break_days)
            if deduction_amount is None:
                deduction_amount = _deduction_for_action(action, billable_break_days)

            violations.append({
                'violation_type': 'break_excess',
                'description': desc,
                'count': billable_break_days,
                'billable_count': billable_break_days,
                'forgiven': 0,
                'action': action,
                'fine': bd_policy.get('fine', {}),
                'allowed_break_minutes': allowed_break_min,
                'required_hours': float(required_hours),
                'per_day_salary': float(per_day),
                'per_half_day_salary': float(per_half_day),
                'per_half_hour_salary': float(per_half_hour),
                'total_days': total_days,
                'deduction_amount': float(deduction_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'requests': annotated_break,
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
        'wfh_days':       0,
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
            summary['wfh_days']     = att_qs.filter(is_wfh=True).count()
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
        'break_excess': 'Break Excess',
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


def _get_holiday_breakdown(year, month, admin_owner=None):
    """
    Split the month's active holidays into three buckets:

    1. sunday_holidays   — holidays that fall on a Sunday.
                           These reduce the working-day divisor (the day was
                           already off; marking it as a holiday simply means
                           employees are not expected to work that Sunday).

    2. paid_non_sunday   — paid holidays on weekdays / Saturday.
                           Working-day count is NOT reduced; instead the
                           day's salary is paid as an allowance bonus so
                           the employee effectively receives extra pay.

    3. unpaid_non_sunday — unpaid holidays on weekdays / Saturday.
                           Working-day count is NOT reduced; the day's
                           salary is deducted (treated like an absent day).

    Returns a dict:
      {
        'sunday_count':        int,
        'paid_non_sunday_count':   int,
        'unpaid_non_sunday_count': int,
        'sunday_holidays':         [Holiday, ...],
        'paid_non_sunday':         [Holiday, ...],
        'unpaid_non_sunday':       [Holiday, ...],
      }
    """
    from master.models import Holiday as _Holiday
    import datetime as _dt

    qs = _Holiday.objects.filter(date__year=year, date__month=month, is_active=True)
    if admin_owner:
        qs = qs.filter(admin_owner=admin_owner)

    sunday_hols        = []
    paid_non_sunday    = []
    unpaid_non_sunday  = []

    for h in qs:
        if h.date.weekday() == 6:          # weekday() == 6 → Sunday
            sunday_hols.append(h)
        elif h.is_paid:
            paid_non_sunday.append(h)
        else:
            unpaid_non_sunday.append(h)

    return {
        'sunday_count':            len(sunday_hols),
        'paid_non_sunday_count':   len(paid_non_sunday),
        'unpaid_non_sunday_count': len(unpaid_non_sunday),
        'sunday_holidays':         sunday_hols,
        'paid_non_sunday':         paid_non_sunday,
        'unpaid_non_sunday':       unpaid_non_sunday,
    }


def _build_payroll_dict(employee, year, month, admin_owner=None):
    """
    Central helper for payroll calculation.

    Holiday treatment
    ─────────────────
    Holidays are split into three groups each month:

    A) Sunday holidays
       → These reduce the working-day divisor because the day was already
         a day off; recognising it as a holiday just makes it official.
         Effect: total_days -= sunday_holiday_count  (higher per_day rate)

    B) Paid non-Sunday holidays (weekday / Saturday)
       → The working-day count is NOT reduced.  Employees receive that
         day's salary as an *allowance* (holiday pay bonus) in addition to
         their normal base salary.
         Effect: one allowance item per paid holiday, amount = per_day

    C) Unpaid non-Sunday holidays (weekday / Saturday)
       → The working-day count is NOT reduced.  The day's salary is
         *deducted* from the employee's net pay (same as an absent day).
         Effect: unpaid_holiday_deduction += per_day per unpaid holiday

    Standard mode (default):
      total_days = calendar days in month − sunday_holiday_count
      per_day    = basic_salary / total_days
      attendance_deduction = (absent_days + half_days * 0.5) * per_day
      + paid_holiday_allowance    (B above)
      + unpaid_holiday_deduction  (C above)

    Normalized mode (salaryCalculation.enabled = True):
      Every month is treated as `normalizedMonthDays` (default 30) days for
      salary purposes, regardless of whether the calendar month has 28/30/31.
      Employees are credited for (normalizedMonthDays + paidOffDaysPerMonth)
      days — e.g. 30 + 4 = 34 by default.
      Absent days are first absorbed by the free off-day allowance; once that
      is exhausted each additional absent day deducts one day's salary.

      per_day            = basic_salary / normalizedMonthDays
      credited_days      = normalizedMonthDays + paidOffDaysPerMonth  (e.g. 34)
      billable_absences  = max(0, absent_days − paidOffDaysPerMonth)
      attendance_deduction = billable_absences * per_day
      + paid_holiday_allowance    (B above — applied in all modes)
      + unpaid_holiday_deduction  (C above — applied in all modes)
    """
    import datetime as _dt
    calendar_days = calendar.monthrange(year, month)[1]

    from master.models import Holiday, PayrollPolicy as _PP

    # ── Holiday breakdown (Sunday vs paid/unpaid weekday) ─────────────────────
    hol_breakdown = _get_holiday_breakdown(year, month, admin_owner)
    sunday_count            = hol_breakdown['sunday_count']
    paid_non_sunday_count   = hol_breakdown['paid_non_sunday_count']
    unpaid_non_sunday_count = hol_breakdown['unpaid_non_sunday_count']
    paid_non_sunday_hols    = hol_breakdown['paid_non_sunday']
    unpaid_non_sunday_hols  = hol_breakdown['unpaid_non_sunday']

    # Only Sunday holidays shrink the working-day divisor
    holiday_count = sunday_count

    # Raw calendar working days (used in standard mode and policy violations)
    total_days = max(1, calendar_days - holiday_count)

    basic_salary = employee.salary

    # ── Resolve salary-calculation policy ────────────────────────────────────
    policy_obj = _PP.objects.filter(admin_owner=admin_owner).first() if admin_owner else None
    policy_data = policy_obj.policy_data if policy_obj else {}
    sal_calc = policy_data.get('salaryCalculation', {})
    normalized_mode = sal_calc.get('enabled', False)
    normalized_month_days = int(sal_calc.get('normalizedMonthDays', 30))
    paid_off_days = int(sal_calc.get('paidOffDaysPerMonth', 4))
    sunday_working = bool(sal_calc.get('sundayWorking', False))

    # When Sunday is a working day, raw total_days should include Sundays too
    if sunday_working:
        # Recalculate: all calendar days minus holidays (no weekday filter)
        total_days = max(1, calendar_days - holiday_count)
    # (standard mode already uses all calendar days minus holidays, so no change needed)

    # ── Attendance ────────────────────────────────────────────────────────────
    att = _get_attendance_summary(employee, year, month, total_days)

    if normalized_mode:
        # Normalize: treat every month as `normalized_month_days` days
        norm_total_days = max(1, normalized_month_days)
        per_day = (Decimal(str(basic_salary)) / Decimal(str(norm_total_days))).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        
        # Effective absence count (absent + 0.5 * half_days)
        effective_absences = Decimal(str(att['absent_days'])) + Decimal(str(att['half_days'])) * Decimal('0.5')
        
        # Free off-days (e.g. 4) act as a paid bonus. Absences subtract from this bonus first.
        extra_days_worked = max(Decimal('0'), Decimal(str(paid_off_days)) - effective_absences).quantize(
            Decimal('0.5'), rounding=ROUND_HALF_UP
        )
        extra_days_bonus = (extra_days_worked * per_day).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        
        # If absences exceed the free paid off-days, the excess absences deduct from the basic salary.
        billable_absences = max(Decimal('0'), effective_absences - Decimal(str(paid_off_days)))
        att_deduction = (billable_absences * per_day).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        
        calc_total_days = norm_total_days
        credited_days = norm_total_days + paid_off_days
    else:
        per_day, att_deduction = _calc_att_deduction(
            basic_salary, total_days, att['absent_days'], att['half_days']
        )
        calc_total_days = total_days
        credited_days = total_days
        billable_absences = att['absent_days']
        extra_days_worked = Decimal('0')
        extra_days_bonus = Decimal('0.00')

    # ── Paid non-Sunday holiday allowance ────────────────────────────────────
    # Each paid weekday/Saturday holiday → employee receives 1 day's pay as an
    # allowance (they keep their full base salary AND get the holiday day paid).
    paid_holiday_allowance_items = []
    paid_holiday_allowance_total = Decimal('0.00')
    for h in paid_non_sunday_hols:
        bonus = per_day.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        paid_holiday_allowance_total += bonus
        paid_holiday_allowance_items.append({
            'source':      'holiday_paid',
            'name':        f"Holiday Pay – {h.name}",
            'amount':      str(bonus),
            'description': (
                f"Paid holiday on {h.date} ({h.get_type_display()}). "
                f"Salary credited as allowance."
            ),
            'holiday_id':  h.id,
            'holiday_date': str(h.date),
        })

    # ── Unpaid non-Sunday holiday deduction ──────────────────────────────────
    # Each unpaid weekday/Saturday holiday → one day's salary is deducted.
    unpaid_holiday_deduction_items = []
    unpaid_holiday_deduction_total = Decimal('0.00')
    for h in unpaid_non_sunday_hols:
        cut = per_day.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        unpaid_holiday_deduction_total += cut
        unpaid_holiday_deduction_items.append({
            'source':      'holiday_unpaid',
            'name':        f"Unpaid Holiday – {h.name}",
            'amount':      str(cut),
            'description': (
                f"Unpaid holiday on {h.date} ({h.get_type_display()}). "
                f"Salary deducted for this day."
            ),
            'holiday_id':  h.id,
            'holiday_date': str(h.date),
        })

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

    policy_violations = _check_policy_violations(employee, year, month, policy_data, admin_owner, total_days=total_days)

    # ── WFH deduction (based on policy salary effect) ─────────────────────────
    wfh_policy   = policy_data.get('attendance', {}).get('workFromHome', {})
    wfh_enabled  = wfh_policy.get('enabled', False)
    wfh_effect   = wfh_policy.get('salaryEffect', 'full_day')  # "full_day" or "half_day"
    wfh_days     = att.get('wfh_days', 0)

    if wfh_enabled and wfh_days > 0 and wfh_effect == 'half_day':
        # Half-day WFH: deduct 0.5 day per WFH day
        wfh_deduction = (
            Decimal(str(wfh_days)) * per_day * Decimal('0.5')
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        # "full_day" or WFH disabled → no salary cut
        wfh_deduction = Decimal('0.00')

    # ── Auto-compute PF & Overtime ────────────────────────────────────────────
    pf_amount,  pf_detail  = _calc_pf_amount(employee)
    ot_amount,  ot_detail  = _calc_overtime_amount(employee, calc_total_days)

    auto_allowances_total = pf_amount + ot_amount + extra_days_bonus

    # Build extra-days detail item for the allowances list
    extra_days_detail = None
    if extra_days_bonus > Decimal('0'):
        extra_days_detail = {
            'source':      'auto_extra_days',
            'name':        'Extra Days Bonus',
            'amount':      str(extra_days_bonus),
            'description': (
                f"Auto-computed: {float(extra_days_worked)} paid off-day(s) credited "
                f"for attendance in {calc_total_days}-day normalized month "
                f"@ {str(per_day)}/day"
            ),
        }

    # ── Totals ────────────────────────────────────────────────────────────────
    total_allowances = (
        Decimal(str(db_allowances_total))
        + auto_allowances_total
        + paid_holiday_allowance_total
    )
    total_deductions = (
        att_deduction
        + Decimal(str(total_manual_deductions))
        + policy_deductions_total
        + wfh_deduction
        + unpaid_holiday_deduction_total
    )

    net_salary = (
        Decimal(str(basic_salary)) + total_allowances - total_deductions
    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return {
        'basic_salary':            str(basic_salary),
        'total_allowances':        str(total_allowances),
        'total_deductions':        str(total_deductions),
        'attendance_deduction':    str(att_deduction),
        'wfh_deduction':           str(wfh_deduction),
        'manual_deductions_total': str(total_manual_deductions),
        'policy_deductions_total': str(policy_deductions_total),
        'net_salary':              str(net_salary),
        'total_days_in_month':     calc_total_days,
        'calendar_days_in_month':  calendar_days,
        'total_working_days':      att['working_days'],
        'db_allowances_total':     str(db_allowances_total),
        'pf_amount':               str(pf_amount),
        'overtime_amount':         str(ot_amount),
        'auto_allowances_total':   str(auto_allowances_total),
        'holiday_paid_allowance_total':    str(paid_holiday_allowance_total),
        'holiday_unpaid_deduction_total':  str(unpaid_holiday_deduction_total),
        'salary_calculation': {
            'mode':                 'normalized' if normalized_mode else 'standard',
            'normalized_month_days': calc_total_days,
            'paid_off_days':         paid_off_days if normalized_mode else 0,
            'credited_days':         credited_days,
            'billable_absences':     float(billable_absences),
            'sunday_working':        sunday_working,
            'extra_days_worked':     float(extra_days_worked),
            'extra_days_bonus':      str(extra_days_bonus),
        },
        'holidays': {
            'sunday_holidays':         [{'id': h.id, 'name': h.name, 'date': str(h.date), 'type': h.type} for h in hol_breakdown['sunday_holidays']],
            'paid_non_sunday':         [{'id': h.id, 'name': h.name, 'date': str(h.date), 'type': h.type, 'allowance': str(per_day)} for h in paid_non_sunday_hols],
            'unpaid_non_sunday':       [{'id': h.id, 'name': h.name, 'date': str(h.date), 'type': h.type, 'deduction': str(per_day)} for h in unpaid_non_sunday_hols],
            'sunday_count':            sunday_count,
            'paid_non_sunday_count':   paid_non_sunday_count,
            'unpaid_non_sunday_count': unpaid_non_sunday_count,
        },
        'attendance': {
            'present_days':         att['present_days'],
            'absent_days':          att['absent_days'],
            'late_days':            att['late_days'],
            'half_days':            att['half_days'],
            'leave_days':           att['leave_days'],
            'wfh_days':             att['wfh_days'],
            'wfh_salary_effect':    wfh_effect if wfh_enabled else 'full_day',
            'working_days':         att['working_days'],
            'paid_days':            att['paid_days'],
            'per_day_salary':       str(per_day),
            'attendance_deduction': str(att_deduction),
            'wfh_deduction':        str(wfh_deduction),
            'leave_breakdown':      att.get('leave_breakdown', {}),
        },
        'allowances': [
            {
                'id': a.id, 'name': a.allowance_name, 'amount': str(a.amount), 
                'description': a.description, 'source': 'manual'
            } for a in db_allowances
        ] + ([pf_detail] if pf_detail else []) + ([ot_detail] if ot_detail else [])
          + ([extra_days_detail] if extra_days_detail else [])
          + paid_holiday_allowance_items,
        'deductions': [
            {
                'id': d.id,
                'name': d.deduction_name,
                'amount': str(d.amount),
                'description': d.description,
                'source': 'policy' if d.deduction_name.startswith('Policy Deduction') else 'manual',
            }
            for d in list(db_other_deductions) + list(db_policy_deductions)
        ] + unpaid_holiday_deduction_items,
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

class PayrollViewSet(ActivityLogMixin, viewsets.ModelViewSet):
    """ViewSet for Payroll CRUD operations"""
    activity_log_module = "Payroll"
    activity_log_object_name = "Payroll"
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
            'wfh_deduction': data.get('wfh_deduction', '0.00'),
            'manual_deductions_total': data['manual_deductions_total'], 'net_salary': data['net_salary'],
            'policy_deductions_total': data['policy_deductions_total'],
            'holiday_paid_allowance_total':   data.get('holiday_paid_allowance_total', '0.00'),
            'holiday_unpaid_deduction_total': data.get('holiday_unpaid_deduction_total', '0.00'),
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
        month_name = dict(Payroll.MONTH_CHOICES).get(payroll.month, str(payroll.month))
        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Payroll',
            description=f"Processed payroll for {payroll.employee.first_name} {payroll.employee.last_name} ({month_name} {payroll.year})",
            request=request,
        )
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
        month_name = dict(Payroll.MONTH_CHOICES).get(payroll.month, str(payroll.month))
        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Payroll',
            description=f"Marked payroll as paid for {payroll.employee.first_name} {payroll.employee.last_name} ({month_name} {payroll.year}); ref: {payroll.payment_reference or 'N/A'}",
            request=request,
        )
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

        # Pre-compute working days (calendar days minus Sunday holidays only) — shared across employees
        _cal_days = calendar.monthrange(year, month)[1]
        _hol_bk = _get_holiday_breakdown(year, month, admin)
        _total_days = max(1, _cal_days - _hol_bk['sunday_count'])

        # Fetch all active employees for this tenant (exclude offboarded/inactive)
        _OFFBOARDED = {'terminated', 'resigned', 'retired', 'offboarded', 'inactive'}
        emp_qs = Employee.objects.all()
        if user.role != 'SUPER_ADMIN':
            if admin is None:
                return Response([], status=status.HTTP_200_OK)
            emp_qs = emp_qs.filter(admin_owner=admin)
        emp_qs = emp_qs.exclude(status__in=_OFFBOARDED)

        violations = []

        for employee in emp_qs.select_related('department'):
            emp_violations = _check_policy_violations(employee, year, month, policy_data, admin, total_days=_total_days)
            duty_start, duty_end = _get_duty_times(employee, admin)
            decision_names = set(
                _policy_decision_qs(employee, year, month, admin)
                .values_list('deduction_name', flat=True)
            )
            all_deduction_name = _policy_decision_name('Deduction', 'all', month, year)

            # Check for per-request deductions applied from Daily View
            per_request_deduction_records = Deduction.objects.filter(
                employee=employee,
                year=year,
                month=month,
                deduction_name__startswith='Request Deduction - ',
                is_active=True,
            )
            per_request_deduction_total = sum(d.amount for d in per_request_deduction_records)

            # Build per-request deduction details (request_type, request_id, amount)
            # Name format: "Request Deduction - Late Arrival #123 - Jun 2026"
            per_request_details = []
            for d in per_request_deduction_records:
                m = re.match(r'^Request Deduction - (Late Arrival|Early Departure) #(\d+)', d.deduction_name)
                if m:
                    label = m.group(1)
                    req_id = int(m.group(2))
                    rtype = 'late' if label == 'Late Arrival' else 'early'
                    per_request_details.append({
                        'request_type': rtype,
                        'request_id':   req_id,
                        'amount':       str(d.amount),
                        'deduction_name': d.deduction_name,
                    })

            # Only include employees with violations OR per-request deductions
            if not emp_violations and per_request_deduction_total <= 0:
                continue

            for violation in emp_violations:
                violation_type = violation.get('violation_type') or 'all'
                deduction_name = _policy_decision_name('Deduction', violation_type, month, year)
                waiver_name = _policy_decision_name('Waiver', violation_type, month, year)
                if deduction_name in decision_names:
                    violation['decision_status'] = 'approved_deduct'
                elif waiver_name in decision_names:
                    violation['decision_status'] = 'waived'
                elif all_deduction_name in decision_names:
                    violation['decision_status'] = 'approved_deduct'
                elif per_request_deduction_total > 0:
                    violation['decision_status'] = 'approved_deduct'
                else:
                    violation['decision_status'] = 'pending'

            policy_deduction_total = Decimal(str(sum(v['deduction_amount'] for v in emp_violations)))
            combined_deduction_total = policy_deduction_total + per_request_deduction_total

            violations.append({
                'employee': {
                    'id':          employee.id,
                    'employee_id': employee.employee_id,
                    'name':        f"{employee.first_name} {employee.last_name}",
                    'position':    employee.position,
                    'department':  employee.department.name if employee.department else '',
                    'salary':      str(employee.salary),
                    'duty_start':  str(duty_start) if duty_start else None,
                    'duty_end':    str(duty_end)   if duty_end   else None,
                },
                'violations':                  emp_violations,
                'total_deduction':             str(combined_deduction_total),
                'policy_deduction_total':      str(policy_deduction_total),
                'per_request_deduction_total': str(per_request_deduction_total),
                'per_request_deductions':      per_request_details,
                'already_applied':             per_request_deduction_total > 0 or any(v.get('decision_status') == 'approved_deduct' for v in emp_violations),
                'already_waived':              any(v.get('decision_status') == 'waived' for v in emp_violations),
                'year':                        year,
                'month':                       month,
            })

        return Response(violations, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='daily-violations')
    def daily_violations(self, request):
        """
        GET /api/payroll/daily-violations/?date=YYYY-MM-DD

        Returns every employee who has a late/early request on the given date,
        annotated with:
          - their request details (minutes late/early, tier action)
          - whether the monthly policy threshold is already breached (policy_hit=true)
          - if policy_hit, the suggested deduction and existing decision (pending/waived/deducted)

        This powers the "Daily" tab of Penalty Review.
        """
        date_str = request.query_params.get('date')
        if not date_str:
            return Response({'error': 'date query param is required (YYYY-MM-DD)'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from datetime import date as _date
            query_date = _date.fromisoformat(date_str)
        except ValueError:
            return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        year  = query_date.year
        month = query_date.month
        user  = request.user
        admin = _get_admin_owner(user)

        from master.models import PayrollPolicy
        from attendance.models import LateArrivalRequest, EarlyDepartureRequest
        from login.models import User as AuthUser

        policy_obj  = PayrollPolicy.objects.filter(admin_owner=admin).first() if admin else None
        policy_data = policy_obj.policy_data if policy_obj else {}

        la_policy  = policy_data.get('attendance', {}).get('lateArrival',    {})
        ed_policy  = policy_data.get('attendance', {}).get('earlyDeparture', {})

        # Pre-compute working days for this month (same as _build_payroll_dict)
        _cal_days_dv = calendar.monthrange(year, month)[1]
        _hol_bk_dv = _get_holiday_breakdown(year, month, admin)
        _total_days_dv = max(1, _cal_days_dv - _hol_bk_dv['sunday_count'])

        # Build a map: auth_user_id → employee record (active employees only)
        _OFFBOARDED = {'terminated', 'resigned', 'retired', 'offboarded', 'inactive'}
        emp_qs = Employee.objects.all()
        if user.role != 'SUPER_ADMIN':
            if admin is None:
                return Response([], status=status.HTTP_200_OK)
            emp_qs = emp_qs.filter(admin_owner=admin)
        emp_qs = emp_qs.exclude(status__in=_OFFBOARDED)

        emp_by_email = {emp.email.lower(): emp for emp in emp_qs.select_related('department')}

        # ── Auto-detect late/early from raw Attendance records ────────────────
        # For every employee who punched in/out today, check if they were
        # late or left early according to the payroll policy.  If so, and they
        # haven't submitted a request yet, create one automatically (same logic
        # as _auto_create_late_request / _auto_create_early_request in
        # attendance/views.py).  This ensures the daily view always shows all
        # policy violations whether or not the employee submitted a request.
        import pytz as _pytz
        from attendance.models import Attendance as _Attendance

        _grace_min  = int(la_policy.get('gracePeriodMin', 0))
        _buffer_min = int(ed_policy.get('earlyBufferMin', 0))
        _la_enabled = la_policy.get('enabled', True)
        _ed_enabled = ed_policy.get('enabled', True)

        _att_qs = _Attendance.objects.filter(
            date=query_date,
            admin_owner=admin,
        ).select_related('user')

        for _att in _att_qs:
            _emp = emp_by_email.get((_att.user.email or '').lower())
            if not _emp:
                continue
            _duty_start, _duty_end = _get_duty_times(_emp, admin)

            # ── Late check-in ─────────────────────────────────────────────────
            if _la_enabled and _att.check_in_time and _duty_start:
                _ci_local = _att.check_in_time.astimezone(_pytz.timezone('Asia/Kolkata'))
                _ci_time  = _ci_local.time()
                _mins_late = _minutes_late(_ci_time, _duty_start, _grace_min)
                if _mins_late > 0:
                    LateArrivalRequest.objects.get_or_create(
                        user=_att.user,
                        date=query_date,
                        admin_owner=admin,
                        defaults={
                            'expected_arrival_time': _ci_time,
                            'reason': f'Auto-detected late check-in ({round(_mins_late)} min late)',
                            'status': 'pending',
                        },
                    )

            # ── Early check-out ───────────────────────────────────────────────
            if _ed_enabled and _att.check_out_time and _duty_end:
                _co_local = _att.check_out_time.astimezone(_pytz.timezone('Asia/Kolkata'))
                _co_time  = _co_local.time()
                _mins_early = _minutes_early(_co_time, _duty_end, _buffer_min)
                if _mins_early > 0:
                    EarlyDepartureRequest.objects.get_or_create(
                        user=_att.user,
                        date=query_date,
                        admin_owner=admin,
                        defaults={
                            'expected_departure_time': _co_time,
                            'reason': f'Auto-detected early check-out ({round(_mins_early)} min early)',
                            'status': 'pending',
                        },
                    )
        # ─────────────────────────────────────────────────────────────────────

        # Fetch all late and early requests for this day (include pending so
        # auto-detected violations are visible; exclude only rejected/cancelled).
        late_day_qs  = LateArrivalRequest.objects.filter(
            date=query_date, admin_owner=admin,
        ).exclude(status__in=['rejected', 'cancelled']).select_related('user').order_by('date')
        early_day_qs = EarlyDepartureRequest.objects.filter(
            date=query_date, admin_owner=admin,
        ).exclude(status__in=['rejected', 'cancelled']).select_related('user').order_by('date')

        # Collect unique auth users who have a request today
        auth_user_ids = set(
            list(late_day_qs.values_list('user_id', flat=True)) +
            list(early_day_qs.values_list('user_id', flat=True))
        )

        result = []

        for auth_uid in auth_user_ids:
            try:
                auth_u = AuthUser.objects.get(pk=auth_uid)
            except AuthUser.DoesNotExist:
                continue

            employee = emp_by_email.get((auth_u.email or '').lower())
            if not employee:
                continue

            duty_start, duty_end = _get_duty_times(employee, admin)

            grace_min  = int(la_policy.get('gracePeriodMin',  0))
            buffer_min = int(ed_policy.get('earlyBufferMin',  0))
            la_tiers   = la_policy.get('tiers', [])
            ed_tiers   = ed_policy.get('tiers', [])

            # -- Today's requests for this employee --
            emp_late  = [r for r in late_day_qs  if r.user_id == auth_uid]
            emp_early = [r for r in early_day_qs if r.user_id == auth_uid]

            late_items = []
            for req in emp_late:
                mins = 0
                if duty_start and req.expected_arrival_time:
                    mins = _minutes_late(req.expected_arrival_time, duty_start, grace_min)
                act = _tier_action_for_minutes(la_tiers, mins) if la_tiers else 'warn_only'
                late_items.append({
                    'id':           req.id,
                    'date':         str(req.date),
                    'arrival_time': str(req.expected_arrival_time),
                    'minutes_late': round(mins),
                    'status':       req.status,
                    'reason':       req.reason,
                    'admin_notes':  req.admin_notes,
                    'tier_action':  act,
                    'is_waived':    req.status == 'waived',
                    'is_deducted':  False,  # resolved below
                    'type':         'late',
                })

            early_items = []
            for req in emp_early:
                mins = 0
                if duty_end and req.expected_departure_time:
                    mins = _minutes_early(req.expected_departure_time, duty_end, buffer_min)
                act = _tier_action_for_minutes(ed_tiers, mins) if ed_tiers else ed_policy.get('unapprovedEarlyLeave', 'warn_only')
                early_items.append({
                    'id':               req.id,
                    'date':             str(req.date),
                    'departure_time':   str(req.expected_departure_time),
                    'minutes_early':    round(mins),
                    'status':           req.status,
                    'reason':           req.reason,
                    'admin_notes':      req.admin_notes,
                    'tier_action':      act,
                    'is_waived':        req.status == 'waived',
                    'is_deducted':      False,  # resolved below
                    'type':             'early',
                })

            # Resolve per-request deduction status by checking Deduction table
            _month_label = timezone.now().strftime('%b %Y')
            late_ded_names  = [f"Request Deduction - Late Arrival #{r['id']} - {_month_label}" for r in late_items]
            early_ded_names = [f"Request Deduction - Early Departure #{r['id']} - {_month_label}" for r in early_items]
            all_ded_names = late_ded_names + early_ded_names
            if all_ded_names:
                existing_ded = set(
                    Deduction.objects.filter(
                        employee=employee, year=year, month=month,
                        deduction_name__in=all_ded_names,
                    ).values_list('deduction_name', flat=True)
                )
                for r in late_items:
                    dn = f"Request Deduction - Late Arrival #{r['id']} - {_month_label}"
                    if dn in existing_ded:
                        r['is_deducted'] = True
                for r in early_items:
                    dn = f"Request Deduction - Early Departure #{r['id']} - {_month_label}"
                    if dn in existing_ded:
                        r['is_deducted'] = True

            # -- Monthly violation check (to know if policy threshold is breached) --
            monthly_violations = _check_policy_violations(employee, year, month, policy_data, admin, total_days=_total_days_dv)

            total_monthly_deduction = sum(v['deduction_amount'] for v in monthly_violations)
            policy_hit = any(v['billable_count'] > 0 and v['deduction_amount'] > 0 for v in monthly_violations)

            # Check existing deduction/waiver decisions for this month
            decision_names = set(
                _policy_decision_qs(employee, year, month, admin)
                .values_list('deduction_name', flat=True)
            )
            all_deduction_name = _policy_decision_name('Deduction', 'all', month, year)
            for v in monthly_violations:
                vtype = v.get('violation_type') or 'all'
                ded_name  = _policy_decision_name('Deduction', vtype, month, year)
                waiv_name = _policy_decision_name('Waiver',    vtype, month, year)
                if ded_name  in decision_names: v['decision_status'] = 'approved_deduct'
                elif waiv_name in decision_names: v['decision_status'] = 'waived'
                elif all_deduction_name in decision_names: v['decision_status'] = 'approved_deduct'
                else: v['decision_status'] = 'pending'

            already_applied = any(v.get('decision_status') == 'approved_deduct' for v in monthly_violations)
            already_waived  = any(v.get('decision_status') == 'waived'          for v in monthly_violations)

            result.append({
                'employee': {
                    'id':           employee.id,
                    'employee_id':  employee.employee_id,
                    'name':         f"{employee.first_name} {employee.last_name}",
                    'email':        employee.email,
                    'position':     employee.position,
                    'department':   employee.department.name if employee.department else '',
                    'salary':       str(employee.salary),
                    'duty_start':   str(duty_start) if duty_start else None,
                    'duty_end':     str(duty_end)   if duty_end   else None,
                    'profile_image': employee.profile_image.url if employee.profile_image else None,
                },
                'date':               date_str,
                'late_requests':      late_items,
                'early_requests':     early_items,
                'policy_hit':         policy_hit,
                'monthly_violations': monthly_violations,
                'total_deduction':    str(round(total_monthly_deduction, 2)),
                'already_applied':    already_applied,
                'already_waived':     already_waived,
                'year':               year,
                'month':              month,
            })

        return Response(result, status=status.HTTP_200_OK)

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

        log_activity(
            user=request.user,
            action_type='CREATE',
            module='Payroll',
            description=f"Applied policy deduction of ₹{deduction_amount} for {employee.first_name} {employee.last_name} ({month_name} {year}): {description}",
            request=request,
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

        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Payroll',
            description=f"Waived policy deduction for {employee.first_name} {employee.last_name} ({month_name} {year}): {description}",
            request=request,
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

    @action(detail=False, methods=['post'], url_path='deduct-request')
    def deduct_request(self, request):
        """
        POST /api/payroll/deduct-request/

        Per-request salary deduction (not a monthly policy penalty).
        Body:
          { "request_type": "late"|"early",
            "request_id": <int>,
            "employee_id": <int>,
            "amount": <decimal>,
            "reason": "<string>" }

        Creates a Deduction record with name "Request Deduction - Late Arrival
        #{id}" so it does not conflict with monthly policy deductions/waivers.
        """
        request_type = request.data.get('request_type')
        request_id   = request.data.get('request_id')
        employee_id  = request.data.get('employee_id')
        amount       = request.data.get('amount')
        reason       = request.data.get('reason', '')

        if not all([request_type, request_id, employee_id, amount]):
            return Response({'error': 'request_type, request_id, employee_id, and amount are required'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            amount = Decimal(str(amount))
        except (ValueError, Exception):
            return Response({'error': 'Invalid amount'}, status=status.HTTP_400_BAD_REQUEST)

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

        now = timezone.now()
        label = 'Late Arrival' if request_type == 'late' else 'Early Departure'
        deduction_name = f"Request Deduction - {label} #{request_id} - {now.strftime('%b %Y')}"

        # Idempotency: check if already deducted
        existing = Deduction.objects.filter(
            employee=employee, deduction_name=deduction_name,
            year=now.year, month=now.month,
        ).first()
        if existing:
            return Response({
                'id': existing.id,
                'employee_id': employee.id,
                'employee_name': f"{employee.first_name} {employee.last_name}",
                'deduction_name': existing.deduction_name,
                'amount': str(existing.amount),
                'year': existing.year,
                'month': existing.month,
                'description': existing.description,
                'message': f'Deduction already applied for this request.',
                'already_exists': True,
            }, status=status.HTTP_200_OK)

        deduction = Deduction.objects.create(
            employee=employee,
            deduction_name=deduction_name,
            year=now.year,
            month=now.month,
            amount=amount,
            description=reason or f"Deduction for {label} request #{request_id}",
            is_active=True,
            admin_owner=admin,
        )

        log_activity(
            user=request.user,
            action_type='CREATE',
            module='Payroll',
            description=f"Per-request deduction of ₹{amount} for {employee} ({label} #{request_id})",
            request=request,
        )

        return Response({
            'id': deduction.id,
            'employee_id': employee.id,
            'employee_name': f"{employee.first_name} {employee.last_name}",
            'deduction_name': deduction.deduction_name,
            'amount': str(deduction.amount),
            'year': deduction.year,
            'month': deduction.month,
            'description': deduction.description,
            'message': f"Deduction of ₹{amount} applied for {label} request.",
        }, status=status.HTTP_201_CREATED)
