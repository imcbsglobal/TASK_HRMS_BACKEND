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


class PayrollViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Payroll CRUD operations
    
    Endpoints:
    - GET    /api/payroll/                    - List all payrolls
    - POST   /api/payroll/                    - Create new payroll
    - GET    /api/payroll/{id}/               - Retrieve specific payroll
    - PUT    /api/payroll/{id}/               - Update payroll
    - PATCH  /api/payroll/{id}/               - Partial update
    - DELETE /api/payroll/{id}/               - Delete payroll
    - POST   /api/payroll/calculate/          - Calculate payroll for employee/month
    - GET    /api/payroll/employee_data/      - Get employee payroll data
    """
    
    queryset = Payroll.objects.select_related('employee', 'processed_by').all()
    serializer_class = PayrollSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter payrolls based on query parameters"""
        queryset = Payroll.objects.select_related('employee', 'employee__department', 'processed_by').all()
        
        # Filter by employee
        employee_id = self.request.query_params.get('employee', None)
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        
        # Filter by year
        year = self.request.query_params.get('year', None)
        if year:
            queryset = queryset.filter(year=year)
        
        # Filter by month
        month = self.request.query_params.get('month', None)
        if month:
            queryset = queryset.filter(month=month)
        
        # Filter by status
        payroll_status = self.request.query_params.get('status', None)
        if payroll_status:
            queryset = queryset.filter(status=payroll_status)
        
        return queryset
    
    def get_serializer_class(self):
        """Use detailed serializer for retrieve action"""
        if self.action == 'retrieve':
            return PayrollDetailSerializer
        return PayrollSerializer
    
    @action(detail=False, methods=['post'], url_path='calculate')
    def calculate_payroll(self, request):
        """
        Calculate and optionally create payroll for an employee for a specific month
        
        POST /api/payroll/calculate/
        Body: {
            "employee_id": 1,
            "year": 2026,
            "month": 4
        }
        """
        serializer = PayrollCalculateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        employee_id = serializer.validated_data['employee_id']
        year = serializer.validated_data['year']
        month = serializer.validated_data['month']
        
        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response(
                {'error': 'Employee not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Calculate total days in month
        total_days = calendar.monthrange(year, month)[1]
        
        # Calculate working days from attendance
        # Try to find user by matching email with employee
        try:
            from login.models import User
            user = User.objects.filter(email=employee.email).first()
            if user:
                working_days = Attendance.objects.filter(
                    user=user,
                    date__year=year,
                    date__month=month,
                    status__in=['present', 'late', 'half_day']
                ).count()
            else:
                # If no user found, use total days as default
                working_days = total_days
        except Exception:
            # If any error, default to total days
            working_days = total_days
        
        # Get basic salary
        basic_salary = employee.salary
        
        # Calculate total allowances
        allowances = Allowance.objects.filter(
            employee=employee,
            year=year,
            month=month,
            is_active=True
        )
        total_allowances = allowances.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Calculate total deductions
        deductions = Deduction.objects.filter(
            employee=employee,
            year=year,
            month=month,
            is_active=True
        )
        total_deductions = deductions.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Calculate net salary
        net_salary = Decimal(str(basic_salary)) + Decimal(str(total_allowances)) - Decimal(str(total_deductions))
        
        # Get allowances and deductions details
        allowances_list = [
            {
                'id': a.id,
                'name': a.allowance_name,
                'amount': str(a.amount),
                'description': a.description,
            }
            for a in allowances
        ]
        
        deductions_list = [
            {
                'id': d.id,
                'name': d.deduction_name,
                'amount': str(d.amount),
                'description': d.description,
            }
            for d in deductions
        ]
        
        # Prepare response data
        calculation_data = {
            'employee_id': employee.id,
            'employee_name': f"{employee.first_name} {employee.last_name}",
            'employee_code': employee.employee_id,
            'year': year,
            'month': month,
            'month_name': dict(Payroll.MONTH_CHOICES).get(month, ''),
            'basic_salary': str(basic_salary),
            'total_allowances': str(total_allowances),
            'total_deductions': str(total_deductions),
            'net_salary': str(net_salary),
            'total_days_in_month': total_days,
            'total_working_days': working_days,
            'allowances': allowances_list,
            'deductions': deductions_list,
        }
        
        return Response(calculation_data, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='employee-data')
    def employee_data(self, request):
        """
        Get employee payroll data for a specific month/year
        
        GET /api/payroll/employee-data/?employee_id=1&year=2026&month=4
        """
        employee_id = request.query_params.get('employee_id')
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        
        if not all([employee_id, year, month]):
            return Response(
                {'error': 'employee_id, year, and month are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            employee = Employee.objects.get(id=employee_id)
        except Employee.DoesNotExist:
            return Response(
                {'error': 'Employee not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Try to convert to integers
        try:
            year = int(year)
            month = int(month)
        except ValueError:
            return Response(
                {'error': 'Invalid year or month'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Calculate total days in month
        total_days = calendar.monthrange(year, month)[1]
        
        # Calculate working days from attendance
        # Try to find user by matching email with employee
        try:
            from login.models import User
            user = User.objects.filter(email=employee.email).first()
            if user:
                working_days = Attendance.objects.filter(
                    user=user,
                    date__year=year,
                    date__month=month,
                    status__in=['present', 'late', 'half_day']
                ).count()
            else:
                # If no user found, use total days as default
                working_days = total_days
        except Exception:
            # If any error, default to total days
            working_days = total_days
        
        # Get basic salary
        basic_salary = employee.salary
        
        # Calculate total allowances
        allowances = Allowance.objects.filter(
            employee=employee,
            year=year,
            month=month,
            is_active=True
        )
        total_allowances = allowances.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Calculate total deductions
        deductions = Deduction.objects.filter(
            employee=employee,
            year=year,
            month=month,
            is_active=True
        )
        total_deductions = deductions.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Calculate net salary
        net_salary = Decimal(str(basic_salary)) + Decimal(str(total_allowances)) - Decimal(str(total_deductions))
        
        # Get allowances and deductions details
        allowances_list = [
            {
                'id': a.id,
                'name': a.allowance_name,
                'amount': str(a.amount),
                'description': a.description,
            }
            for a in allowances
        ]
        
        deductions_list = [
            {
                'id': d.id,
                'name': d.deduction_name,
                'amount': str(d.amount),
                'description': d.description,
            }
            for d in deductions
        ]
        
        # Prepare response data
        response_data = {
            'employee': {
                'id': employee.id,
                'employee_id': employee.employee_id,
                'name': f"{employee.first_name} {employee.last_name}",
                'email': employee.email,
                'position': employee.position,
                'department': employee.department.name if employee.department else '',
                'phone': employee.phone,
            },
            'payroll': {
                'year': year,
                'month': month,
                'month_name': dict(Payroll.MONTH_CHOICES).get(month, ''),
                'basic_salary': str(basic_salary),
                'total_allowances': str(total_allowances),
                'total_deductions': str(total_deductions),
                'net_salary': str(net_salary),
                'total_days_in_month': total_days,
                'total_working_days': working_days,
            },
            'allowances': allowances_list,
            'deductions': deductions_list,
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'], url_path='process')
    def process_payroll(self, request, pk=None):
        """
        Process a payroll (change status to processed)
        
        POST /api/payroll/{id}/process/
        """
        payroll = self.get_object()
        
        if payroll.status == 'processed' or payroll.status == 'paid':
            return Response(
                {'error': 'Payroll is already processed or paid'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        payroll.status = 'processed'
        payroll.processed_by = request.user
        payroll.processed_at = timezone.now()
        payroll.save()
        
        serializer = PayrollDetailSerializer(payroll)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'], url_path='mark-paid')
    def mark_paid(self, request, pk=None):
        """
        Mark a payroll as paid
        
        POST /api/payroll/{id}/mark-paid/
        Body: {
            "payment_date": "2026-04-30",
            "payment_reference": "TXN123456"
        }
        """
        payroll = self.get_object()
        
        if payroll.status == 'paid':
            return Response(
                {'error': 'Payroll is already marked as paid'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        payment_date = request.data.get('payment_date')
        payment_reference = request.data.get('payment_reference', '')
        
        payroll.status = 'paid'
        if payment_date:
            payroll.payment_date = payment_date
        payroll.payment_reference = payment_reference
        payroll.save()
        
        serializer = PayrollDetailSerializer(payroll)
        return Response(serializer.data, status=status.HTTP_200_OK)
