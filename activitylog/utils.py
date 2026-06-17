from .models import ActivityLog


def log_activity(
    user,
    action_type,
    module,
    description,
    request=None,
    ip_address=None,
    user_agent=None
):
    """
    Utility function to create an activity log entry quickly.

    Args:
        user (User): User performing the action
        action_type (str): One of CREATE / UPDATE / DELETE / LOGIN / LOGOUT / OTHER
        module (str): Module/section name (e.g., Attendance, Payroll)
        description (str): Detailed description of the action
        request (HttpRequest, optional): Django request object to auto-fill IP and user agent
        ip_address (str, optional): Manual IP address if request not available
        user_agent (str, optional): Manual user agent if request not available
    """
    # Extract IP and user agent from request if available
    if request:
        if not ip_address:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                ip_address = x_forwarded_for.split(',')[0].strip()
            else:
                ip_address = request.META.get('REMOTE_ADDR')

        if not user_agent:
            user_agent = request.META.get('HTTP_USER_AGENT', '')

    log = ActivityLog.objects.create(
        user=user,
        action_type=action_type,
        module=module,
        description=description,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return log


class ActivityLogMixin:
    """
    DRF ModelViewSet mixin that automatically logs CREATE / UPDATE / DELETE
    actions to the ActivityLog.

    Sub-classes must define:
        activity_log_module  (str)  – e.g. "Master", "Payroll"
        activity_log_object_name (str, optional) – human label for the object,
            defaults to the model's verbose_name.

    Override `get_activity_log_description` for custom descriptions.
    """

    activity_log_module: str = "System"
    activity_log_object_name: str = ""

    # ── helpers ──────────────────────────────────────────────────────────────

    def _object_label(self, instance):
        """Return a short human-readable label for the instance."""
        name = self.activity_log_object_name
        if not name:
            name = instance.__class__.__name__
        # Try to get a meaningful identifier from the object
        for attr in ('name', 'title', 'full_name', 'username', 'employee_id', '__str__'):
            val = getattr(instance, attr, None)
            if val and callable(val):
                val = val()
            if val:
                return f"{name} '{val}'"
        return name

    def get_activity_log_description(self, action_type, instance):
        label = self._object_label(instance)
        if action_type == 'CREATE':
            return f"Created {label}"
        if action_type == 'UPDATE':
            return f"Updated {label}"
        if action_type == 'DELETE':
            return f"Deleted {label}"
        return f"{action_type} {label}"

    # ── DRF hooks ─────────────────────────────────────────────────────────────

    def perform_create(self, serializer):
        super().perform_create(serializer)
        instance = serializer.instance
        log_activity(
            user=self.request.user,
            action_type='CREATE',
            module=self.activity_log_module,
            description=self.get_activity_log_description('CREATE', instance),
            request=self.request,
        )

    def perform_update(self, serializer):
        super().perform_update(serializer)
        instance = serializer.instance
        log_activity(
            user=self.request.user,
            action_type='UPDATE',
            module=self.activity_log_module,
            description=self.get_activity_log_description('UPDATE', instance),
            request=self.request,
        )

    def perform_destroy(self, instance):
        description = self.get_activity_log_description('DELETE', instance)
        super().perform_destroy(instance)
        log_activity(
            user=self.request.user,
            action_type='DELETE',
            module=self.activity_log_module,
            description=description,
            request=self.request,
        )
