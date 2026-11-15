from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.admin.sites import NotRegistered

from .models import Errand, ErrandImage, Review, RunnerProfile, Transaction, Wallet

User = get_user_model()

try:
    admin.site.unregister(User)
except NotRegistered:
    pass


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        'id',
        'username',
        'email',
        'is_runner',
        'is_staff',
        'is_active',
        'date_joined',
    )
    list_filter = ('is_runner', 'is_staff', 'is_active')
    search_fields = ('username', 'email')
    ordering = ('-date_joined',)
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Errand App Info', {'fields': ('is_runner', 'phone')}),
    )


@admin.register(RunnerProfile)
class RunnerProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'verified')
    list_filter = ('verified',)
    search_fields = ('user__username', 'user__email')


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'balance', 'updated_at')
    search_fields = ('user__username', 'user__email')


class ErrandImageInline(admin.TabularInline):
    model = ErrandImage
    extra = 0


@admin.register(Errand)
class ErrandAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'title',
        'customer',
        'runner',
        'status',
        'price',
        'created_at',
    )
    list_filter = ('status', 'category', 'urgency', 'created_at')
    search_fields = (
        'title',
        'customer__username',
        'runner__username',
        'pickup_address',
        'dropoff_address',
    )
    readonly_fields = ('created_at', 'updated_at')
    inlines = [ErrandImageInline]


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'wallet', 'errand', 'amount', 'type', 'balance_after', 'created_at')
    list_filter = ('type', 'created_at')
    search_fields = ('wallet__user__username', 'description')
    readonly_fields = ('created_at',)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('errand', 'reviewer', 'runner', 'rating', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = ('reviewer__username', 'runner__username', 'comment')
    readonly_fields = ('created_at',)
