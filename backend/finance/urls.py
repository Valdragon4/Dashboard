from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("transactions/", views.transactions, name="transactions"),
    path("transactions/new/", views.transaction_create, name="transaction_create"),
    path("transactions/<int:transaction_id>/delete/", views.transaction_delete, name="transaction_delete"),
    path("accounts/", views.accounts, name="accounts"),
    path("accounts/new/", views.account_create, name="account_create"),
    path("accounts/<int:account_id>/", views.account_detail, name="account_detail"),
    path("accounts/<int:account_id>/delete/", views.account_delete, name="account_delete"),
    path("accounts/<int:account_id>/delete-all-transactions/", views.delete_all_investment_transactions, name="delete_all_investment_transactions"),
    path("reset/user", views.reset_user_finance, name="reset_user_finance"),
    path("settings/", views.settings_view, name="settings"),
    path("api/investments/update-valuation", views.update_investment_valuation, name="update_investment_valuation"),
    path("api/accounts/<int:account_id>/toggle-dashboard", views.toggle_account_in_dashboard, name="toggle_account_in_dashboard"),
    path("api/transactions/<int:transaction_id>/update-category", views.update_transaction_category, name="update_transaction_category"),
    # API pour synchronisation manuelle depuis la liste des comptes (Story 1.9)
    path("api/accounts/<int:account_id>/sync/", views.account_sync_api, name="account_sync_api"),
    # Connexions bancaires (Story 1.8)
    path("bank-connections/", views.bank_connections_list, name="bank_connections_list"),
    path("bank-connections/new/", views.bank_connection_create, name="bank_connection_create"),
    path("bank-connections/<int:connection_id>/", views.bank_connection_update, name="bank_connection_update"),
    path("bank-connections/<int:connection_id>/delete/", views.bank_connection_delete, name="bank_connection_delete"),
    path("bank-connections/<int:connection_id>/sync/", views.bank_connection_sync, name="bank_connection_sync"),
    path("bank-connections/<int:connection_id>/2fa/", views.bank_connection_2fa, name="bank_connection_2fa"),
    # Logs de synchronisation (Story 1.10)
    path("bank-connections/logs/", views.sync_logs_list, name="sync_logs_list"),
    path("bank-connections/logs/<int:log_id>/", views.sync_log_detail, name="sync_log_detail"),
    path("bank-connections/logs/export/", views.sync_logs_export, name="sync_logs_export"),
    # Gestion des invitations utilisateur (superuser uniquement)
    path("admin/invitations/", views.invitation_list, name="invitation_list"),
    path("admin/invitations/new/", views.invitation_create, name="invitation_create"),
    path("admin/invitations/<uuid:token>/delete/", views.invitation_delete, name="invitation_delete"),
    # Inscription via invitation (public)
    path("invite/<uuid:token>/", views.register_with_invitation, name="register_with_invitation"),
]


