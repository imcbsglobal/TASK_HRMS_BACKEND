from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from HR.models import Candidate
from .models import Employee, Department
from .serializers import EmployeeSerializer, DepartmentSerializer


class CandidateToEmployeeView(APIView):
    def get(self, request, candidate_id):
        candidate = Candidate.objects.get(id=candidate_id)
        name_parts = candidate.name.split(" ")

        return Response({
            "candidate_id": candidate.id,
            "first_name": name_parts[0],
            "last_name": " ".join(name_parts[1:]),
            "email": candidate.email,
            "phone": candidate.phone,
        })


class EmployeeListCreateView(APIView):
    def get(self, request):
        employees = Employee.objects.select_related("department", "candidate")
        serializer = EmployeeSerializer(employees, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = EmployeeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class EmployeeDetailView(APIView):
    def put(self, request, pk):
        employee = Employee.objects.get(pk=pk)
        serializer = EmployeeSerializer(employee, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        Employee.objects.filter(pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DepartmentListCreateView(APIView):
    """
    List all departments or create a new department
    """
    def get(self, request):
        departments = Department.objects.all()
        serializer = DepartmentSerializer(departments, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = DepartmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class DepartmentDetailView(APIView):
    """
    Retrieve, update or delete a department
    """
    def get(self, request, pk):
        try:
            department = Department.objects.get(pk=pk)
            serializer = DepartmentSerializer(department)
            return Response(serializer.data)
        except Department.DoesNotExist:
            return Response(
                {"error": "Department not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )

    def put(self, request, pk):
        try:
            department = Department.objects.get(pk=pk)
            serializer = DepartmentSerializer(department, data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        except Department.DoesNotExist:
            return Response(
                {"error": "Department not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )

    def delete(self, request, pk):
        try:
            department = Department.objects.get(pk=pk)
            # Check if department has employees
            if department.employees.exists():
                return Response(
                    {"error": "Cannot delete department with associated employees"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            department.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Department.DoesNotExist:
            return Response(
                {"error": "Department not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )