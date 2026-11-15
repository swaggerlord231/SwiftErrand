from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),

    # Authentication
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # Customer dashboard and settings
    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/settings/', views.account_settings, name='account_settings'),
    path('wallet/', views.wallet, name='wallet'),
    path('wallet/deposit/', views.wallet_deposit, name='wallet_deposit'),
    path('wallet/withdraw/', views.wallet_withdraw, name='wallet_withdraw'),

    # Errands (customer)
    path('errands/', views.my_errands, name='my_errands'),
    path('errands/create/', views.create_errand, name='create_errand'),
    path('errands/<int:id>/', views.errand_detail, name='errand_detail'),
    path('errands/<int:id>/edit/', views.edit_errand, name='edit_errand'),
    path('errands/<int:id>/cancel/', views.cancel_errand, name='cancel_errand'),
    path('errands/<int:id>/rate/', views.rate_runner, name='rate_runner'),

    # Errands (runner)
    path('runner/register/', views.become_runner, name='become_runner'),
    path('runner/dashboard/', views.runner_dashboard, name='runner_dashboard'),
    path('runner/errands/', views.available_errands, name='available_errands'),
    path('runner/errand/<int:id>/', views.errand_detail_runner, name='errand_detail_runner'),

    # Errand state transitions (POST only)
    path('errands/<int:id>/accept/', views.accept_errand, name='accept_errand'),
    path('errands/<int:id>/start_pickup/', views.start_pickup, name='start_pickup'),
    path('errands/<int:id>/picked_up/', views.picked_up, name='picked_up'),
    path('errands/<int:id>/mark_delivered/', views.mark_delivered, name='mark_delivered'),
    path('errands/<int:id>/confirm_received/', views.confirm_received, name='confirm_received'),

    # Admin
    path('admins/dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('admins/users/', views.admin_users, name='admin_users'),
    path('admins/users/<int:user_id>/', views.admin_user_detail, name='admin_user_detail'),
    path('admins/runners/', views.admin_runners, name='admin_runners'),
    path('admins/errands/', views.admin_errands, name='admin_errands'),
    path('admins/transactions/', views.admin_transactions, name='admin_transactions'),
    path('admins/disputes/', views.admin_disputes, name='admin_disputes'),
]
