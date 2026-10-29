from django.utils import timezone

from .models import AppNotification


def notify_users(users, *, title, message, url=''):
    recipients = {
        user.pk: user
        for user in users
        if user is not None and user.is_authenticated
    }
    if not recipients:
        return

    AppNotification.objects.bulk_create([
        AppNotification(
            recipient=user,
            title=title,
            message=message,
            url=url,
        )
        for user in recipients.values()
    ])


def mark_as_read(notification):
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=['read_at'])
