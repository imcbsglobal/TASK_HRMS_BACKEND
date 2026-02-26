from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Sum
from django.utils import timezone
from datetime import datetime
import calendar
from decimal import Decimal

from .models import Payroll
from .serializers import PayrollSerializer, PayrollDetailSerializer, PayrollCalculateSerializer
from employee_management.models import Employee
from master.models import Allowance, Deduction
from attendance.models import Attendance


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_attendance_summary(employee, year, month, total_days):
    """
    Match Employee → auth User by email, then count every attendance status
    for the given month.

    Returned dict keys
    ──────────────────
    present_days  – fully present (checked in + out, no issues)
    absent_days   – no check-in at all
    late_days     – arrived late (approved late-arrival request)
    half_days     – checked in but no check-out
    leave_days    – on approved leave
    working_days  – present + late + half_day  (physically attended office)
    paid_days     – present + late + leave      (no salary deduction)
    """
    summary = {
        'present_days': 0,
        'absent_days':  0,
        'late_days':    0,
        'half_days':    0,
        'leave_days':   0,
        'working_days': 0,
        'paid_days':    0,
    }
    try:
        from login.models import User
        user = User.objects.filter(email=employee.email).first()
        if user:
            att_qs = Attendance.objects.filter(
                user=user, date__year=year, date__month=month,
            )
            summary['present_days'] = att_qs.filter(status='present').count()
            summary['absent_days']  = att_qs.filter(status='absent').count()
            summary['late_days']    = att_qs.filter(status='late').count()
            summary['half_days']    = att_qs.filter(status='half_day').count()
            summary['leave_days']   = att_qs.filter(status='leave').count()
            summary['working_days'] = summary['present_days'] + summary['late_days'] + summary['half_days']
            summary['paid_days']    = summary['present_days'] + summary['late_days'] + summary['leave_days']
        else:
            # No linked user – treat all days as paid to avoid incorrect deductions
            summary['present_days'] = total_days
            summary['working_days'] = total_days
            summary['paid_days']    = total_days
    except Exception:
        summary['present_days'] = total_days
        summary['working_days'] = total_days
        summary['paid_days']    = total_days
    return summary


def _calc_att_deduction(basic_salary, total_days, absent_days, half_days):
    """
    Deduction formula
    ─────────────────
    per_day_salary   = basic_salary / total_days_in_month
    absent_deduction = absent_days              × per_day_salary
    half_deduction   = half_days   × 0.5        × per_day_salary
    total_deduction  = absent_deduction + half_deduction

    Late days  → no deduction (treated as present)
    Leave days → no deduction (treated as paid leave)
    """
    basic   = Decimal(str(basic_salary))
    divisor = Decimal(str(total_days)) if total_days else Decimal('1')
    per_day = (basic / divisor).quantize(Decimal('0.01'))
    deduct  = (
        (Decimal(str(absent_days)) + Decimal(str(half_days)) * Decimal('0.5'))
        * per_day
    ).quantize(Decimal('0.01'))
    return per_day, deduct


def _build_payroll_dict(employee, year, month):
    """
    Central helper used by both employee_data and calculate_payroll.
    Returns a complete dict ready for JSON serialisation.
    """
    total_days   = calendar.monthrange(year, month)[1]
    basic_salary = employee.salary

    att        = _get_attendance_summary(employee, year, month, total_days)
    per_day, att_deduction = _calc_att_deduction(
        basic_salary, total_days, att['absent_days'], att['half_days']
    )

    allowances = Allowance.objects.filter(employee=employee, year=year, month=month, is_active=True)
    deductions = Deduction.objects.filter(employee=employee, year=year, month=month, is_active=True)
    total_allowances        = allowances.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    total_manual_deductions = deductions.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    total_deductions = att_deduction + Decimal(str(total_manual_deductions))
    net_salary = (
        Decimal(str(basic_salary)) + Decimal(str(total_allowances)) - total_deductions
    )

    return {
        'basic_salary':            str(basic_salary),
        'total_allowances':        str(total_allowances),
        'total_deductions':        str(total_deductions),
        'attendance_deduction':    str(att_deduction),
        'manual_deductions_total': str(total_manual_deductions),
        'net_salary':              str(net_salary),
        'total_days_in_month':     total_days,
        'total_working_days':      att['working_days'],
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
        },
        'allowances': [
            {'id': a.id, 'name': a.allowance_name, 'amount': str(a.amount), 'description': a.description}
            for a in allowances
        ],
        'deductions': [
            {'id': d.id, 'name': d.deduction_name, 'amount': str(d.amount), 'description': d.description}
            for d in deductions
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────

class PayrollViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Payroll CRUD operations

    GET    /api/payroll/               – list
    POST   /api/payroll/               – create
    GET    /api/payroll/{id}/          – detail
    PUT    /api/payroll/{id}/          – update
    PATCH  /api/payroll/{id}/          – partial update
    DELETE /api/payroll/{id}/          – delete
    POST   /api/payroll/calculate/     – calculate (not saved)
    GET    /api/payroll/employee-data/ – data used by Payroll.jsx
    POST   /api/payroll/{id}/process/  – mark as processed
    POST   /api/payroll/{id}/mark-paid/– mark as paid
    """

    queryset           = Payroll.objects.select_related('employee', 'processed_by').all()
    serializer_class   = PayrollSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Payroll.objects.select_related(
            'employee', 'employee__department', 'processed_by'
        ).all()
        p = self.request.query_params
        if p.get('employee'):    qs = qs.filter(employee_id=p['employee'])
        if p.get('year'):        qs = qs.filter(year=p['year'])
        if p.get('month'):       qs = qs.filter(month=p['month'])
        if p.get('status'):      qs = qs.filter(status=p['status'])
        return qs

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return PayrollDetailSerializer
        return PayrollSerializer

    # ── POST /api/payroll/calculate/ ──────────────────────────────────────────
    @action(detail=False, methods=['post'], url_path='calculate')
    def calculate_payroll(self, request):
        """
        Preview-only calculation (nothing is saved to DB).
        """
        ser = PayrollCalculateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        employee_id = ser.validated_data['employee_id']
        year        = ser.validated_data['year']
        month       = ser.validated_data['month']

        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)

        data = _build_payroll_dict(employee, year, month)
        data.update({
            'employee_id':   employee.id,
            'employee_name': f"{employee.first_name} {employee.last_name}",
            'employee_code': employee.employee_id,
            'year':          year,
            'month':         month,
            'month_name':    dict(Payroll.MONTH_CHOICES).get(month, ''),
        })
        return Response(data, status=status.HTTP_200_OK)

    # ── GET /api/payroll/employee-data/ ───────────────────────────────────────
    @action(detail=False, methods=['get'], url_path='employee-data')
    def employee_data(self, request):
        """
        Full payroll + attendance breakdown for Payroll.jsx.

        Query params: employee_id, year, month
        """
        employee_id = request.query_params.get('employee_id')
        year        = request.query_params.get('year')
        month       = request.query_params.get('month')

        if not all([employee_id, year, month]):
            return Response(
                {'error': 'employee_id, year, and month are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            year  = int(year)
            month = int(month)
        except ValueError:
            return Response({'error': 'Invalid year or month'}, status=status.HTTP_400_BAD_REQUEST)

        data = _build_payroll_dict(employee, year, month)
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
            'year':                     year,
            'month':                    month,
            'month_name':               dict(Payroll.MONTH_CHOICES).get(month, ''),
            'basic_salary':             data['basic_salary'],
            'total_allowances':         data['total_allowances'],
            'total_deductions':         data['total_deductions'],
            'attendance_deduction':     data['attendance_deduction'],
            'manual_deductions_total':  data['manual_deductions_total'],
            'net_salary':               data['net_salary'],
            'total_days_in_month':      data['total_days_in_month'],
            'total_working_days':       data['total_working_days'],
        }
        return Response(data, status=status.HTTP_200_OK)

    # ── POST /api/payroll/{id}/process/ ───────────────────────────────────────
    @action(detail=True, methods=['post'], url_path='process')
    def process_payroll(self, request, pk=None):
        payroll = self.get_object()
        if payroll.status in ('processed', 'paid'):
            return Response(
                {'error': 'Payroll is already processed or paid'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payroll.status       = 'processed'
        payroll.processed_by = request.user
        payroll.processed_at = timezone.now()
        payroll.save()
        return Response(PayrollDetailSerializer(payroll).data, status=status.HTTP_200_OK)

    # ── POST /api/payroll/{id}/mark-paid/ ─────────────────────────────────────
    @action(detail=True, methods=['post'], url_path='mark-paid')
    def mark_paid(self, request, pk=None):
        payroll = self.get_object()
        if payroll.status == 'paid':
            return Response(
                {'error': 'Payroll is already marked as paid'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payroll.status            = 'paid'
        payroll.payment_date      = request.data.get('payment_date') or payroll.payment_date
        payroll.payment_reference = request.data.get('payment_reference', '')
        payroll.save()
        return Response(PayrollDetailSerializer(payroll).data, status=status.HTTP_200_OK)