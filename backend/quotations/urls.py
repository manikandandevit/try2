"""
URLs for quotations app.
"""
from django.urls import path
from . import views

app_name = 'quotations'

urlpatterns = [
    path('', views.index, name='index'),
    path('api/chat/', views.chat, name='chat'),
    path('api/quotation/', views.get_quotation, name='get_quotation'),
    path('api/quotation/<int:quotation_id>/', views.get_quotation_by_id, name='get_quotation_by_id'),
    path('api/reset/', views.reset_quotation, name='reset_quotation'),
    path('api/sync-quotation/', views.sync_quotation, name='sync_quotation'),
    path('api/conversation-history/', views.get_conversation_history, name='get_conversation_history'),
    path('api/sync-conversation-history/', views.sync_conversation_history, name='sync_conversation_history'),
    path('api/company-info/', views.get_company_info, name='get_company_info'),
    path('api/company-login/', views.get_company_login, name='get_company_login'),
    path('api/login/', views.login, name='login'),
    path('api/logout/', views.logout, name='logout'),
    path('api/refresh-token/', views.refresh_token, name='refresh_token'),
    path('api/check-auth/', views.check_auth, name='check_auth'),
    # Client CRUD endpoints
    path('api/clients/', views.list_clients, name='list_clients'),
    path('api/clients/<int:client_id>/', views.client_detail, name='client_detail'),
    path('api/clients/<int:client_id>/quotations/', views.client_quotations, name='client_quotations'),
    # User CRUD endpoints
    path('api/users/', views.list_users, name='list_users'),
    path('api/users/<int:user_id>/', views.user_detail, name='user_detail'),
    path('api/users/<int:user_id>/reset-password/', views.reset_user_password, name='reset_user_password'),
    # Master endpoints for frontend
    path('api/get-all-users/', views.get_all_users, name='get_all_users'),
    path('api/user/<int:user_id>/', views.get_user_by_id, name='get_user_by_id'),
    path('api/user/status/<int:user_id>/', views.update_user_status, name='update_user_status'),
    path('api/get-active-departments/', views.get_active_departments, name='get_active_departments'),
    # Email sending endpoint
    path('api/send-quotation-email/', views.send_quotation_email, name='send_quotation_email'),
    # Dashboard statistics endpoint
    path('api/dashboard-stats/', views.dashboard_stats, name='dashboard_stats'),
    # Company settings endpoints
    path('api/company-details/', views.get_company_details, name='get_company_details'),
    path('api/company-details/update/', views.update_company_details, name='update_company_details'),
]
