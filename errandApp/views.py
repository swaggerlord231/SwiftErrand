from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Q, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .decorators import admin_required, runner_required
from .forms import DepositForm, ErrandForm, ProfileForm, ReviewForm, WithdrawForm
from .models import Errand, ErrandImage, Review, RunnerProfile, Transaction, Wallet

User = get_user_model()

ACTIVE_STATUSES = [Errand.STATUS_ACCEPTED, Errand.STATUS_PICKUP, Errand.STATUS_TRANSIT]


# ---------------------------------------------------------------------------
# Public pages and authentication
# ---------------------------------------------------------------------------

def home(request):
    return render(request, 'errandApp/public/home.html')


def register(request):
    context = {}

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip().lower()
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')
        wants_runner = request.POST.get('is_runner') == 'on'

        context.update({
            'first_name': first_name,
            'last_name': last_name,
            'username': username,
            'email': email,
        })

        error = None
        if not all([first_name, last_name, username, email, password1, password2]):
            error = 'Please fill in all required fields.'
        elif password1 != password2:
            error = 'Passwords do not match.'
        elif User.objects.filter(Q(username__iexact=username) | Q(email__iexact=email)).exists():
            error = 'That username or email is already registered.'
        else:
            try:
                validate_password(password1)
            except ValidationError as exc:
                error = ' '.join(exc.messages)

        if error:
            context['error'] = error
            return render(request, 'errandApp/auth/sign_up.html', context)

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password1,
            first_name=first_name,
            last_name=last_name,
            is_runner=wants_runner,
        )
        if wants_runner:
            RunnerProfile.objects.get_or_create(user=user)

        login(request, user)
        messages.success(request, f'Welcome to Swift Errand, {user.display_name}!')
        return redirect('runner_dashboard' if user.is_runner else 'dashboard')

    return render(request, 'errandApp/auth/sign_up.html', context)


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
    username = ''

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            messages.success(request, f'Welcome back, {user.display_name}!')

            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect('dashboard')

        error = 'Invalid username or password.'

    return render(request, 'errandApp/auth/sign_in.html', {
        'error': error,
        'username': username,
        'next': request.GET.get('next', ''),
    })


@login_required
@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('home')


# ---------------------------------------------------------------------------
# Customer dashboard
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    user = request.user
    errands = Errand.objects.filter(customer=user)

    recent_errands = errands.select_related('runner').order_by('-created_at')[:5]
    total_spent = (
        errands.filter(status=Errand.STATUS_COMPLETED).aggregate(total=Sum('price'))['total']
        or Decimal('0.00')
    )

    today = timezone.localdate()
    trend_days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    trend_labels = [day.strftime('%a') for day in trend_days]
    trend_posted = [
        errands.filter(created_at__date=day).count() for day in trend_days
    ]
    trend_completed = [
        errands.filter(completed_at__date=day, status=Errand.STATUS_COMPLETED).count()
        for day in trend_days
    ]

    status_counts = [
        errands.filter(status=Errand.STATUS_COMPLETED).count(),
        errands.filter(status__in=ACTIVE_STATUSES + [Errand.STATUS_DELIVERED]).count(),
        errands.filter(status=Errand.STATUS_PENDING).count(),
        errands.filter(status=Errand.STATUS_CANCELLED).count(),
    ]

    context = {
        'total_errands': errands.count(),
        'completed_errands': errands.filter(
            status__in=[Errand.STATUS_DELIVERED, Errand.STATUS_COMPLETED]
        ).count(),
        'in_progress_errands': errands.filter(
            status__in=ACTIVE_STATUSES + [Errand.STATUS_DELIVERED]
        ).count(),
        'pending_errands': errands.filter(status=Errand.STATUS_PENDING).count(),
        'recent_errands': recent_errands,
        'total_spent': total_spent,
        'trend_labels': trend_labels,
        'trend_posted': trend_posted,
        'trend_completed': trend_completed,
        'status_counts': status_counts,
        'is_runner': user.is_runner,
    }
    return render(request, 'errandApp/dashboard/index.html', context)


@login_required
def account_settings(request):
    profile_form = ProfileForm(instance=request.user)
    password_form = PasswordChangeForm(user=request.user)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'password':
            password_form = PasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Your password was updated successfully.')
                return redirect('account_settings')
            messages.error(request, 'Please correct the password errors below.')

        else:
            profile_form = ProfileForm(request.POST, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, 'Your profile was updated successfully.')
                return redirect('account_settings')
            messages.error(request, 'Please correct the profile errors below.')

    return render(request, 'errandApp/dashboard/profile_settings.html', {
        'profile_form': profile_form,
        'password_form': password_form,
    })


# ---------------------------------------------------------------------------
# Errands (customer)
# ---------------------------------------------------------------------------

def _save_errand_images(request, errand):
    files = request.FILES.getlist('images')
    existing = errand.images.count()
    max_images = getattr(settings, 'MAX_IMAGES_PER_ERRAND', 5)
    max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 5 * 1024 * 1024)
    added = 0

    for upload in files:
        if existing + added >= max_images:
            messages.warning(request, f'Only {max_images} images are allowed per errand.')
            break
        if upload.size > max_size:
            messages.warning(request, f'"{upload.name}" is larger than 5 MB and was skipped.')
            continue
        if upload.content_type and not upload.content_type.startswith('image/'):
            messages.warning(request, f'"{upload.name}" is not an image and was skipped.')
            continue
        ErrandImage.objects.create(errand=errand, image=upload)
        added += 1

    return added


def _first_form_error(form):
    for errors in form.errors.values():
        if errors:
            return errors[0]
    return 'Please check the form and try again.'


def _wallet_for(user):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


@login_required
def create_errand(request):
    wallet = _wallet_for(request.user)

    if request.method == 'POST':
        form = ErrandForm(request.POST, request.FILES)
        if form.is_valid():
            price = form.cleaned_data['price']
            try:
                with transaction.atomic():
                    locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
                    if locked_wallet.balance < price:
                        raise ValueError(
                            f'Insufficient wallet balance. This errand costs '
                            f'₦{price:,.2f} but your wallet has '
                            f'₦{locked_wallet.balance:,.2f}.'
                        )

                    errand = form.save(commit=False)
                    errand.customer = request.user
                    errand.escrow_funded = True
                    errand.save()
                    locked_wallet.debit(
                        price,
                        description=f'Funds held for errand #{errand.id}: {errand.title}',
                        errand=errand,
                    )
                    _save_errand_images(request, errand)
            except ValueError as exc:
                messages.error(request, f'{exc} Please deposit funds into your wallet first.')
            else:
                messages.success(
                    request,
                    'Errand created successfully! The price has been held in '
                    'your wallet and will be paid to the runner on completion.',
                )
                return redirect('my_errands')
        else:
            messages.error(request, 'Please fix the highlighted errors and try again.')
    else:
        form = ErrandForm()

    wallet.refresh_from_db()
    return render(request, 'errandApp/errands/create_errand.html', {
        'form': form,
        'category_choices': Errand.CATEGORY_CHOICES,
        'urgency_choices': Errand.URGENCY_CHOICES,
        'wallet_balance': wallet.balance,
    })


@login_required
def my_errands(request):
    errands = (
        Errand.objects.filter(customer=request.user)
        .select_related('runner')
        .order_by('-created_at')
    )

    status = request.GET.get('status', '').strip()
    search = request.GET.get('search', '').strip()
    if status in dict(Errand.STATUS_CHOICES):
        errands = errands.filter(status=status)
    if search:
        errands = errands.filter(
            Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(pickup_address__icontains=search)
            | Q(dropoff_address__icontains=search)
        )

    return render(request, 'errandApp/errands/listErrands.html', {
        'errands': errands,
        'status_choices': Errand.STATUS_CHOICES,
        'active_status': status,
        'search': search,
    })


@login_required
def errand_detail(request, id):
    errand = get_object_or_404(
        Errand.objects.select_related('customer', 'runner').prefetch_related('images'),
        id=id,
    )
    is_customer = request.user == errand.customer
    is_runner = request.user == errand.runner
    is_admin = request.user.is_staff or request.user.is_superuser

    if not (is_customer or is_runner or is_admin):
        return HttpResponseForbidden('You do not have access to this errand.')

    can_review = (
        is_customer
        and errand.status == Errand.STATUS_COMPLETED
        and errand.runner_id is not None
        and not Review.objects.filter(errand=errand).exists()
    )

    return render(request, 'errandApp/errands/errandDetail.html', {
        'errand': errand,
        'is_customer': is_customer,
        'is_runner': is_runner,
        'is_admin': is_admin,
        'can_review': can_review,
        'review': getattr(errand, 'review', None),
    })


@login_required
def edit_errand(request, id):
    errand = get_object_or_404(Errand, id=id, customer=request.user)

    if errand.status != Errand.STATUS_PENDING:
        messages.error(request, 'Only pending errands can be edited.')
        return redirect('errand_detail', id=id)

    if request.method == 'POST':
        form = ErrandForm(request.POST, request.FILES, instance=errand)
        if form.is_valid():
            new_price = form.cleaned_data['price']
            try:
                with transaction.atomic():
                    locked = Errand.objects.select_for_update().get(pk=errand.pk)
                    if locked.status != Errand.STATUS_PENDING:
                        messages.error(request, 'Only pending errands can be edited.')
                        return redirect('errand_detail', id=id)

                    difference = new_price - locked.price
                    wallet = None
                    if locked.escrow_funded and difference:
                        wallet, _ = Wallet.objects.select_for_update().get_or_create(
                            user=request.user
                        )
                        if difference > 0 and wallet.balance < difference:
                            raise ValueError(
                                f'You need ₦{difference:,.2f} more in your wallet '
                                f'to raise the price to ₦{new_price:,.2f}.'
                            )

                    form.save()
                    if wallet is not None:
                        if difference > 0:
                            wallet.debit(
                                difference,
                                description=f'Additional funds for errand #{locked.id}',
                                errand=locked,
                            )
                        else:
                            wallet.credit(
                                -difference,
                                description=f'Price reduction refund for errand #{locked.id}',
                                errand=locked,
                            )
                    _save_errand_images(request, errand)
            except ValueError as exc:
                messages.error(request, f'{exc} Please deposit funds and try again.')
            else:
                messages.success(request, 'Errand updated successfully.')
                return redirect('errand_detail', id=id)
        else:
            messages.error(request, 'Please fix the highlighted errors.')
    else:
        form = ErrandForm(instance=errand)

    return render(request, 'errandApp/errands/edit_errand.html', {
        'errand': errand,
        'form': form,
    })


@login_required
@require_POST
def cancel_errand(request, id):
    with transaction.atomic():
        errand = get_object_or_404(
            Errand.objects.select_for_update(), id=id, customer=request.user
        )

        if not errand.can_be_cancelled:
            messages.error(request, 'You can only cancel a pending errand.')
            return redirect('errand_detail', id=errand.id)

        errand.status = Errand.STATUS_CANCELLED
        errand.cancelled_at = timezone.now()

        if errand.escrow_funded:
            wallet, _ = Wallet.objects.select_for_update().get_or_create(
                user=request.user
            )
            wallet.credit(
                errand.price,
                description=f'Refund for cancelled errand #{errand.id}: {errand.title}',
                errand=errand,
            )
            errand.escrow_funded = False

        errand.save()

    messages.success(request, 'Errand cancelled. Any held funds were returned to your wallet.')
    return redirect('my_errands')


@login_required
def rate_runner(request, id):
    errand = get_object_or_404(
        Errand.objects.select_related('runner'),
        id=id,
        customer=request.user,
        status=Errand.STATUS_COMPLETED,
    )

    if not errand.runner:
        messages.error(request, 'This errand has no runner to review.')
        return redirect('errand_detail', id=id)

    if Review.objects.filter(errand=errand).exists():
        messages.info(request, 'You have already reviewed this errand.')
        return redirect('errand_detail', id=id)

    if request.method == 'POST':
        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.errand = errand
            review.reviewer = request.user
            review.runner = errand.runner
            review.save()
            messages.success(request, 'Thanks for your feedback!')
            return redirect('errand_detail', id=id)
    else:
        form = ReviewForm()

    return render(request, 'errandApp/errands/rate_runner.html', {
        'errand': errand,
        'form': form,
    })


# ---------------------------------------------------------------------------
# Errands (runner)
# ---------------------------------------------------------------------------

@login_required
def errand_detail_runner(request, id):
    errand = get_object_or_404(
        Errand.objects.select_related('customer', 'runner').prefetch_related('images'),
        id=id,
    )

    is_assigned_runner = request.user == errand.runner
    is_admin = request.user.is_staff or request.user.is_superuser

    if errand.status != Errand.STATUS_PENDING and not (is_assigned_runner or is_admin):
        return HttpResponseForbidden('This errand is no longer available.')

    if request.method == 'POST':
        if 'accept_errand' in request.POST:
            if not request.user.is_runner:
                messages.error(request, 'Only registered runners can accept errands.')
                return redirect('errand_detail_runner', id=id)
            if errand.customer_id == request.user.id:
                messages.error(request, 'You cannot accept your own errand.')
                return redirect('errand_detail_runner', id=id)

            with transaction.atomic():
                locked = Errand.objects.select_for_update().get(pk=errand.pk)
                if locked.status != Errand.STATUS_PENDING or locked.runner_id is not None:
                    messages.error(request, 'Sorry, this errand was already accepted.')
                    return redirect('available_errands')

                locked.runner = request.user
                locked.status = Errand.STATUS_ACCEPTED
                locked.accepted_at = timezone.now()
                locked.save()

            messages.success(request, 'Errand accepted. Head to the pickup location.')
            return redirect('runner_dashboard')

        if is_assigned_runner:
            _save_errand_images(request, errand)
            messages.success(request, 'Images uploaded.')

        return redirect('errand_detail_runner', id=id)

    return render(request, 'errandApp/errands/errand_detail_runner.html', {
        'errand': errand,
        'is_assigned_runner': is_assigned_runner,
    })


@login_required
@require_POST
def accept_errand(request, id):
    if not request.user.is_runner:
        messages.error(request, 'Only registered runners can accept errands.')
        return redirect('available_errands')

    with transaction.atomic():
        errand = get_object_or_404(
            Errand.objects.select_for_update(),
            id=id,
            status=Errand.STATUS_PENDING,
            runner__isnull=True,
        )
        if errand.customer_id == request.user.id:
            messages.error(request, 'You cannot accept your own errand.')
            return redirect('available_errands')

        errand.runner = request.user
        errand.status = Errand.STATUS_ACCEPTED
        errand.accepted_at = timezone.now()
        errand.save()

    messages.success(request, 'Errand accepted successfully.')
    return redirect('errand_detail', id=id)


@login_required
@require_POST
def start_pickup(request, id):
    errand = get_object_or_404(
        Errand, id=id, runner=request.user, status=Errand.STATUS_ACCEPTED
    )
    errand.status = Errand.STATUS_PICKUP
    errand.pickup_started_at = timezone.now()
    errand.save()
    messages.success(request, 'Status updated: on the way to pickup.')
    return redirect('errand_detail', id=id)


@login_required
@require_POST
def picked_up(request, id):
    errand = get_object_or_404(
        Errand, id=id, runner=request.user, status=Errand.STATUS_PICKUP
    )
    errand.status = Errand.STATUS_TRANSIT
    errand.picked_up_at = timezone.now()
    errand.save()
    messages.success(request, 'Status updated: items picked up and in transit.')
    return redirect('errand_detail', id=id)


@login_required
@require_POST
def mark_delivered(request, id):
    errand = get_object_or_404(
        Errand, id=id, runner=request.user, status=Errand.STATUS_TRANSIT
    )
    errand.status = Errand.STATUS_DELIVERED
    errand.delivered_at = timezone.now()
    errand.save()
    messages.success(request, 'Status updated: delivered. Awaiting customer confirmation.')
    return redirect('errand_detail', id=id)


@login_required
@require_POST
def confirm_received(request, id):
    with transaction.atomic():
        errand = get_object_or_404(
            Errand.objects.select_for_update(),
            id=id,
            customer=request.user,
            status=Errand.STATUS_DELIVERED,
        )
        errand.status = Errand.STATUS_COMPLETED
        errand.completed_at = timezone.now()
        errand.escrow_funded = False
        errand.save()

        if errand.runner_id:
            wallet, _ = Wallet.objects.get_or_create(user=errand.runner)
            wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
            wallet.credit(
                errand.runner_payout,
                description=f'Payout for errand #{errand.id}: {errand.title}',
                errand=errand,
            )

    messages.success(request, 'Errand completed. Thank you for confirming!')
    return redirect('errand_detail', id=id)


@login_required
@require_POST
def become_runner(request):
    if request.user.is_runner:
        messages.info(request, 'You are already registered as a runner.')
        return redirect('runner_dashboard')

    request.user.is_runner = True
    request.user.save(update_fields=['is_runner'])
    RunnerProfile.objects.get_or_create(user=request.user)
    messages.success(request, 'You are now a runner! Browse available errands to get started.')
    return redirect('runner_dashboard')


@login_required
@runner_required
def runner_dashboard(request):
    available_errands = (
        Errand.objects.filter(status=Errand.STATUS_PENDING, runner__isnull=True)
        .select_related('customer')
        .prefetch_related('images')
        .order_by('-created_at')
    )
    my_errands = (
        Errand.objects.filter(runner=request.user)
        .select_related('customer')
        .order_by('-created_at')
    )
    completed = my_errands.filter(status=Errand.STATUS_COMPLETED)
    reviews = Review.objects.filter(runner=request.user)

    context = {
        'available_errands': available_errands[:6],
        'available_count': available_errands.count(),
        'my_errands': my_errands,
        'active_count': my_errands.filter(
            status__in=ACTIVE_STATUSES + [Errand.STATUS_DELIVERED]
        ).count(),
        'completed_count': completed.count(),
        'total_earnings': sum((errand.runner_payout for errand in completed), Decimal('0.00')),
        'average_rating': reviews.aggregate(avg=Avg('rating'))['avg'] or 0,
        'total_reviews': reviews.count(),
    }
    return render(request, 'errandApp/runners/runner_dashboard.html', context)


@login_required
@runner_required
def available_errands(request):
    errands = (
        Errand.objects.filter(status=Errand.STATUS_PENDING, runner__isnull=True)
        .select_related('customer')
        .prefetch_related('images')
        .order_by('-created_at')
    )

    search = request.GET.get('search', '').strip()
    location = request.GET.get('location', '').strip()
    price_range = request.GET.get('price_range', '').strip()
    sort = request.GET.get('sort', '-created_at').strip()

    if search:
        errands = errands.filter(
            Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(category__icontains=search)
        )
    if location:
        errands = errands.filter(
            Q(pickup_address__icontains=location) | Q(dropoff_address__icontains=location)
        )

    ranges = {
        '0-1000': (0, 1000),
        '1000-5000': (1000, 5000),
        '5000-10000': (5000, 10000),
        '10000+': (10000, None),
    }
    if price_range in ranges:
        low, high = ranges[price_range]
        errands = errands.filter(price__gte=low)
        if high is not None:
            errands = errands.filter(price__lte=high)

    allowed_sorts = {'-created_at', 'created_at', '-price', 'price'}
    errands = errands.order_by(sort if sort in allowed_sorts else '-created_at')

    return render(request, 'errandApp/runners/available_errand.html', {
        'errands': errands,
        'search': search,
        'location': location,
        'price_range': price_range,
        'sort': sort,
    })


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------

@login_required
def wallet(request):
    user_wallet = _wallet_for(request.user)
    transactions = user_wallet.transactions.select_related('errand').all()
    return render(request, 'errandApp/wallet/wallet.html', {
        'wallet': user_wallet,
        'transactions': transactions,
        'deposit_form': DepositForm(),
        'withdraw_form': WithdrawForm(),
    })


@login_required
@require_POST
def wallet_deposit(request):
    form = DepositForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_form_error(form))
        return redirect('wallet')

    amount = form.cleaned_data['amount']
    with transaction.atomic():
        user_wallet = Wallet.objects.select_for_update().get(
            pk=_wallet_for(request.user).pk
        )
        user_wallet.credit(amount, description='Wallet deposit')

    messages.success(request, f'₦{amount:,.2f} was added to your wallet.')
    return redirect('wallet')


@login_required
@require_POST
def wallet_withdraw(request):
    form = WithdrawForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_form_error(form))
        return redirect('wallet')

    amount = form.cleaned_data['amount']
    bank_name = form.cleaned_data['bank_name']
    account_number = form.cleaned_data['account_number']

    with transaction.atomic():
        user_wallet = Wallet.objects.select_for_update().get(
            pk=_wallet_for(request.user).pk
        )
        if amount > user_wallet.balance:
            messages.error(
                request,
                f'You cannot withdraw ₦{amount:,.2f}; your available balance '
                f'is ₦{user_wallet.balance:,.2f}.',
            )
            return redirect('wallet')

        user_wallet.debit(
            amount,
            description=f'Withdrawal to {bank_name} ••••{account_number[-4:]}',
        )

    messages.success(
        request,
        f'₦{amount:,.2f} withdrawal to {bank_name} was processed.',
    )
    return redirect('wallet')


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------

@admin_required
def admin_dashboard(request):
    errands = Errand.objects.all()
    completed = errands.filter(status=Errand.STATUS_COMPLETED)
    revenue = completed.aggregate(total=Sum('price'))['total'] or Decimal('0.00')
    commission = sum((errand.commission for errand in completed), Decimal('0.00'))

    context = {
        'total_users': User.objects.count(),
        'total_runners': RunnerProfile.objects.count(),
        'total_errands': errands.count(),
        'pending_errands': errands.filter(status=Errand.STATUS_PENDING).count(),
        'completed_errands': completed.count(),
        'total_transactions': Transaction.objects.count(),
        'gross_revenue': revenue,
        'platform_commission': commission,
        'recent_errands': errands.select_related('customer', 'runner')[:8],
        'recent_transactions': Transaction.objects.select_related('wallet__user')[:8],
    }
    return render(request, 'errandApp/admins/dashboard.html', context)


@admin_required
def admin_users(request):
    users = User.objects.order_by('-date_joined')
    search = request.GET.get('search', '').strip()
    role = request.GET.get('role', '').strip()

    if search:
        users = users.filter(
            Q(username__icontains=search) | Q(email__icontains=search)
        )
    if role == 'runner':
        users = users.filter(is_runner=True)
    elif role == 'customer':
        users = users.filter(is_runner=False)
    elif role == 'staff':
        users = users.filter(is_staff=True)

    return render(request, 'errandApp/admins/users.html', {
        'users': users,
        'search': search,
        'role': role,
    })


@admin_required
def admin_user_detail(request, user_id):
    user_obj = get_object_or_404(User, id=user_id)
    wallet = getattr(user_obj, 'wallet', None)
    runner_profile = getattr(user_obj, 'runner_profile', None)
    errands = user_obj.errands.all()

    if request.method == 'POST':
        if user_obj == request.user and 'is_active' not in request.POST:
            messages.error(request, 'You cannot deactivate your own account.')
            return redirect('admin_user_detail', user_id=user_obj.id)

        user_obj.is_runner = 'is_runner' in request.POST
        user_obj.is_active = 'is_active' in request.POST
        user_obj.email = request.POST.get('email', user_obj.email).strip()
        user_obj.save()

        if user_obj.is_runner:
            RunnerProfile.objects.get_or_create(user=user_obj)

        messages.success(request, 'User updated successfully.')
        return redirect('admin_user_detail', user_id=user_obj.id)

    context = {
        'user_obj': user_obj,
        'wallet': wallet,
        'runner_profile': runner_profile,
        'errands': errands,
        'errands_success': errands.filter(
            status__in=[Errand.STATUS_DELIVERED, Errand.STATUS_COMPLETED]
        ).count(),
        'active_errands': errands.filter(
            status__in=[Errand.STATUS_PENDING] + ACTIVE_STATUSES
        ).count(),
    }
    return render(request, 'errandApp/admins/user_detail.html', context)


@admin_required
def admin_runners(request):
    runners = RunnerProfile.objects.select_related('user').all()
    search = request.GET.get('search', '').strip()
    verified = request.GET.get('verified', '').strip()

    if search:
        runners = runners.filter(
            Q(user__username__icontains=search) | Q(user__email__icontains=search)
        )
    if verified == 'yes':
        runners = runners.filter(verified=True)
    elif verified == 'no':
        runners = runners.filter(verified=False)

    return render(request, 'errandApp/admins/runners.html', {
        'runners': runners,
        'search': search,
        'verified': verified,
    })


@admin_required
def admin_errands(request):
    errands = Errand.objects.select_related('customer', 'runner').all()
    status = request.GET.get('status', '').strip()
    search = request.GET.get('search', '').strip()

    if status in dict(Errand.STATUS_CHOICES):
        errands = errands.filter(status=status)
    if search:
        errands = errands.filter(
            Q(title__icontains=search)
            | Q(pickup_address__icontains=search)
            | Q(dropoff_address__icontains=search)
            | Q(customer__username__icontains=search)
            | Q(runner__username__icontains=search)
        )

    return render(request, 'errandApp/admins/errands.html', {
        'errands': errands,
        'status_choices': Errand.STATUS_CHOICES,
        'active_status': status,
        'search': search,
    })


@admin_required
def admin_transactions(request):
    transactions = Transaction.objects.select_related('wallet__user', 'errand').all()
    tx_type = request.GET.get('type', '').strip()
    search = request.GET.get('search', '').strip()

    if tx_type in dict(Transaction.TYPE_CHOICES):
        transactions = transactions.filter(type=tx_type)
    if search:
        transactions = transactions.filter(
            Q(wallet__user__username__icontains=search)
            | Q(description__icontains=search)
        )

    return render(request, 'errandApp/admins/transactions.html', {
        'transactions': transactions,
        'type_choices': Transaction.TYPE_CHOICES,
        'active_type': tx_type,
        'search': search,
    })


@admin_required
def admin_disputes(request):
    # Disputes are not modelled yet; the page shows an empty state.
    return render(request, 'errandApp/admins/disputes.html', {'disputes': []})
