from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def runner_required(view_func):
    """Allow only authenticated users who have a runner profile."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.is_runner:
            messages.error(request, 'Please register as a runner first.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)

    return wrapper


def admin_required(view_func):
    """Allow only staff and superusers."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not (request.user.is_staff or request.user.is_superuser):
            messages.error(request, 'You do not have permission to view that page.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)

    return wrapper
