from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class User(AbstractUser):
    """Custom user with a runner flag and a phone number."""

    is_runner = models.BooleanField(default=False)
    phone = models.CharField(max_length=30, blank=True)

    @property
    def display_name(self):
        return self.get_full_name() or self.username


class RunnerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='runner_profile')
    verified = models.BooleanField(default=False)
    bio = models.TextField(blank=True)

    def __str__(self):
        return f"Runner: {self.user.username}"


class Wallet(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='wallet')
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - ₦{self.balance}"

    def credit(self, amount, description='', errand=None):
        return self._record(Decimal(amount), Transaction.TYPE_CREDIT, description, errand)

    def debit(self, amount, description='', errand=None):
        return self._record(Decimal(amount), Transaction.TYPE_DEBIT, description, errand)

    def _record(self, amount, transaction_type, description, errand):
        if amount <= 0:
            raise ValueError('Transaction amount must be greater than zero.')

        if transaction_type == Transaction.TYPE_DEBIT and amount > self.balance:
            raise ValueError('Insufficient wallet balance.')

        if transaction_type == Transaction.TYPE_CREDIT:
            self.balance += amount
        else:
            self.balance -= amount

        self.save(update_fields=['balance', 'updated_at'])
        return Transaction.objects.create(
            wallet=self,
            amount=amount,
            type=transaction_type,
            description=description,
            errand=errand,
            balance_after=self.balance,
        )


class Errand(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_PICKUP = 'pickup'
    STATUS_TRANSIT = 'transit'
    STATUS_DELIVERED = 'delivered'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_PICKUP, 'On the way to pickup'),
        (STATUS_TRANSIT, 'Picked up & in transit'),
        (STATUS_DELIVERED, 'Delivered'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    CATEGORY_CHOICES = [
        ('delivery', 'Delivery'),
        ('shopping', 'Shopping'),
        ('pickup', 'Pickup'),
        ('courier', 'Courier Service'),
        ('other', 'Other'),
    ]

    URGENCY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]

    customer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='errands')
    runner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='running_errands',
    )
    pickup_address = models.CharField(max_length=255)
    dropoff_address = models.CharField(max_length=255)
    title = models.CharField(max_length=100)
    description = models.TextField()
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    urgency = models.CharField(max_length=10, choices=URGENCY_CHOICES)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)

    # True while the customer's price is held in escrow for an open errand.
    escrow_funded = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    pickup_started_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    COMMISSION_RATE = Decimal('0.15')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} - {self.get_status_display()}"

    @property
    def is_active(self):
        return self.status in {
            self.STATUS_ACCEPTED,
            self.STATUS_PICKUP,
            self.STATUS_TRANSIT,
            self.STATUS_DELIVERED,
        }

    @property
    def can_be_cancelled(self):
        return self.status == self.STATUS_PENDING

    @property
    def commission(self):
        return (self.price * self.COMMISSION_RATE).quantize(Decimal('0.01'))

    @property
    def runner_payout(self):
        return (self.price - self.commission).quantize(Decimal('0.01'))

    @property
    def status_css_class(self):
        return {
            self.STATUS_PENDING: 'pending',
            self.STATUS_ACCEPTED: 'in-progress',
            self.STATUS_PICKUP: 'in-progress',
            self.STATUS_TRANSIT: 'in-progress',
            self.STATUS_DELIVERED: 'completed',
            self.STATUS_COMPLETED: 'completed',
            self.STATUS_CANCELLED: 'cancelled',
        }.get(self.status, 'pending')

    @property
    def cover_image(self):
        """First attached image whose file still exists, or None."""
        for image in self.images.all():
            if image.file_exists:
                return image
        return None

    @property
    def has_visible_images(self):
        return self.cover_image is not None

    @property
    def visible_image_count(self):
        return sum(1 for image in self.images.all() if image.file_exists)


class ErrandImage(models.Model):
    """An image attached to an errand.

    ``file_exists`` lets templates skip rows whose file has been removed from
    storage instead of rendering a broken image.
    """

    errand = models.ForeignKey(Errand, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='errand_images/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image for {self.errand.title}"

    @property
    def file_exists(self):
        """True when the underlying file is still present in storage."""
        try:
            return bool(self.image and self.image.storage.exists(self.image.name))
        except Exception:
            return False


class Transaction(models.Model):
    TYPE_CREDIT = 'credit'
    TYPE_DEBIT = 'debit'
    TYPE_CHOICES = [
        (TYPE_CREDIT, 'Credit'),
        (TYPE_DEBIT, 'Debit'),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    errand = models.ForeignKey(
        Errand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transactions',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    description = models.CharField(max_length=255, blank=True)
    balance_after = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_type_display()} ₦{self.amount} - {self.wallet.user.username}"


class Review(models.Model):
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    errand = models.OneToOneField(Errand, on_delete=models.CASCADE, related_name='review')
    reviewer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews_given')
    runner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews_received')
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.rating}/5 for {self.runner.username}"


@receiver(post_save, sender=User)
def ensure_user_profile(sender, instance, created, **kwargs):
    """Every user gets a wallet; runners also get a runner profile."""
    Wallet.objects.get_or_create(user=instance)
    if instance.is_runner:
        RunnerProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=Errand)
def touch_errand_timestamp(sender, instance, **kwargs):
    """Keep a sensible completion timestamp when an errand is marked completed."""
    if instance.status == Errand.STATUS_COMPLETED and instance.completed_at is None:
        Errand.objects.filter(pk=instance.pk, completed_at__isnull=True).update(
            completed_at=timezone.now()
        )
