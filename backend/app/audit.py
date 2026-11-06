from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from ipaddress import ip_address

from .models import AuditLog


_audit_request_context = ContextVar('audit_request_context', default=None)
_IGNORED_MODELS = {AuditLog}


def set_audit_request_context(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return _audit_request_context.set(None)

    profile = getattr(user, 'profile', None)
    return _audit_request_context.set({
        'actor': user,
        'actor_username': user.get_username(),
        'actor_role': profile.role if profile else '',
        'actor_is_superuser': user.is_superuser,
        'ip_address': _client_ip(request),
    })


def reset_audit_request_context(token):
    _audit_request_context.reset(token)


def _client_ip(request):
    value = request.META.get('REMOTE_ADDR')
    if not value:
        return None
    try:
        return str(ip_address(value))
    except ValueError:
        return None


def _current_actor():
    return _audit_request_context.get()


def _safe_value(field, value):
    if value is None:
        return None
    if field.choices:
        return str(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal, date, datetime)):
        return str(value)
    return '[changed]'


def _field_snapshot(instance):
    snapshot = {}
    for field in instance._meta.concrete_fields:
        if field.primary_key:
            continue
        snapshot[field.attname] = getattr(instance, field.attname)
    return snapshot


def _record(action, instance, changes=None):
    context = _current_actor()
    if not context:
        return

    model_name = instance._meta.label
    object_id = str(instance.pk or '')
    changes = changes or {}
    AuditLog.objects.create(
        actor=context['actor'],
        actor_username=context['actor_username'],
        actor_role=context['actor_role'],
        actor_is_superuser=context['actor_is_superuser'],
        ip_address=context['ip_address'],
        action=action,
        model_name=model_name,
        object_id=object_id,
        object_label=f"{instance._meta.verbose_name} #{object_id}",
        note=_audit_note(action, instance, changes, context),
        changes=changes,
    )


def _audit_note(action, instance, changes, context):
    actor = context['actor_username'] or 'A user'
    if action == 'login':
        return f"{actor} logged in."
    if action == 'logout':
        return f"{actor} logged out."
    if action == 'password_changed':
        return f"{actor} changed their password."

    if instance._meta.label == 'app.Appraisal':
        employee = instance.employee.user.get_full_name() or instance.employee.user.username
        year = instance.cycle.year
        status_change = changes.get('status', {})
        new_status = status_change.get('after')
        if action == 'created':
            return f"{actor} created the {year} appraisal for {employee}."
        if new_status == 'pending_manager':
            reviewer = _profile_name(instance.supervisor_id)
            return (
                f"{employee} submitted the {year} appraisal to supervisor {reviewer}."
            )
        if new_status == 'pending_hod':
            reviewer = _profile_name(instance.hod_id)
            return f"Supervisor {actor} submitted {employee}'s {year} appraisal to HOD {reviewer}."
        if new_status == 'pending_hr':
            return f"{actor} submitted {employee}'s {year} appraisal to HR for final approval."
        if new_status == 'completed':
            return f"HR {actor} approved and completed {employee}'s {year} appraisal."
        if new_status in {'returned_employee', 'returned_supervisor', 'returned_hod'}:
            recipient = {
                'returned_employee': employee,
                'returned_supervisor': _profile_name(instance.supervisor_id),
                'returned_hod': _profile_name(instance.hod_id),
            }[new_status]
            return f"{actor} returned {employee}'s {year} appraisal to {recipient}."
        if action == 'updated':
            return f"{actor} updated the {year} appraisal for {employee}."

    label = getattr(instance._meta, 'verbose_name', instance.__class__.__name__)
    if action == 'created':
        return f"{actor} created {label} #{instance.pk}."
    if action == 'deleted':
        return f"{actor} deleted {label} #{instance.pk}."
    if action == 'updated':
        changed_fields = ', '.join(changes.keys())
        return f"{actor} updated {label} #{instance.pk}: {changed_fields}."
    return f"{actor} performed {action.replace('_', ' ')} on {label} #{instance.pk}."


def _profile_name(profile_id):
    if not profile_id:
        return 'unassigned'
    from .models import EmployeeProfile

    profile = EmployeeProfile.objects.select_related('user').filter(pk=profile_id).first()
    if not profile:
        return 'unassigned'
    return profile.user.get_full_name() or profile.user.username


def record_auth_event(user, action, request):
    token = set_audit_request_context(request)
    try:
        _record(action, user)
    finally:
        reset_audit_request_context(token)


def _tracked_model(model):
    return (
        model._meta.app_label in {'app', 'auth'}
        and model not in _IGNORED_MODELS
        and model._meta.model_name != 'appnotification'
    )


def register_audit_signals():
    from django.contrib.auth.signals import user_logged_in, user_logged_out
    from django.db.models.signals import m2m_changed, post_save, pre_delete, pre_save
    from django.dispatch import receiver

    @receiver(pre_save, dispatch_uid='app.audit.capture_before', weak=False)
    def capture_before(sender, instance, raw=False, **kwargs):
        if raw or not _current_actor() or not _tracked_model(sender) or not instance.pk:
            return
        try:
            previous = sender.objects.get(pk=instance.pk)
        except sender.DoesNotExist:
            instance._audit_previous = None
            return
        instance._audit_previous = _field_snapshot(previous)

    @receiver(post_save, dispatch_uid='app.audit.record_save', weak=False)
    def record_save(sender, instance, created, raw=False, update_fields=None, **kwargs):
        if raw or not _current_actor() or not _tracked_model(sender):
            return
        if created:
            changes = {'record': 'created'}
            action = 'created'
        else:
            previous = getattr(instance, '_audit_previous', None)
            current = _field_snapshot(instance)
            changes = {}
            for field in instance._meta.concrete_fields:
                if field.primary_key:
                    continue
                if update_fields is not None and field.name not in update_fields:
                    continue
                old_value = previous.get(field.attname) if previous else None
                new_value = current[field.attname]
                if old_value != new_value:
                    changes[field.name] = {
                        'before': _safe_value(field, old_value),
                        'after': _safe_value(field, new_value),
                    }
            if not changes:
                return
            action = 'updated'
        _record(action, instance, changes)

    @receiver(pre_delete, dispatch_uid='app.audit.record_delete', weak=False)
    def record_delete(sender, instance, **kwargs):
        if _current_actor() and _tracked_model(sender):
            _record('deleted', instance, {'record': 'deleted'})

    @receiver(m2m_changed, dispatch_uid='app.audit.record_many_to_many', weak=False)
    def record_many_to_many(sender, instance, action, reverse, pk_set, **kwargs):
        if (
            action not in {'post_add', 'post_remove', 'post_clear'}
            or not _current_actor()
        ):
            return
        if reverse or not _tracked_model(instance.__class__):
            return
        field = next(
            (
                field for field in instance._meta.many_to_many
                if field.remote_field.through is sender
            ),
            None,
        )
        if not field:
            return
        _record('updated', instance, {
            field.name: {
                'operation': action.removeprefix('post_'),
                'count': len(pk_set) if pk_set is not None else None,
            },
        })

    user_logged_in.connect(
        lambda sender, request, user, **kwargs: record_auth_event(user, 'login', request),
        dispatch_uid='app.audit.login',
        weak=False,
    )
    user_logged_out.connect(
        lambda sender, request, user, **kwargs: (
            record_auth_event(user, 'logout', request) if user else None
        ),
        dispatch_uid='app.audit.logout',
        weak=False,
    )
