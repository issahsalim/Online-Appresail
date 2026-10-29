from .models import AppNotification


def app_notifications(request):
    if not request.user.is_authenticated:
        return {
            'navbar_notifications': [],
            'unread_notification_count': 0,
        }

    recipient_notifications = AppNotification.objects.filter(
        recipient=request.user,
    )
    return {
        'navbar_notifications': recipient_notifications[:6],
        'unread_notification_count': recipient_notifications.filter(
            read_at__isnull=True,
        ).count(),
    }
