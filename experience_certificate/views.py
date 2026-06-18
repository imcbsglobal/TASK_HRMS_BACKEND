from datetime import date

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from employee_management.models import Employee
from login.models import CompanySettings

from .models import ExperienceCertificate
from .pdf import generate_experience_certificate_pdf
from .serializers import ExperienceCertificateSerializer
from activitylog.utils import log_activity


def _get_admin_owner(user):
    if user.role == 'ADMIN':
        return user
    if user.role == 'USER':
        return user.admin_owner
    return None


def _employee_qs(user):
    if user.role == 'SUPER_ADMIN':
        return Employee.objects.select_related('department')
    admin = _get_admin_owner(user)
    if admin is None:
        return Employee.objects.none()
    return Employee.objects.select_related('department').filter(admin_owner=admin)


def _certificate_qs(user):
    qs = ExperienceCertificate.objects.select_related(
        'employee', 'employee__department', 'admin_owner', 'issued_by'
    )
    if user.role == 'SUPER_ADMIN':
        return qs
    admin = _get_admin_owner(user)
    if admin is None:
        return qs.none()
    return qs.filter(admin_owner=admin)


def _company_settings_dict(user):
    admin = _get_admin_owner(user)
    if admin is None:
        return {}

    settings_obj = CompanySettings.objects.filter(owner=admin).first()
    if not settings_obj:
        return {
            'name': admin.company_name or 'Company',
            'address': '',
            'email': '',
            'phone': '',
            'website': '',
        }

    return {
        'name': settings_obj.name,
        'address': settings_obj.address,
        'email': settings_obj.email,
        'phone': settings_obj.phone,
        'website': settings_obj.website,
    }


class ExperienceCertificateListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        certificates = _certificate_qs(request.user)
        employee_id = request.query_params.get('employee')
        status_filter = request.query_params.get('status')

        if employee_id:
            certificates = certificates.filter(employee_id=employee_id)
        if status_filter:
            certificates = certificates.filter(status=status_filter)

        serializer = ExperienceCertificateSerializer(
            certificates,
            many=True,
            context={'request': request},
        )
        return Response(serializer.data)

    def post(self, request):
        if request.user.role == 'USER':
            return Response(
                {'detail': 'You do not have permission to create experience certificates.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        employee_id = request.data.get('employee')
        try:
            employee = _employee_qs(request.user).get(pk=employee_id)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = ExperienceCertificateSerializer(
            data=request.data,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        certificate = serializer.save(
            employee=employee,
            admin_owner=employee.admin_owner,
            employee_name=f'{employee.first_name} {employee.last_name}'.strip(),
            employee_code=employee.employee_id,
            designation=validated.get('designation') or employee.position,
            department=validated.get('department') or (
                employee.department.name if employee.department else ''
            ),
            employment_type=validated.get('employment_type') or employee.employment_type,
            start_date=validated.get('start_date') or employee.date_of_joining,
        )
        log_activity(
            user=request.user,
            action_type='CREATE',
            module='Experience Certificate',
            description=f"Created experience certificate {certificate.certificate_number} for {certificate.employee_name}",
            request=request,
        )
        return Response(
            ExperienceCertificateSerializer(certificate, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class ExperienceCertificateDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_certificate(self, request, pk):
        try:
            return _certificate_qs(request.user).get(pk=pk), None
        except ExperienceCertificate.DoesNotExist:
            return None, Response(
                {'error': 'Experience certificate not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

    def get(self, request, pk):
        certificate, error = self._get_certificate(request, pk)
        if error:
            return error
        return Response(ExperienceCertificateSerializer(certificate, context={'request': request}).data)

    def patch(self, request, pk):
        if request.user.role == 'USER':
            return Response(
                {'detail': 'You do not have permission to update experience certificates.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        certificate, error = self._get_certificate(request, pk)
        if error:
            return error
        if certificate.status == 'issued':
            return Response(
                {'error': 'Issued certificates cannot be edited. Revoke and create a new certificate if needed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ExperienceCertificateSerializer(
            certificate,
            data=request.data,
            partial=True,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Experience Certificate',
            description=f"Updated experience certificate {certificate.certificate_number} for {certificate.employee_name}",
            request=request,
        )
        return Response(serializer.data)

    def delete(self, request, pk):
        if request.user.role not in ('SUPER_ADMIN', 'ADMIN'):
            return Response(
                {'detail': 'You do not have permission to delete experience certificates.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        certificate, error = self._get_certificate(request, pk)
        if error:
            return error
        cert_number = certificate.certificate_number
        cert_employee = certificate.employee_name
        certificate.delete()
        log_activity(
            user=request.user,
            action_type='DELETE',
            module='Experience Certificate',
            description=f"Deleted experience certificate {cert_number} for {cert_employee}",
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExperienceCertificateIssueView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role == 'USER':
            return Response(
                {'detail': 'You do not have permission to issue experience certificates.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            certificate = _certificate_qs(request.user).get(pk=pk)
        except ExperienceCertificate.DoesNotExist:
            return Response({'error': 'Experience certificate not found.'}, status=status.HTTP_404_NOT_FOUND)

        certificate.status = 'issued'
        certificate.issued_by = request.user
        certificate.issued_at = timezone.now()
        issue_date = request.data.get('issue_date')
        if issue_date:
            try:
                certificate.issue_date = date.fromisoformat(issue_date)
            except ValueError:
                return Response(
                    {'error': 'issue_date must be in YYYY-MM-DD format.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        certificate.save(update_fields=['status', 'issued_by', 'issued_at', 'issue_date', 'updated_at'])

        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Experience Certificate',
            description=f"Issued experience certificate {certificate.certificate_number} for {certificate.employee_name}",
            request=request,
        )
        return Response(ExperienceCertificateSerializer(certificate, context={'request': request}).data)


class ExperienceCertificateRevokeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role not in ('SUPER_ADMIN', 'ADMIN'):
            return Response(
                {'detail': 'You do not have permission to revoke experience certificates.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            certificate = _certificate_qs(request.user).get(pk=pk)
        except ExperienceCertificate.DoesNotExist:
            return Response({'error': 'Experience certificate not found.'}, status=status.HTTP_404_NOT_FOUND)

        certificate.status = 'revoked'
        certificate.save(update_fields=['status', 'updated_at'])
        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Experience Certificate',
            description=f"Revoked experience certificate {certificate.certificate_number} for {certificate.employee_name}",
            request=request,
        )
        return Response(ExperienceCertificateSerializer(certificate, context={'request': request}).data)


class ExperienceCertificateDownloadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        try:
            certificate = _certificate_qs(request.user).get(pk=pk)
        except ExperienceCertificate.DoesNotExist:
            return Response({'error': 'Experience certificate not found.'}, status=status.HTTP_404_NOT_FOUND)

        if certificate.status == 'revoked':
            return Response(
                {'error': 'Revoked certificates cannot be downloaded.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            pdf_bytes = generate_experience_certificate_pdf(
                certificate,
                _company_settings_dict(request.user),
            )
        except Exception as exc:
            return Response({'error': f'PDF generation failed: {exc}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        filename_name = certificate.employee_name.replace(' ', '_')
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="Experience_Certificate_{filename_name}.pdf"'
        )
        log_activity(
            user=request.user,
            action_type='OTHER',
            module='Experience Certificate',
            description=f"Downloaded experience certificate {certificate.certificate_number} for {certificate.employee_name}",
            request=request,
        )
        return response
