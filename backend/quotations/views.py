"""
Views for quotations app.
"""
import json
import base64
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.db.models import Q, Count
from django.utils import timezone
from django.db.models.functions import TruncMonth
from .services import OpenRouterService, QuotationManager


def get_session_keys_for_request(request):
    """
    Return per-user/per-login session keys for quotation and conversation history.

    Problem:
    - Previously we used plain keys like 'quotation' and 'conversation_history'.
    - If admin and a normal user use the same browser (same Django session cookie),
      they end up sharing the same quotation + chat history.

    Fix:
    - Scope session keys by authenticated identity (user_id / company / anonymous).
    - This keeps each user's chatbot + quotation isolated, even on same machine.
    """
    try:
        user_info = get_user_from_token(request)
    except Exception:
        user_info = None

    prefix = "anon"

    if user_info:
        user_type = user_info.get("user_type", "user")
        user_id = user_info.get("user_id")
        user_email = user_info.get("user_email") or "unknown"

        if user_type == "user" and user_id:
            prefix = f"user_{user_id}"
        elif user_type == "company":
            # Single company admin identity
            prefix = "company"
        else:
            # Fallback to email-based prefix
            safe_email = user_email.replace("@", "_at_").replace(".", "_")
            prefix = f"user_{safe_email}"

    return {
        "quotation": f"{prefix}_quotation",
        "conversation_history": f"{prefix}_conversation_history",
    }


def migrate_legacy_session_for_request(request, session_keys):
    """
    One-time migration of old global session keys to per-user keys.

    - Old implementation stored everything under:
        'quotation' and 'conversation_history'
    - New implementation uses per-identity keys like:
        'user_1_quotation', 'company_quotation', etc.

    To avoid leaking data between admin/user1/user2:
    - For authenticated requests only, if legacy keys exist AND
      per-user keys do not, move data to the new keys and delete
      the old globals from this session.
    - Anonymous sessions keep using the old keys.
    """
    try:
        user_info = get_user_from_token(request)
    except Exception:
        user_info = None

    # Only migrate for authenticated users; anonymous keeps legacy keys.
    if not user_info:
        return

    legacy_mapping = {
        "quotation": "quotation",
        "conversation_history": "conversation_history",
    }

    for logical_key, legacy_key in legacy_mapping.items():
        new_key = session_keys.get(logical_key)
        if not new_key:
            continue

        if legacy_key in request.session and new_key not in request.session:
            request.session[new_key] = request.session[legacy_key]
            # Remove legacy key to prevent future leakage across identities
            try:
                del request.session[legacy_key]
            except KeyError:
                pass


def ensure_quotation_number(quotation):
    """
    Ensure quotation has a quotation_number for preview.
    
    CRITICAL: Once a quotation has a quotation_number, it NEVER changes.
    This ensures one quotation = one permanent number for its entire lifetime.

    Uses generate_quotation_number() from models so that:
    - The number is reserved globally (via Company.quotation_numberfield)
    - All users (admin / user1 / user2) see unique, sequential numbers
    - Once assigned, the number is permanent and never regenerated
    """
    # If quotation already has a number, NEVER change it - return as is
    if quotation.get('quotation_number'):
        return quotation
    
    # Only generate if quotation_number is completely missing
    # Import here to avoid circular imports
    from .models import generate_quotation_number

    # Reserve the next global quotation number (this updates Company)
    # This number will be permanent for this quotation
    next_number = generate_quotation_number()
    quotation['quotation_number'] = next_number
    return quotation


def index(request):
    """API Documentation - List all available endpoints."""
    # Define all API endpoints manually for clarity
    all_endpoints = [
        # Authentication
        {'path': '/api/login/', 'method': 'POST', 'name': 'login', 'description': 'Login and get JWT tokens', 'category': 'Authentication', 'auth_required': False},
        {'path': '/api/logout/', 'method': 'POST', 'name': 'logout', 'description': 'Logout and revoke refresh token', 'category': 'Authentication', 'auth_required': False},
        {'path': '/api/refresh-token/', 'method': 'POST', 'name': 'refresh_token', 'description': 'Get new access token using refresh token', 'category': 'Authentication', 'auth_required': False},
        {'path': '/api/check-auth/', 'method': 'GET', 'name': 'check_auth', 'description': 'Check if access token is valid', 'category': 'Authentication', 'auth_required': True},
        
        # Quotation Management
        {'path': '/api/chat/', 'method': 'POST', 'name': 'chat', 'description': 'Chat with AI for quotation', 'category': 'Quotation Management', 'auth_required': True},
        {'path': '/api/quotation/', 'method': 'GET', 'name': 'get_quotation', 'description': 'Get current quotation', 'category': 'Quotation Management', 'auth_required': True},
        {'path': '/api/reset/', 'method': 'POST', 'name': 'reset_quotation', 'description': 'Reset quotation to empty state', 'category': 'Quotation Management', 'auth_required': True},
        {'path': '/api/sync-quotation/', 'method': 'POST', 'name': 'sync_quotation', 'description': 'Sync quotation state from frontend', 'category': 'Quotation Management', 'auth_required': True},
        {'path': '/api/conversation-history/', 'method': 'GET', 'name': 'get_conversation_history', 'description': 'Get conversation history', 'category': 'Quotation Management', 'auth_required': True},
        {'path': '/api/sync-conversation-history/', 'method': 'POST', 'name': 'sync_conversation_history', 'description': 'Sync conversation history from frontend', 'category': 'Quotation Management', 'auth_required': True},
        
        # Company Info
        {'path': '/api/company-info/', 'method': 'GET', 'name': 'get_company_info', 'description': 'Get company information', 'category': 'Company Info', 'auth_required': False},
        {'path': '/api/company-login/', 'method': 'GET', 'name': 'get_company_login', 'description': 'Get company login page data', 'category': 'Company Info', 'auth_required': False},
        
        # Client Management
        {'path': '/api/clients/', 'method': 'GET', 'name': 'list_clients', 'description': 'List all clients (with optional search)', 'category': 'Client Management', 'auth_required': True},
        {'path': '/api/clients/', 'method': 'POST', 'name': 'create_client', 'description': 'Create a new client', 'category': 'Client Management', 'auth_required': True},
        {'path': '/api/clients/<id>/', 'method': 'PUT', 'name': 'update_client', 'description': 'Update a client', 'category': 'Client Management', 'auth_required': True},
        {'path': '/api/clients/<id>/', 'method': 'DELETE', 'name': 'delete_client', 'description': 'Delete a client', 'category': 'Client Management', 'auth_required': True},
        
        # User Management
        {'path': '/api/users/', 'method': 'GET', 'name': 'list_users', 'description': 'List all users (with optional search)', 'category': 'User Management', 'auth_required': True},
        {'path': '/api/users/', 'method': 'POST', 'name': 'create_user', 'description': 'Create a new user', 'category': 'User Management', 'auth_required': True},
        {'path': '/api/users/<id>/', 'method': 'PUT', 'name': 'update_user', 'description': 'Update a user', 'category': 'User Management', 'auth_required': True},
        {'path': '/api/users/<id>/', 'method': 'DELETE', 'name': 'delete_user', 'description': 'Delete a user', 'category': 'User Management', 'auth_required': True},
        {'path': '/api/users/<id>/reset-password/', 'method': 'POST', 'name': 'reset_user_password', 'description': 'Reset user password', 'category': 'User Management', 'auth_required': True},
        
        # Email
        {'path': '/api/send-quotation-email/', 'method': 'POST', 'name': 'send_quotation_email', 'description': 'Send quotation PDF via email', 'category': 'Email', 'auth_required': True},
    ]
    
    # Organize by category
    organized_apis = {}
    for endpoint in all_endpoints:
        category = endpoint['category']
        if category not in organized_apis:
            organized_apis[category] = []
        organized_apis[category].append(endpoint)

    # Explicit order for documentation display so that
    # login/authentication comes first, then customer/client APIs.
    category_order = [
        'Authentication',
        'Client Management',
        'User Management',
        'Quotation Management',
        'Company Info',
        'Email',
    ]
    
    # Determine response format
    accept_header = request.META.get('HTTP_ACCEPT', '')
    if 'application/json' in accept_header or request.GET.get('format') == 'json':
        # Return JSON format
        return JsonResponse({
            'message': 'Kattappa API Endpoints',
            'base_url': request.build_absolute_uri('/'),
            'endpoints': organized_apis,
            'total_endpoints': len(all_endpoints)
        }, json_dumps_params={'indent': 2})
    else:
        # Return HTML format
        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kattappa API Documentation</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}
        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
        }}
        .header p {{
            font-size: 1.1em;
            opacity: 0.9;
        }}
        .content {{
            padding: 40px;
        }}
        .section {{
            margin-bottom: 40px;
        }}
        .section-title {{
            font-size: 1.8em;
            color: #333;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 3px solid #667eea;
        }}
        .endpoint {{
            background: #f8f9fa;
            border-left: 4px solid #667eea;
            padding: 15px 20px;
            margin-bottom: 15px;
            border-radius: 4px;
            transition: all 0.3s ease;
        }}
        .endpoint:hover {{
            background: #e9ecef;
            transform: translateX(5px);
        }}
        .endpoint-path {{
            font-family: 'Courier New', monospace;
            font-size: 1.1em;
            color: #667eea;
            font-weight: bold;
            margin-bottom: 5px;
        }}
        .endpoint-name {{
            color: #666;
            font-size: 0.9em;
        }}
        .endpoint-view {{
            color: #999;
            font-size: 0.85em;
            font-family: 'Courier New', monospace;
            margin-top: 5px;
        }}
        .info-box {{
            background: #e7f3ff;
            border-left: 4px solid #2196F3;
            padding: 20px;
            margin-bottom: 30px;
            border-radius: 4px;
        }}
        .info-box h3 {{
            color: #1976D2;
            margin-bottom: 10px;
        }}
        .info-box p {{
            color: #555;
            line-height: 1.6;
        }}
        .format-links {{
            text-align: center;
            margin-top: 20px;
            padding: 20px;
            background: #f8f9fa;
        }}
        .format-links a {{
            display: inline-block;
            margin: 0 10px;
            padding: 10px 20px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 5px;
            transition: background 0.3s ease;
        }}
        .format-links a:hover {{
            background: #5568d3;
        }}
        .stats {{
            display: flex;
            justify-content: space-around;
            margin: 30px 0;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 8px;
        }}
        .stat-item {{
            text-align: center;
        }}
        .stat-number {{
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }}
        .stat-label {{
            color: #666;
            font-size: 0.9em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Kattappa API Documentation</h1>
            <p>Complete list of all available API endpoints</p>
        </div>
        <div class="content">
            <div class="info-box">
                <h3>📋 API Information</h3>
                <p><strong>Base URL:</strong> {request.build_absolute_uri('/')}</p>
                <p><strong>Total Endpoints:</strong> {len(all_endpoints)}</p>
                <p><strong>Flow:</strong> First call the <code>/api/login/</code> endpoint to get your JWT tokens, then call the customer/client and other protected APIs using the access token.</p>
                <p><strong>Format:</strong> All endpoints return JSON. Use <code>Authorization: Bearer &lt;token&gt;</code> header for protected endpoints.</p>
                <p><strong>JWT Authentication:</strong> Access tokens expire in 15 minutes. Use refresh token to get new access token.</p>
            </div>
            
            <div class="stats">
                <div class="stat-item">
                    <div class="stat-number">{len(organized_apis.get('Authentication', []))}</div>
                    <div class="stat-label">Authentication APIs</div>
                </div>
                <div class="stat-item">
                    <div class="stat-number">{len(organized_apis.get('Client Management', []))}</div>
                    <div class="stat-label">Customer / Client APIs</div>
                </div>
                <div class="stat-item">
                    <div class="stat-number">{len(organized_apis.get('User Management', []))}</div>
                    <div class="stat-label">User APIs</div>
                </div>
                <div class="stat-item">
                    <div class="stat-number">{len(organized_apis.get('Quotation Management', []))}</div>
                    <div class="stat-label">Quotation APIs</div>
                </div>
            </div>
"""
        
        # Add each category (using the explicit order defined above)
        for category in category_order:
            endpoints = organized_apis.get(category, [])
            if endpoints:
                html_content += f"""
            <div class="section">
                <h2 class="section-title">{category}</h2>
"""
                for endpoint in endpoints:
                    auth_badge = '<span style="background: #28a745; color: white; padding: 2px 8px; border-radius: 3px; font-size: 0.8em; margin-left: 10px;">🔒 Auth Required</span>' if endpoint['auth_required'] else '<span style="background: #6c757d; color: white; padding: 2px 8px; border-radius: 3px; font-size: 0.8em; margin-left: 10px;">Public</span>'
                    method_badge = f'<span style="background: #007bff; color: white; padding: 2px 8px; border-radius: 3px; font-size: 0.8em; margin-right: 10px; font-weight: bold;">{endpoint["method"]}</span>'
                    html_content += f"""
                <div class="endpoint">
                    <div class="endpoint-path">{method_badge}{endpoint['path']}{auth_badge}</div>
                    <div class="endpoint-name">{endpoint['description']}</div>
                    <div class="endpoint-view">Endpoint: {endpoint['name']}</div>
                </div>
"""
                html_content += "            </div>"
        
        html_content += """
            <div class="format-links">
                <a href="?format=json">View as JSON</a>
                <a href="/admin/">Django Admin</a>
            </div>
        </div>
    </div>
</body>
</html>
"""
        return HttpResponse(html_content)


@csrf_exempt
@require_http_methods(["POST"])
def chat(request):
    """Handle chat messages and return AI response with updated quotation. When quotation_id provided, load/save from DB."""
    try:
        from .models import Quotation
        session_keys = get_session_keys_for_request(request)
        migrate_legacy_session_for_request(request, session_keys)

        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        quotation_id = data.get('quotation_id')
        enhance_mode = data.get('enhance_mode', False) or 'ENHANCE_QUOTATION' in user_message.upper()
        
        if not user_message:
            return JsonResponse({'error': 'Message is required'}, status=400)
        
        current_quotation = None
        conversation_history = []
        
        if quotation_id:
            try:
                quotation_obj = Quotation.objects.get(id=quotation_id)
                qdata = quotation_obj.quotation_data or {}
                current_quotation = dict(qdata)
                current_quotation['id'] = quotation_obj.id
                current_quotation['quotation_number'] = quotation_obj.quotation_number
                conversation_history = qdata.get('conversation_history', []) or []
            except (Quotation.DoesNotExist, ValueError):
                pass
        
        if current_quotation is None:
            conversation_history = request.session.get(session_keys['conversation_history'], [])
            current_quotation = request.session.get(session_keys['quotation'], None)
            if current_quotation is None:
                # Try to get last quotation from database for this user
                user_info = get_user_from_token(request)
                if user_info:
                    user_type = user_info.get('user_type', 'company')
                    user_id = user_info.get('user_id')
                    
                    if user_type == 'user' and user_id:
                        last_quotation = Quotation.objects.filter(
                            quotation_data__created_by_user_id=user_id
                        ).order_by('-updated_at').first()
                    else:
                        last_quotation = Quotation.objects.filter(
                            quotation_data__created_by_type='company'
                        ).order_by('-updated_at').first()
                    
                    if last_quotation:
                        qdata = last_quotation.quotation_data or {}
                        current_quotation = dict(qdata)
                        current_quotation['id'] = last_quotation.id
                        current_quotation['quotation_number'] = last_quotation.quotation_number
                        conversation_history = qdata.get('conversation_history', []) or []
                    else:
                        current_quotation = QuotationManager.initialize_quotation()
                else:
                    current_quotation = QuotationManager.initialize_quotation()
        
        existing_quotation_number = current_quotation.get('quotation_number')
        if len(conversation_history) <= 1 and not current_quotation.get('id'):
            current_quotation = QuotationManager.initialize_quotation()
            if existing_quotation_number:
                current_quotation['quotation_number'] = existing_quotation_number
        
        openrouter_service = OpenRouterService()
        message, updated_quotation = openrouter_service.process_user_message(
            user_message=user_message,
            current_quotation=current_quotation,
            conversation_history=conversation_history,
            enhance_mode=enhance_mode
        )
        
        if existing_quotation_number:
            updated_quotation['quotation_number'] = existing_quotation_number
        updated_quotation = QuotationManager.normalize_quotation(updated_quotation)
        if existing_quotation_number:
            updated_quotation['quotation_number'] = existing_quotation_number
        if not QuotationManager.validate_quotation(updated_quotation):
            updated_quotation = QuotationManager.normalize_quotation(current_quotation)
            if existing_quotation_number:
                updated_quotation['quotation_number'] = existing_quotation_number
            if "issue" not in message.lower() and "error" not in message.lower():
                message = message + " (Note: Some quotation data was invalid and has been corrected.)"
        
        # Format success message when quotation is created with services
        updated_services = updated_quotation.get('services', [])
        current_services = current_quotation.get('services', [])
        if updated_services and len(updated_services) > 0:
            # Check if services were just added (new quotation or services increased)
            if not current_services or len(updated_services) > len(current_services):
                grand_total = updated_quotation.get('grand_total', 0) or 0
                service_count = len(updated_services)
                # Format the message with service count and total (remove .00 if present)
                total_str = f"{grand_total:,.2f}".replace('.00', '')
                message = f"I've created a quotation based on your scenario with {service_count} service{'s' if service_count != 1 else ''}. Total: ₹{total_str}"
        
        # Do NOT auto-generate quotation_number here - only generate when actually creating a new quote in database
        # updated_quotation = ensure_quotation_number(updated_quotation)
        
        conversation_history = conversation_history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": message}
        ]
        conversation_history = conversation_history[-50:]
        
        if quotation_id and updated_quotation.get('id'):
            try:
                quotation_obj = Quotation.objects.get(id=quotation_id)
                qdata = dict(quotation_obj.quotation_data or {})
                for k, v in updated_quotation.items():
                    if k != 'conversation_history' and k not in ('id', 'created_at', 'updated_at'):
                        qdata[k] = v
                qdata['conversation_history'] = conversation_history
                
                # Track who updated this quotation
                user_info = get_user_from_token(request)
                if user_info:
                    updated_by_type = user_info.get('user_type', 'company')
                    updated_by_user_id = user_info.get('user_id')
                    if updated_by_type == 'user' and updated_by_user_id:
                        qdata['updated_by_type'] = 'user'
                        qdata['updated_by_user_id'] = updated_by_user_id
                    else:
                        qdata['updated_by_type'] = 'company'
                        qdata['updated_by_user_id'] = None
                
                quotation_obj.quotation_data = qdata
                quotation_obj.save()
            except (Quotation.DoesNotExist, ValueError):
                pass
        
        # Auto-save quotation to database if user is authenticated and quotation has services
        # This ensures quotation persists across logout/login
        if not quotation_id and updated_quotation.get('services') and len(updated_quotation.get('services', [])) > 0:
            user_info = get_user_from_token(request)
            if user_info:
                try:
                    # Check if quotation already exists in DB (by checking if id exists)
                    if not updated_quotation.get('id'):
                        # Create new quotation in database
                        user_type = user_info.get('user_type', 'company')
                        user_id = user_info.get('user_id')
                        
                        # Set created_by fields
                        if user_type == 'user' and user_id:
                            updated_quotation['created_by_type'] = 'user'
                            updated_quotation['created_by_user_id'] = user_id
                        else:
                            updated_quotation['created_by_type'] = 'company'
                            updated_quotation['created_by_user_id'] = None
                        
                        # Track who updated
                        updated_quotation['updated_by_type'] = user_type
                        updated_quotation['updated_by_user_id'] = user_id if user_type == 'user' else None
                        
                        updated_quotation['conversation_history'] = conversation_history
                        
                        quotation_obj = Quotation.objects.create(
                            quotation_data=updated_quotation
                        )
                        updated_quotation['id'] = quotation_obj.id
                        updated_quotation['quotation_number'] = quotation_obj.quotation_number
                    else:
                        # Update existing quotation
                        quotation_obj = Quotation.objects.get(id=updated_quotation['id'])
                        qdata = dict(quotation_obj.quotation_data or {})
                        for k, v in updated_quotation.items():
                            if k not in ('id', 'created_at', 'updated_at', 'conversation_history'):
                                qdata[k] = v
                        qdata['conversation_history'] = conversation_history
                        
                        # Track who updated
                        user_type = user_info.get('user_type', 'company')
                        user_id = user_info.get('user_id')
                        if user_type == 'user' and user_id:
                            qdata['updated_by_type'] = 'user'
                            qdata['updated_by_user_id'] = user_id
                        else:
                            qdata['updated_by_type'] = 'company'
                            qdata['updated_by_user_id'] = None
                        
                        quotation_obj.quotation_data = qdata
                        quotation_obj.save()
                        updated_quotation['quotation_number'] = quotation_obj.quotation_number
                except Exception as e:
                    # If DB save fails, continue with session storage
                    print(f"Auto-save quotation failed: {e}")
        
        request.session[session_keys['quotation']] = updated_quotation
        request.session[session_keys['conversation_history']] = conversation_history[-20:]
        
        return JsonResponse({'response': message, 'quotation': updated_quotation})
    
    except json.JSONDecodeError:
        return JsonResponse({
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': f'Server error: {str(e)}'
        }, status=500)


@require_http_methods(["GET"])
def get_quotation(request):
    """Get current quotation from session, or last quotation from database if session is empty."""
    from .models import Quotation
    session_keys = get_session_keys_for_request(request)
    migrate_legacy_session_for_request(request, session_keys)
    quotation = request.session.get(session_keys['quotation'], None)
    
    # If session is empty, try to get last quotation from database for this user
    if not quotation or (not quotation.get('services') or len(quotation.get('services', [])) == 0):
        user_info = get_user_from_token(request)
        if user_info:
            user_type = user_info.get('user_type', 'company')
            user_id = user_info.get('user_id')
            
            if user_type == 'user' and user_id:
                last_quotation = Quotation.objects.filter(
                    quotation_data__created_by_user_id=user_id
                ).order_by('-updated_at').first()
            else:
                last_quotation = Quotation.objects.filter(
                    quotation_data__created_by_type='company'
                ).order_by('-updated_at').first()
            
            if last_quotation:
                qdata = last_quotation.quotation_data or {}
                quotation = dict(qdata)
                quotation['id'] = last_quotation.id
                quotation['quotation_number'] = last_quotation.quotation_number
                # Update session with this quotation
                request.session[session_keys['quotation']] = quotation
            else:
                quotation = QuotationManager.initialize_quotation()
        else:
            quotation = QuotationManager.initialize_quotation()
    
    # Do NOT auto-generate quotation_number here - only generate when actually creating a new quote in database
    # quotation = ensure_quotation_number(quotation)
    
    return JsonResponse({
        'quotation': quotation
    })


@csrf_exempt
@require_http_methods(["GET"])
def get_quotation_by_id(request, quotation_id):
    """Get quotation by ID from database."""
    try:
        from .models import Quotation
        
        quotation_obj = Quotation.objects.get(id=quotation_id)
        quotation_data = quotation_obj.quotation_data or {}
        
        # Add quotation_number and other metadata
        quotation_data['quotation_number'] = quotation_obj.quotation_number
        quotation_data['id'] = quotation_obj.id
        quotation_data['created_at'] = quotation_obj.created_at.isoformat() if quotation_obj.created_at else None
        quotation_data['updated_at'] = quotation_obj.updated_at.isoformat() if quotation_obj.updated_at else None
        
        return JsonResponse({
            'success': True,
            'quotation': quotation_data
        })
    except Quotation.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Quotation not found'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error fetching quotation: {str(e)}'
        }, status=500)


@require_http_methods(["GET"])
def get_last_quotation_id(request):
    """
    Return the last quotation ID the current user worked on.
    - New user (no quotations): returns null -> frontend shows empty.
    - Existing user: returns most recent quotation id -> frontend redirects to /quotation/:id.
    """
    from .models import Quotation
    try:
        user_info = get_user_from_token(request)
        if not user_info:
            return JsonResponse({'success': True, 'last_quotation_id': None})

        user_type = user_info.get('user_type', 'company')
        user_id = user_info.get('user_id')

        if user_type == 'user' and user_id:
            # Regular user: only return quotations created by this user
            q = Quotation.objects.filter(
                quotation_data__created_by_user_id=user_id
            ).order_by('-updated_at').first()
        else:
            # Company admin: only return quotations created by company admin
            # Do NOT fallback to any quotation - admin should only see their own quotations
            q = Quotation.objects.filter(
                quotation_data__created_by_type='company'
            ).order_by('-updated_at').first()

        return JsonResponse({
            'success': True,
            'last_quotation_id': q.id if q else None
        })
    except Exception as e:
        return JsonResponse({
            'success': True,
            'last_quotation_id': None
        })


@require_http_methods(["GET"])
def get_conversation_history(request):
    """Get conversation history - from Quotation by ID if quotation_id provided, else from session."""
    from .models import Quotation
    quotation_id = request.GET.get('quotation_id')
    if quotation_id:
        try:
            quotation_obj = Quotation.objects.get(id=quotation_id)
            qdata = quotation_obj.quotation_data or {}
            conversation_history = qdata.get('conversation_history', [])
            return JsonResponse({'messages': conversation_history})
        except (Quotation.DoesNotExist, ValueError):
            return JsonResponse({'messages': []})
    session_keys = get_session_keys_for_request(request)
    migrate_legacy_session_for_request(request, session_keys)
    conversation_history = request.session.get(session_keys['conversation_history'], [])
    return JsonResponse({'messages': conversation_history})


@csrf_exempt
@require_http_methods(["POST"])
def sync_conversation_history(request):
    """Sync conversation history - to Quotation by ID if quotation_id provided, else to session."""
    try:
        from .models import Quotation
        data = json.loads(request.body)
        messages = data.get('messages', [])
        quotation_id = data.get('quotation_id')
        
        if not isinstance(messages, list):
            return JsonResponse({'error': 'Messages must be an array'}, status=400)
        
        conversation_history = []
        for msg in messages:
            if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                conversation_history.append({'role': msg['role'], 'content': msg['content']})
        conversation_history = conversation_history[-50:]
        
        if quotation_id:
            try:
                quotation_obj = Quotation.objects.get(id=quotation_id)
                qdata = quotation_obj.quotation_data or {}
                qdata['conversation_history'] = conversation_history
                quotation_obj.quotation_data = qdata
                quotation_obj.save()
            except (Quotation.DoesNotExist, ValueError):
                pass
        else:
            session_keys = get_session_keys_for_request(request)
            migrate_legacy_session_for_request(request, session_keys)
            if len(conversation_history) <= 1:
                request.session[session_keys['quotation']] = QuotationManager.initialize_quotation()
            request.session[session_keys['conversation_history']] = conversation_history
        
        return JsonResponse({'success': True, 'messages': conversation_history})
    
    except json.JSONDecodeError:
        return JsonResponse({
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': f'Server error: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def reset_quotation(request):
    """Reset quotation to empty state."""
    session_keys = get_session_keys_for_request(request)
    migrate_legacy_session_for_request(request, session_keys)
    request.session[session_keys['quotation']] = QuotationManager.initialize_quotation()
    request.session[session_keys['conversation_history']] = []
    return JsonResponse({
        'success': True,
        'quotation': QuotationManager.initialize_quotation()
    })


@csrf_exempt
@require_http_methods(["POST"])
def sync_quotation(request):
    """Sync quotation state from frontend. When quotation has id, persist to DB (auto-save)."""
    try:
        from .models import Quotation
        session_keys = get_session_keys_for_request(request)
        migrate_legacy_session_for_request(request, session_keys)

        data = json.loads(request.body)
        quotation = data.get('quotation', None)
        
        if not quotation:
            return JsonResponse({'error': 'Quotation data is required'}, status=400)
        
        existing_quotation = request.session.get(session_keys['quotation'], {})
        existing_number = existing_quotation.get('quotation_number')
        payload_has_db_id = quotation.get('id') and quotation.get('quotation_number')
        
        quotation = QuotationManager.normalize_quotation(quotation)
        if payload_has_db_id:
            quotation['quotation_number'] = quotation.get('quotation_number')
        elif existing_number:
            quotation['quotation_number'] = existing_number
        
        # Only generate quotation_number when actually creating a new quotation in database
        # Do NOT generate just for syncing session quotations
        # quotation = ensure_quotation_number(quotation)
        
        if not QuotationManager.validate_quotation(quotation):
            return JsonResponse({'error': 'Invalid quotation structure'}, status=400)
        
        if quotation.get('id'):
            try:
                quotation_obj = Quotation.objects.get(id=quotation['id'])
                qdata = dict(quotation_obj.quotation_data or {})
                for k, v in quotation.items():
                    if k not in ('id', 'created_at', 'updated_at', 'conversation_history'):
                        qdata[k] = v
                if 'conversation_history' in quotation and isinstance(quotation.get('conversation_history'), list):
                    qdata['conversation_history'] = quotation['conversation_history']
                
                # Track who created/updated this quotation
                user_info = get_user_from_token(request)
                if user_info:
                    user_type = user_info.get('user_type', 'company')
                    user_id = user_info.get('user_id')
                    
                    # Set created_by fields if not already set (first time saving)
                    if 'created_by_type' not in qdata or not qdata.get('created_by_type'):
                        if user_type == 'user' and user_id:
                            qdata['created_by_type'] = 'user'
                            qdata['created_by_user_id'] = user_id
                        else:
                            qdata['created_by_type'] = 'company'
                            qdata['created_by_user_id'] = None
                    
                    # Track who updated this quotation
                    updated_by_type = user_type
                    updated_by_user_id = user_id
                    if updated_by_type == 'user' and updated_by_user_id:
                        qdata['updated_by_type'] = 'user'
                        qdata['updated_by_user_id'] = updated_by_user_id
                    else:
                        qdata['updated_by_type'] = 'company'
                        qdata['updated_by_user_id'] = None
                
                quotation_obj.quotation_data = qdata
                quotation_obj.save()
            except (Quotation.DoesNotExist, ValueError):
                pass
        
        request.session[session_keys['quotation']] = quotation
        return JsonResponse({'success': True, 'quotation': quotation})
    
    except json.JSONDecodeError:
        return JsonResponse({
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': f'Server error: {str(e)}'
        }, status=500)


@require_http_methods(["GET"])
def get_company_info(request):
    """Get company information for header display."""
    try:
        from .models import Company
        from django.conf import settings
        
        # Get company from database
        company = Company.get_company()
        
        # Get quotation logo URL
        quotation_logo_url = None
        if company.quotation_logo:
            try:
                logo_url = company.quotation_logo.url
                if logo_url and logo_url.strip():
                    logo_url = logo_url.strip()
                    if not logo_url.startswith('/'):
                        logo_url = '/' + logo_url
                    quotation_logo_url = logo_url
            except (AttributeError, ValueError):
                quotation_logo_url = None
        
        # Return company information from database with quotation logo
        company_info = {
            'company_name': company.company_name or 'MAKLOGISTICS',
            'tagline': company.tagline or 'DIGITAL SOLUTION ARCHITECTS',
            'website': '',
            'phone_number': company.phone_number or '9042510714',
            'email': company.email or 'maklogistics@gmail.com',
            'address': company.address or 'TBI@TCE, Thiruparankundaram, Madurai – 625 015',
            'logo_url': quotation_logo_url  # Use quotation_logo instead of logo_url
        }
        
        return JsonResponse(company_info)
    except Exception as e:
        # Fallback to hardcoded info if error
        company_info = {
            'company_name': 'MAKLOGISTICS',
            'tagline': 'DIGITAL SOLUTION ARCHITECTS',
            'website': '',
            'phone_number': '9042510714',
            'email': 'maklogistics@gmail.com',
            'address': 'TBI@TCE, Thiruparankundaram, Madurai – 625 015',
            'logo_url': None
        }
        return JsonResponse(company_info)


@require_http_methods(["GET"])
def get_company_login(request):
    """Get company login credentials and images for login page."""
    try:
        from .models import Company
        from django.conf import settings
        
        # Default values
        login_data = {
            'email': '',
            'brand_name': '',
            'login_logo_url': None,
            'login_image_url': None
        }
        
        company = Company.get_company()
        
        if company:
            login_data['email'] = company.email or login_data['email']
            login_data['brand_name'] = company.brand_name or login_data['brand_name']
            
            # Get login logo URL
            if company.login_logo:
                try:
                    logo_url = company.login_logo.url
                    if logo_url:
                        logo_url = logo_url.strip()
                        # Django's .url already returns the correct path like /media/company_login/file.jpg
                        # Just ensure it starts with / for relative paths
                        if logo_url and not logo_url.startswith('http') and not logo_url.startswith('/'):
                            logo_url = '/' + logo_url
                        login_data['login_logo_url'] = logo_url
                except Exception as e:
                    print(f"Error getting login logo URL: {str(e)}")
                    login_data['login_logo_url'] = None
            
            # Get login image URL
            if company.login_image:
                try:
                    image_url = company.login_image.url
                    if image_url:
                        image_url = image_url.strip()
                        # Django's .url already returns the correct path like /media/company_login/file.jpg
                        # Just ensure it starts with / for relative paths
                        if image_url and not image_url.startswith('http') and not image_url.startswith('/'):
                            image_url = '/' + image_url
                        login_data['login_image_url'] = image_url
                except Exception as e:
                    print(f"Error getting login image URL: {str(e)}")
                    login_data['login_image_url'] = None
        
        return JsonResponse(login_data)
    except Exception as e:
        import traceback
        print(f"Error in get_company_login: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'error': f'Error fetching company login data: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def login(request):
    print("login request: ", request)
    """Handle login authentication with JWT - validates user credentials against Company or User model."""
    try:
        from .models import Company, User
        from .jwt_utils import (
            create_access_token, create_refresh_token,
            get_client_ip, get_user_agent
        )
        
        # Parse request data
        if not request.body:
            print("Empty request body")
            return JsonResponse({
                'success': False,
                'error': 'Request body is required'
            }, status=400)
        
        try:
            data = json.loads(request.body)
            print(f"Login request data: {data}")
        except json.JSONDecodeError as e:
            print(f"JSON decode error: {str(e)}, Body: {request.body}")
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON in request body'
            }, status=400)
        
        user_email_or_username = data.get('email', '').strip()  # Can be email or username
        user_password = data.get('password', '').strip()
        
        print(f"Login attempt - Email/Username: {user_email_or_username}, Password length: {len(user_password) if user_password else 0}")
        
        # Validate input
        if not user_email_or_username:
            return JsonResponse({
                'success': False,
                'error': 'Email or Username is required'
            }, status=400)
        
        if not user_password:
            return JsonResponse({
                'success': False,
                'error': 'Password is required'
            }, status=400)
        
        # First, try to authenticate as User (by email or username)
        try:
            print(f"Checking for user with email/username: {user_email_or_username}")
            # Try to find user by email first, then by username
            from django.db.models import Q
            user = User.objects.filter(
                Q(email__iexact=user_email_or_username) | Q(username__iexact=user_email_or_username)
            ).first()
            
            if not user:
                raise User.DoesNotExist("User not found")
            print(f"User found: {user.email} (username: {user.username}), Active: {user.is_active}")
            
            # User exists, check password and active status
            if not user.is_active:
                print(f"User account is inactive: {user_email_or_username}")
                return JsonResponse({
                    'success': False,
                    'error': 'Your account is inactive. Please contact administrator.'
                }, status=401)
            
            print(f"Checking password for user: {user_email_or_username}")
            print(f"Stored password hash (first 50 chars): {user.password[:50] if user.password else 'None'}")
            print(f"Input password length: {len(user_password)}")
            
            # Check if password is hashed
            from django.contrib.auth.hashers import is_password_usable
            is_hashed = is_password_usable(user.password) if user.password else False
            print(f"Is password hashed: {is_hashed}")
            
            password_valid = user.check_password(user_password)
            print(f"Password check result: {password_valid}")
            
            # If password is not hashed but matches directly, hash it and update
            if not password_valid and not is_hashed:
                print(f"Password is not hashed. Checking direct match...")
                if user.password == user_password:
                    print(f"Direct match found! Hashing password and updating user...")
                    user.set_password(user_password)
                    user.save()
                    password_valid = True
                    print(f"Password hashed and updated. Login should work now.")
                else:
                    print(f"Direct match also failed. Password mismatch.")
            
            if password_valid:
                # User login successful - generate JWT tokens
                print(f"Password valid, generating tokens for user: {user.email}")
                try:
                    # Since is_admin and permissions are removed, set defaults
                    access_token = create_access_token(
                        user_email=user.email,
                        user_type='user',
                        user_id=user.id,
                        is_admin=False,  # All users are regular users now
                        permissions=[]  # No permissions field in new model
                    )
                    print(f"Access token generated successfully")
                    
                    refresh_token = create_refresh_token(
                        user_email=user.email,
                        user_type='user',
                        user_id=user.id,
                        ip_address=get_client_ip(request),
                        user_agent=get_user_agent(request)
                    )
                    print(f"Refresh token generated successfully")
                except Exception as token_error:
                    print(f"Error generating tokens: {str(token_error)}")
                    import traceback
                    traceback.print_exc()
                    return JsonResponse({
                        'success': False,
                        'error': f'Error generating authentication tokens: {str(token_error)}'
                    }, status=500)
                
                return JsonResponse({
                    'success': True,
                    'message': 'Login successful',
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'token_type': 'Bearer',
                    'expires_in': 900,  # 15 minutes in seconds
                    'user': {
                        'email': user.email,
                        'user_type': 'user',
                        'is_admin': False,
                        'permissions': [],
                        'name': user.name,
                        'phone': user.phone
                    }
                })
            else:
                # User exists but password is wrong
                print(f"Password mismatch for user: {user_email_or_username}")
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid email/username or password'
                }, status=401)
        except User.DoesNotExist:
            print(f"User not found: {user_email_or_username}, trying company login")
            pass
        except Exception as user_error:
            print(f"Error during user authentication: {str(user_error)}")
            import traceback
            traceback.print_exc()
            # Continue to company login
            pass
        
        # If not a user, try company login
        # Search for company by email (case-insensitive)
        company = None
        email_match = False
        password_match = False
        
        try:
            # First, try to find company by exact email match
            company = Company.objects.filter(email__iexact=user_email_or_username.strip()).first()
            
            # If no company found with matching email, try to get any company with email
            if not company:
                company = Company.objects.filter(email__isnull=False).exclude(email='').first()
            
            # If still no company, use get_company() as fallback
            if not company:
                company = Company.get_company()
            
            # Check if company credentials are configured
            if not company or not company.email or not company.password:
                print(f"Login failed: Company not found or credentials not configured. Email: {user_email_or_username}")
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid email or password'
                }, status=401)
            
            # Validate credentials: Compare user input with company credentials
            email_match = company.email.lower().strip() == user_email_or_username.lower().strip()
            # Compare passwords (strip whitespace to handle edge cases)
            password_match = (company.password or '').strip() == (user_password or '').strip()
            
            # Debug logging
            print(f"Company login attempt - Email match: {email_match}, Password match: {password_match}")
            print(f"Company email: {company.email}, User email: {user_email_or_username}")
            print(f"Company password length: {len(company.password) if company.password else 0}, User password length: {len(user_password)}")
            
        except Exception as e:
            # Log the error for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f'Company login error: {str(e)}', exc_info=True)
            print(f"Company login exception: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': 'Invalid email or password'
            }, status=401)
        
        if email_match and password_match:
            # Company login successful - generate JWT tokens
            print(f"Company credentials valid, generating tokens for: {company.email}")
            try:
                access_token = create_access_token(
                    user_email=company.email,
                    user_type='company',
                    user_id=None,
                    is_admin=True,
                    permissions=[]
                )
                print(f"Company access token generated successfully")
                
                refresh_token = create_refresh_token(
                    user_email=company.email,
                    user_type='company',
                    user_id=None,
                    ip_address=get_client_ip(request),
                    user_agent=get_user_agent(request)
                )
                print(f"Company refresh token generated successfully")
            except Exception as token_error:
                print(f"Error generating company tokens: {str(token_error)}")
                import traceback
                traceback.print_exc()
                return JsonResponse({
                    'success': False,
                    'error': f'Error generating authentication tokens: {str(token_error)}'
                }, status=500)
            
            return JsonResponse({
                'success': True,
                'message': 'Login successful',
                'access_token': access_token,
                'refresh_token': refresh_token,
                'token_type': 'Bearer',
                'expires_in': 900,  # 15 minutes in seconds
                'user': {
                    'email': company.email,
                    'user_type': 'company',
                    'is_admin': True,
                    'permissions': [],
                    'company_name': company.company_name or company.brand_name or ''
                }
            })
        else:
            # Login failed - credentials don't match
            return JsonResponse({
                'success': False,
                'error': 'Invalid email or password'
            }, status=401)
    
    except Exception as e:
        # Production-level exception handling
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f'Login error: {str(e)}', exc_info=True)
        
        # Print full traceback for debugging
        print(f"Login exception: {str(e)}")
        traceback.print_exc()
        
        return JsonResponse({
            'success': False,
            'error': f'An unexpected error occurred: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def logout(request):
    """Handle logout - revokes refresh token."""
    try:
        from .jwt_utils import revoke_refresh_token
        
        # Parse request data
        data = json.loads(request.body)
        refresh_token = data.get('refresh_token', '').strip()
        
        if not refresh_token:
            return JsonResponse({
                'success': False,
                'error': 'Refresh token is required'
            }, status=400)
        
        # Revoke refresh token
        revoked = revoke_refresh_token(refresh_token)
        
        if not revoked:
            return JsonResponse({
                'success': False,
                'error': 'Invalid or already revoked refresh token'
            }, status=400)
        
        # Log logout action (optional, for security auditing)
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f'User logged out - refresh token revoked')
        
        return JsonResponse({
            'success': True,
            'message': 'Logout successful'
        })
    
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        # Production-level exception handling
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Logout error: {str(e)}', exc_info=True)
        
        return JsonResponse({
            'success': False,
            'error': 'An error occurred during logout.'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def refresh_token(request):
    """Refresh access token using refresh token."""
    try:
        from .jwt_utils import (
            verify_refresh_token, create_access_token,
            rotate_refresh_token, get_client_ip, get_user_agent
        )
        
        # Parse request data
        data = json.loads(request.body)
        refresh_token_string = data.get('refresh_token', '').strip()
        
        if not refresh_token_string:
            return JsonResponse({
                'success': False,
                'error': 'Refresh token is required'
            }, status=400)
        
        # Verify refresh token
        refresh_token_obj = verify_refresh_token(refresh_token_string)
        
        if not refresh_token_obj:
            return JsonResponse({
                'success': False,
                'error': 'Invalid or expired refresh token'
            }, status=401)
        
        # Get user info from refresh token
        user_email = refresh_token_obj.user_email
        user_type = refresh_token_obj.user_type
        user_id = refresh_token_obj.user_id
        
        # Get user details for permissions
        is_admin = False
        permissions = []
        
        if user_type == 'user' and user_id:
            from .models import User
            try:
                user = User.objects.get(id=user_id)
                is_admin = user.is_admin
                permissions = user.permissions if not user.is_admin else []
            except User.DoesNotExist:
                pass
        elif user_type == 'company':
            is_admin = True
        
        # Generate new access token
        access_token = create_access_token(
            user_email=user_email,
            user_type=user_type,
            user_id=user_id,
            is_admin=is_admin,
            permissions=permissions
        )
        
        # Rotate refresh token for enhanced security (optional but recommended)
        new_refresh_token = None
        if getattr(settings, 'ENABLE_TOKEN_ROTATION', True):
            new_refresh_token = rotate_refresh_token(
                refresh_token_string,
                ip_address=get_client_ip(request),
                user_agent=get_user_agent(request)
            )
        
        response_data = {
            'success': True,
            'access_token': access_token,
            'token_type': 'Bearer',
            'expires_in': 900,  # 15 minutes in seconds
        }
        
        # Include new refresh token if rotated
        if new_refresh_token:
            response_data['refresh_token'] = new_refresh_token
        
        return JsonResponse(response_data)
    
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Refresh token error: {str(e)}', exc_info=True)
        
        return JsonResponse({
            'success': False,
            'error': 'An error occurred while refreshing token.'
        }, status=500)


def get_user_from_token(request):
    """Helper function to extract user info from JWT token in request."""
    try:
        from .jwt_utils import verify_access_token
        
        # Get token from Authorization header
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        
        if not auth_header.startswith('Bearer '):
            return None
        
        token = auth_header.split('Bearer ')[1].strip()
        
        # Verify token
        payload = verify_access_token(token)
        
        if not payload:
            return None
        
        # Extract user info from token
        return {
            'user_email': payload.get('user_email'),
            'is_admin': payload.get('is_admin', False),
            'permissions': payload.get('permissions', []),
            'user_type': payload.get('user_type', 'user'),
            'user_id': payload.get('user_id')
        }
    except Exception:
        return None


def check_auth(request):
    """Check if user is authenticated via JWT."""
    try:
        from .jwt_utils import verify_access_token
        
        # Get token from Authorization header
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        
        if not auth_header.startswith('Bearer '):
            return JsonResponse({
                'authenticated': False,
                'user_email': None
            })
        
        token = auth_header.split('Bearer ')[1].strip()
        
        # Verify token
        payload = verify_access_token(token)
        
        if not payload:
            return JsonResponse({
                'authenticated': False,
                'user_email': None
            })
        
        # Extract user info from token
        user_email = payload.get('user_email')
        is_admin = payload.get('is_admin', False)
        permissions = payload.get('permissions', [])
        user_type = payload.get('user_type', 'user')
        user_id = payload.get('user_id')
        user_details = None
        
        # Verify user still exists in database
        if user_type == 'user' and user_id:
            try:
                from .models import User
                user = User.objects.filter(id=user_id).first()
                if not user:
                    # User was deleted - return unauthenticated
                    return JsonResponse({
                        'authenticated': False,
                        'user_email': None,
                        'error': 'User account has been deleted'
                    })
                # Check if user is active
                if not user.is_active:
                    return JsonResponse({
                        'authenticated': False,
                        'user_email': None,
                        'error': 'User account is inactive'
                    })
                # User exists and is active - get user name
                user_name = user.name or user.email
                # Since is_admin and permissions are removed, set defaults
                is_admin = False
                permissions = []
                # Get additional user details for profile
                user_details = {
                    'name': user.name,
                    'email': user.email,
                    'phone': user.phone,
                    'is_active': user.is_active,
                    'created_at': user.created_at.isoformat() if user.created_at else None,
                    'updated_at': user.updated_at.isoformat() if user.updated_at else None,
                }
            except Exception:
                # Error checking user - return unauthenticated for security
                return JsonResponse({
                    'authenticated': False,
                    'user_email': None
                })
        elif user_type == 'company':
            # For company type, verify company still exists
            try:
                from .models import Company
                company = Company.get_company()
                if not company or not company.email:
                    return JsonResponse({
                        'authenticated': False,
                        'user_email': None,
                        'error': 'Company account not found'
                    })
                user_name = "Admin"
                # Get company details for admin profile (company_name for navbar display)
                user_details = {
                    'company_email': company.email,
                    'company_name': company.company_name or company.brand_name or '',
                    'send_email': company.sendemail,
                    'send_number': company.sendnumber,
                    'created_at': company.created_at.isoformat() if company.created_at else None,
                    'updated_at': company.updated_at.isoformat() if company.updated_at else None,
                }
            except Exception:
                return JsonResponse({
                    'authenticated': False,
                    'user_email': None
                })
        else:
            # Unknown user type
            return JsonResponse({
                'authenticated': False,
                'user_email': None
            })
        
        response_data = {
            'authenticated': True,
            'user_email': user_email,
            'is_admin': is_admin,
            'permissions': permissions,
            'user_name': user_name,
            'user_type': user_type
        }
        
        # Add user details if available
        if user_details:
            response_data['user_details'] = user_details
        
        return JsonResponse(response_data)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Check auth error: {str(e)}', exc_info=True)
        
        return JsonResponse({
            'authenticated': False,
            'user_email': None
        })


# ============================================
# CLIENT CRUD OPERATIONS
# ============================================

@csrf_exempt
@require_http_methods(["GET", "POST"])
def list_clients(request):
    """List all clients (GET) or create a new client (POST)."""
    from .models import Client, QuotationSend, Quotation

    if request.method == 'GET':
        # List clients with optional search
        try:
            search_query = request.GET.get('search', '').strip()
            clients = Client.objects.all()

            # Apply search filter if provided
            if search_query:
                # Base filters: search across multiple text fields
                search_filters = (
                    Q(customer_name__icontains=search_query)
                    | Q(company_name__icontains=search_query)
                    | Q(email__icontains=search_query)
                    | Q(phone_number__icontains=search_query)
                    | Q(address__icontains=search_query)
                )

                # If search query is numeric, also search by ID
                try:
                    search_id = int(search_query)
                    search_filters |= Q(id=search_id)
                except ValueError:
                    # Not a number, skip ID search
                    pass

                clients = clients.filter(search_filters)

            # Total unique quotations per customer (same logic as client_quotations)
            from collections import defaultdict
            draft_by_client = defaultdict(set)
            for cid, qid in Quotation.objects.filter(
                quotation_data__client_id__isnull=False
            ).values_list('quotation_data__client_id', 'id'):
                if cid:
                    draft_by_client[cid].add(qid)

            sent_by_email = defaultdict(set)
            for email, qid in QuotationSend.objects.filter(
                recipient_email__isnull=False
            ).values_list('recipient_email', 'quotation_id'):
                sent_by_email[email].add(qid)

            def total_quotations_for_client(client):
                draft_ids = draft_by_client.get(client.id, set())
                sent_ids = sent_by_email.get(client.email, set())
                return len(draft_ids | sent_ids)

            # Build response with created_by and updated_by information
            clients_list = []
            for client in clients:
                created_by_name = "-"  # Default for existing customers without created_by info
                if client.created_by_type == 'company':
                    try:
                        from .models import Company
                        company = Company.get_company()
                        created_by_name = company.company_name or company.brand_name or 'Company'
                    except Exception:
                        created_by_name = 'Company'
                elif client.created_by_type == 'user' and client.created_by_user_id:
                    try:
                        from .models import User
                        creator_user = User.objects.get(id=client.created_by_user_id)
                        created_by_name = creator_user.name or creator_user.email or 'User'
                    except User.DoesNotExist:
                        created_by_name = 'User'
                
                updated_by_name = "-"  # Default for customers that haven't been updated yet
                if client.updated_by_type == 'company':
                    try:
                        from .models import Company
                        company = Company.get_company()
                        updated_by_name = company.company_name or company.brand_name or 'Company'
                    except Exception:
                        updated_by_name = 'Company'
                elif client.updated_by_type == 'user' and client.updated_by_user_id:
                    try:
                        from .models import User
                        updater_user = User.objects.get(id=client.updated_by_user_id)
                        updated_by_name = updater_user.name or updater_user.email or 'User'
                    except User.DoesNotExist:
                        updated_by_name = 'User'
                
                clients_list.append({
                    'id': client.id,
                    'customer_name': client.customer_name,
                    'company_name': client.company_name or '',
                    'phone_number': client.phone_number or '',
                    'email': client.email,
                    'address': client.address or '',
                    'is_active': client.is_active,
                    'quotation_sent_count': total_quotations_for_client(client),
                    'created_by': created_by_name or "-",
                    'created_at': client.created_at.isoformat() if client.created_at else None,
                    'updated_by': updated_by_name or "-",
                    'updated_at': client.updated_at.isoformat() if client.updated_at else None,
                })

            return JsonResponse({
                'clients': clients_list,
                'count': len(clients_list)
            })
        except Exception as e:
            return JsonResponse({
                'error': f'Error fetching clients: {str(e)}'
            }, status=500)
    
    elif request.method == 'POST':
        # Create a new customer
        try:
            print("=" * 50)
            print("CUSTOMER CREATE REQUEST RECEIVED")
            print("=" * 50)
            print(f"Request body: {request.body}")
            
            data = json.loads(request.body)
            print(f"Parsed data: {data}")
            
            customer_name = data.get('customer_name', '').strip()
            company_name = data.get('company_name', '').strip() or None
            phone_number = data.get('phone_number', '').strip() or None
            email = data.get('email', '').strip()
            address = data.get('address', '').strip() or None
            
            print(f"Customer Name: {customer_name}")
            print(f"Email: {email}")
            
            # Validation
            if not customer_name:
                print("ERROR: Customer Name is required")
                return JsonResponse({
                    'error': 'Customer Name is required'
                }, status=400)
            
            if not email:
                print("ERROR: Email is required")
                return JsonResponse({
                    'error': 'Email is required'
                }, status=400)
            
            # Check if email already exists
            if Client.objects.filter(email=email).exists():
                print(f"ERROR: Email {email} already exists")
                return JsonResponse({
                    'error': 'Customer with this email already exists'
                }, status=400)
            
            # Get creator information from token
            creator_info = get_user_from_token(request)
            print(f"Creator info: {creator_info}")
            
            created_by_type = None
            created_by_user_id = None
            
            if creator_info:
                user_type = creator_info.get('user_type', 'user')
                if user_type == 'company':
                    created_by_type = 'company'
                    created_by_user_id = None
                elif user_type == 'user':
                    created_by_type = 'user'
                    created_by_user_id = creator_info.get('user_id')
            
            print(f"Creating customer with: name={customer_name}, email={email}")
            
            # Create customer
            client = Client.objects.create(
                customer_name=customer_name,
                company_name=company_name,
                phone_number=phone_number,
                email=email,
                address=address,
                # New customers are active by default; can be toggled later
                is_active=True,
                created_by_type=created_by_type,
                created_by_user_id=created_by_user_id
            )
            
            print(f"✅ Customer created successfully! ID: {client.id}")
            print(f"Total customers in DB: {Client.objects.count()}")
            print("=" * 50)
            
            return JsonResponse({
                'success': True,
                'client': {
                    'id': client.id,
                    'customer_name': client.customer_name,
                    'company_name': client.company_name or '',
                    'phone_number': client.phone_number or '',
                    'email': client.email,
                    'address': client.address or '',
                    'created_at': client.created_at.isoformat(),
                    'updated_at': client.updated_at.isoformat()
                }
            }, status=201)
        
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON - {str(e)}")
            return JsonResponse({
                'error': 'Invalid JSON in request body'
            }, status=400)
        except Exception as e:
            print(f"ERROR: Exception occurred - {str(e)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({
                'error': f'Error creating client: {str(e)}'
            }, status=500)


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def client_detail(request, client_id):
    """Update (PUT) or delete (DELETE) a client."""
    from .models import Client
    
    # Get client
    try:
        client = Client.objects.get(id=client_id)
    except Client.DoesNotExist:
        return JsonResponse({
            'error': 'Client not found'
        }, status=404)
    
    if request.method == 'DELETE':
        # Delete client
        try:
            client.delete()
            return JsonResponse({
                'success': True,
                'message': 'Client deleted successfully'
            })
        except Exception as e:
            return JsonResponse({
                'error': f'Error deleting client: {str(e)}'
            }, status=500)
    
    elif request.method == 'PUT':
        # Update customer
        try:
            data = json.loads(request.body)
            customer_name = data.get('customer_name', '').strip()
            company_name = data.get('company_name', '').strip() or None
            phone_number = data.get('phone_number', '').strip() or None
            email = data.get('email', '').strip()
            address = data.get('address', '').strip() or None
            
            # Validation
            if not customer_name:
                return JsonResponse({
                    'error': 'Customer Name is required'
                }, status=400)
            
            if not email:
                return JsonResponse({
                    'error': 'Email is required'
                }, status=400)
            
            # Check if email already exists (excluding current client)
            if Client.objects.filter(email=email).exclude(id=client_id).exists():
                return JsonResponse({
                    'error': 'Customer with this email already exists'
                }, status=400)
            
            # Get user info from token to track who updated
            user_info = get_user_from_token(request)
            updated_by_type = None
            updated_by_user_id = None
            if user_info:
                updated_by_type = user_info.get('user_type', 'user')
                updated_by_user_id = user_info.get('user_id')
            
            # Update customer
            client.customer_name = customer_name
            client.company_name = company_name
            client.phone_number = phone_number
            client.email = email
            client.address = address
            # If is_active is provided in payload, update it; otherwise keep current value
            if 'is_active' in data:
                client.is_active = bool(data.get('is_active'))
            # Track who updated
            if updated_by_type:
                client.updated_by_type = updated_by_type
                client.updated_by_user_id = updated_by_user_id
            client.save()
            
            return JsonResponse({
                'success': True,
                'client': {
                    'id': client.id,
                    'customer_name': client.customer_name,
                    'company_name': client.company_name or '',
                    'phone_number': client.phone_number or '',
                    'email': client.email,
                    'address': client.address or '',
                    'created_at': client.created_at.isoformat(),
                    'updated_at': client.updated_at.isoformat()
                }
            })
        
        except json.JSONDecodeError:
            return JsonResponse({
                'error': 'Invalid JSON in request body'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'error': f'Error updating client: {str(e)}'
            }, status=500)


def _get_creator_name(user_type, user_id):
    """Return display name for who created/sent the quotation: company name or user name."""
    from .models import Company, User
    if user_type == 'company' or user_id is None:
        try:
            company = Company.get_company()
            return company.company_name or company.brand_name or 'Company'
        except Exception:
            return 'Company'
    try:
        uid = int(user_id) if user_id is not None else None
        if uid is None:
            company = Company.get_company()
            return company.company_name or company.brand_name or 'Company'
        user = User.objects.get(id=uid)
        return user.name or user.email or 'User'
    except (User.DoesNotExist, ValueError, TypeError):
        return 'User'


@csrf_exempt
@require_http_methods(["GET", "POST"])
def client_quotations(request, client_id):
    """Get all quotations for a client (sent + drafts). POST creates a draft quotation for this client."""
    from .models import Client, QuotationSend, Quotation, User, Company

    # Get client
    try:
        client = Client.objects.get(id=client_id)
    except Client.DoesNotExist:
        return JsonResponse({
            'error': 'Client not found'
        }, status=404)

    if request.method == 'POST':
        # Create draft quotation for this client; record who created it (company or user)
        try:
            user_info = get_user_from_token(request)
            raw_uid = (user_info or {}).get('user_id')
            try:
                created_by_user_id = int(raw_uid) if raw_uid is not None else None
            except (TypeError, ValueError):
                created_by_user_id = None
            user_type = (user_info or {}).get('user_type', 'company')
            # If token has user_id, creator is that user
            if created_by_user_id is not None:
                user_type = 'user'
            # Fallback: token has user_email but no user_id - resolve user by email so user name shows correctly
            elif user_info and (user_info.get('user_email') or '').strip():
                try:
                    u = User.objects.get(email__iexact=(user_info.get('user_email') or '').strip())
                    created_by_user_id = u.id
                    user_type = 'user'
                except User.DoesNotExist:
                    pass
            quotation_data = {
                'status': 'draft',
                'grand_total': 0,
                'subtotal': 0,
                'client_id': client_id,
                'created_by_type': user_type,
                'created_by_user_id': created_by_user_id,
                'quotation_to': {
                    'name': client.customer_name,
                    'email': client.email,
                    'address': client.address or '',
                    'phone': client.phone_number or '',
                },
                'services': [],
            }
            quotation = Quotation.objects.create(quotation_data=quotation_data)
            user_name = _get_creator_name(user_type, created_by_user_id)
            return JsonResponse({
                'success': True,
                'quotation': {
                    'id': quotation.id,
                    'quotation_number': quotation.quotation_number,
                    'amount': 0,
                    'status': 'draft',
                    'sent_at': None,
                    'send_type': None,
                    'user_name': user_name,
                }
            })
        except Exception as e:
            return JsonResponse({
                'error': f'Error creating draft quotation: {str(e)}'
            }, status=500)

    # GET: list sent quotations + draft quotations for this client; include creator name (company or user)
    try:
        quotations_list = []
        seen_ids = set()

        # 1) Draft quotations linked to this client (quotation_data.client_id)
        draft_quotations = Quotation.objects.filter(
            quotation_data__client_id=client_id
        ).order_by('-created_at')
        for quotation in draft_quotations:
            seen_ids.add(quotation.id)
            qdata = quotation.quotation_data or {}
            created_by_type = qdata.get('created_by_type', 'company')
            created_by_user_id = qdata.get('created_by_user_id')
            user_name = _get_creator_name(created_by_type, created_by_user_id)
            
            # Get updated_by information
            updated_by_type = qdata.get('updated_by_type', created_by_type)
            updated_by_user_id = qdata.get('updated_by_user_id', created_by_user_id)
            updated_by_name = _get_creator_name(updated_by_type, updated_by_user_id)
            
            quotations_list.append({
                'id': quotation.id,
                'quotation_number': quotation.quotation_number,
                'amount': qdata.get('grand_total', 0),
                'status': qdata.get('status', 'draft'),
                'sent_at': quotation.created_at.isoformat() if quotation.created_at else None,
                'send_type': None,
                'user_name': user_name,
                'created_by': user_name,
                'created_at': quotation.created_at.isoformat() if quotation.created_at else None,
                'updated_by': updated_by_name,
                'updated_at': quotation.updated_at.isoformat() if quotation.updated_at else None,
            })

        # 2) Quotations sent to this client (by email); user_id on send = who sent it (None = company)
        quotation_sends = QuotationSend.objects.filter(
            recipient_email=client.email
        ).select_related('quotation').order_by('-sent_at')
        for send in quotation_sends:
            if send.quotation_id in seen_ids:
                continue
            seen_ids.add(send.quotation_id)
            quotation = send.quotation
            quotation_data = quotation.quotation_data or {}
            grand_total = quotation_data.get('grand_total', 0)
            status = quotation_data.get('status', 'submitted')
            user_name = _get_creator_name('company' if send.user_id is None else 'user', send.user_id)
            
            # Get updated_by information from quotation_data, fallback to creator
            updated_by_type = quotation_data.get('updated_by_type', 'company' if send.user_id is None else 'user')
            updated_by_user_id = quotation_data.get('updated_by_user_id', send.user_id)
            updated_by_name = _get_creator_name(updated_by_type, updated_by_user_id)
            
            quotations_list.append({
                'id': quotation.id,
                'quotation_number': quotation.quotation_number,
                'amount': grand_total,
                'status': status,
                'sent_at': send.sent_at.isoformat(),
                'send_type': send.send_type,
                'user_name': user_name,
                'created_by': user_name,
                'created_at': quotation.created_at.isoformat() if quotation.created_at else None,
                'updated_by': updated_by_name,
                'updated_at': quotation.updated_at.isoformat() if quotation.updated_at else None,
            })

        # Sort: most recent first (use sent_at / created_at)
        quotations_list.sort(
            key=lambda x: x.get('sent_at') or '',
            reverse=True
        )

        return JsonResponse({
            'success': True,
            'client': {
                'id': client.id,
                'customer_name': client.customer_name,
                'email': client.email,
            },
            'quotations': quotations_list,
            'count': len(quotations_list)
        })
    except Exception as e:
        return JsonResponse({
            'error': f'Error fetching client quotations: {str(e)}'
        }, status=500)


# ============================================
# USER CRUD OPERATIONS
# ============================================

@csrf_exempt
@require_http_methods(["GET", "POST"])
def list_users(request):
    """List all users (GET) or create a new user (POST)."""
    from .models import User
    
    if request.method == 'GET':
        # List users with optional search
        try:
            search_query = request.GET.get('search', '').strip()
            users = User.objects.all()
            
            # Apply search filter if provided
            if search_query:
                users = users.filter(
                    Q(email__icontains=search_query) | 
                    Q(name__icontains=search_query) |
                    Q(phone__icontains=search_query) |
                    Q(username__icontains=search_query)
                )
            
            # Build response with created_by information
            users_list = []
            for user in users:
                created_by_name = "-"  # Default for existing users without created_by info
                if user.created_by_type == 'company':
                    try:
                        from .models import Company
                        company = Company.get_company()
                        created_by_name = company.company_name or company.brand_name or 'Company'
                    except Exception:
                        created_by_name = 'Company'
                elif user.created_by_type == 'user' and user.created_by_user_id:
                    try:
                        creator_user = User.objects.get(id=user.created_by_user_id)
                        created_by_name = creator_user.name or creator_user.email or 'User'
                    except User.DoesNotExist:
                        created_by_name = 'User'
                
                users_list.append({
                    'id': user.id,
                    'name': user.name,
                    'email': user.email,
                    'phone': user.phone,
                    'active': user.is_active,
                    'created_by': created_by_name or "-",
                })
            
            return JsonResponse({
                'users': users_list,
                'count': len(users_list)
            })
        except Exception as e:
            return JsonResponse({
                'error': f'Error fetching users: {str(e)}'
            }, status=500)
    
    elif request.method == 'POST':
        # Create a new user
        try:
            data = json.loads(request.body)
            email = data.get('email', '').strip()
            username = data.get('username', '').strip() or None
            password = data.get('password', '').strip()
            name = data.get('name', '').strip()
            phone = data.get('phone', '').strip()
            is_active = data.get('is_active', True)
            
            # Validation
            if not email:
                return JsonResponse({
                    'success': False,
                    'error': 'Email is required'
                }, status=400)
            
            if not password:
                return JsonResponse({
                    'success': False,
                    'error': 'Password is required'
                }, status=400)
            
            if len(password) < 6:
                return JsonResponse({
                    'success': False,
                    'error': 'Password must be at least 6 characters long'
                }, status=400)
            
            if not name:
                return JsonResponse({
                    'success': False,
                    'error': 'Name is required'
                }, status=400)
            
            if not phone:
                return JsonResponse({
                    'success': False,
                    'error': 'Phone is required'
                }, status=400)
            
            # Check if email already exists
            if User.objects.filter(email=email).exists():
                return JsonResponse({
                    'success': False,
                    'error': 'User with this email already exists'
                }, status=400)
            
            # Check if username already exists (if provided)
            if username and User.objects.filter(username=username).exists():
                return JsonResponse({
                    'success': False,
                    'error': 'User with this username already exists'
                }, status=400)
            
            # Get creator information from token
            creator_info = get_user_from_token(request)
            created_by_type = None
            created_by_user_id = None
            
            if creator_info:
                user_type = creator_info.get('user_type', 'user')
                if user_type == 'company':
                    created_by_type = 'company'
                    created_by_user_id = None
                elif user_type == 'user':
                    created_by_type = 'user'
                    created_by_user_id = creator_info.get('user_id')
            
            # Create user with hashed password
            user = User(
                email=email,
                username=username,
                name=name,
                phone=phone,
                is_active=is_active,
                created_by_type=created_by_type,
                created_by_user_id=created_by_user_id
            )
            # Explicitly hash the password using set_password
            user.set_password(password)
            user.save()
            
            return JsonResponse({
                'success': True,
                'message': 'User created successfully',
                'user': {
                    'id': user.id,
                    'name': user.name,
                    'email': user.email,
                    'phone': user.phone,
                    'active': user.is_active,
                }
            }, status=201)
        
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON in request body'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error creating user: {str(e)}'
            }, status=500)


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def user_detail(request, user_id):
    """Update (PUT) or delete (DELETE) a user."""
    from .models import User
    
    # Get user
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({
            'error': 'User not found'
        }, status=404)
    
    if request.method == 'DELETE':
        # Delete user
        try:
            user.delete()
            return JsonResponse({
                'success': True,
                'message': 'User deleted successfully'
            })
        except Exception as e:
            return JsonResponse({
                'error': f'Error deleting user: {str(e)}'
            }, status=500)
    
    elif request.method == 'PUT':
        # Update user
        try:
            data = json.loads(request.body)
            email = data.get('email', '').strip()
            username = data.get('username', '').strip() or None
            name = data.get('name', '').strip()
            phone = data.get('phone', '').strip()
            password = data.get('password', '').strip()
            is_active = data.get('is_active', True)
            
            # Get user info from token to track who updated
            user_info = get_user_from_token(request)
            updated_by_type = None
            updated_by_user_id = None
            if user_info:
                updated_by_type = user_info.get('user_type', 'user')
                updated_by_user_id = user_info.get('user_id')
            
            # Validation
            if not email:
                return JsonResponse({
                    'error': 'Email is required'
                }, status=400)
            
            if not name:
                return JsonResponse({
                    'error': 'Name is required'
                }, status=400)
            
            if not phone:
                return JsonResponse({
                    'error': 'Phone is required'
                }, status=400)
            
            # Check if email already exists (excluding current user)
            if User.objects.filter(email=email).exclude(id=user_id).exists():
                return JsonResponse({
                    'error': 'User with this email already exists'
                }, status=400)
            
            # Check if username already exists (if provided, excluding current user)
            if username and User.objects.filter(username=username).exclude(id=user_id).exists():
                return JsonResponse({
                    'error': 'User with this username already exists'
                }, status=400)
            
            # Update user
            user.email = email
            user.username = username
            user.name = name
            user.phone = phone
            user.is_active = is_active
            # Track who updated
            if updated_by_type:
                user.updated_by_type = updated_by_type
                user.updated_by_user_id = updated_by_user_id
            
            # Update password if provided
            if password:
                if len(password) < 6:
                    return JsonResponse({
                        'success': False,
                        'error': 'Password must be at least 6 characters long'
                    }, status=400)
                # Explicitly hash the password using set_password
                user.set_password(password)
            
            user.save()
            
            # Force refresh from database to ensure changes are saved
            user.refresh_from_db()
            
            return JsonResponse({
                'success': True,
                'message': 'User updated successfully',
                'user': {
                    'id': user.id,
                    'name': user.name,
                    'username': user.username or "",
                    'email': user.email,
                    'phone': user.phone,
                    'active': user.is_active,
                }
            })
        
        except json.JSONDecodeError:
            return JsonResponse({
                'error': 'Invalid JSON in request body'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'error': f'Error updating user: {str(e)}'
            }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def reset_user_password(request, user_id):
    """Reset password for a user."""
    from .models import User
    
    # Get user
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({
            'error': 'User not found'
        }, status=404)
    
    try:
        data = json.loads(request.body)
        new_password = data.get('new_password', '').strip()
        confirm_password = data.get('confirm_password', '').strip()
        
        # Validation
        if not new_password:
            return JsonResponse({
                'error': 'New password is required'
            }, status=400)
        
        if len(new_password) < 6:
            return JsonResponse({
                'error': 'Password must be at least 6 characters long'
            }, status=400)
        
        # Check if passwords match
        if new_password != confirm_password:
            return JsonResponse({
                'error': 'Passwords do not match'
            }, status=400)
        
        # Reset password (will be hashed in save() method)
        user.set_password(new_password)
        user.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Password reset successfully'
        })
    
    except json.JSONDecodeError:
        return JsonResponse({
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': f'Error resetting password: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_all_users(request):
    """Get all users with pagination, search, and active filter."""
    from .models import User
    
    try:
        # Get query parameters
        page = int(request.GET.get('page', 1))
        limit = int(request.GET.get('limit', 10))
        search = request.GET.get('search', '').strip()
        is_active = request.GET.get('isActive', '').strip()
        
        # Build query
        users = User.objects.all()
        
        # Apply search filter
        if search:
            users = users.filter(
                Q(email__icontains=search) | 
                Q(name__icontains=search) |
                Q(phone__icontains=search) |
                Q(username__icontains=search)
            )
        
        # Apply active filter
        if is_active.lower() == 'true':
            users = users.filter(is_active=True)
        elif is_active.lower() == 'false':
            users = users.filter(is_active=False)
        
        # Get total count
        total_count = users.count()
        
        # Apply pagination
        offset = (page - 1) * limit
        users = users[offset:offset + limit]
        
        # Build response with created_by and updated_by information
        users_list = []
        for user in users:
            created_by_name = "-"  # Default for existing users without created_by info
            if user.created_by_type == 'company':
                try:
                    from .models import Company
                    company = Company.get_company()
                    created_by_name = company.company_name or company.brand_name or 'Company'
                except Exception:
                    created_by_name = 'Company'
            elif user.created_by_type == 'user' and user.created_by_user_id:
                try:
                    creator_user = User.objects.get(id=user.created_by_user_id)
                    created_by_name = creator_user.name or creator_user.email or 'User'
                except User.DoesNotExist:
                    created_by_name = 'User'
            
            updated_by_name = "-"  # Default for users that haven't been updated yet
            if user.updated_by_type == 'company':
                try:
                    from .models import Company
                    company = Company.get_company()
                    updated_by_name = company.company_name or company.brand_name or 'Company'
                except Exception:
                    updated_by_name = 'Company'
            elif user.updated_by_type == 'user' and user.updated_by_user_id:
                try:
                    updater_user = User.objects.get(id=user.updated_by_user_id)
                    updated_by_name = updater_user.name or updater_user.email or 'User'
                except User.DoesNotExist:
                    updated_by_name = 'User'
            
            users_list.append({
                'id': user.id,
                'name': user.name,
                'username': user.username or "",
                'email': user.email,
                'phone': user.phone,
                'active': user.is_active,
                'created_by': created_by_name or "-",
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'updated_by': updated_by_name or "-",
                'updated_at': user.updated_at.isoformat() if user.updated_at else None,
            })
        
        return JsonResponse({
            'success': True,
            'data': users_list,
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total_count,
                'pages': (total_count + limit - 1) // limit if limit > 0 else 0
            }
        })
    except Exception as e:
        return JsonResponse({
            'error': f'Error fetching users: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_user_by_id(request, user_id):
    """Get user by ID."""
    from .models import User
    
    try:
        user = User.objects.get(id=user_id)
        return JsonResponse({
            'success': True,
            'data': {
                'id': user.id,
                'name': user.name,
                'email': user.email,
                'phone': user.phone,
                'active': user.is_active,
            }
        })
    except User.DoesNotExist:
        return JsonResponse({
            'error': 'User not found'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'error': f'Error fetching user: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["PUT"])
def update_user_status(request, user_id):
    """Toggle user active status."""
    from .models import User
    
    try:
        user = User.objects.get(id=user_id)
        
        # Get user info from token to track who updated
        user_info = get_user_from_token(request)
        updated_by_type = None
        updated_by_user_id = None
        if user_info:
            updated_by_type = user_info.get('user_type', 'user')
            updated_by_user_id = user_info.get('user_id')
        
        user.is_active = not user.is_active
        # Track who updated
        if updated_by_type:
            user.updated_by_type = updated_by_type
            user.updated_by_user_id = updated_by_user_id
        user.save()
        
        return JsonResponse({
            'success': True,
            'message': f'User {"activated" if user.is_active else "deactivated"} successfully',
            'data': {
                'id': user.id,
                'name': user.name,
                'email': user.email,
                'phone': user.phone,
                'active': user.is_active,
            }
        })
    except User.DoesNotExist:
        return JsonResponse({
            'error': 'User not found'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'error': f'Error updating user status: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_active_departments(request):
    """Get active departments (placeholder - returns empty array for now)."""
    try:
        # This is a placeholder endpoint for departments
        # Can be implemented later if needed
        return JsonResponse({
            'success': True,
            'data': []
        })
    except Exception as e:
        return JsonResponse({
            'error': f'Error fetching departments: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def send_quotation_email(request):
    """Send quotation PDF via email using SMTP."""
    try:
        from .models import Company
        
        # Parse request data
        data = json.loads(request.body)
        recipient_email = data.get('recipient_email', '').strip()
        recipient_phone = data.get('recipient_phone', '').strip()
        customer_name = data.get('customer_name', '').strip()
        pdf_base64 = data.get('pdf_base64', '').strip()
        pdf_filename = data.get('pdf_filename', 'quotation.pdf')
        quotation_id = data.get('quotation_id')
        
        # Validation
        if not recipient_email:
            return JsonResponse({
                'error': 'Recipient email is required'
            }, status=400)
        
        if not pdf_base64:
            return JsonResponse({
                'error': 'PDF data is required'
            }, status=400)
        
        # Get company email credentials
        company = Company.get_company()
        
        if not company.sendemail or not company.sendpassword:
            return JsonResponse({
                'error': 'Email credentials not configured. Please configure sendemail and sendpassword in admin panel.'
            }, status=400)
        
        # Decode PDF from base64
        try:
            pdf_data = base64.b64decode(pdf_base64)
        except Exception as e:
            return JsonResponse({
                'error': f'Invalid PDF data: {str(e)}'
            }, status=400)
        
        # Extract email domain to determine SMTP server
        email_domain = company.sendemail.split('@')[1].lower()
        
        # Common SMTP server configurations
        smtp_configs = {
            'gmail.com': {
                'host': 'smtp.gmail.com',
                'port': 587,
                'use_tls': True
            },
            'outlook.com': {
                'host': 'smtp-mail.outlook.com',
                'port': 587,
                'use_tls': True
            },
            'hotmail.com': {
                'host': 'smtp-mail.outlook.com',
                'port': 587,
                'use_tls': True
            },
            'yahoo.com': {
                'host': 'smtp.mail.yahoo.com',
                'port': 587,
                'use_tls': True
            }
        }
        
        # Default SMTP config (Gmail-like)
        smtp_config = smtp_configs.get(email_domain, {
            'host': 'smtp.gmail.com',
            'port': 587,
            'use_tls': True
        })
        
        # Create email message
        msg = MIMEMultipart()
        msg['From'] = company.sendemail
        msg['To'] = recipient_email
        msg['Subject'] = f'Quotation for {customer_name or "Customer"}'
        
        # Email body (using company name from database)
        company_name = company.company_name or 'MAKLOGISTICS'
        
        body = f"""Dear {customer_name or 'Customer'},

Please find the quotation attached to this email.

If you have any questions or need further assistance, please don't hesitate to contact us.

Best regards,
{company_name}
"""
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach PDF
        attachment = MIMEBase('application', 'pdf')
        attachment.set_payload(pdf_data)
        encoders.encode_base64(attachment)
        attachment.add_header(
            'Content-Disposition',
            f'attachment; filename= {pdf_filename}'
        )
        msg.attach(attachment)
        
        # Send email via SMTP
        try:
            server = smtplib.SMTP(smtp_config['host'], smtp_config['port'])
            if smtp_config['use_tls']:
                server.starttls()
            server.login(company.sendemail, company.sendpassword)
            server.send_message(msg)
            server.quit()
            
            # Track email send and update status to submitted
            try:
                from .models import Quotation, QuotationSend
                user_info = get_user_from_token(request)
                user_id = None
                if user_info and user_info.get('user_type') == 'user' and user_info.get('user_id'):
                    user_id = user_info.get('user_id')
                if quotation_id:
                    quotation_obj = Quotation.objects.get(id=quotation_id)
                    qdata = quotation_obj.quotation_data or {}
                    qdata['status'] = 'submitted'
                    
                    # Track who updated this quotation
                    if user_info:
                        updated_by_type = user_info.get('user_type', 'company')
                        updated_by_user_id = user_info.get('user_id')
                        if updated_by_type == 'user' and updated_by_user_id:
                            qdata['updated_by_type'] = 'user'
                            qdata['updated_by_user_id'] = updated_by_user_id
                        else:
                            qdata['updated_by_type'] = 'company'
                            qdata['updated_by_user_id'] = None
                    
                    quotation_obj.quotation_data = qdata
                    quotation_obj.save()
                    QuotationSend.objects.create(
                        quotation=quotation_obj,
                        send_type='email',
                        recipient_email=recipient_email,
                        user_id=user_id,
                        sent_at=timezone.now()
                    )
                else:
                    session_keys = get_session_keys_for_request(request)
                    migrate_legacy_session_for_request(request, session_keys)
                    quotation_data = request.session.get(session_keys['quotation'], None)
                    if quotation_data:
                        quotation_data_clean = quotation_data.copy()
                        quotation_data_clean['status'] = 'submitted'
                        quotation_number = quotation_data_clean.get('quotation_number') or None
                        quotation = Quotation.objects.create(
                            quotation_number=quotation_number,
                            quotation_data=quotation_data_clean
                        )
                        QuotationSend.objects.create(
                            quotation=quotation,
                            send_type='email',
                            recipient_email=recipient_email,
                            user_id=user_id,
                            sent_at=timezone.now()
                        )
            except Exception as track_error:
                # Log but don't fail the email send if tracking fails
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f'Failed to track email send: {str(track_error)}')
            
            return JsonResponse({
                'success': True,
                'message': f'Quotation sent successfully to {recipient_email}'
            })
        except smtplib.SMTPAuthenticationError:
            return JsonResponse({
                'error': 'Email authentication failed. Please check your email and password in admin panel.'
            }, status=401)
        except smtplib.SMTPException as e:
            return JsonResponse({
                'error': f'SMTP error: {str(e)}'
            }, status=500)
        except Exception as e:
            return JsonResponse({
                'error': f'Error sending email: {str(e)}'
            }, status=500)
    
    except json.JSONDecodeError:
        return JsonResponse({
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Email send error: {str(e)}', exc_info=True)
        
        return JsonResponse({
            'error': f'Error sending email: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def send_quotation_whatsapp(request):
    """Create QuotationSend record for WhatsApp, update status to submitted, return wa.me link."""
    try:
        from .models import Quotation, QuotationSend, Company
        data = json.loads(request.body)
        quotation_id = data.get('quotation_id')
        import re
        recipient_phone = re.sub(r'\D', '', (data.get('recipient_phone') or '').strip())
        if len(recipient_phone) > 10:
            recipient_phone = recipient_phone[-10:]
        customer_name = (data.get('customer_name') or '').strip()
        if not quotation_id:
            return JsonResponse({'error': 'Quotation ID is required'}, status=400)
        quotation_obj = Quotation.objects.get(id=quotation_id)
        user_info = get_user_from_token(request)
        user_id = None
        if user_info and user_info.get('user_type') == 'user' and user_info.get('user_id'):
            user_id = user_info.get('user_id')
        qdata = quotation_obj.quotation_data or {}
        qdata['status'] = 'submitted'
        
        # Track who updated this quotation
        if user_info:
            updated_by_type = user_info.get('user_type', 'company')
            updated_by_user_id = user_info.get('user_id')
            if updated_by_type == 'user' and updated_by_user_id:
                qdata['updated_by_type'] = 'user'
                qdata['updated_by_user_id'] = updated_by_user_id
            else:
                qdata['updated_by_type'] = 'company'
                qdata['updated_by_user_id'] = None
        
        quotation_obj.quotation_data = qdata
        quotation_obj.save()
        phone_for_wa = recipient_phone
        if not phone_for_wa.startswith('91') and len(phone_for_wa) == 10:
            phone_for_wa = '91' + phone_for_wa
        QuotationSend.objects.create(
            quotation=quotation_obj,
            send_type='whatsapp',
            recipient_phone=recipient_phone or None,
            user_id=user_id,
            sent_at=timezone.now()
        )
        wa_link = f"https://wa.me/{phone_for_wa}" if phone_for_wa else None
        return JsonResponse({
            'success': True,
            'message': 'Status updated to Submitted. Open WhatsApp to share.',
            'wa_link': wa_link
        })
    except Quotation.DoesNotExist:
        return JsonResponse({'error': 'Quotation not found'}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["PATCH", "PUT"])
def update_quotation_status(request, quotation_id):
    """Update quotation status (e.g. Submitted → Awarded)."""
    try:
        from .models import Quotation
        data = json.loads(request.body)
        new_status = (data.get('status') or '').strip().lower()
        if new_status not in ('draft', 'submitted', 'awarded'):
            return JsonResponse({'error': 'Invalid status'}, status=400)
        quotation_obj = Quotation.objects.get(id=quotation_id)
        qdata = quotation_obj.quotation_data or {}
        qdata['status'] = new_status
        
        # Track who updated this quotation
        user_info = get_user_from_token(request)
        if user_info:
            updated_by_type = user_info.get('user_type', 'company')
            updated_by_user_id = user_info.get('user_id')
            if updated_by_type == 'user' and updated_by_user_id:
                qdata['updated_by_type'] = 'user'
                qdata['updated_by_user_id'] = updated_by_user_id
            else:
                qdata['updated_by_type'] = 'company'
                qdata['updated_by_user_id'] = None
        
        quotation_obj.quotation_data = qdata
        quotation_obj.save()
        return JsonResponse({'success': True, 'status': new_status})
    except Quotation.DoesNotExist:
        return JsonResponse({'error': 'Quotation not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def dashboard_stats(request):
    """Get dashboard statistics including KPIs and chart data."""
    try:
        from .models import Quotation, Client, User, QuotationSend
        
        # Get user info from token to filter by user
        user_info = get_user_from_token(request)
        user_id = None
        is_admin = False
        user_type = 'user'
        if user_info:
            user_id = user_info.get('user_id')
            is_admin = user_info.get('is_admin', False)
            user_type = user_info.get('user_type', 'user')
        
        # Filter logic:
        # - Company email login (user_type == 'company') → See ALL sends (from all users and admin)
        # - User with full access (user_type == 'user' and is_admin == True) → See ALL sends (from all users and admin)
        # - Regular user (user_type == 'user' and is_admin == False) → See only their sends (user_id = their_id)
        
        # Get current year for filtering
        current_year = timezone.now().year
        year_param = request.GET.get('year', str(current_year))
        try:
            year = int(year_param)
        except ValueError:
            year = current_year
        
        # Check for week parameter (date string in format YYYY-MM-DD)
        week_date_param = request.GET.get('week_date', None)
        week_data = None
        if week_date_param:
            try:
                from datetime import datetime, timedelta
                week_date = datetime.strptime(week_date_param, '%Y-%m-%d').date()
                # Calculate Sunday of that week
                # weekday() returns Monday=0, Tuesday=1, ..., Sunday=6
                day_of_week = week_date.weekday()
                # If Sunday (6), no change needed. Otherwise, go back to Sunday
                if day_of_week == 6:  # Sunday
                    week_start = week_date
                else:
                    # Go back (day_of_week + 1) days to reach Sunday
                    # Monday (0) -> back 1 day, Tuesday (1) -> back 2 days, etc.
                    week_start = week_date - timedelta(days=day_of_week + 1)
                
                week_end = week_start + timedelta(days=6)  # Saturday
                
                # Get daily quotation counts for the week (Sunday to Saturday)
                daily_data = []
                weekdays = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
                
                for i in range(7):
                    current_day = week_start + timedelta(days=i)
                    day_quotations = Quotation.objects.filter(
                        created_at__date=current_day
                    )
                    day_count = day_quotations.count()
                    
                    daily_data.append({
                        'day': weekdays[i],
                        'date': current_day.strftime('%Y-%m-%d'),
                        'total': day_count
                    })
                
                week_data = daily_data
            except (ValueError, TypeError):
                week_data = None
        
        # Base queryset for QuotationSend - filter based on user type
        quotation_send_filter = {}
        if user_type == 'company':
            # Company admin sees ONLY admin sends (user_id IS NULL) - not user sends
            quotation_send_filter['user_id__isnull'] = True
        elif user_type == 'user' and is_admin:
            # User with full access sees ALL sends (from all users and admin)
            # No filter - show everything (they have admin-like permissions)
            quotation_send_filter = {}
        elif user_type == 'user' and user_id:
            # Regular users see only their sends
            quotation_send_filter['user_id'] = user_id
        
        # KPI Cards - GLOBAL/COMPANY-BASED (all users see same totals)
        # Always show ALL quotations regardless of user type - global company totals
        base_quotations = Quotation.objects.all()
        total_customers = Client.objects.count()
        active_customers = Client.objects.filter(is_active=True).count()
        inactive_customers = Client.objects.filter(is_active=False).count()
        total_users = User.objects.filter(is_active=True).count()
        
        # Calculate KPIs from base_quotations
        total_quotations = base_quotations.count()
        
        # Calculate Total Draft (count of quotations with status='draft')
        total_draft = 0
        for quotation in base_quotations:
            quotation_data = quotation.quotation_data or {}
            status = quotation_data.get('status', 'draft').lower()
            if status == 'draft':
                total_draft += 1
        
        # Calculate Total Submitted Value (sum of grand_total for submitted quotations)
        total_submitted_value = 0
        for quotation in base_quotations:
            quotation_data = quotation.quotation_data or {}
            status = quotation_data.get('status', 'draft').lower()
            if status == 'submitted':
                grand_total = quotation.get_grand_total()
                try:
                    total_submitted_value += float(grand_total) if grand_total else 0
                except (ValueError, TypeError):
                    pass
        
        # Calculate Total Awarded Value (sum of grand_total for awarded quotations)
        total_awarded_value = 0
        for quotation in base_quotations:
            quotation_data = quotation.quotation_data or {}
            status = quotation_data.get('status', 'draft').lower()
            if status == 'awarded':
                grand_total = quotation.get_grand_total()
                try:
                    total_awarded_value += float(grand_total) if grand_total else 0
                except (ValueError, TypeError):
                    pass
        
        # Monthly Quotation CREATED counts data (for bar chart)
        # Count quotations CREATED (not sent) per month - includes drafts and all statuses
        # Filter by user: company sees all, admin sees all, regular user sees all (no created_by on Quotation)
        months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
        monthly_data = []
        
        for month_num in range(1, 13):
            # Count quotations CREATED in this month
            month_quotations = Quotation.objects.filter(
                created_at__year=year,
                created_at__month=month_num
            )
            total_created = month_quotations.count()
            
            # Also get sends for this month (for email/whatsapp breakdown if needed)
            month_sends_all = QuotationSend.objects.filter(
                sent_at__year=year,
                sent_at__month=month_num,
                **quotation_send_filter
            )
            email_count = month_sends_all.filter(send_type='email').count()
            whatsapp_count = month_sends_all.filter(send_type='whatsapp').count()
            
            monthly_data.append({
                'month': months[month_num - 1],
                'email': email_count,
                'whatsapp': whatsapp_count,
                'total': total_created  # Total quotations CREATED in this month
            })
        
        # Total Email vs WhatsApp breakdown (for pie chart) - filter by user if not admin
        email_sends = QuotationSend.objects.filter(
            send_type='email',
            **quotation_send_filter
        ).select_related('quotation')
        whatsapp_sends = QuotationSend.objects.filter(
            send_type='whatsapp',
            **quotation_send_filter
        ).select_related('quotation')
        
        total_email_sends = email_sends.count()
        total_whatsapp_sends = whatsapp_sends.count()
        total_sends = total_email_sends + total_whatsapp_sends
        
        # Calculate grand total amounts for email and whatsapp sends
        email_grand_total = 0
        for send in email_sends:
            try:
                grand_total = send.quotation.get_grand_total()
                if grand_total:
                    email_grand_total += float(grand_total)
            except (AttributeError, ValueError, TypeError):
                pass
        
        whatsapp_grand_total = 0
        for send in whatsapp_sends:
            try:
                grand_total = send.quotation.get_grand_total()
                if grand_total:
                    whatsapp_grand_total += float(grand_total)
            except (AttributeError, ValueError, TypeError):
                pass
        
        total_grand_total = email_grand_total + whatsapp_grand_total
        
        # Calculate percentages (for pie chart visualization)
        email_percentage = round((total_email_sends / total_sends * 100) if total_sends > 0 else 0, 1)
        whatsapp_percentage = round((total_whatsapp_sends / total_sends * 100) if total_sends > 0 else 0, 1)
        
        # Status breakdown for pie chart (Draft, Submitted, Awarded) by month/year
        month_param = request.GET.get('month', None)
        status_breakdown = {
            'draft': 0,
            'submitted': 0,
            'awarded': 0
        }
        
        if month_param:
            try:
                month_num = int(month_param)
                if 1 <= month_num <= 12:
                    # Filter quotations by month and year
                    month_quotations = Quotation.objects.filter(
                        created_at__year=year,
                        created_at__month=month_num
                    )
                    
                    # Count by status from quotation_data
                    for quotation in month_quotations:
                        quotation_data = quotation.quotation_data or {}
                        status = quotation_data.get('status', 'draft').lower()
                        if status in status_breakdown:
                            status_breakdown[status] += 1
                        else:
                            # Default to draft if status is invalid
                            status_breakdown['draft'] += 1
            except ValueError:
                pass
        # If month not provided, status_breakdown remains with all zeros
        
        # Get customer list with quotation counts and user breakdown
        # Customer card is UNIVERSAL - show ALL quotations sent to each customer (from all users)
        customers_list = []
        for client in Client.objects.all().order_by('-created_at')[:10]:  # Get latest 10 customers
            # Count ALL quotations sent to this customer (by email) - UNIVERSAL count (not filtered)
            all_quotation_sends = QuotationSend.objects.filter(
                recipient_email__iexact=client.email,
                send_type='email'
            )
            quotation_count = all_quotation_sends.count()  # Universal count - all users' sends
            
            # Calculate submitted quotation total for this customer
            # Get all quotations linked to this customer (via client_id or recipient_email)
            submitted_total = 0
            processed_quotation_ids = set()
            
            # Method 1: Quotations with client_id matching this customer
            quotations_by_client_id = Quotation.objects.filter(
                quotation_data__client_id=client.id
            )
            for quotation in quotations_by_client_id:
                quotation_data = quotation.quotation_data or {}
                status = quotation_data.get('status', 'draft').lower()
                if status == 'submitted':
                    grand_total = quotation.get_grand_total()
                    try:
                        submitted_total += float(grand_total) if grand_total else 0
                        processed_quotation_ids.add(quotation.id)
                    except (ValueError, TypeError):
                        pass
            
            # Method 2: Quotations sent to this customer's email (via QuotationSend)
            quotations_by_email = Quotation.objects.filter(
                sends__recipient_email__iexact=client.email
            ).distinct()
            for quotation in quotations_by_email:
                # Skip if already counted via client_id
                if quotation.id in processed_quotation_ids:
                    continue
                quotation_data = quotation.quotation_data or {}
                status = quotation_data.get('status', 'draft').lower()
                if status == 'submitted':
                    grand_total = quotation.get_grand_total()
                    try:
                        submitted_total += float(grand_total) if grand_total else 0
                        processed_quotation_ids.add(quotation.id)
                    except (ValueError, TypeError):
                        pass
            
            # Get user breakdown for this customer (who sent how many)
            # Customer card is UNIVERSAL - show breakdown by ALL users for everyone
            user_breakdown = []
            from django.db.models import Count
            # Always get ALL sends for breakdown (universal)
            user_sends = all_quotation_sends.values('user_id').annotate(count=Count('id'))
            for send_data in user_sends:
                send_user_id = send_data['user_id']
                send_count = send_data['count']
                if send_user_id is None:
                    user_breakdown.append({'user_name': 'Admin', 'count': send_count})
                else:
                    try:
                        send_user = User.objects.get(id=send_user_id)
                        user_name = send_user.name or send_user.email
                        user_breakdown.append({'user_name': user_name, 'count': send_count})
                    except User.DoesNotExist:
                        user_breakdown.append({'user_name': f'User {send_user_id}', 'count': send_count})
            
            customers_list.append({
                'id': client.id,
                'customer_name': client.customer_name,
                'company_name': client.company_name or '',
                'email': client.email,
                'phone_number': client.phone_number or '',
                'total_quotation': quotation_count,
                'submitted_value': round(submitted_total, 2),  # Total value of submitted quotations
                'status': 'Active' if client.is_active else 'Inactive',
                'user_breakdown': user_breakdown  # Who sent how many
            })
        
        response_data = {
            'kpis': {
                'total_quotations': total_quotations,
                'total_customers': total_customers,
                'active_customers': active_customers,
                'inactive_customers': inactive_customers,
                'total_users': total_users,
                'total_draft': total_draft,
                'total_submitted_value': round(total_submitted_value, 2),
                'total_awarded_value': round(total_awarded_value, 2)
            },
            'monthly_sends': monthly_data,
            'send_breakdown': {
                'email': {
                    'count': total_email_sends,
                    'percentage': email_percentage,
                    'grand_total': round(email_grand_total, 2)
                },
                'whatsapp': {
                    'count': total_whatsapp_sends,
                    'percentage': whatsapp_percentage,
                    'grand_total': round(whatsapp_grand_total, 2)
                },
                'total': total_sends,
                'total_grand_total': round(total_grand_total, 2)
            },
            'customers': customers_list,
            'year': year,
            'status_breakdown': {
                'draft': status_breakdown['draft'],
                'submitted': status_breakdown['submitted'],
                'awarded': status_breakdown['awarded'],
                'total': sum(status_breakdown.values())
            }
        }
        
        # Add week_data if available
        if week_data is not None:
            response_data['week_data'] = week_data
        
        return JsonResponse({
            'success': True,
            'data': response_data
        })
    
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Dashboard stats error: {str(e)}', exc_info=True)
        
        return JsonResponse({
            'error': f'Error fetching dashboard stats: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def dashboard_customer_list(request):
    """
    Get customer list for dashboard (chart keela customer list).
    Returns data in exact shape expected by frontend recentDetails:
    id, name, company, email, phone, totalQuotation, status.
    """
    try:
        from .models import Client, QuotationSend, Quotation
        from collections import defaultdict

        user_info = get_user_from_token(request)
        if not user_info:
            return JsonResponse({
                'success': False,
                'error': 'Authentication required'
            }, status=401)

        # Same total-quotation logic as list_clients: drafts + sent by email
        draft_by_client = defaultdict(set)
        for cid, qid in Quotation.objects.filter(
            quotation_data__client_id__isnull=False
        ).values_list('quotation_data__client_id', 'id'):
            if cid:
                draft_by_client[cid].add(qid)

        sent_by_email = defaultdict(set)
        for email, qid in QuotationSend.objects.filter(
            recipient_email__isnull=False
        ).values_list('recipient_email', 'quotation_id'):
            sent_by_email[email].add(qid)

        def total_quotations_for_client(client):
            draft_ids = draft_by_client.get(client.id, set())
            sent_ids = sent_by_email.get(client.email, set())
            return len(draft_ids | sent_ids)

        def submitted_value_for_client(client):
            """Calculate total value of submitted quotations for this client."""
            submitted_total = 0
            # Get all quotation IDs for this client
            draft_ids = draft_by_client.get(client.id, set())
            sent_ids = sent_by_email.get(client.email, set())
            all_quotation_ids = draft_ids | sent_ids
            
            # Get all quotations and sum submitted values
            quotations = Quotation.objects.filter(id__in=all_quotation_ids)
            for quotation in quotations:
                quotation_data = quotation.quotation_data or {}
                status = quotation_data.get('status', 'draft').lower()
                if status == 'submitted':
                    grand_total = quotation.get_grand_total()
                    try:
                        submitted_total += float(grand_total) if grand_total else 0
                    except (ValueError, TypeError):
                        pass
            return submitted_total

        # Latest customers first, limit for dashboard (default 20, max 100)
        try:
            limit = min(int(request.GET.get('limit', 20)), 100)
        except (ValueError, TypeError):
            limit = 20
        clients = Client.objects.all().order_by('-created_at')[:limit]

        customers_list = [
            {
                'id': client.id,
                'name': client.customer_name,
                'company': client.company_name or '',
                'email': client.email,
                'phone': client.phone_number or '',
                'totalQuotation': total_quotations_for_client(client),
                'submittedValue': round(submitted_value_for_client(client), 2),  # Total value of submitted quotations
                'status': 'Active' if client.is_active else 'Inactive',
            }
            for client in clients
        ]

        return JsonResponse({
            'success': True,
            'customers': customers_list,
            'count': len(customers_list)
        })
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Dashboard customer list error: {str(e)}', exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_company_details(request):
    """Get all company details for settings page."""
    try:
        from .models import Company
        from django.conf import settings
        
        # Check authentication - only authenticated users can view company details
        user_info = get_user_from_token(request)
        if not user_info:
            return JsonResponse({
                'success': False,
                'error': 'Authentication required'
            }, status=401)
        
        company = Company.get_company()
        
        # Get image URLs
        login_logo_url = None
        login_image_url = None
        quotation_logo_url = None
        
        if company.login_logo:
            try:
                logo_url = company.login_logo.url
                if logo_url and logo_url.strip():
                    logo_url = logo_url.strip()
                    if not logo_url.startswith('/'):
                        logo_url = '/' + logo_url
                    login_logo_url = logo_url
            except (AttributeError, ValueError):
                login_logo_url = None
        
        if company.login_image:
            try:
                image_url = company.login_image.url
                if image_url and image_url.strip():
                    image_url = image_url.strip()
                    if not image_url.startswith('/'):
                        image_url = '/' + image_url
                    login_image_url = image_url
            except (AttributeError, ValueError):
                login_image_url = None
        
        if company.quotation_logo:
            try:
                logo_url = company.quotation_logo.url
                if logo_url and logo_url.strip():
                    logo_url = logo_url.strip()
                    if not logo_url.startswith('/'):
                        logo_url = '/' + logo_url
                    quotation_logo_url = logo_url
            except (AttributeError, ValueError):
                quotation_logo_url = None
        
        return JsonResponse({
            'success': True,
            'company': {
                'company_name': company.company_name or '',
                'brand_name': company.brand_name or '',
                'email': company.email or '',
                'password': company.password or '',
                'tagline': company.tagline or '',
                'phone_number': company.phone_number or '',
                'address': company.address or '',
                'sendemail': company.sendemail or '',
                'sendpassword': company.sendpassword or '',
                'sendnumber': company.sendnumber or '',
                'openrouter_api_key': company.openrouter_api_key or '',
                'openrouter_model': company.openrouter_model or 'google/gemini-flash-1.5:free',
                'openrouter_model_2': company.openrouter_model_2 or '',
                'openrouter_model_3': company.openrouter_model_3 or '',
                'login_logo_url': login_logo_url,
                'login_image_url': login_image_url,
                'quotation_logo_url': quotation_logo_url,
                'created_at': company.created_at.isoformat() if company.created_at else None,
                'updated_at': company.updated_at.isoformat() if company.updated_at else None
            }
        })
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Get company details error: {str(e)}', exc_info=True)
        
        return JsonResponse({
            'success': False,
            'error': f'Error fetching company details: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["PUT", "POST"])
def update_company_details(request):
    """Update company details."""
    try:
        from .models import Company
        
        # Check authentication - only authenticated users can update company details
        user_info = get_user_from_token(request)
        if not user_info:
            return JsonResponse({
                'success': False,
                'error': 'Authentication required'
            }, status=401)
        
        company = Company.get_company()
        
        # Handle both JSON and multipart/form-data
        # Note: Django's request.POST is only populated for POST requests
        # For PUT with FormData, we need to use POST method from frontend
        if request.content_type and 'multipart/form-data' in request.content_type:
            # Handle form data with file uploads
            # request.POST should work for POST method
            data = request.POST
            files = request.FILES
        else:
            # Handle JSON data
            try:
                data = json.loads(request.body)
                files = {}
            except json.JSONDecodeError:
                # Fallback to POST data if JSON parsing fails
                data = request.POST if request.POST else {}
                files = request.FILES if hasattr(request, 'FILES') else {}
        
        # Update fields
        company_name = data.get('company_name', '').strip() or None
        brand_name = data.get('brand_name', '').strip() or None
        email = data.get('email', '').strip()
        password = data.get('password', '').strip() or None
        tagline = data.get('tagline', '').strip() or None
        phone_number = data.get('phone_number', '').strip() or None
        address = data.get('address', '').strip() or None
        sendemail = data.get('sendemail', '').strip() or None
        sendpassword = data.get('sendpassword', '').strip() or None
        sendnumber = data.get('sendnumber', '').strip() or None
        openrouter_api_key = data.get('openrouter_api_key', '').strip() or None
        openrouter_model = data.get('openrouter_model', '').strip() or None
        openrouter_model_2 = data.get('openrouter_model_2', '').strip() or None
        openrouter_model_3 = data.get('openrouter_model_3', '').strip() or None
        
        # Handle file uploads
        if 'login_logo' in files:
            company.login_logo = files['login_logo']
        if 'login_image' in files:
            company.login_image = files['login_image']
        if 'quotation_logo' in files:
            company.quotation_logo = files['quotation_logo']
        
        # Validation - email is optional in update (keep existing if not provided)
        # Company should already have an email from get_company(), but validate if updating
        if email:
            # Validate email format if provided
            if '@' not in email or '.' not in email.split('@')[1]:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid email format'
                }, status=400)
        
        # Update company - save all fields permanently
        if company_name is not None:
            company.company_name = company_name
        if brand_name is not None:
            company.brand_name = brand_name
        # Only update email if provided (Login Credentials not editable in settings)
        if email:
            company.email = email
        # Only update password if provided (Login Credentials not editable in settings)
        if password is not None:
            company.password = password
        if tagline is not None:
            company.tagline = tagline
        if phone_number is not None:
            company.phone_number = phone_number
        if address is not None:
            company.address = address
        if sendemail is not None:
            company.sendemail = sendemail
        if sendpassword is not None:
            company.sendpassword = sendpassword
        if sendnumber is not None:
            company.sendnumber = sendnumber
        if openrouter_api_key is not None:
            company.openrouter_api_key = openrouter_api_key
        if openrouter_model is not None:
            company.openrouter_model = openrouter_model
        if openrouter_model_2 is not None:
            company.openrouter_model_2 = openrouter_model_2
        if openrouter_model_3 is not None:
            company.openrouter_model_3 = openrouter_model_3
        
        # Save to database permanently
        company.save()
        
        # Get updated image URLs
        login_logo_url = None
        login_image_url = None
        quotation_logo_url = None
        
        if company.login_logo:
            try:
                logo_url = company.login_logo.url
                if logo_url and logo_url.strip():
                    logo_url = logo_url.strip()
                    if not logo_url.startswith('/'):
                        logo_url = '/' + logo_url
                    login_logo_url = logo_url
            except (AttributeError, ValueError):
                pass
        
        if company.login_image:
            try:
                image_url = company.login_image.url
                if image_url and image_url.strip():
                    image_url = image_url.strip()
                    if not image_url.startswith('/'):
                        image_url = '/' + image_url
                    login_image_url = image_url
            except (AttributeError, ValueError):
                pass
        
        if company.quotation_logo:
            try:
                logo_url = company.quotation_logo.url
                if logo_url and logo_url.strip():
                    logo_url = logo_url.strip()
                    if not logo_url.startswith('/'):
                        logo_url = '/' + logo_url
                    quotation_logo_url = logo_url
            except (AttributeError, ValueError):
                pass
        
        return JsonResponse({
            'success': True,
            'message': 'Company details updated successfully',
            'company': {
                'company_name': company.company_name or '',
                'brand_name': company.brand_name or '',
                'email': company.email or '',
                'password': company.password or '',
                'tagline': company.tagline or '',
                'phone_number': company.phone_number or '',
                'address': company.address or '',
                'sendemail': company.sendemail or '',
                'sendpassword': company.sendpassword or '',
                'sendnumber': company.sendnumber or '',
                'openrouter_api_key': company.openrouter_api_key or '',
                'openrouter_model': company.openrouter_model or 'google/gemini-flash-1.5:free',
                'openrouter_model_2': company.openrouter_model_2 or '',
                'openrouter_model_3': company.openrouter_model_3 or '',
                'login_logo_url': login_logo_url,
                'login_image_url': login_image_url,
                'quotation_logo_url': quotation_logo_url,
                'created_at': company.created_at.isoformat() if company.created_at else None,
                'updated_at': company.updated_at.isoformat() if company.updated_at else None
            }
        })
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Update company details error: {str(e)}', exc_info=True)
        
        return JsonResponse({
            'success': False,
            'error': f'Error updating company details: {str(e)}'
        }, status=500)

