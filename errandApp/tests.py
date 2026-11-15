import base64
import shutil
import tempfile
from decimal import Decimal

from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import ErrandForm
from .models import Errand, ErrandImage, Review, RunnerProfile, Transaction, Wallet

# 1x1 transparent GIF, used for valid image upload tests.
GIF_BYTES = base64.b64decode(
    'R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=='
)


def make_errand(customer, **overrides):
    data = {
        'customer': customer,
        'title': 'Pick up a parcel',
        'description': 'Collect a small parcel from the courier office.',
        'pickup_address': '12 Marina Road',
        'dropoff_address': '8 Broad Street',
        'category': 'pickup',
        'urgency': 'medium',
        'price': Decimal('2000.00'),
    }
    data.update(overrides)
    return Errand.objects.create(**data)


def fund_wallet(user, amount='10000.00'):
    """Credit a user's wallet so they can fund escrowed errands in tests."""
    wallet, _ = Wallet.objects.get_or_create(user=user)
    wallet.credit(Decimal(amount), description='Test deposit')
    return wallet


class ModelTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.customer = User.objects.create_user('customer', password='Str0ngPass!23')
        self.runner = User.objects.create_user(
            'runner', password='Str0ngPass!23', is_runner=True
        )

    def test_wallet_created_for_every_user(self):
        self.assertTrue(Wallet.objects.filter(user=self.customer).exists())
        self.assertTrue(Wallet.objects.filter(user=self.runner).exists())

    def test_runner_profile_created_for_runners_only(self):
        self.assertTrue(RunnerProfile.objects.filter(user=self.runner).exists())
        self.assertFalse(RunnerProfile.objects.filter(user=self.customer).exists())

    def test_commission_and_payout(self):
        errand = make_errand(self.customer, price=Decimal('2000.00'))
        self.assertEqual(errand.commission, Decimal('300.00'))
        self.assertEqual(errand.runner_payout, Decimal('1700.00'))

    def test_commission_rounds_to_two_places(self):
        errand = make_errand(self.customer, price=Decimal('1999.99'))
        self.assertEqual(errand.commission, Decimal('300.00'))
        self.assertEqual(errand.commission + errand.runner_payout, Decimal('1999.99'))

    def test_status_css_classes(self):
        errand = make_errand(self.customer)
        self.assertEqual(errand.status_css_class, 'pending')
        errand.status = Errand.STATUS_TRANSIT
        self.assertEqual(errand.status_css_class, 'in-progress')
        errand.status = Errand.STATUS_COMPLETED
        self.assertEqual(errand.status_css_class, 'completed')
        errand.status = Errand.STATUS_CANCELLED
        self.assertEqual(errand.status_css_class, 'cancelled')

    def test_cancellable_only_while_pending(self):
        errand = make_errand(self.customer)
        self.assertTrue(errand.can_be_cancelled)
        errand.status = Errand.STATUS_ACCEPTED
        self.assertFalse(errand.can_be_cancelled)

    def test_wallet_credit_and_debit_create_transactions(self):
        wallet = self.runner.wallet
        credit = wallet.credit(Decimal('500.00'), description='Test credit')
        self.assertEqual(wallet.balance, Decimal('500.00'))
        self.assertEqual(credit.balance_after, Decimal('500.00'))
        self.assertEqual(credit.type, Transaction.TYPE_CREDIT)

        wallet.debit(Decimal('200.00'), description='Test debit')
        self.assertEqual(wallet.balance, Decimal('300.00'))
        self.assertEqual(wallet.transactions.count(), 2)

    def test_wallet_rejects_invalid_amounts(self):
        wallet = self.runner.wallet
        with self.assertRaises(ValueError):
            wallet.credit(Decimal('0'))
        with self.assertRaises(ValueError):
            wallet.debit(Decimal('10.00'))

    def test_review_is_one_per_errand(self):
        errand = make_errand(
            self.customer, runner=self.runner, status=Errand.STATUS_COMPLETED
        )
        Review.objects.create(
            errand=errand, reviewer=self.customer, runner=self.runner, rating=5
        )
        with self.assertRaises(Exception):
            Review.objects.create(
                errand=errand, reviewer=self.customer, runner=self.runner, rating=4
            )

    def test_completed_errand_gets_completion_timestamp(self):
        errand = make_errand(self.customer)
        errand.status = Errand.STATUS_COMPLETED
        errand.save()
        errand.refresh_from_db()
        self.assertIsNotNone(errand.completed_at)


class FormTests(TestCase):
    def test_minimum_price(self):
        form = ErrandForm(data={
            'title': 'Cheap errand',
            'description': 'Too cheap',
            'pickup_address': 'A',
            'dropoff_address': 'B',
            'category': 'other',
            'urgency': 'low',
            'price': '50',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('price', form.errors)

    def test_valid_errand_form(self):
        form = ErrandForm(data={
            'title': 'Valid errand',
            'description': 'Something useful',
            'pickup_address': 'A',
            'dropoff_address': 'B',
            'category': 'delivery',
            'urgency': 'high',
            'price': '500',
        })
        self.assertTrue(form.is_valid(), form.errors)


class AuthTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            'existing', password='Str0ngPass!23', email='existing@example.com'
        )

    def test_home_is_public(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)

    def test_register_creates_account_and_logs_in(self):
        response = self.client.post(reverse('register'), {
            'first_name': 'Ada',
            'last_name': 'Okafor',
            'username': 'ada',
            'email': 'ada@example.com',
            'password1': 'Str0ngPass!23',
            'password2': 'Str0ngPass!23',
        })
        self.assertRedirects(response, reverse('dashboard'))
        user = self.User.objects.get(username='ada')
        self.assertTrue(Wallet.objects.filter(user=user).exists())
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_register_as_runner_redirects_to_runner_dashboard(self):
        response = self.client.post(reverse('register'), {
            'first_name': 'Ada',
            'last_name': 'Okafor',
            'username': 'adarunner',
            'email': 'adarunner@example.com',
            'password1': 'Str0ngPass!23',
            'password2': 'Str0ngPass!23',
            'is_runner': 'on',
        })
        self.assertRedirects(response, reverse('runner_dashboard'))
        user = self.User.objects.get(username='adarunner')
        self.assertTrue(RunnerProfile.objects.filter(user=user).exists())

    def test_register_rejects_mismatched_passwords(self):
        response = self.client.post(reverse('register'), {
            'first_name': 'Ada',
            'last_name': 'Okafor',
            'username': 'ada',
            'email': 'ada@example.com',
            'password1': 'Str0ngPass!23',
            'password2': 'Different!23',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Passwords do not match')
        self.assertFalse(self.User.objects.filter(username='ada').exists())

    def test_register_rejects_duplicate_username_or_email(self):
        response = self.client.post(reverse('register'), {
            'first_name': 'Other',
            'last_name': 'Person',
            'username': 'existing',
            'email': 'new@example.com',
            'password1': 'Str0ngPass!23',
            'password2': 'Str0ngPass!23',
        })
        self.assertContains(response, 'already registered')

        response = self.client.post(reverse('register'), {
            'first_name': 'Other',
            'last_name': 'Person',
            'username': 'brandnew',
            'email': 'existing@example.com',
            'password1': 'Str0ngPass!23',
            'password2': 'Str0ngPass!23',
        })
        self.assertContains(response, 'already registered')

    def test_register_rejects_weak_password(self):
        response = self.client.post(reverse('register'), {
            'first_name': 'Weak',
            'last_name': 'Person',
            'username': 'weakpass',
            'email': 'weak@example.com',
            'password1': '123',
            'password2': '123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.User.objects.filter(username='weakpass').exists())

    def test_login_and_logout(self):
        response = self.client.post(reverse('login'), {
            'username': 'existing',
            'password': 'Str0ngPass!23',
        })
        self.assertRedirects(response, reverse('dashboard'))

        # Logout is POST-only to avoid CSRF-style logout links.
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('home'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_honours_safe_next(self):
        response = self.client.post(
            reverse('login') + '?next=' + reverse('wallet'),
            {'username': 'existing', 'password': 'Str0ngPass!23'},
        )
        self.assertRedirects(response, reverse('wallet'))

    def test_login_rejects_external_next(self):
        response = self.client.post(
            reverse('login') + '?next=https://evil.example.com/',
            {'username': 'existing', 'password': 'Str0ngPass!23'},
        )
        self.assertRedirects(response, reverse('dashboard'))

    def test_login_rejects_bad_password(self):
        response = self.client.post(reverse('login'), {
            'username': 'existing',
            'password': 'wrong',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Invalid username or password')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_sends_email(self):
        response = self.client.post(reverse('password_reset'), {
            'email': 'existing@example.com',
        })
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('password', mail.outbox[0].subject.lower())


class AccessControlTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.customer = User.objects.create_user('customer', password='Str0ngPass!23')
        self.other = User.objects.create_user('other', password='Str0ngPass!23')
        self.runner = User.objects.create_user(
            'runner', password='Str0ngPass!23', is_runner=True
        )
        self.staff = User.objects.create_user(
            'staff', password='Str0ngPass!23', is_staff=True
        )
        self.errand = make_errand(self.customer)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(
            response, reverse('login') + '?next=' + reverse('dashboard')
        )

    def test_errand_detail_is_private(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse('errand_detail', args=[self.errand.pk]))
        self.assertEqual(response.status_code, 403)

    def test_staff_can_view_any_errand(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('errand_detail', args=[self.errand.pk]))
        self.assertEqual(response.status_code, 200)

    def test_runner_pages_require_runner_account(self):
        self.client.force_login(self.customer)
        for name in ('runner_dashboard', 'available_errands'):
            response = self.client.get(reverse(name))
            self.assertRedirects(response, reverse('dashboard'))

    def test_admin_pages_require_staff(self):
        self.client.force_login(self.customer)
        for name in (
            'admin_dashboard',
            'admin_users',
            'admin_runners',
            'admin_errands',
            'admin_transactions',
            'admin_disputes',
        ):
            response = self.client.get(reverse(name))
            self.assertRedirects(response, reverse('dashboard'))

    def test_admin_pages_available_to_staff(self):
        self.client.force_login(self.staff)
        for name in (
            'admin_dashboard',
            'admin_users',
            'admin_runners',
            'admin_errands',
            'admin_transactions',
            'admin_disputes',
        ):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)

    def test_admin_pages_redirect_anonymous_to_login(self):
        response = self.client.get(reverse('admin_dashboard'))
        self.assertRedirects(response, reverse('login'))


class ErrandWorkflowTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.customer = User.objects.create_user('customer', password='Str0ngPass!23')
        self.runner = User.objects.create_user(
            'runner', password='Str0ngPass!23', is_runner=True
        )
        self.other_runner = User.objects.create_user(
            'otherrunner', password='Str0ngPass!23', is_runner=True
        )
        self.errand = make_errand(self.customer, price=Decimal('2000.00'))

    def test_customer_can_create_errand(self):
        fund_wallet(self.customer, '5000.00')
        self.client.force_login(self.customer)
        response = self.client.post(reverse('create_errand'), {
            'title': 'Buy groceries',
            'description': 'Milk, eggs and bread',
            'pickup_address': 'Market',
            'dropoff_address': 'Home',
            'category': 'shopping',
            'urgency': 'high',
            'price': '1500',
        })
        self.assertRedirects(response, reverse('my_errands'))
        errand = Errand.objects.get(title='Buy groceries')
        self.assertEqual(errand.customer, self.customer)
        self.assertEqual(errand.status, Errand.STATUS_PENDING)
        self.assertTrue(errand.escrow_funded)

        # The price is moved out of the wallet into escrow.
        wallet = Wallet.objects.get(user=self.customer)
        self.assertEqual(wallet.balance, Decimal('3500.00'))
        self.assertEqual(
            wallet.transactions.filter(errand=errand, type=Transaction.TYPE_DEBIT).count(),
            1,
        )

    def test_cannot_create_errand_without_wallet_funds(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('create_errand'), {
            'title': 'Too expensive',
            'description': 'No money for this',
            'pickup_address': 'Market',
            'dropoff_address': 'Home',
            'category': 'shopping',
            'urgency': 'high',
            'price': '5000',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Insufficient wallet balance')
        self.assertFalse(Errand.objects.filter(title='Too expensive').exists())
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, Decimal('0.00'))

    def test_customer_can_edit_pending_errand_only(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('edit_errand', args=[self.errand.pk]), {
            'title': 'Updated title',
            'description': self.errand.description,
            'pickup_address': self.errand.pickup_address,
            'dropoff_address': self.errand.dropoff_address,
            'category': self.errand.category,
            'urgency': self.errand.urgency,
            'price': '2500',
        })
        self.assertRedirects(response, reverse('errand_detail', args=[self.errand.pk]))
        self.errand.refresh_from_db()
        self.assertEqual(self.errand.title, 'Updated title')
        self.assertEqual(self.errand.price, Decimal('2500.00'))

        self.errand.status = Errand.STATUS_ACCEPTED
        self.errand.save()
        response = self.client.get(reverse('edit_errand', args=[self.errand.pk]))
        self.assertRedirects(response, reverse('errand_detail', args=[self.errand.pk]))

    def test_edit_price_increase_charges_difference(self):
        wallet = fund_wallet(self.customer, '5000.00')
        self.errand.price = Decimal('2000.00')
        self.errand.escrow_funded = True
        self.errand.save()
        wallet.debit(Decimal('2000.00'), description='Escrow hold', errand=self.errand)

        self.client.force_login(self.customer)
        response = self.client.post(reverse('edit_errand', args=[self.errand.pk]), {
            'title': self.errand.title,
            'description': self.errand.description,
            'pickup_address': self.errand.pickup_address,
            'dropoff_address': self.errand.dropoff_address,
            'category': self.errand.category,
            'urgency': self.errand.urgency,
            'price': '2500',
        })
        self.assertRedirects(response, reverse('errand_detail', args=[self.errand.pk]))
        self.assertEqual(
            Wallet.objects.get(user=self.customer).balance, Decimal('2500.00')
        )

    def test_edit_price_decrease_refunds_difference(self):
        wallet = fund_wallet(self.customer, '5000.00')
        self.errand.price = Decimal('2000.00')
        self.errand.escrow_funded = True
        self.errand.save()
        wallet.debit(Decimal('2000.00'), description='Escrow hold', errand=self.errand)

        self.client.force_login(self.customer)
        response = self.client.post(reverse('edit_errand', args=[self.errand.pk]), {
            'title': self.errand.title,
            'description': self.errand.description,
            'pickup_address': self.errand.pickup_address,
            'dropoff_address': self.errand.dropoff_address,
            'category': self.errand.category,
            'urgency': self.errand.urgency,
            'price': '1500',
        })
        self.assertRedirects(response, reverse('errand_detail', args=[self.errand.pk]))
        self.assertEqual(
            Wallet.objects.get(user=self.customer).balance, Decimal('3500.00')
        )

    def test_edit_price_increase_without_funds_is_rejected(self):
        wallet = fund_wallet(self.customer, '2000.00')
        self.errand.price = Decimal('2000.00')
        self.errand.escrow_funded = True
        self.errand.save()
        wallet.debit(Decimal('2000.00'), description='Escrow hold', errand=self.errand)

        self.client.force_login(self.customer)
        response = self.client.post(reverse('edit_errand', args=[self.errand.pk]), {
            'title': self.errand.title,
            'description': self.errand.description,
            'pickup_address': self.errand.pickup_address,
            'dropoff_address': self.errand.dropoff_address,
            'category': self.errand.category,
            'urgency': self.errand.urgency,
            'price': '5000',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'You need')
        self.errand.refresh_from_db()
        self.assertEqual(self.errand.price, Decimal('2000.00'))
        self.assertEqual(
            Wallet.objects.get(user=self.customer).balance, Decimal('0.00')
        )

    def test_non_owner_cannot_edit(self):
        self.client.force_login(self.runner)
        response = self.client.get(reverse('edit_errand', args=[self.errand.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cancel_requires_post(self):
        self.client.force_login(self.customer)
        self.assertEqual(
            self.client.get(reverse('cancel_errand', args=[self.errand.pk])).status_code,
            405,
        )

    def test_customer_can_cancel_pending_errand(self):
        wallet = fund_wallet(self.customer, '5000.00')
        self.errand.escrow_funded = True
        self.errand.save(update_fields=['escrow_funded'])
        wallet.debit(self.errand.price, description='Escrow hold', errand=self.errand)
        self.assertEqual(Wallet.objects.get(pk=wallet.pk).balance, Decimal('3000.00'))

        self.client.force_login(self.customer)
        response = self.client.post(reverse('cancel_errand', args=[self.errand.pk]))
        self.assertRedirects(response, reverse('my_errands'))
        self.errand.refresh_from_db()
        self.assertEqual(self.errand.status, Errand.STATUS_CANCELLED)
        self.assertIsNotNone(self.errand.cancelled_at)
        self.assertFalse(self.errand.escrow_funded)
        self.assertEqual(
            Wallet.objects.get(user=self.customer).balance, Decimal('5000.00')
        )

    def test_cannot_cancel_after_acceptance(self):
        self.errand.status = Errand.STATUS_ACCEPTED
        self.errand.runner = self.runner
        self.errand.save()
        self.client.force_login(self.customer)
        response = self.client.post(reverse('cancel_errand', args=[self.errand.pk]))
        self.assertRedirects(response, reverse('errand_detail', args=[self.errand.pk]))
        self.errand.refresh_from_db()
        self.assertEqual(self.errand.status, Errand.STATUS_ACCEPTED)

    def test_non_runner_cannot_accept(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('accept_errand', args=[self.errand.pk]))
        self.assertRedirects(
            response, reverse('available_errands'), fetch_redirect_response=False
        )
        self.errand.refresh_from_db()
        self.assertIsNone(self.errand.runner)

    def test_runner_cannot_accept_own_errand(self):
        customer_runner = self.customer
        customer_runner.is_runner = True
        customer_runner.save()
        self.client.force_login(customer_runner)
        response = self.client.post(reverse('accept_errand', args=[self.errand.pk]))
        self.assertRedirects(response, reverse('available_errands'))
        self.errand.refresh_from_db()
        self.assertIsNone(self.errand.runner)
        self.assertEqual(self.errand.status, Errand.STATUS_PENDING)

    def test_second_runner_cannot_accept_taken_errand(self):
        self.client.force_login(self.runner)
        self.client.post(reverse('accept_errand', args=[self.errand.pk]))

        self.client.force_login(self.other_runner)
        response = self.client.post(reverse('accept_errand', args=[self.errand.pk]))
        self.assertEqual(response.status_code, 404)
        self.errand.refresh_from_db()
        self.assertEqual(self.errand.runner, self.runner)

    def test_full_lifecycle_pays_runner_and_allows_one_review(self):
        self.client.force_login(self.runner)
        self.client.post(reverse('accept_errand', args=[self.errand.pk]))
        self.errand.refresh_from_db()
        self.assertEqual(self.errand.status, Errand.STATUS_ACCEPTED)
        self.assertEqual(self.errand.runner, self.runner)
        self.assertIsNotNone(self.errand.accepted_at)

        self.client.post(reverse('start_pickup', args=[self.errand.pk]))
        self.errand.refresh_from_db()
        self.assertEqual(self.errand.status, Errand.STATUS_PICKUP)
        self.assertIsNotNone(self.errand.pickup_started_at)

        self.client.post(reverse('picked_up', args=[self.errand.pk]))
        self.errand.refresh_from_db()
        self.assertEqual(self.errand.status, Errand.STATUS_TRANSIT)
        self.assertIsNotNone(self.errand.picked_up_at)

        self.client.post(reverse('mark_delivered', args=[self.errand.pk]))
        self.errand.refresh_from_db()
        self.assertEqual(self.errand.status, Errand.STATUS_DELIVERED)
        self.assertIsNotNone(self.errand.delivered_at)

        self.client.force_login(self.customer)
        self.client.post(reverse('confirm_received', args=[self.errand.pk]))
        self.errand.refresh_from_db()
        self.assertEqual(self.errand.status, Errand.STATUS_COMPLETED)
        self.assertIsNotNone(self.errand.completed_at)

        wallet = Wallet.objects.get(user=self.runner)
        self.assertEqual(wallet.balance, Decimal('1700.00'))
        transaction = wallet.transactions.get()
        self.assertEqual(transaction.amount, Decimal('1700.00'))
        self.assertEqual(transaction.errand, self.errand)
        self.assertEqual(transaction.type, Transaction.TYPE_CREDIT)

        # A second confirmation must not pay twice.
        self.client.post(reverse('confirm_received', args=[self.errand.pk]))
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal('1700.00'))
        self.assertEqual(wallet.transactions.count(), 1)

        response = self.client.post(reverse('rate_runner', args=[self.errand.pk]), {
            'rating': '5',
            'comment': 'Fast and friendly',
        })
        self.assertRedirects(response, reverse('errand_detail', args=[self.errand.pk]))
        self.assertEqual(Review.objects.filter(errand=self.errand).count(), 1)

        # Reviewing again is a no-op.
        self.client.post(reverse('rate_runner', args=[self.errand.pk]), {
            'rating': '3',
            'comment': 'Changed my mind',
        })
        self.assertEqual(Review.objects.filter(errand=self.errand).count(), 1)

    def test_runner_cannot_skip_states(self):
        self.client.force_login(self.runner)
        self.client.post(reverse('accept_errand', args=[self.errand.pk]))
        # Cannot mark delivered while only accepted.
        response = self.client.post(reverse('mark_delivered', args=[self.errand.pk]))
        self.assertEqual(response.status_code, 404)
        self.errand.refresh_from_db()
        self.assertEqual(self.errand.status, Errand.STATUS_ACCEPTED)

    def test_other_runner_cannot_advance_errand(self):
        self.errand.status = Errand.STATUS_ACCEPTED
        self.errand.runner = self.runner
        self.errand.save()
        self.client.force_login(self.other_runner)
        response = self.client.post(reverse('start_pickup', args=[self.errand.pk]))
        self.assertEqual(response.status_code, 404)

    def test_customer_cannot_confirm_before_delivery(self):
        self.errand.status = Errand.STATUS_TRANSIT
        self.errand.runner = self.runner
        self.errand.save()
        self.client.force_login(self.customer)
        response = self.client.post(reverse('confirm_received', args=[self.errand.pk]))
        self.assertEqual(response.status_code, 404)

    def test_review_only_allowed_after_completion(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('rate_runner', args=[self.errand.pk]))
        self.assertEqual(response.status_code, 404)

    def test_become_runner(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('become_runner'))
        self.assertRedirects(response, reverse('runner_dashboard'))
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_runner)
        self.assertTrue(RunnerProfile.objects.filter(user=self.customer).exists())


class FilteringTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.customer = User.objects.create_user('customer', password='Str0ngPass!23')
        self.runner = User.objects.create_user(
            'runner', password='Str0ngPass!23', is_runner=True
        )
        self.cheap = make_errand(
            self.customer, title='Cheap delivery', price=Decimal('500.00')
        )
        self.expensive = make_errand(
            self.customer, title='Expensive shopping', price=Decimal('9000.00')
        )
        self.taken = make_errand(
            self.customer, title='Taken errand', status=Errand.STATUS_ACCEPTED,
            runner=self.runner,
        )

    def test_my_errands_status_filter(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('my_errands'), {'status': 'accepted'})
        self.assertContains(response, 'Taken errand')
        self.assertNotContains(response, 'Cheap delivery')

    def test_my_errands_search(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('my_errands'), {'search': 'shopping'})
        self.assertContains(response, 'Expensive shopping')
        self.assertNotContains(response, 'Cheap delivery')

    def test_available_errands_excludes_taken(self):
        self.client.force_login(self.runner)
        response = self.client.get(reverse('available_errands'))
        self.assertContains(response, 'Cheap delivery')
        self.assertNotContains(response, 'Taken errand')

    def test_available_errands_price_filter_and_sort(self):
        self.client.force_login(self.runner)
        response = self.client.get(
            reverse('available_errands'), {'price_range': '0-1000', 'sort': 'price'}
        )
        self.assertContains(response, 'Cheap delivery')
        self.assertNotContains(response, 'Expensive shopping')

        response = self.client.get(
            reverse('available_errands'), {'sort': 'bogus; DROP TABLE'}
        )
        self.assertEqual(response.status_code, 200)


class ImageUploadTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media_dir = tempfile.mkdtemp(prefix='swift-errand-media-')
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.customer = User.objects.create_user('customer', password='Str0ngPass!23')
        self.runner = User.objects.create_user(
            'runner', password='Str0ngPass!23', is_runner=True
        )
        fund_wallet(self.customer)

    def _upload(self, name='photo.gif', content=None, content_type='image/gif'):
        return SimpleUploadedFile(name, content or GIF_BYTES, content_type=content_type)

    def test_create_errand_with_image(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('create_errand'), {
            'title': 'Photo errand',
            'description': 'With a photo',
            'pickup_address': 'A',
            'dropoff_address': 'B',
            'category': 'delivery',
            'urgency': 'low',
            'price': '800',
            'images': self._upload(),
        })
        self.assertRedirects(response, reverse('my_errands'))
        errand = Errand.objects.get(title='Photo errand')
        self.assertEqual(errand.images.count(), 1)
        self.assertTrue(errand.images.first().file_exists)

    def test_missing_image_reports_not_exists(self):
        errand = make_errand(self.customer)
        image = ErrandImage(errand=errand, image='errand_images/does-not-exist.gif')
        self.assertFalse(image.file_exists)

    def test_customer_detail_shows_task_photos(self):
        errand = make_errand(self.customer)
        image = ErrandImage.objects.create(
            errand=errand, image=self._upload('detail.gif')
        )
        self.client.force_login(self.customer)
        response = self.client.get(reverse('errand_detail', args=[errand.pk]))
        self.assertContains(response, 'Task Photos')
        self.assertContains(response, image.image.url)

    def test_runner_available_list_shows_photo(self):
        errand = make_errand(self.customer)
        image = ErrandImage.objects.create(
            errand=errand, image=self._upload('cover.gif')
        )
        self.client.force_login(self.runner)
        response = self.client.get(reverse('available_errands'))
        self.assertContains(response, image.image.url)
        self.assertContains(response, 'errand-cover')

    def test_runner_dashboard_shows_photo(self):
        errand = make_errand(self.customer)
        image = ErrandImage.objects.create(
            errand=errand, image=self._upload('dash.gif')
        )
        self.client.force_login(self.runner)
        response = self.client.get(reverse('runner_dashboard'))
        self.assertContains(response, image.image.url)

    def test_missing_image_file_is_not_rendered(self):
        errand = make_errand(self.customer)
        ErrandImage.objects.create(
            errand=errand, image='errand_images/does-not-exist.gif'
        )
        self.client.force_login(self.customer)
        response = self.client.get(reverse('errand_detail', args=[errand.pk]))
        self.assertNotContains(response, 'Task Photos')

    def test_non_image_upload_is_skipped(self):
        self.client.force_login(self.customer)
        self.client.post(reverse('create_errand'), {
            'title': 'Bad file errand',
            'description': 'With a text file',
            'pickup_address': 'A',
            'dropoff_address': 'B',
            'category': 'delivery',
            'urgency': 'low',
            'price': '800',
            'images': self._upload('notes.txt', b'hello', 'text/plain'),
        })
        errand = Errand.objects.get(title='Bad file errand')
        self.assertEqual(errand.images.count(), 0)

    @override_settings(MAX_UPLOAD_SIZE=10)
    def test_oversized_upload_is_skipped(self):
        self.client.force_login(self.customer)
        self.client.post(reverse('create_errand'), {
            'title': 'Big image errand',
            'description': 'With a big file',
            'pickup_address': 'A',
            'dropoff_address': 'B',
            'category': 'delivery',
            'urgency': 'low',
            'price': '800',
            'images': self._upload(),
        })
        errand = Errand.objects.get(title='Big image errand')
        self.assertEqual(errand.images.count(), 0)

    @override_settings(MAX_IMAGES_PER_ERRAND=1)
    def test_image_limit_is_enforced(self):
        errand = make_errand(self.customer)
        self.client.force_login(self.customer)
        self.client.post(reverse('edit_errand', args=[errand.pk]), {
            'title': errand.title,
            'description': errand.description,
            'pickup_address': errand.pickup_address,
            'dropoff_address': errand.dropoff_address,
            'category': errand.category,
            'urgency': errand.urgency,
            'price': '2000',
            'images': [self._upload('one.gif'), self._upload('two.gif')],
        })
        self.assertEqual(errand.images.count(), 1)


class SettingsTests(TestCase):
    def test_media_is_not_project_root(self):
        self.assertEqual(settings.MEDIA_URL, '/media/')
        self.assertEqual(
            str(settings.MEDIA_ROOT).replace('\\', '/').rstrip('/'),
            str(settings.BASE_DIR).replace('\\', '/').rstrip('/') + '/media',
        )

    def test_login_redirects_configured(self):
        self.assertEqual(settings.LOGIN_URL, '/login/')
        self.assertEqual(settings.LOGIN_REDIRECT_URL, '/dashboard/')

    def test_commission_setting_matches_model(self):
        self.assertEqual(
            Decimal(str(settings.DEFAULT_COMMISSION_RATE)), Errand.COMMISSION_RATE
        )


class AccountSettingsTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_user(
            'settingsuser', password='Str0ngPass!23', email='old@example.com'
        )

    def test_profile_update(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('account_settings'), {
            'first_name': 'Grace',
            'last_name': 'Eze',
            'email': 'grace@example.com',
            'phone': '08012345678',
        })
        self.assertRedirects(response, reverse('account_settings'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Grace')
        self.assertEqual(self.user.email, 'grace@example.com')
        self.assertEqual(self.user.phone, '08012345678')

    def test_password_change(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('account_settings'), {
            'action': 'password',
            'old_password': 'Str0ngPass!23',
            'new_password1': 'Even0Stronger!45',
            'new_password2': 'Even0Stronger!45',
        })
        self.assertRedirects(response, reverse('account_settings'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Even0Stronger!45'))

    def test_wallet_page_lists_transactions(self):
        self.user.wallet.credit(Decimal('250.00'), description='Deposit')
        self.client.force_login(self.user)
        response = self.client.get(reverse('wallet'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Deposit')
        self.assertContains(response, '250.00')


class WalletViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_user('walletuser', password='Str0ngPass!23')
        self.runner = User.objects.create_user(
            'walletrunner', password='Str0ngPass!23', is_runner=True
        )

    def test_wallet_page_has_deposit_and_withdraw_buttons(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('wallet'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('wallet_deposit'))
        self.assertContains(response, reverse('wallet_withdraw'))
        self.assertContains(response, 'Deposit funds')
        self.assertContains(response, 'Withdraw funds')

    def test_deposit_credits_wallet(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('wallet_deposit'), {'amount': '2500'})
        self.assertRedirects(response, reverse('wallet'))
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, Decimal('2500.00'))
        transaction = wallet.transactions.get()
        self.assertEqual(transaction.type, Transaction.TYPE_CREDIT)
        self.assertEqual(transaction.amount, Decimal('2500.00'))

    def test_deposit_below_minimum_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('wallet_deposit'), {'amount': '50'})
        self.assertRedirects(response, reverse('wallet'))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal('0.00'))

    def test_withdraw_debits_wallet(self):
        fund_wallet(self.user, '3000.00')
        self.client.force_login(self.user)
        response = self.client.post(reverse('wallet_withdraw'), {
            'amount': '1200',
            'bank_name': 'GTBank',
            'account_name': 'Jane Doe',
            'account_number': '0123456789',
        })
        self.assertRedirects(response, reverse('wallet'))
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, Decimal('1800.00'))
        transaction = wallet.transactions.filter(type=Transaction.TYPE_DEBIT).get()
        self.assertIn('GTBank', transaction.description)
        self.assertIn('6789', transaction.description)

    def test_cannot_withdraw_more_than_balance(self):
        fund_wallet(self.user, '1000.00')
        self.client.force_login(self.user)
        response = self.client.post(reverse('wallet_withdraw'), {
            'amount': '2500',
            'bank_name': 'GTBank',
            'account_name': 'Jane Doe',
            'account_number': '0123456789',
        })
        self.assertRedirects(response, reverse('wallet'))
        self.assertEqual(
            Wallet.objects.get(user=self.user).balance, Decimal('1000.00')
        )
        self.assertFalse(
            Wallet.objects.get(user=self.user)
            .transactions.filter(type=Transaction.TYPE_DEBIT)
            .exists()
        )

    def test_withdraw_requires_valid_account_number(self):
        fund_wallet(self.user, '1000.00')
        self.client.force_login(self.user)
        self.client.post(reverse('wallet_withdraw'), {
            'amount': '500',
            'bank_name': 'GTBank',
            'account_name': 'Jane Doe',
            'account_number': '123',
        })
        self.assertEqual(
            Wallet.objects.get(user=self.user).balance, Decimal('1000.00')
        )

    def test_wallet_actions_require_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('wallet_deposit')).status_code, 405)
        self.assertEqual(self.client.get(reverse('wallet_withdraw')).status_code, 405)

    def test_runner_can_deposit_and_withdraw(self):
        self.client.force_login(self.runner)
        self.client.post(reverse('wallet_deposit'), {'amount': '4000'})
        self.assertEqual(
            Wallet.objects.get(user=self.runner).balance, Decimal('4000.00')
        )
        self.client.post(reverse('wallet_withdraw'), {
            'amount': '1500',
            'bank_name': 'Access Bank',
            'account_name': 'Runner One',
            'account_number': '9876543210',
        })
        self.assertEqual(
            Wallet.objects.get(user=self.runner).balance, Decimal('2500.00')
        )

    def test_create_page_shows_wallet_balance(self):
        fund_wallet(self.user, '7500.00')
        self.client.force_login(self.user)
        response = self.client.get(reverse('create_errand'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '7500.00')
        self.assertContains(response, reverse('wallet'))


class AdminManagementTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.staff = User.objects.create_user(
            'staff', password='Str0ngPass!23', is_staff=True
        )
        self.customer = User.objects.create_user(
            'customer', password='Str0ngPass!23', email='customer@example.com'
        )
        self.runner = User.objects.create_user(
            'runner', password='Str0ngPass!23', is_runner=True
        )
        self.errand = make_errand(
            self.customer, runner=self.runner, status=Errand.STATUS_COMPLETED
        )
        self.errand.wallet_tx = Transaction.objects.create(
            wallet=self.runner.wallet,
            errand=self.errand,
            amount=Decimal('1700.00'),
            type=Transaction.TYPE_CREDIT,
            description='Payout',
            balance_after=Decimal('1700.00'),
        )

    def test_user_detail_page_renders(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse('admin_user_detail', args=[self.customer.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.customer.username)

    def test_staff_can_update_user(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('admin_user_detail', args=[self.customer.pk]),
            {'email': 'updated@example.com', 'is_active': 'on'},
        )
        self.assertRedirects(
            response, reverse('admin_user_detail', args=[self.customer.pk])
        )
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.email, 'updated@example.com')
        self.assertTrue(self.customer.is_active)

    def test_staff_cannot_deactivate_themselves(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('admin_user_detail', args=[self.staff.pk]),
            {'email': self.staff.email},
        )
        self.assertRedirects(
            response, reverse('admin_user_detail', args=[self.staff.pk])
        )
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)

    def test_users_filtering(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('admin_users'), {'role': 'runner'})
        self.assertContains(response, 'runner')
        self.assertNotContains(response, 'customer@example.com')

        response = self.client.get(reverse('admin_users'), {'search': 'customer@'})
        self.assertContains(response, 'customer@example.com')
        self.assertNotContains(response, '@runner')

    def test_errands_filtering(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('admin_errands'), {'status': 'completed'})
        self.assertContains(response, self.errand.title)
        response = self.client.get(reverse('admin_errands'), {'status': 'pending'})
        self.assertNotContains(response, self.errand.title)

    def test_transactions_filtering(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('admin_transactions'), {'type': 'credit'})
        self.assertContains(response, 'Payout')
        response = self.client.get(reverse('admin_transactions'), {'type': 'debit'})
        self.assertNotContains(response, 'Payout')

    def test_runners_filtering(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('admin_runners'), {'verified': 'yes'})
        self.assertNotContains(response, '@runner')
        response = self.client.get(reverse('admin_runners'), {'verified': 'no'})
        self.assertContains(response, '@runner')
