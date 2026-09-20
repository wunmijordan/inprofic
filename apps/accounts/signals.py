from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .analytics import record_platform_event
from .models import PlatformEvent


@receiver(user_logged_in)
def capture_login(sender, request, user, **kwargs):
    record_platform_event(PlatformEvent.EVENT_LOGIN, request=request, user=user)


@receiver(user_logged_out)
def capture_logout(sender, request, user, **kwargs):
    if user is not None:
        record_platform_event(PlatformEvent.EVENT_LOGOUT, request=request, user=user)
