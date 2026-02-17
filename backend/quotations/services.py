"""
Services for OpenRouter API integration and quotation management.
"""
import json
import re
import requests
import hashlib
from difflib import SequenceMatcher
from django.conf import settings
from django.core.cache import cache
from typing import Dict, Any, Optional, Tuple, List


class IntentClassifier:
    """Classify user intent from messages."""
    
    INTENT_PATTERNS = {
        'add': [
            r'\b(add|include|insert|create|new)\b',
            r'\b(add|include|insert|create|new)\s+service\b',
            r'\b(add|include|insert|create|new)\s+[a-zA-Z]+\b',  # "add pants", "add service"
        ],
        'remove': [
            r'\b(remove|delete|drop|exclude)\b',
            r'\b(remove|delete|drop|exclude)\s+service\b',
        ],
        'change': [
            r'\b(change|update|modify|edit|alter)\b',
            r'\b(change|update|modify|edit|alter)\s+(price|quantity|name|gst)\b',
        ],
        'view': [
            r'\b(show|display|view|see|list|get)\b',
            r'\b(show|display|view|see|list|get)\s+(quotation|services|total)\b',
        ],
        'reset': [
            r'\b(reset|clear|start\s+over|new\s+quotation|empty)\b',
        ],
        'calculate': [
            r'\b(calculate|compute|total|sum)\b',
        ],
    }
    
    @staticmethod
    def classify(user_message: str) -> str:
        """Classify user intent from message."""
        user_lower = user_message.lower()
        
        # Check each intent pattern
        for intent, patterns in IntentClassifier.INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, user_lower, re.IGNORECASE):
                    return intent
        
        return 'unknown'
    
    @staticmethod
    def extract_entities(user_message: str) -> Dict[str, Any]:
        """Extract entities (service names, prices, quantities) from message."""
        entities = {
            'service_name': None,
            'quantity': None,
            'price': None,
            'old_value': None,
            'new_value': None,
        }
        
        # Common prefixes to remove from service names
        PREFIXES_TO_REMOVE = [
            r'^(?:create\s+a?\s*)?(?:quotation\s+for\s+)',
            r'^(?:add\s+a?\s*)?(?:service\s+for\s+)',
            r'^(?:create\s+a?\s*)?(?:service\s+for\s+)',
            r'^(?:add\s+a?\s*)?(?:quotation\s+for\s+)',
            r'^(?:create\s+a?\s*)',
            r'^(?:add\s+a?\s*)',
            r'^(?:quotation\s+for\s+)',
            r'^(?:service\s+for\s+)',
            r'^(?:for\s+)',
        ]
        
        # Common suffixes to remove
        SUFFIXES_TO_REMOVE = [
            r'\s+(?:service|services|work|works|quotation|quotations)\s*$',
        ]
        
        def clean_service_name(name: str) -> str:
            """Clean service name by removing common prefixes and suffixes."""
            if not name:
                return name
            
            # Remove prefixes
            for prefix_pattern in PREFIXES_TO_REMOVE:
                name = re.sub(prefix_pattern, '', name, flags=re.IGNORECASE).strip()
            
            # Remove suffixes
            for suffix_pattern in SUFFIXES_TO_REMOVE:
                name = re.sub(suffix_pattern, '', name, flags=re.IGNORECASE).strip()
            
            return name.strip()
        
        # Extract service name with improved patterns
        # Pattern 1: "create a Quotation For Website quantity 1 price 45000"
        # Pattern 2: "add service Web Development quantity 2 price 25000"
        # Pattern 3: "add Web Development quantity 2 price 25000"
        # Pattern 4: "add service pants 10, 100" or "add service pant"
        service_patterns = [
            # Handle "create/quotation for" patterns - extract what comes after "for" and before quantity/price
            r'(?:create|add|make|new)\s+(?:a\s+)?(?:quotation\s+for|service\s+for|for)\s+(.+?)(?:\s+(?:quantity|qty|price|rate|₹|rs|rupees?|cost|\d))',
            # Handle "add service X quantity Y price Z" or "add service X Y Z" (numbers)
            r'(?:add|create|insert|include)\s+(?:a\s+)?(?:service\s+)?(.+?)(?:\s+(?:quantity|qty|price|rate|₹|rs|rupees?|cost|\d))',
            # Handle "X quantity Y price Z" (direct pattern)
            r'^(.+?)(?:\s+(?:quantity|qty|price|rate|₹|rs|rupees?|cost|\d))',
            # Handle patterns with numbers at the end (quantity/price)
            r'(.+?)(?:\s+(?:quantity|qty)\s+\d+)',
            # Handle simple "add service X" (no numbers) - extract everything after "add service"
            r'(?:add|create|insert|include)\s+(?:a\s+)?(?:service\s+)?([a-zA-Z\s]+?)(?:\s*$|\s*$)',
        ]
        
        for pattern in service_patterns:
            match = re.search(pattern, user_message, re.IGNORECASE)
            if match:
                service_name = match.group(1).strip()
                
                # Clean up the service name
                service_name = clean_service_name(service_name)
                
                # Additional cleanup: remove any trailing numbers or common words
                # But be careful - if it's "pants 10", we want to keep "pants" and extract "10" separately
                # So only remove trailing numbers if they're not part of a sequence
                # Check if there are numbers after this that might be quantity/price
                service_name = re.sub(r'\s+\d+\s*$', '', service_name).strip()
                
                # Remove trailing common words that might be part of the pattern
                service_name = re.sub(r'\s+(service|services|work|works|quotation|quotations)\s*$', '', service_name, flags=re.IGNORECASE).strip()
                
                # Validate: service name should be meaningful (at least 2 chars, not just numbers)
                if service_name and len(service_name) > 1 and not service_name.isdigit():
                    # Final check: if it still contains "quotation for" or "service for", extract the part after "for"
                    if re.search(r'(?:quotation|service)\s+for\s+', service_name, re.IGNORECASE):
                        after_for = re.split(r'(?:quotation|service)\s+for\s+', service_name, flags=re.IGNORECASE)
                        if len(after_for) > 1:
                            service_name = after_for[-1].strip()
                    
                    # Additional validation: ensure it's not just common words
                    if service_name.lower() not in ['add', 'create', 'insert', 'include', 'service', 'for', 'a', 'an', 'the']:
                        entities['service_name'] = service_name
                        break
        
        # Extract quantity
        quantity_patterns = [
            r'(?:quantity|qty)\s+(\d+)',
            r'(\d+)\s+(?:quantity|qty|units?|items?)',
        ]
        for pattern in quantity_patterns:
            match = re.search(pattern, user_message, re.IGNORECASE)
            if match:
                try:
                    entities['quantity'] = int(match.group(1))
                    break
                except ValueError:
                    pass
        
        # Extract price
        price_patterns = [
            r'(?:price|rate|cost)\s+(?:is\s+)?(?:₹|rs\.?|rupees?)?\s*(\d+(?:\.\d+)?)',
            r'(?:₹|rs\.?|rupees?)?\s*(\d+(?:\.\d+)?)\s+(?:price|rate|cost)',
            r'(\d+(?:\.\d+)?)\s*(?:rupees?|₹|rs\.?)',
        ]
        for pattern in price_patterns:
            match = re.search(pattern, user_message, re.IGNORECASE)
            if match:
                try:
                    entities['price'] = float(match.group(1))
                    break
                except ValueError:
                    pass
        
        # Enhanced extraction: Handle patterns like "add service pants 10, 100" or "add service pant 10 100"
        # Extract all numbers after service name (comma-separated or space-separated)
        if not entities.get('quantity') or not entities.get('price'):
            # Pattern: service name followed by two numbers separated by comma: "pants 10, 100"
            number_sequence_pattern = r'(?:add|create|insert|include)\s+(?:a\s+)?(?:service\s+)?[a-zA-Z\s]+\s+(\d+)\s*[,]\s*(\d+(?:\.\d+)?)'
            match = re.search(number_sequence_pattern, user_message, re.IGNORECASE)
            if match:
                try:
                    if not entities.get('quantity'):
                        entities['quantity'] = int(match.group(1))
                    if not entities.get('price'):
                        entities['price'] = float(match.group(2))
                except (ValueError, IndexError):
                    pass
            
            # Pattern: service name followed by two space-separated numbers: "pant 10 100"
            if not entities.get('quantity') or not entities.get('price'):
                # Match pattern: "add service [words] [number] [number]"
                number_sequence_pattern2 = r'(?:add|create|insert|include)\s+(?:a\s+)?(?:service\s+)?([a-zA-Z\s]+?)\s+(\d+)\s+(\d+(?:\.\d+)?)(?:\s|$)'
                match = re.search(number_sequence_pattern2, user_message, re.IGNORECASE)
                if match:
                    try:
                        # Check if service name was already extracted
                        if not entities.get('service_name'):
                            potential_service = match.group(1).strip()
                            # Clean service name
                            potential_service = clean_service_name(potential_service)
                            if potential_service and len(potential_service) > 1 and not potential_service.isdigit():
                                entities['service_name'] = potential_service
                        if not entities.get('quantity'):
                            entities['quantity'] = int(match.group(2))
                        if not entities.get('price'):
                            entities['price'] = float(match.group(3))
                    except (ValueError, IndexError):
                        pass
        
        # Extract old and new values for change operations
        change_patterns = [
            r'(?:change|update|modify)\s+(?:from\s+)?(\d+(?:\.\d+)?)\s+to\s+(\d+(?:\.\d+)?)',
            r'(\d+(?:\.\d+)?)\s+to\s+(\d+(?:\.\d+)?)',
        ]
        for pattern in change_patterns:
            match = re.search(pattern, user_message, re.IGNORECASE)
            if match:
                try:
                    entities['old_value'] = float(match.group(1))
                    entities['new_value'] = float(match.group(2))
                    break
                except ValueError:
                    pass
        
        return entities


class FuzzyMatcher:
    """Fuzzy string matching for service names."""
    
    @staticmethod
    def similarity(a: str, b: str) -> float:
        """Calculate similarity ratio between two strings (0.0 to 1.0)."""
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()
    
    @staticmethod
    def find_best_match(query: str, candidates: List[str], threshold: float = 0.6) -> Optional[Tuple[str, float]]:
        """Find best matching candidate for query."""
        if not query or not candidates:
            return None
        
        best_match = None
        best_score = 0.0
        
        for candidate in candidates:
            score = FuzzyMatcher.similarity(query, candidate)
            if score > best_score and score >= threshold:
                best_score = score
                best_match = candidate
        
        return (best_match, best_score) if best_match else None
    
    @staticmethod
    def find_service_by_name(query: str, services: List[Dict[str, Any]], threshold: float = 0.6) -> Optional[Dict[str, Any]]:
        """Find service by fuzzy matching name."""
        if not services:
            return None
        
        service_names = [s.get('service_name', '') for s in services]
        match = FuzzyMatcher.find_best_match(query, service_names, threshold)
        
        if match:
            matched_name, score = match
            # Find the service with this name
            for service in services:
                if service.get('service_name', '').lower() == matched_name.lower():
                    return service
        
        return None


class ConversationHistoryService:
    """Service to search past conversations and extract pricing information."""
    
    @staticmethod
    def search_past_conversations(service_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Search past conversations for similar services and extract pricing.
        
        Args:
            service_name: The service name to search for
            limit: Maximum number of past conversations to return
            
        Returns:
            List of dictionaries with service info, pricing, and conversation context
        """
        from .models import Quotation
        
        if not service_name or not service_name.strip():
            return []
        
        service_name_lower = service_name.lower().strip()
        
        # Get all quotations with conversation history
        quotations = Quotation.objects.filter(
            quotation_data__conversation_history__isnull=False
        ).exclude(
            quotation_data__conversation_history=[]
        ).order_by('-updated_at')[:100]  # Check last 100 quotations
        
        matching_conversations = []
        
        for quotation in quotations:
            qdata = quotation.quotation_data or {}
            conversation_history = qdata.get('conversation_history', []) or []
            services = qdata.get('services', []) or []
            
            # Check if conversation mentions the service
            conversation_text = ' '.join([
                msg.get('content', '') for msg in conversation_history
            ]).lower()
            
            # Check if service name appears in conversation or in services
            # Use word-based matching for better results (e.g., "car rent" matches "car rental")
            service_words = set(service_name_lower.split())
            service_found = False
            
            # Check if at least 50% of service words appear in conversation
            conversation_words = set(conversation_text.split())
            matching_words = service_words.intersection(conversation_words)
            if len(matching_words) >= max(1, len(service_words) * 0.5):
                service_found = True
            
            # Also check direct substring match
            if service_name_lower in conversation_text or any(word in conversation_text for word in service_words if len(word) > 2):
                service_found = True
            
            # Also check services in quotation data using fuzzy matching
            for service in services:
                service_name_in_quote = service.get('service_name', '').lower()
                
                # Direct match
                if service_name_lower in service_name_in_quote or service_name_in_quote in service_name_lower:
                    service_found = True
                    # Extract pricing from this service
                    matching_conversations.append({
                        'service_name': service.get('service_name', ''),
                        'quantity': service.get('quantity', 0),
                        'unit_price': service.get('unit_price', 0) or service.get('price', 0),
                        'amount': service.get('amount', 0),
                        'conversation_snippet': conversation_text[:200] if conversation_text else '',
                        'quotation_id': quotation.id,
                        'quotation_number': quotation.quotation_number,
                        'updated_at': quotation.updated_at.isoformat() if quotation.updated_at else None
                    })
                    break
                
                # Word-based fuzzy match
                service_words_quote = set(service_name_in_quote.split())
                matching_words = service_words.intersection(service_words_quote)
                if len(matching_words) >= max(1, min(len(service_words), len(service_words_quote)) * 0.6):
                    service_found = True
                    # Extract pricing from this service
                    matching_conversations.append({
                        'service_name': service.get('service_name', ''),
                        'quantity': service.get('quantity', 0),
                        'unit_price': service.get('unit_price', 0) or service.get('price', 0),
                        'amount': service.get('amount', 0),
                        'conversation_snippet': conversation_text[:200] if conversation_text else '',
                        'quotation_id': quotation.id,
                        'quotation_number': quotation.quotation_number,
                        'updated_at': quotation.updated_at.isoformat() if quotation.updated_at else None
                    })
                    break
            
            # If found in conversation but not in services, try to extract from conversation text
            if service_found and not any(m.get('quotation_id') == quotation.id for m in matching_conversations):
                # Try to extract pricing from conversation
                price_info = ConversationHistoryService._extract_pricing_from_text(conversation_text, service_name_lower)
                if price_info:
                    matching_conversations.append({
                        'service_name': service_name,
                        'quantity': price_info.get('quantity', 0),
                        'unit_price': price_info.get('unit_price', 0),
                        'amount': price_info.get('amount', 0),
                        'conversation_snippet': conversation_text[:200],
                        'quotation_id': quotation.id,
                        'quotation_number': quotation.quotation_number,
                        'updated_at': quotation.updated_at.isoformat() if quotation.updated_at else None,
                        'extracted_from_text': True
                    })
        
        # Intelligent sorting by relevance
        def calculate_relevance_score(conv):
            """Calculate relevance score for a conversation match."""
            conv_service = conv.get('service_name', '').lower()
            score = 0
            
            # Exact match gets highest score
            if service_name_lower == conv_service:
                score += 100
            # Substring match
            elif service_name_lower in conv_service or conv_service in service_name_lower:
                score += 80
            # Word overlap score
            else:
                conv_words = set(conv_service.split())
                query_words = set(service_name_lower.split())
                overlap = len(query_words.intersection(conv_words))
                if len(query_words) > 0:
                    score += (overlap / len(query_words)) * 60
            
            # Recency bonus (more recent = higher score)
            # This is handled by sorting by updated_at after score
            
            return score
        
        # Sort by relevance score (highest first), then by recency
        matching_conversations.sort(
            key=lambda x: (
                -calculate_relevance_score(x),  # Negative for descending order
                x.get('updated_at', '') or ''  # Most recent first
            ),
            reverse=True
        )
        
        return matching_conversations[:limit]
    
    @staticmethod
    def _extract_pricing_from_text(text: str, service_name: str) -> Optional[Dict[str, Any]]:
        """
        Intelligently extract pricing information from conversation text.
        Works for ANY service type, not just specific examples.
        """
        import re
        
        # Create flexible service pattern - match any word from service name
        service_words = [word for word in service_name.split() if len(word) > 2]
        if service_words:
            # Match if any significant word from service name appears
            service_pattern = '|'.join(re.escape(word) for word in service_words)
        else:
            service_pattern = re.escape(service_name)
        
        # Also try without service name pattern for general number extraction
        # This helps when service name variations exist
        
        # Patterns to find prices and quantities
        # Pattern 1: Service with time/unit quantity and price
        # Examples: "car rent 3 days 300", "cleaning 2 visits 500", "website 5 pages 2000"
        # Match: number + (days/hours/units/items/pages/visits/etc) + price
        time_units = r'(?:days?|day|hours?|hour|units?|unit|items?|item|pages?|page|visits?|visit|sessions?|session|months?|month|years?|year|sqft|sq\.?\s*ft|meters?|meter|kg|kgs?|liters?|liter)'
        pattern1 = rf'(?:{service_pattern}).*?(\d+)\s*{time_units}.*?(?:₹|rs\.?|rupees?|cost|price|rate)?\s*(\d+(?:\.\d+)?)'
        match1 = re.search(pattern1, text, re.IGNORECASE)
        if match1:
            quantity = int(match1.group(1))
            price = float(match1.group(2))
            # Intelligent price interpretation:
            # If price is significantly larger than quantity, it's likely total amount
            # Otherwise, it might be unit price
            if price > quantity * 50:  # More intelligent threshold
                unit_price = price / quantity if quantity > 0 else price
                amount = price
            elif price > quantity * 5:
                # Could be either - check context for "per" or "total"
                if 'per' in text.lower() or 'each' in text.lower():
                    unit_price = price
                    amount = price * quantity
                else:
                    unit_price = price / quantity if quantity > 0 else price
                    amount = price
            else:
                unit_price = price
                amount = price * quantity
            return {
                'quantity': quantity,
                'unit_price': round(unit_price, 2),
                'amount': round(amount, 2)
            }
        
        # Pattern 2: "service name quantity X price Y" or "service name X Y" (two numbers)
        pattern2 = rf'(?:{service_pattern}).*?(?:quantity|qty)\s*(\d+).*?(?:price|rate|cost|₹|rs\.?|rupees?)\s*(?:₹|rs\.?|rupees?)?\s*(\d+(?:\.\d+)?)'
        match2 = re.search(pattern2, text, re.IGNORECASE)
        if match2:
            quantity = int(match2.group(1))
            price = float(match2.group(2))
            return {
                'quantity': quantity,
                'unit_price': price / quantity if quantity > 0 else price,
                'amount': price
            }
        
        # Pattern 3: "service name X Y" where X is quantity and Y is price (simple pattern)
        # Example: "car rent 3 300" or "cleaning service 2 500"
        pattern3 = rf'(?:{service_pattern}).*?\b(\d+)\s+(\d+(?:\.\d+)?)\b'
        match3 = re.search(pattern3, text, re.IGNORECASE)
        if match3:
            first_num = int(match3.group(1))
            second_num = float(match3.group(2))
            # Heuristic: if second number is much larger, it's likely total price
            if second_num > first_num * 10:
                return {
                    'quantity': first_num,
                    'unit_price': round(second_num / first_num, 2) if first_num > 0 else second_num,
                    'amount': round(second_num, 2)
                }
            else:
                # Both could be quantity and unit price
                return {
                    'quantity': first_num,
                    'unit_price': round(second_num, 2),
                    'amount': round(first_num * second_num, 2)
                }
        
        # Pattern 4: "service name ₹X" or "service name X rupees" (single price)
        pattern4 = rf'(?:{service_pattern}).*?(?:₹|rs\.?|rupees?|cost|price|rate)?\s*(\d+(?:\.\d+)?)'
        match4 = re.search(pattern4, text, re.IGNORECASE)
        if match4:
            price = float(match4.group(1))
            # Check if "per" is mentioned to determine if it's unit price
            per_match = re.search(rf'(?:{service_pattern}).*?per\s+(?:day|hour|unit|item|page|visit|session)', text, re.IGNORECASE)
            if per_match:
                return {
                    'quantity': 1,  # Default quantity when "per" is mentioned
                    'unit_price': round(price, 2),
                    'amount': round(price, 2)
                }
            return {
                'quantity': 1,
                'unit_price': round(price, 2),
                'amount': round(price, 2)
            }
        
        # Pattern 5: General number extraction - find any two numbers near service name
        # This is a fallback for unusual patterns
        numbers_near_service = re.findall(rf'(?:{service_pattern}).*?(\d+(?:\.\d+)?).*?(\d+(?:\.\d+)?)', text, re.IGNORECASE)
        if numbers_near_service:
            for num_pair in numbers_near_service:
                first_num = float(num_pair[0])
                second_num = float(num_pair[1])
                # Heuristic: first number is usually quantity, second is price
                if first_num < 1000 and second_num > first_num:
                    return {
                        'quantity': int(first_num),
                        'unit_price': round(second_num / first_num, 2) if first_num > 0 else second_num,
                        'amount': round(second_num, 2)
                    }
        
        return None
    
    @staticmethod
    def get_suggested_pricing(service_name: str) -> Optional[Dict[str, Any]]:
        """
        Intelligently get suggested pricing for ANY service based on past conversations.
        Uses weighted average and intelligent filtering.
        
        Returns:
            Dictionary with suggested quantity, unit_price, and amount, or None
        """
        past_conversations = ConversationHistoryService.search_past_conversations(service_name, limit=10)
        
        if not past_conversations:
            return None
        
        # Intelligent pricing calculation with weights
        weighted_unit_prices = []
        quantities = []
        valid_conversations = []
        
        for conv in past_conversations:
            unit_price = conv.get('unit_price', 0)
            quantity = conv.get('quantity', 0)
            
            # Only consider valid pricing
            if unit_price > 0:
                # Weight by relevance (exact matches weighted higher)
                conv_service = conv.get('service_name', '').lower()
                service_name_lower = service_name.lower()
                
                if service_name_lower == conv_service:
                    weight = 3.0  # Exact match gets 3x weight
                elif service_name_lower in conv_service or conv_service in service_name_lower:
                    weight = 2.0  # Substring match gets 2x weight
                else:
                    weight = 1.0  # Fuzzy match gets normal weight
                
                # Add weighted prices
                for _ in range(int(weight)):
                    weighted_unit_prices.append(unit_price)
                    quantities.append(quantity)
                    valid_conversations.append(conv)
        
        if not weighted_unit_prices:
            return None
        
        # Calculate weighted average
        avg_unit_price = sum(weighted_unit_prices) / len(weighted_unit_prices) if weighted_unit_prices else 0
        
        # For quantity, use median (more stable than average)
        quantities_sorted = sorted(quantities)
        median_quantity = quantities_sorted[len(quantities_sorted) // 2] if quantities_sorted else 1
        
        # Also calculate mode (most common quantity)
        from collections import Counter
        quantity_counts = Counter(quantities)
        mode_quantity = quantity_counts.most_common(1)[0][0] if quantity_counts else median_quantity
        
        # Use mode if it's reasonable, otherwise use median
        suggested_quantity = mode_quantity if mode_quantity > 0 else median_quantity
        
        return {
            'suggested_unit_price': round(avg_unit_price, 2),
            'suggested_quantity': suggested_quantity,
            'suggested_amount': round(avg_unit_price * suggested_quantity, 2),
            'based_on_conversations': len(valid_conversations),
            'past_examples': past_conversations[:5],  # Top 5 examples for better context
            'price_range': {
                'min': round(min(weighted_unit_prices), 2) if weighted_unit_prices else 0,
                'max': round(max(weighted_unit_prices), 2) if weighted_unit_prices else 0
            }
        }


class ConversationOptimizer:
    """Optimize conversation history for better context management."""
    
    MAX_HISTORY_MESSAGES = 20  # Keep more messages
    SUMMARY_THRESHOLD = 15  # Summarize if more than this
    
    @staticmethod
    def summarize_conversation(history: List[Dict[str, str]]) -> str:
        """Create a summary of conversation history."""
        if not history or len(history) <= 2:
            return ""
        
        # Extract key information from conversation
        summary_parts = []
        user_messages = [msg['content'] for msg in history if msg.get('role') == 'user']
        assistant_messages = [msg['content'] for msg in history if msg.get('role') == 'assistant']
        
        # Count services mentioned
        service_count = sum(1 for msg in user_messages if 'add' in msg.lower() or 'service' in msg.lower())
        
        if service_count > 0:
            summary_parts.append(f"User has added/modified {service_count} service(s) in this conversation.")
        
        # Extract last few important messages
        if len(history) > 4:
            summary_parts.append("Recent conversation context:")
            for msg in history[-4:]:
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')[:100]  # Truncate long messages
                summary_parts.append(f"{role}: {content}")
        
        return "\n".join(summary_parts)
    
    @staticmethod
    def optimize_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Optimize conversation history by keeping important messages."""
        if not history:
            return []
        
        if len(history) <= ConversationOptimizer.MAX_HISTORY_MESSAGES:
            return history
        
        # Keep first message (welcome/initial)
        optimized = [history[0]] if history else []
        
        # Keep last N messages (most recent context)
        recent_messages = history[-ConversationOptimizer.MAX_HISTORY_MESSAGES + 1:]
        optimized.extend(recent_messages)
        
        return optimized


class OpenRouterService:
    """Service to handle OpenRouter API interactions."""
    
    # Fallback models in order of preference (most reliable first)
    # Note: Free models may not always be available, so we try multiple options
    FALLBACK_MODELS = [
        'google/gemini-flash-1.5:free',
        'meta-llama/llama-3.1-8b-instruct:free',
        'microsoft/phi-3-mini-128k-instruct:free',
        'qwen/qwen-2-7b-instruct:free',
        'mistralai/mistral-7b-instruct:free',
        'anthropic/claude-3-haiku',  # Cheaper than sonnet
        'anthropic/claude-3.5-sonnet',  # Requires API key and credits
    ]
    
    # Cache timeout in seconds (5 minutes)
    CACHE_TIMEOUT = 300
    
    def __init__(self):
        # Load credentials from Company model (preferred) or fallback to settings
        from .models import Company
        company = Company.get_company()
        
        # Get API key from Company model, fallback to settings/env
        self.api_key = company.openrouter_api_key or settings.OPENROUTER_API_KEY
        # Get model from Company model, fallback to settings/env, then default
        self.model = company.openrouter_model or settings.OPENROUTER_MODEL or 'google/gemini-flash-1.5:free'
        self.api_url = settings.OPENROUTER_API_URL
        
        # Debug: Check if API key is loaded (remove in production)
        if not self.api_key:
            print("WARNING: OPENROUTER_API_KEY is not set in Company settings or Django settings!")
            print("Please set it in Company Details settings page or .env file.")
    
    def _get_cache_key(self, user_message: str, quotation_state: Dict[str, Any]) -> str:
        """Generate cache key for request."""
        # Create hash of message + quotation state
        cache_data = json.dumps({
            'message': user_message.lower().strip(),
            'quotation': quotation_state
        }, sort_keys=True)
        return f"chat_response:{hashlib.md5(cache_data.encode()).hexdigest()}"
    
    def _get_cached_response(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Get cached response if available."""
        try:
            cached = cache.get(cache_key)
            if cached:
                print(f"✅ Cache hit for: {cache_key[:20]}...")
                return cached
        except Exception as e:
            print(f"Cache read error: {e}")
        return None
    
    def _set_cached_response(self, cache_key: str, response: Dict[str, Any]) -> None:
        """Cache response for future use."""
        try:
            cache.set(cache_key, response, self.CACHE_TIMEOUT)
            print(f"💾 Cached response: {cache_key[:20]}...")
        except Exception as e:
            print(f"Cache write error: {e}")
    
    def get_system_prompt(self) -> str:
        """Get the system prompt for SynQuot AI assistant."""
        return """You are SynQuot, a professional AI Quotation Assistant.

THIS IS A STRICT SYSTEM PROMPT.
FOLLOW EVERY RULE WITHOUT EXCEPTION.

----------------------------------
CORE PRINCIPLE
----------------------------------
You do NOT control the quotation by words.
You control the quotation ONLY by structured data (JSON).

The UI will render the quotation ONLY from the JSON you return.

----------------------------------
MANDATORY RESPONSE FORMAT
----------------------------------
EVERY response MUST be valid JSON in this exact format:

{
  "message": "<short human readable reply>",
  "quotation": {
    "services": [
      {
        "service_name": "",
        "quantity": 0,
        "unit_price": 0,
        "amount": 0,
        "key_features": [
          "Feature 1",
          "Feature 2",
          "Feature 3",
          "Feature 4"
        ]
      }
    ],
    "subtotal": 0,
    "gst_percentage": 0,
    "gst_amount": 0,
    "shipping": 0,
    "grand_total": 0
  }
}

NO extra text.
NO markdown.
NO explanations outside JSON.
ONLY return the JSON object.

----------------------------------
STATE MANAGEMENT RULES
----------------------------------
1. ALWAYS use the existing quotation state provided in the context.
2. NEVER reset quotation unless user clearly says:
   "reset", "start over", "new quotation", "clear all".
3. NEVER remove a service unless user clearly says:
   "remove", "delete", "drop".

----------------------------------
SERVICE NAME CHANGE RULES (CRITICAL)
----------------------------------
When user asks to change a service name:

Examples of valid requests:
- "change the Service Name Vehicle to Website Service"
- "change Vehicle to Website Service"
- "change service name Vehicle to Website Service"
- "rename Vehicle to Website Service"
- "change Vehicle service name to Website Service"

Steps to follow:
1. Find the service with matching name (case-insensitive, partial match OK)
2. Update ONLY the service_name field
3. Keep ALL other fields unchanged (quantity, unit_price, amount)
4. Recalculate: amount = quantity × unit_price
5. Recalculate: subtotal, gst_amount, grand_total
6. Return the COMPLETE quotation with ALL services

IMPORTANT: If multiple services match, update the FIRST matching service.
If no service matches, ask for clarification but DO NOT modify quotation.

----------------------------------
EDITING RULES (CRITICAL)
----------------------------------
If the user asks to change something:

• Change ONLY the mentioned field.
• Recalculate dependent values.
• Leave everything else untouched.

Examples:

- "change price 25000 to 40000" or "change price to 40000"
  → Find service with price 25000, update unit_price to 40000
  → Recalculate: amount, subtotal, gst_amount, grand_total

- "change quantity 1 to 5" or "change quantity to 5"
  → Update quantity of the last service (or matching service)
  → Recalculate: amount, subtotal, gst_amount, grand_total

- "change GST 8 to 10" or "change GST to 10"
  → Update gst_percentage to 10
  → Recalculate: gst_amount, grand_total

- "change shipping 500 to 2000" or "change shipping to 2000"
  → Update shipping to 2000 (a flat rupee amount, NOT a percentage)
  → Recalculate: grand_total = subtotal + gst_amount + shipping

- "change service name X to Y"
  → Find service with name X, update service_name to Y
  → Keep quantity, unit_price unchanged
  → Recalculate: amount, subtotal, gst_amount, grand_total

----------------------------------
CALCULATION RULES
----------------------------------
amount = quantity × unit_price (for each service)
subtotal = sum of all service amounts
gst_amount = (subtotal × gst_percentage) / 100
shipping = flat rupee amount added on top of the subtotal (NOT a percentage)
grand_total = subtotal + gst_amount + shipping

Round all monetary values to 2 decimal places.

----------------------------------
INTELLIGENT PRICING FROM PAST CONVERSATIONS (CRITICAL)
----------------------------------
You have access to past conversation history that contains pricing information for similar services.

WHEN USER ASKS TO ADD A SERVICE:
1. If past pricing history is provided in the context (look for "PAST PRICING HISTORY" section):
   - USE the suggested pricing from past conversations
   - This pricing is based on real past quotations, so it's reliable
   - If user doesn't specify quantity/price, AUTOMATICALLY use the suggested values
   - Mention in your message that you're using pricing from past conversations
   
2. INTELLIGENT PRICING EXTRACTION:
   - If user provides partial info (e.g., "car rent service" without price):
     * Check past pricing history first
     * If found, use suggested_unit_price and suggested_quantity
     * Add service with those values automatically
     * Say: "I've added [service] based on past pricing: ₹[price] per unit, quantity [qty]"
   
   - If user provides full info (e.g., "car rent 3 days 300"):
     * Extract: service_name="car rent", quantity=3, unit_price=100 (300/3)
     * Use user's values (they override past pricing)
   
   - If user provides service name only (e.g., "cleaning service"):
     * Check past pricing history
     * If found, use suggested pricing
     * If not found, add with quantity=0, unit_price=0 and ask user

3. SMART PRICE INTERPRETATION:
   - "service 3 days 300" → quantity=3, total=300, unit_price=100/day
   - "service 3 300" → quantity=3, if 300 > 30 then total=300, unit_price=100; else unit_price=300
   - "service ₹500" → quantity=1, unit_price=500
   - "service per day 500" → unit_price=500, quantity=1 (default)
   - "service 5 units at ₹100 each" → quantity=5, unit_price=100

4. LEARNING FROM CONTEXT:
   - Understand service types intelligently:
     * "rent", "rental", "hire" → usually time-based (per day/hour)
     * "cleaning", "maintenance", "service" → usually one-time or per visit
     * "development", "design", "work" → usually project-based
   - Adjust unit pricing logic based on service type
   - Extract quantities from context: "3 days", "5 pages", "2 hours", etc.

----------------------------------
ADDING SERVICES RULES
----------------------------------
If the user asks to ADD a service:

1. Extract service name from the request (even if incomplete).
2. Check if past pricing history is available (look for "PAST PRICING HISTORY" in context).
3. Check if quantity and unit_price are provided in the request.
4. PRIORITY ORDER:
   a) User-provided values (highest priority)
   b) Past pricing history suggestions (if available)
   c) Default values (quantity=0, unit_price=0)
5. If BOTH quantity and unit_price are available (from user OR past history):
   - Add the service immediately with those values
   - ALWAYS include "key_features" array with EXACTLY 4 relevant features for that service
   - Generate 4 professional, relevant key features based on the service type
   - Recalculate totals
   - If using past pricing, mention it in your message
6. If quantity OR price is missing (but service name is clear):
   - Check past pricing history first
   - If past pricing available, use it
   - If not, ADD the service with default values: quantity=0, unit_price=0
   - ALWAYS include "key_features" array with EXACTLY 4 relevant features for that service
   - Generate 4 professional, relevant key features based on the service type
   - Recalculate totals
   - In your message, inform the user appropriately
7. If service name cannot be determined:
   - DO NOT modify quotation JSON
   - Ask a clear follow-up question: "What service would you like to add?"

IMPORTANT: When user says "add service X" or "add X" without quantity/price:
- Extract service name: X
- Add service with quantity=0, unit_price=0
- User can modify these values later through chat commands
- This allows users to quickly add services and fill in details later

KEY FEATURES RULES (MANDATORY):
- EVERY service MUST have a "key_features" array with EXACTLY 4 items
- Features should be relevant to the service type
- Features should be professional and descriptive
- Each feature should be a short string (1-2 sentences max)
- Generate features based on the service name and context

Examples of valid ADD commands with key features:
- "add service Tiles Work Quantity 5 price 5450" 
  → Extract: service_name="Tiles Work", quantity=5, unit_price=5450
  → Generate 4 relevant features like: ["Premium quality tiles", "Professional installation", "Waterproof finish", "5-year warranty"]
  
- "add Tiles Work quantity 5 and price 5450" 
  → Extract: service_name="Tiles Work", quantity=5, unit_price=5450
  → Generate 4 relevant features based on tiles work
  
- "add service Web Development with quantity 2 and price 25000" 
  → Extract: service_name="Web Development", quantity=2, unit_price=25000
  → Generate 4 relevant features like: ["Responsive design", "SEO optimized", "Fast loading speed", "Mobile friendly"]
  
- "create a Quotation For Website quantity 1 price 45000" 
  → Extract: service_name="Website" (NOT "Quotation For Website")
  → Generate 4 relevant features for website development
  
- "create Quotation For Mobile App quantity 1 price 50000" 
  → Extract: service_name="Mobile App" (NOT "Quotation For Mobile App")
  → Generate 4 relevant features for mobile app development

CRITICAL SERVICE NAME EXTRACTION RULES:
- When user says "create a Quotation For X" or "add Service For X", the service name is ONLY "X", NOT "Quotation For X" or "Service For X"
- Remove ALL prefixes: "create a", "quotation for", "service for", "add a", "for"
- Remove ALL suffixes: "service", "services", "work", "works", "quotation", "quotations"
- The service name should be the ACTUAL service/product name, not descriptive phrases
- Examples:
  * "create a Quotation For Website" → service_name="Website"
  * "add Service For Mobile App Development" → service_name="Mobile App Development"
  * "create Quotation For E-commerce Platform" → service_name="E-commerce Platform"
  * "add a Service For Digital Marketing" → service_name="Digital Marketing"

IMPORTANT: When parsing "add service X Quantity Y price Z" or "create Quotation For X quantity Y price Z":
- The service name is the ACTUAL service name, NOT the descriptive phrase
- Do NOT include "Quotation For", "Service For", "create a", "add a" in the service name
- Do NOT include "Quantity" or "qty" in the service name
- Extract the numeric values for quantity and price correctly

----------------------------------
SCENARIO-BASED QUOTATION GENERATION (CRITICAL)
----------------------------------
You are a professional Quotation Generator AI. When a user provides a SCENARIO or DESCRIPTION of services needed, you MUST automatically parse it and generate a complete quotation.

SCENARIO DETECTION:
A scenario is a natural language description that contains:
- Service descriptions (e.g., "Travel from Madurai to Chennai", "Website development")
- Pricing information (e.g., "Car rent per day: 500", "Cost per page: 2000")
- Quantity information (e.g., "Days: 2", "Pages: 5")
- Multiple services in one description

WHEN USER PROVIDES A SCENARIO:
1. Parse the entire scenario to extract ALL services, quantities, and prices
2. Create services for EACH item mentioned
3. Calculate totals automatically
4. Generate appropriate key_features for each service (EXACTLY 4 features per service)
5. Set default GST to 18% if not specified
6. Set shipping to 0 if not specified
7. Return the COMPLETE quotation JSON with all services

SCENARIO PARSING RULES:
- Extract service names from descriptions (e.g., "Car Rental", "Driver Charge", "Website Pages", "Hosting Service")
- Extract quantities from phrases like "Days: 2", "Pages: 5", "quantity: 3", etc.
- Extract prices from phrases like "per day: 500", "per page: 2000", "cost: 3000", etc.
- Handle unit-based pricing (per day, per page, per unit, etc.)
- Calculate total amount for each service: quantity × unit_price

SCENARIO EXAMPLES:

Example 1:
Input: "Travel from Madurai to Chennai. Car rent per day: 500. Days: 2. Driver charge per day: 1000"
Parse:
- Service 1: "Car Rental" - quantity: 2 (Days), unit_price: 500 (per day), amount: 1000
- Service 2: "Driver Charge" - quantity: 2 (Days), unit_price: 1000 (per day), amount: 2000
- Subtotal: 3000
- GST: 18% = 540
- Grand Total: 3540

Example 2:
Input: "Website development. Pages: 5. Cost per page: 2000. Hosting: 3000"
Parse:
- Service 1: "Website Pages" - quantity: 5, unit_price: 2000, amount: 10000
- Service 2: "Hosting Service" - quantity: 1 (Year), unit_price: 3000, amount: 3000
- Subtotal: 13000
- GST: 18% = 2340
- Grand Total: 15340

Example 3:
Input: "Home renovation. Tiles work: 100 sqft at ₹50 per sqft. Painting: 500 sqft at ₹30 per sqft. Plumbing: ₹5000"
Parse:
- Service 1: "Tiles Work" - quantity: 100 (sqft), unit_price: 50, amount: 5000
- Service 2: "Painting Work" - quantity: 500 (sqft), unit_price: 30, amount: 15000
- Service 3: "Plumbing Service" - quantity: 1, unit_price: 5000, amount: 5000
- Subtotal: 25000
- GST: 18% = 4500
- Grand Total: 29500

KEY FEATURES FOR SCENARIO-BASED SERVICES:
- Generate 4 relevant, professional features for each service based on the scenario context
- Features should match the service type (e.g., travel services, web development, construction, etc.)
- Make features descriptive and valuable

IMPORTANT SCENARIO RULES:
1. If the scenario describes a NEW quotation (no existing services), CREATE a fresh quotation
2. If the scenario is ADDED to existing services, ADD the new services to the existing quotation
3. Always preserve existing services unless user explicitly says to reset
4. Extract ALL services mentioned in the scenario - don't miss any
5. If quantity is not specified but implied (e.g., "Hosting: 3000" implies quantity 1), use quantity: 1
6. If unit is not clear, use appropriate default (e.g., "Days", "Units", "Items", "Years")

SCENARIO RESPONSE FORMAT:
Your message should acknowledge the scenario and confirm the quotation was generated:
"I've created a quotation based on your scenario with [X] services. Total: ₹[amount]"

Then return the complete quotation JSON with all parsed services.

----------------------------------
REMOVING SERVICES RULES
----------------------------------
If the user asks to REMOVE or DELETE a service:

1. Extract the service name from the command
2. Find the matching service (case-insensitive, partial match OK)
3. Remove ONLY that service
4. Recalculate totals

Examples of valid REMOVE commands:
- "remove pipeline works quantity 10 and price 1200" → Extract service_name="pipeline works" (ignore quantity/price)
- "remove Tiles Work" → Remove service with name containing "Tiles Work"
- "delete Construction Work" → Remove service with name containing "Construction Work"

IMPORTANT: When parsing "remove X quantity Y price Z":
- The service name is everything before "quantity"/"qty"
- Ignore quantity and price values in remove commands - they're just for identification
- Use the service name to find and remove the matching service

----------------------------------
INVALID PARSE RULE
----------------------------------
If the instruction cannot be parsed clearly:
- Do NOT change quotation
- Return current quotation unchanged
- Respond with a helpful clarification question

----------------------------------
QUESTION RULE
----------------------------------
Ask a question ONLY if required data is missing.
Never repeat already provided information.
Keep questions short and specific.

----------------------------------
BUSINESS RULES
----------------------------------
- Currency: INR (₹)
- Default GST for Indian services: 18% (if not specified)
- Foreign client → GST 0%
- All prices in Indian Rupees

----------------------------------
FAIL-SAFE RULE
----------------------------------
If user instruction is unclear:
- Return current quotation unchanged
- Ask ONE clear clarification question
- Do NOT guess or make assumptions

----------------------------------
JSON VALIDATION
----------------------------------
Before returning:
1. Ensure ALL services have: service_name, quantity, unit_price, amount, key_features
2. Ensure key_features is an array with EXACTLY 4 items (strings)
3. Ensure quotation has: services, subtotal, gst_percentage, gst_amount, shipping, grand_total
4. Ensure all numeric values are numbers (not strings)
5. Ensure amounts are calculated correctly

----------------------------------
REMEMBER
----------------------------------
If the preview is wrong, YOU are wrong.
Your JSON is the single source of truth.
ALWAYS return valid JSON that matches the exact format above."""
    
    def chat_completion(self, messages: list, model_override: Optional[str] = None) -> Optional[str]:
        """Send chat completion request to OpenRouter API with automatic fallback."""
        if not self.api_key:
            print("OpenRouter API Error: API key is not set")
            return None
        
        # Use override model or default model
        current_model = model_override or self.model
        models_to_try = [current_model] + [m for m in self.FALLBACK_MODELS if m != current_model]
        
        # OpenRouter API headers (correct format)
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'HTTP-Referer': 'https://synquot.local',  # Optional: for tracking
            'X-Title': 'SynQuot AI Quotation Maker'  # Optional: app name
        }
        
        # Try each model until one works
        last_error = None
        for model_to_try in models_to_try:
            # Adjust max_tokens based on model type and account credits
            # Free models and accounts with limited credits need lower max_tokens
            max_tokens = 1500  # Reduced from 2000 to fit within credit limits
            if ':free' in model_to_try:
                max_tokens = 1000  # Free models typically have lower limits
            elif 'claude' in model_to_try.lower() or 'gpt' in model_to_try.lower():
                max_tokens = 1200  # Premium models but account might have limited credits
            
            payload = {
                'model': model_to_try,
                'messages': messages,
                'temperature': 0.3,  # Lower temperature for more consistent JSON output
                'max_tokens': max_tokens
            }
            
            # Try to use JSON mode if the model supports it
            json_mode_models = [
                'anthropic/claude',
                'openai/gpt',
                'google/gemini',
                'meta-llama/llama-3',
                'mistralai/mistral'
            ]
            
            if any(model_name in model_to_try.lower() for model_name in json_mode_models):
                try:
                    payload['response_format'] = {'type': 'json_object'}
                except:
                    pass  # Some models don't support this parameter
            
            # Note: The system prompt is strict about returning JSON only.
            # The parsing logic handles both pure JSON and markdown-wrapped JSON.
            
            try:
                # Debug: Print request details (remove in production)
                print(f"OpenRouter API Request - URL: {self.api_url}")
                print(f"OpenRouter API Request - Model: {model_to_try}")
                print(f"OpenRouter API Request - API Key present: {bool(self.api_key)}")
                
                response = requests.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=30
                )
                
                # If successful, return the response
                if response.status_code == 200:
                    response.raise_for_status()
                    data = response.json()
                    
                    # Check for errors in response
                    if 'error' in data:
                        error_detail = data['error']
                        if isinstance(error_detail, dict):
                            last_error = error_detail.get('message', str(error_detail))
                        else:
                            last_error = str(error_detail)
                        print(f"OpenRouter API Error in response: {last_error}")
                        continue  # Try next model
                    
                    # Success! Update the model setting for future use
                    if model_to_try != self.model:
                        print(f"✅ Successfully using fallback model: {model_to_try}")
                    
                    return data.get('choices', [{}])[0].get('message', {}).get('content', '')
                
                # Better error handling with detailed messages
                if response.status_code == 404:
                    try:
                        error_data = response.json() if response.text else {}
                        error_detail = error_data.get('error', {})
                        error_message = error_detail.get('message', 'Unknown error')
                        metadata = error_detail.get('metadata', {})
                        raw_error = metadata.get('raw', '')
                        
                        # Check if it's a model not found error
                        if 'model' in error_message.lower() or 'route' in error_message.lower() or 'matching route' in raw_error.lower() or 'no endpoints found' in error_message.lower():
                            last_error = f"Model '{model_to_try}' not found or not available"
                            print(f"⚠️  Model '{model_to_try}' not available, trying next fallback...")
                            continue  # Try next model
                        else:
                            error_msg = f"OpenRouter API 404 Error: Endpoint not found.\n"
                            error_msg += f"URL: {self.api_url}\n"
                            error_msg += f"Response: {response.text}\n"
                            print(error_msg)
                            last_error = error_msg
                            continue
                    except:
                        error_msg = f"OpenRouter API 404 Error: {response.text}\n"
                        print(error_msg)
                        last_error = error_msg
                        continue
                elif response.status_code == 401:
                    error_msg = "OpenRouter API 401 Error: Invalid API key.\n"
                    error_msg += "Please check your OPENROUTER_API_KEY in Company Details settings.\n"
                    error_msg += f"API Key present: {bool(self.api_key)}, Length: {len(self.api_key) if self.api_key else 0}"
                    print(error_msg)
                    return None  # Don't retry with invalid API key
                elif response.status_code == 402:
                    # Insufficient credits - try with reduced max_tokens
                    try:
                        error_data = response.json() if response.text else {}
                        error_detail = error_data.get('error', {})
                        error_message = error_detail.get('message', '')
                        
                        # Extract available token limit from error message
                        if 'can only afford' in error_message.lower():
                            # Try to extract the number (e.g., "can only afford 1234")
                            import re
                            match = re.search(r'can only afford (\d+)', error_message.lower())
                            if match:
                                available_tokens = int(match.group(1))
                                # Retry with reduced tokens (use 80% of available to be safe)
                                reduced_tokens = int(available_tokens * 0.8)
                                print(f"⚠️  Insufficient credits. Retrying with reduced max_tokens: {reduced_tokens}")
                                
                                # Retry with reduced tokens
                                payload['max_tokens'] = reduced_tokens
                                retry_response = requests.post(
                                    self.api_url,
                                    headers=headers,
                                    json=payload,
                                    timeout=30
                                )
                                
                                if retry_response.status_code == 200:
                                    retry_data = retry_response.json()
                                    if 'error' not in retry_data:
                                        print(f"✅ Successfully used model {model_to_try} with reduced tokens ({reduced_tokens})")
                                        return retry_data.get('choices', [{}])[0].get('message', {}).get('content', '')
                    except Exception as retry_error:
                        print(f"Error during retry with reduced tokens: {retry_error}")
                    
                    error_msg = f"OpenRouter API 402 Error: Insufficient credits.\n"
                    error_msg += f"Response: {response.text}\n"
                    print(error_msg)
                    last_error = error_msg
                    continue  # Try next model
                elif response.status_code != 200:
                    error_msg = f"OpenRouter API Error {response.status_code}:\n"
                    error_msg += f"Response: {response.text}"
                    print(error_msg)
                    last_error = error_msg
                    continue  # Try next model
                
            except requests.exceptions.RequestException as e:
                print(f"OpenRouter API Request Error for model {model_to_try}: {e}")
                last_error = str(e)
                continue  # Try next model
            except Exception as e:
                print(f"OpenRouter API Error for model {model_to_try}: {e}")
                last_error = str(e)
                continue  # Try next model
        
        # All models failed
        error_msg = f"❌ All models failed. Last error: {last_error}\n"
        error_msg += f"\n✅ Tried models:\n"
        for model in models_to_try:
            error_msg += f"  - {model}\n"
        error_msg += f"\n💡 Please check your OPENROUTER_API_KEY in Company Details settings and ensure at least one model is available.\n"
        print(error_msg)
        return None
    
    def parse_response_json(self, response_text: str) -> Optional[Dict[str, Any]]:
        """Parse JSON-only response from AI. Returns dict with 'message' and 'quotation' keys."""
        if not response_text or not response_text.strip():
            return None
        
        # Clean response text - remove markdown code blocks if present
        cleaned_text = response_text.strip()
        
        # Remove markdown code blocks (handle various formats)
        if cleaned_text.startswith('```json'):
            cleaned_text = cleaned_text[7:]
        elif cleaned_text.startswith('```'):
            cleaned_text = cleaned_text[3:]
        if cleaned_text.endswith('```'):
            cleaned_text = cleaned_text[:-3]
        cleaned_text = cleaned_text.strip()
        
        # Remove any leading/trailing whitespace or newlines
        cleaned_text = cleaned_text.strip()
        
        # Try to parse as JSON directly
        try:
            parsed = json.loads(cleaned_text)
            # Validate structure
            if isinstance(parsed, dict) and 'message' in parsed and 'quotation' in parsed:
                # Validate quotation structure
                quotation = parsed.get('quotation', {})
                if isinstance(quotation, dict) and 'services' in quotation:
                    return parsed
        except json.JSONDecodeError as e:
            # Try to find the JSON object in the response
            pass
        
        # Fallback: try to find JSON object in response using multiple strategies
        # Strategy 1: Find first { and last } (handles nested objects)
        try:
            first_brace = cleaned_text.find('{')
            last_brace = cleaned_text.rfind('}')
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                potential_json = cleaned_text[first_brace:last_brace + 1]
                parsed = json.loads(potential_json)
                if isinstance(parsed, dict) and 'message' in parsed and 'quotation' in parsed:
                    quotation = parsed.get('quotation', {})
                    if isinstance(quotation, dict) and 'services' in quotation:
                        return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        
        # Strategy 2: Use regex to find JSON-like structures
        try:
            # More sophisticated pattern that handles nested objects
            json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
            matches = re.finditer(json_pattern, cleaned_text, re.DOTALL)
            
            for match in matches:
                try:
                    potential_json = match.group(0)
                    parsed = json.loads(potential_json)
                    if isinstance(parsed, dict) and 'message' in parsed and 'quotation' in parsed:
                        quotation = parsed.get('quotation', {})
                        if isinstance(quotation, dict) and 'services' in quotation:
                            return parsed
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        
        # Strategy 3: Try to extract JSON from lines (sometimes AI adds text before/after)
        try:
            lines = cleaned_text.split('\n')
            json_lines = []
            in_json = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('{'):
                    in_json = True
                    json_lines = [line]
                elif in_json:
                    json_lines.append(line)
                    if stripped.endswith('}') and stripped.count('{') <= stripped.count('}'):
                        potential_json = '\n'.join(json_lines)
                        parsed = json.loads(potential_json)
                        if isinstance(parsed, dict) and 'message' in parsed and 'quotation' in parsed:
                            quotation = parsed.get('quotation', {})
                            if isinstance(quotation, dict) and 'services' in quotation:
                                return parsed
                        in_json = False
                        json_lines = []
        except (json.JSONDecodeError, ValueError):
            pass
        
        return None
    
    def _get_enhanced_system_prompt(self, intent: str, entities: Dict[str, Any]) -> str:
        """Get enhanced system prompt based on user intent."""
        base_prompt = self.get_system_prompt()
        
        # Add intent-specific guidance
        intent_guidance = {
            'add': "\n\nCURRENT INTENT: User wants to ADD a service.\n"
                   "- If quantity and price are provided, use those values.\n"
                   "- If quantity or price is missing, add service with quantity=0, unit_price=0.\n"
                   "- User can modify these values later. Always add the service if service name is clear.",
            'remove': "\n\nCURRENT INTENT: User wants to REMOVE a service. Use fuzzy matching to find the service name.",
            'change': "\n\nCURRENT INTENT: User wants to CHANGE/UPDATE something. Only modify the specified field.",
            'view': "\n\nCURRENT INTENT: User wants to VIEW the quotation. Return current quotation without modifications.",
            'calculate': "\n\nCURRENT INTENT: User wants to CALCULATE totals. Ensure all calculations are correct.",
            'reset': "\n\nCURRENT INTENT: User wants to RESET the quotation. Return empty quotation structure.",
        }
        
        guidance = intent_guidance.get(intent, "")
        
        # Add entity hints if available
        entity_hints = []
        if entities.get('service_name'):
            entity_hints.append(f"Service name mentioned: {entities['service_name']}")
        if entities.get('quantity'):
            entity_hints.append(f"Quantity mentioned: {entities['quantity']}")
        if entities.get('price'):
            entity_hints.append(f"Price mentioned: ₹{entities['price']}")
        
        if entity_hints:
            guidance += "\n\nEXTRACTED ENTITIES: " + ", ".join(entity_hints)
        
        return base_prompt + guidance
    
    def _enhance_service_names(self, current_quotation: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Enhance service names using AI to format them intelligently."""
        if not current_quotation or not current_quotation.get('services'):
            return "No services to enhance.", current_quotation
        
        # Build enhancement prompt
        services_list = "\n".join([
            f"- {s.get('service_name', 'Unknown')}"
            for s in current_quotation.get('services', [])
        ])
        
        enhancement_prompt = f"""You are a professional quotation formatting assistant.

TASK: Format all service names in the quotation professionally and intelligently.

Current service names:
{services_list}

CRITICAL FORMATTING RULES:
1. Title Case for multi-word services: 
   - "web development" → "Web Development"
   - "mobile app" → "Mobile App"
   - "graphic design" → "Graphic Design"

2. Compound words and single words:
   - "tshirt" → "T-Shirt" (preferred) or "Tshirt" (if more appropriate)
   - "pants" → "Pants" (capitalize first letter)
   - "shirt" → "Shirt"
   - "shoes" → "Shoes"

3. Technical terms (keep as acronyms):
   - "api" → "API"
   - "ui" → "UI"
   - "ux" → "UX"
   - "seo" → "SEO"

4. E-commerce and compound words:
   - "ecommerce" → "E-Commerce"
   - "website" → "Website" (single word, capitalize)
   - "software" → "Software"

5. Professional formatting:
   - Capitalize first letter of each significant word
   - Use hyphens for compound words when it improves readability
   - Keep brand names and proper nouns correctly capitalized
   - Make names professional and business-appropriate

6. IMPORTANT: 
   - Do NOT change quantities, prices, amounts, or any other data
   - ONLY update the service_name field
   - Preserve all other fields exactly as they are
   - Keep the same number of services

EXAMPLES:
- "tshirt" → "T-Shirt"
- "pants" → "Pants"  
- "web development" → "Web Development"
- "mobile app development" → "Mobile App Development"
- "api integration" → "API Integration"

Return the complete quotation JSON with ONLY service_name fields updated. All other fields must remain exactly the same.

Current quotation JSON:
{json.dumps(current_quotation, indent=2)}"""

        messages = [
            {
                "role": "system",
                "content": self.get_system_prompt() + "\n\nENHANCEMENT MODE: Format service names professionally. Return JSON with formatted names only."
            },
            {
                "role": "user",
                "content": enhancement_prompt
            }
        ]
        
        try:
            ai_response = self.chat_completion(messages)
            if not ai_response:
                return "Enhancement failed. Please try again.", current_quotation
            
            parsed_response = self.parse_response_json(ai_response)
            if parsed_response and parsed_response.get('quotation'):
                enhanced_quotation = parsed_response.get('quotation')
                # Normalize and validate
                enhanced_quotation = QuotationManager.normalize_quotation(enhanced_quotation)
                if QuotationManager.validate_quotation(enhanced_quotation):
                    message = parsed_response.get('message', 'Service names have been enhanced professionally.')
                    return message, enhanced_quotation
        except Exception as e:
            print(f"Enhancement error: {e}")
        
        # Fallback: Use simple formatting rules
        return self._fallback_enhance_service_names(current_quotation)
    
    def _fallback_enhance_service_names(self, current_quotation: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Fallback enhancement using simple formatting rules."""
        if not current_quotation or not current_quotation.get('services'):
            return "No services to enhance.", current_quotation
        
        enhanced_services = []
        for service in current_quotation.get('services', []):
            service_name = service.get('service_name', '')
            if service_name:
                # Convert to lowercase first for consistent processing
                name_lower = service_name.lower().strip()
                
                # Handle single words (capitalize first letter)
                if ' ' not in name_lower:
                    # Special cases for compound words
                    if name_lower == 'tshirt' or name_lower == 't-shirt':
                        formatted_name = 'T-Shirt'
                    elif name_lower in ['pants', 'pant']:
                        formatted_name = 'Pants'
                    elif name_lower in ['shirt', 'shirts']:
                        formatted_name = 'Shirt' if name_lower == 'shirt' else 'Shirts'
                    elif name_lower in ['shoes', 'shoe']:
                        formatted_name = 'Shoes' if name_lower == 'shoes' else 'Shoe'
                    else:
                        # Capitalize first letter
                        formatted_name = name_lower.capitalize()
                else:
                    # Multi-word: title case
                    words = name_lower.split()
                    formatted_name = ' '.join([word.capitalize() for word in words])
                
                # Handle technical terms and common replacements
                replacements = {
                    'Api': 'API',
                    'Ui': 'UI',
                    'Ux': 'UX',
                    'Seo': 'SEO',
                    'Ecommerce': 'E-Commerce',
                    'Website': 'Website',
                    'Web Development': 'Web Development',
                    'Mobile App': 'Mobile App',
                }
                
                for old, new in replacements.items():
                    if old in formatted_name:
                        formatted_name = formatted_name.replace(old, new)
                
                enhanced_service = {**service, 'service_name': formatted_name}
            else:
                enhanced_service = service
            enhanced_services.append(enhanced_service)
        
        enhanced_quotation = {
            **current_quotation,
            'services': enhanced_services
        }
        enhanced_quotation = QuotationManager.calculate_totals(enhanced_quotation)
        
        return "Service names have been enhanced.", enhanced_quotation
    
    def _generate_key_features(self, service_name: str) -> List[str]:
        """Generate 4 relevant key features for a service based on its name."""
        service_lower = service_name.lower()
        
        # Generic features that work for most services
        generic_features = [
            "Professional quality service",
            "Expert team handling",
            "Timely delivery guaranteed",
            "Customer satisfaction priority"
        ]
        
        # Service-specific features based on keywords
        if any(word in service_lower for word in ['web', 'website', 'site', 'app', 'application', 'software', 'development']):
            return [
                "Responsive design for all devices",
                "SEO optimized for better visibility",
                "Fast loading speed and performance",
                "Mobile-friendly interface"
            ]
        elif any(word in service_lower for word in ['construction', 'building', 'work', 'renovation', 'repair']):
            return [
                "High-quality materials used",
                "Experienced professionals",
                "Timely project completion",
                "Quality assurance guaranteed"
            ]
        elif any(word in service_lower for word in ['marketing', 'advertising', 'promotion', 'branding']):
            return [
                "Targeted audience reach",
                "Data-driven strategies",
                "ROI-focused campaigns",
                "Multi-channel approach"
            ]
        elif any(word in service_lower for word in ['design', 'graphic', 'logo', 'creative']):
            return [
                "Modern and creative designs",
                "Brand-aligned aesthetics",
                "Multiple design options",
                "Professional quality output"
            ]
        elif any(word in service_lower for word in ['consulting', 'advisory', 'strategy']):
            return [
                "Expert industry knowledge",
                "Customized solutions",
                "Strategic planning support",
                "Ongoing guidance provided"
            ]
        else:
            return generic_features
    
    def _handle_error(self, error_type: str, user_message: str, current_quotation: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Handle different types of errors with appropriate messages."""
        error_messages = {
            'api_connection': "I'm having trouble connecting to the AI service. Please check your internet connection and try again.",
            'api_key': "API configuration error. Please contact support.",
            'parse_error': "I couldn't understand the response format. Let me try a different approach.",
            'validation_error': "There was an issue with the quotation format. I've kept your current quotation safe.",
            'timeout': "The request took too long. Please try again with a simpler request.",
            'rate_limit': "Too many requests. Please wait a moment and try again.",
        }
        
        message = error_messages.get(error_type, "An unexpected error occurred. Please try again.")
        
        # Try fallback processing for parse errors
        if error_type == 'parse_error':
            return self._fallback_processing(user_message, current_quotation)
        
        return message, current_quotation
    
    def _fallback_processing(self, user_message: str, current_quotation: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Fallback processing when AI response parsing fails."""
        intent = IntentClassifier.classify(user_message)
        entities = IntentClassifier.extract_entities(user_message)
        
        user_lower = user_message.lower()
        
        # Handle remove with fuzzy matching
        if intent == 'remove' and entities.get('service_name'):
            service_name = entities['service_name']
            matched_service = FuzzyMatcher.find_service_by_name(service_name, current_quotation.get('services', []))
            
            if matched_service:
                # Remove the matched service
                current_quotation['services'] = [
                    s for s in current_quotation['services']
                    if s.get('service_name', '').lower() != matched_service.get('service_name', '').lower()
                ]
                current_quotation = QuotationManager.calculate_totals(current_quotation)
                return f"I've removed '{matched_service.get('service_name')}' from the quotation.", current_quotation
            else:
                return f"I couldn't find a service matching '{service_name}'. Please check the service name and try again.", current_quotation
        
        # Handle add with extracted entities
        elif intent == 'add' and entities.get('service_name'):
            service_name = entities['service_name']
            quantity = entities.get('quantity', 0)  # Default to 0 if not provided
            price = entities.get('price', 0)  # Default to 0 if not provided
            
            # Generate key features based on service name
            key_features = self._generate_key_features(service_name)
            
            new_service = {
                'service_name': service_name,
                'quantity': quantity,
                'unit_price': price,
                'amount': round(quantity * price, 2),
                'key_features': key_features
            }
            current_quotation['services'].append(new_service)
            current_quotation = QuotationManager.calculate_totals(current_quotation)
            
            if quantity > 0 and price > 0:
                return f"I've added '{service_name}' with quantity {quantity} and price ₹{price:,.2f}.", current_quotation
            else:
                return f"I've added '{service_name}' with quantity 0 and price 0. You can modify these values later.", current_quotation
        
        # Handle view
        elif intent == 'view':
            service_count = len(current_quotation.get('services', []))
            total = current_quotation.get('grand_total', 0)
            return f"Current quotation has {service_count} service(s) with a grand total of ₹{total:,.2f}.", current_quotation
        
        # Handle reset
        elif intent == 'reset':
            current_quotation = QuotationManager.initialize_quotation()
            return "I've reset the quotation. You can now start adding new services.", current_quotation
        
        # Generic fallback
        return "I'm having trouble processing your request. Could you please rephrase it? For example: 'Add service [name] with quantity [number] and price [amount]'.", current_quotation
    
    def process_user_message(
        self, 
        user_message: str, 
        current_quotation: Optional[Dict[str, Any]] = None,
        conversation_history: list = None,
        enhance_mode: bool = False
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Process user message and return AI response with updated quotation.
        
        Returns:
            Tuple of (message, updated_quotation_json)
        """
        if conversation_history is None:
            conversation_history = []
        
        # Initialize quotation if not provided
        if current_quotation is None:
            current_quotation = QuotationManager.initialize_quotation()
        
        # Check if this is enhancement mode
        if enhance_mode or "ENHANCE_QUOTATION" in user_message.upper():
            return self._enhance_service_names(current_quotation)
        
        # Classify intent and extract entities
        intent = IntentClassifier.classify(user_message)
        entities = IntentClassifier.extract_entities(user_message)
        
        # If user is adding a service, check past conversations for pricing suggestions
        pricing_suggestion = None
        if intent == 'add' and entities.get('service_name'):
            service_name = entities['service_name']
            pricing_suggestion = ConversationHistoryService.get_suggested_pricing(service_name)
        
        # Check cache for simple queries (view, calculate)
        if intent in ['view', 'calculate']:
            cache_key = self._get_cache_key(user_message, current_quotation)
            cached = self._get_cached_response(cache_key)
            if cached:
                return cached.get('message', ''), cached.get('quotation', current_quotation)
        
        # Optimize conversation history
        optimized_history = ConversationOptimizer.optimize_history(conversation_history)
        
        # Add summary if history is long
        history_summary = ""
        if len(optimized_history) > ConversationOptimizer.SUMMARY_THRESHOLD:
            history_summary = ConversationOptimizer.summarize_conversation(optimized_history[:-10])
            optimized_history = optimized_history[-10:]  # Keep only last 10 for API
        
        # Build messages for API
        messages = [
            {
                "role": "system",
                "content": self._get_enhanced_system_prompt(intent, entities)
            }
        ]
        
        # Add conversation summary if available
        if history_summary:
            messages.append({
                "role": "system",
                "content": f"Conversation summary: {history_summary}"
            })
        
        # Add conversation history
        messages.extend(optimized_history)
        
        # Add intelligent pricing suggestion context if available
        if pricing_suggestion and intent == 'add' and entities.get('service_name'):
            suggested_price = pricing_suggestion.get('suggested_unit_price', 0)
            suggested_qty = pricing_suggestion.get('suggested_quantity', 1)
            past_count = pricing_suggestion.get('based_on_conversations', 0)
            past_examples = pricing_suggestion.get('past_examples', [])
            price_range = pricing_suggestion.get('price_range', {})
            
            if suggested_price > 0:
                examples_text = "\n".join([
                    f"  - {ex.get('service_name', '')}: {ex.get('quantity', 0)} units × ₹{ex.get('unit_price', 0):,.2f} = ₹{ex.get('amount', 0):,.2f}"
                    for ex in past_examples[:5]  # Show more examples for better context
                ])
                
                price_range_text = ""
                if price_range.get('min') and price_range.get('max'):
                    price_range_text = f"\n- Price range from past: ₹{price_range['min']:,.2f} - ₹{price_range['max']:,.2f} per unit"
                
                pricing_context = f"""
----------------------------------
INTELLIGENT PRICING FROM PAST CONVERSATIONS
----------------------------------
Service: "{entities.get('service_name', '')}"

Based on {past_count} past conversation(s) with similar services, here are intelligent pricing suggestions:

RECOMMENDED PRICING:
- Suggested unit price: ₹{suggested_price:,.2f} per unit
- Suggested quantity: {suggested_qty} units
- Suggested total amount: ₹{suggested_price * suggested_qty:,.2f}
{price_range_text}

PAST EXAMPLES (for reference):
{examples_text if examples_text else "No specific examples available."}

INTELLIGENT USAGE RULES:
1. If user provides NO quantity/price → AUTOMATICALLY use suggested pricing above
2. If user provides PARTIAL info (only quantity OR only price) → Fill missing value from suggestions
3. If user provides FULL info (both quantity AND price) → Use user's values (they override suggestions)
4. Always mention in your message when using past pricing: "Based on past quotations, I've set the price to ₹X"

This pricing is based on REAL past quotations, so it's reliable and intelligent.
"""
                messages.append({
                    "role": "system",
                    "content": pricing_context
                })
        
        # Add current quotation context (formatted better)
        services_list = "\n".join([
            f"- {s.get('service_name', 'Unknown')}: Qty {s.get('quantity', 0)} × ₹{s.get('unit_price', 0):,.2f} = ₹{s.get('amount', 0):,.2f}"
            for s in current_quotation.get('services', [])
        ])
        
        quotation_context = f"""Current quotation state:
Services:
{services_list if services_list else "No services added yet."}

Totals:
- Subtotal: ₹{current_quotation.get('subtotal', 0):,.2f}
- GST ({current_quotation.get('gst_percentage', 0)}%): ₹{current_quotation.get('gst_amount', 0):,.2f}
- Grand Total: ₹{current_quotation.get('grand_total', 0):,.2f}"""
        
        messages.append({
            "role": "system",
            "content": quotation_context
        })
        
        # Add user message
        messages.append({
            "role": "user",
            "content": user_message
        })
        
        # Get AI response
        try:
            ai_response = self.chat_completion(messages)
        except requests.exceptions.Timeout:
            return self._handle_error('timeout', user_message, current_quotation)
        except requests.exceptions.RequestException as e:
            if '429' in str(e) or 'rate limit' in str(e).lower():
                return self._handle_error('rate_limit', user_message, current_quotation)
            return self._handle_error('api_connection', user_message, current_quotation)
        
        if not ai_response:
            return self._handle_error('api_connection', user_message, current_quotation)
        
        # Parse JSON response (should contain both message and quotation)
        parsed_response = self.parse_response_json(ai_response)
        
        if parsed_response:
            message = parsed_response.get('message', 'I\'ve updated the quotation.')
            updated_quotation = parsed_response.get('quotation', current_quotation)
            
            # Normalize the quotation to ensure consistency
            updated_quotation = QuotationManager.normalize_quotation(updated_quotation)
            
            # Validate the normalized quotation
            if not QuotationManager.validate_quotation(updated_quotation):
                return self._handle_error('validation_error', user_message, current_quotation)
            
            # Cache response for view/calculate intents
            if intent in ['view', 'calculate']:
                cache_key = self._get_cache_key(user_message, current_quotation)
                self._set_cached_response(cache_key, {
                    'message': message,
                    'quotation': updated_quotation
                })
            
            return message, updated_quotation
        
        # Fallback processing if parsing fails
        return self._handle_error('parse_error', user_message, current_quotation)
        


class QuotationManager:
    """Manager for quotation operations."""
    
    @staticmethod
    def initialize_quotation() -> Dict[str, Any]:
        """Initialize empty quotation structure."""
        return {
            "services": [],
            "subtotal": 0,
            "gst_percentage": 0,
            "gst_amount": 0,
            "shipping": 0,
            "grand_total": 0
        }
    
    @staticmethod
    def calculate_totals(quotation: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate subtotal, GST, shipping, and grand_total from services."""
        services = quotation.get("services", [])
        
        # Recalculate amounts for each service
        for service in services:
            # Support both unit_price and legacy fields (price/unit_rate) for backward compatibility
            unit_price = service.get("unit_price") or service.get("price") or service.get("unit_rate", 0)
            quantity = service.get("quantity", 0)
            
            # Ensure numeric types
            try:
                unit_price = float(unit_price) if unit_price else 0.0
                quantity = int(float(quantity)) if quantity else 0
            except (ValueError, TypeError):
                unit_price = 0.0
                quantity = 0
            
            # Ensure unit_price field exists
            if "unit_price" not in service:
                service["unit_price"] = unit_price
            
            # Calculate amount
            service["amount"] = round(unit_price * quantity, 2)
        
        # Calculate subtotal
        subtotal = sum(
            float(service.get("amount", 0) or 0)
            for service in services
        )
        
        quotation["subtotal"] = round(subtotal, 2)
        
        # Calculate GST
        gst_percentage = quotation.get("gst_percentage", 0)
        try:
            gst_percentage = float(gst_percentage) if gst_percentage else 0.0
        except (ValueError, TypeError):
            gst_percentage = 0.0
        
        # Set default GST to 18% if services exist and GST is 0
        if gst_percentage == 0 and len(services) > 0:
            # Check if user explicitly set GST to 0 (by checking if it was in the original)
            # For now, we'll keep it at 0 unless explicitly set
            pass
        
        quotation["gst_percentage"] = gst_percentage
        
        if gst_percentage > 0:
            gst_amount = (subtotal * gst_percentage) / 100
        else:
            gst_amount = 0
        
        quotation["gst_amount"] = round(gst_amount, 2)

        # Normalize shipping (flat amount, not percentage)
        shipping = quotation.get("shipping", 0) or 0
        try:
            shipping = float(shipping)
        except (ValueError, TypeError):
            shipping = 0.0
        if shipping < 0:
            shipping = 0.0
        quotation["shipping"] = round(shipping, 2)

        # Grand total includes subtotal + GST + shipping
        quotation["grand_total"] = round(subtotal + gst_amount + shipping, 2)
        
        return quotation
    
    @staticmethod
    def validate_quotation(quotation: Dict[str, Any]) -> bool:
        """Validate quotation structure."""
        if not isinstance(quotation, dict):
            return False
        
        if "services" not in quotation:
            return False
        
        if not isinstance(quotation["services"], list):
            return False
        
        # Validate each service
        for service in quotation["services"]:
            if not isinstance(service, dict):
                return False
            
            # Required fields
            if "service_name" not in service or not service["service_name"]:
                return False
            
            # Quantity must be present and numeric
            if "quantity" not in service:
                return False
            try:
                quantity = float(service["quantity"])
                if quantity < 0:
                    return False
            except (ValueError, TypeError):
                return False
            
            # Must have unit_price, price, or unit_rate (for backward compatibility)
            has_price = False
            if "unit_price" in service:
                try:
                    float(service["unit_price"])
                    has_price = True
                except (ValueError, TypeError):
                    pass
            if not has_price and "price" in service:
                try:
                    float(service["price"])
                    has_price = True
                except (ValueError, TypeError):
                    pass
            if not has_price and "unit_rate" in service:
                try:
                    float(service["unit_rate"])
                    has_price = True
                except (ValueError, TypeError):
                    pass
            
            if not has_price:
                return False
            
            # Amount should be present (will be recalculated if missing)
            if "amount" not in service:
                # This is OK, we'll recalculate it
                pass
        
        return True
    
    @staticmethod
    def normalize_quotation(quotation: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize quotation structure to ensure consistency."""
        if not isinstance(quotation, dict):
            quotation = QuotationManager.initialize_quotation()
        
        # Ensure services list exists
        if "services" not in quotation or not isinstance(quotation["services"], list):
            quotation["services"] = []
        
        # Normalize each service
        for service in quotation["services"]:
            if not isinstance(service, dict):
                continue
            
            # Ensure service_name is a string
            if "service_name" not in service or not service["service_name"]:
                service["service_name"] = "Unnamed Service"
            
            # Normalize price fields - ensure unit_price exists
            unit_price = service.get("unit_price") or service.get("price") or service.get("unit_rate", 0)
            try:
                unit_price = float(unit_price)
            except (ValueError, TypeError):
                unit_price = 0
            
            service["unit_price"] = unit_price
            # Keep backward compatibility
            if "price" not in service:
                service["price"] = unit_price
            if "unit_rate" not in service:
                service["unit_rate"] = unit_price
            
            # Normalize quantity
            quantity = service.get("quantity", 1)
            try:
                quantity = int(float(quantity))
                if quantity < 0:
                    quantity = 0
            except (ValueError, TypeError):
                quantity = 0
            service["quantity"] = quantity
            
            # Recalculate amount
            service["amount"] = round(unit_price * quantity, 2)
            
            # Ensure key_features exists and has exactly 4 items
            if "key_features" not in service or not isinstance(service["key_features"], list):
                service["key_features"] = []
            
            # Ensure exactly 4 features (pad with empty strings or generate defaults if needed)
            while len(service["key_features"]) < 4:
                service["key_features"].append("")
            # Trim to 4 if more than 4
            service["key_features"] = service["key_features"][:4]
        
        # Ensure all required quotation fields exist
        if "subtotal" not in quotation:
            quotation["subtotal"] = 0
        if "gst_percentage" not in quotation:
            quotation["gst_percentage"] = 0
        if "gst_amount" not in quotation:
            quotation["gst_amount"] = 0
        if "shipping" not in quotation:
            quotation["shipping"] = 0
        if "grand_total" not in quotation:
            quotation["grand_total"] = 0
        
        # Recalculate totals
        quotation = QuotationManager.calculate_totals(quotation)
        
        return quotation

