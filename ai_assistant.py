"""
AI Assistant Module for Cleaning Service System
Provides natural language processing for admin operations via Telegram or API
Uses Groq API with openai/gpt-oss-20b model
"""

import os
import json
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

# DB drivers
try:
    import mysql.connector
except ImportError:
    mysql = None

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None


# Try to import Groq - primary AI provider
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    Groq = None

class AIAssistant:
    """AI Assistant for cleaning service management"""
    
    def __init__(self, db_config: Dict[str, Any]):
        self.db_config = db_config
        self.db_engine = (db_config.get('engine') or 'mysql').strip().lower()
        self.ai_provider = None
        self.api_key = None
        self.model = None
        self.enabled = False
        self.max_tokens = 8192
        self.temperature = 1.0
        self.reasoning_effort = 'medium'
        self.tools = self._define_tools()  # Define available tools
        self._load_settings()
    
    def _define_tools(self) -> List[Dict]:
        """Define tools available to the AI (kept compact to save tokens)."""
        return [
            {"type": "function", "function": {
                "name": "search_requests",
                "description": "Search requests by name/email/ref/status",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"},
                    "status": {"type": ["string", "null"], "enum": ["pending", "in_progress", "completed", "cancelled", "survey_needed", None]}
                }, "required": ["query"]}
            }},
            {"type": "function", "function": {
                "name": "update_request_status",
                "description": "Update a request status",
                "parameters": {"type": "object", "properties": {
                    "ref_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled", "survey_needed"]}
                }, "required": ["ref_id", "status"]}
            }},
            {"type": "function", "function": {
                "name": "add_request_note",
                "description": "Add admin note to a request",
                "parameters": {"type": "object", "properties": {
                    "ref_id": {"type": "string"},
                    "note": {"type": "string"}
                }, "required": ["ref_id", "note"]}
            }},
            {"type": "function", "function": {
                "name": "get_request_details",
                "description": "Get full details of a request",
                "parameters": {"type": "object", "properties": {
                    "ref_id": {"type": "string"}
                }, "required": ["ref_id"]}
            }},
            {"type": "function", "function": {
                "name": "get_daily_report",
                "description": "Today's request counts and status breakdown",
                "parameters": {"type": "object", "properties": {}, "required": []}
            }},
            {"type": "function", "function": {
                "name": "get_pending_requests",
                "description": "List all pending requests",
                "parameters": {"type": "object", "properties": {}, "required": []}
            }},
            {"type": "function", "function": {
                "name": "get_revenue_report",
                "description": "Revenue report",
                "parameters": {"type": "object", "properties": {
                    "days": {"type": "integer"}
                }, "required": []}
            }},
            {"type": "function", "function": {
                "name": "send_customer_email",
                "description": "Send email to customer",
                "parameters": {"type": "object", "properties": {
                    "ref_id": {"type": "string"},
                    "subject": {"type": "string"},
                    "message": {"type": "string"}
                }, "required": ["ref_id", "subject", "message"]}
            }},
            {"type": "function", "function": {
                "name": "list_services",
                "description": "List services",
                "parameters": {"type": "object", "properties": {
                    "active_only": {"type": "boolean"}
                }, "required": []}
            }},
            {"type": "function", "function": {
                "name": "get_customer_history",
                "description": "Get customer request history by email or phone",
                "parameters": {"type": "object", "properties": {
                    "email": {"type": "string"},
                    "phone": {"type": "string"}
                }, "required": []}
            }}
        ]
    
    def _get_db_connection(self):
        """Get database connection"""
        if self.db_engine == 'postgres':
            dsn = (self.db_config.get('postgres_url') or '').strip()
            if not dsn:
                raise ValueError('Postgres engine selected but postgres_url is not configured')
            if psycopg2 is None:
                raise RuntimeError('psycopg2 is not installed (install psycopg2-binary)')

            raw = psycopg2.connect(dsn)

            class _PGConnWrapper:
                def __init__(self, conn):
                    self._conn = conn

                def cursor(self, dictionary=False, *args, **kwargs):
                    if dictionary:
                        if RealDictCursor is None:
                            return self._conn.cursor(*args, **kwargs)
                        return self._conn.cursor(cursor_factory=RealDictCursor)
                    return self._conn.cursor(*args, **kwargs)

                def commit(self):
                    return self._conn.commit()

                def close(self):
                    return self._conn.close()

                def __getattr__(self, name):
                    return getattr(self._conn, name)

            return _PGConnWrapper(raw)

        if mysql is None and mysql.connector is None:
            raise RuntimeError('mysql-connector-python is not installed')
        # MySQL config should not include 'engine'
        mysql_config = {k: v for k, v in self.db_config.items() if k != 'engine'}
        return mysql.connector.connect(**mysql_config)

    @staticmethod
    def _day_bounds_utc() -> Tuple[datetime, datetime]:
        now = datetime.utcnow()
        start = datetime(now.year, now.month, now.day)
        end = start + timedelta(days=1)
        return start, end
    
    def _load_settings(self):
        """Load AI settings from database"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM ai_settings WHERE id = 1")
            settings = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if settings:
                self.ai_provider = settings.get('ai_provider', 'groq')
                self.api_key = settings.get('api_key', '')
                self.model = settings.get('model', 'openai/gpt-oss-20b')
                self.enabled = bool(settings.get('is_enabled', 0))
                self.max_tokens = settings.get('max_tokens', 8192)
                self.temperature = float(settings.get('temperature', 1.0))
                self.reasoning_effort = settings.get('reasoning_effort', 'medium')
        except Exception as e:
            print(f"AI Settings load error: {e}")
            self.enabled = False
    
    def reload_settings(self):
        """Reload settings from database"""
        self._load_settings()
    
    def is_ready(self) -> Tuple[bool, str]:
        """Check if AI assistant is ready to use"""
        if not self.enabled:
            return False, "AI Assistant is disabled. Enable it in Admin Settings."
        
        if not self.api_key:
            return False, "API key not configured. Add your API key in Admin Settings."
        
        if self.ai_provider == 'groq' and not GROQ_AVAILABLE:
            return False, "Groq package not installed. Run: pip install groq"
     
        return True, "Ready"
    
    # Keywords that warrant injecting live business data into the system prompt
    _BUSINESS_KEYWORDS = (
        'pending', 'request', 'booking', 'summary', 'report', 'revenue',
        'service', 'customer', 'status', 'today', 'completed', 'cancel',
        'analytics', 'stats', 'how are', 'show', 'list', 'search', 'find',
        'mark', 'update', 'note', 'email', 'disable', 'enable', 'history',
        'ref-', 'ref ', 'invoice', 'payment', 'job', 'application',
    )

    def _needs_business_context(self, message: str) -> bool:
        """Return True if the message is a business query needing live DB data."""
        lower = message.lower()
        return any(kw in lower for kw in self._BUSINESS_KEYWORDS)

    def get_system_context(self, include_live_data: bool = False) -> str:
        """Build system context. Live DB data only injected when actually needed."""
        now_str = datetime.now().strftime('%A, %d %B %Y at %H:%M')

        base = (
            f"You are the AI assistant for Done-Well Cleaners, a professional cleaning service.\n"
            f"You are chatting with the business owner/admin via Telegram.\n"
            f"Be friendly, concise, and helpful. Current time: {now_str}.\n"
            f"You can look up requests, update statuses, search customers, and run reports.\n"
            f"Keep replies short — this is a chat interface."
        )

        if not include_live_data:
            return base

        # Only fetch DB data when the user is asking a business question
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("SELECT status, COUNT(*) as count FROM requests GROUP BY status")
            request_stats = {row['status']: row['count'] for row in cursor.fetchall()}

            start_today, end_today = self._day_bounds_utc()
            cursor.execute(
                "SELECT COUNT(*) as count FROM requests WHERE created_at >= %s AND created_at < %s",
                (start_today, end_today)
            )
            today_row = cursor.fetchone()
            today_total = today_row['count'] if today_row else 0

            cursor.close()
            conn.close()

            pending = request_stats.get('pending', 0)
            attention = " ⚠️ NEEDS ATTENTION" if pending > 5 else ""
            snapshot = (
                f"\nLIVE SNAPSHOT — Pending:{pending}{attention} | "
                f"In Progress:{request_stats.get('in_progress', 0)} | "
                f"Completed:{request_stats.get('completed', 0)} | "
                f"Today:{today_total}"
            )
            return base + snapshot
        except Exception:
            return base
    
    def _call_groq(self, messages: List[Dict]) -> str:
        """Call Groq API with streaming (no tools)"""
        if not GROQ_AVAILABLE:
            return "Groq not available"
        
        client = Groq(api_key=self.api_key)
        
        # Groq streaming completion - no tools for streaming
        completion = client.chat.completions.create(
            model=self.model or "openai/gpt-oss-20b",
            messages=messages,
            temperature=self.temperature,
            max_completion_tokens=self.max_tokens,
            top_p=1,
            stream=True,
            stop=None
        )
        
        # Collect streamed response
        response_text = ""
        for chunk in completion:
            if chunk.choices[0].delta.content:
                response_text += chunk.choices[0].delta.content
        
        return response_text
    
    def _call_groq_with_tools(self, messages: List[Dict]) -> Dict[str, Any]:
        """Call Groq API with function calling/tools support"""
        if not GROQ_AVAILABLE:
            return {"content": "Groq not available", "tool_calls": None}
        
        client = Groq(api_key=self.api_key)
        
        # Build API parameters
        api_params = {
            "model": self.model or "openai/gpt-oss-20b",
            "messages": messages,
            "temperature": self.temperature,
            "max_completion_tokens": min(self.max_tokens, 600),
            "top_p": 1,
            "stream": False
        }
        
        # Only add tools if they exist - with tool_choice="auto" to enable tool use
        if self.tools:
            api_params["tools"] = self.tools
            api_params["tool_choice"] = "auto"  # CRITICAL: Allow AI to use tools
        
        completion = client.chat.completions.create(**api_params)
        
        message = completion.choices[0].message
        
        return {
            "content": message.content or "",
            "tool_calls": message.tool_calls if hasattr(message, 'tool_calls') else None
        }
    
    def _call_groq_non_streaming(self, messages: List[Dict]) -> str:
        """Call Groq API without streaming (legacy - no tools)"""
        if not GROQ_AVAILABLE:
            return "Groq not available"
        
        client = Groq(api_key=self.api_key)
        
        completion = client.chat.completions.create(
            model=self.model or "openai/gpt-oss-20b",
            messages=messages,
            temperature=self.temperature,
            max_completion_tokens=self.max_tokens,
            top_p=1,
            stream=False,
            stop=None
        )
        
        return completion.choices[0].message.content
    
    def process_message(self, user_message: str, chat_id: str = None) -> Dict[str, Any]:
        """Process a user message and return response with any actions"""
        ready, error = self.is_ready()
        if not ready:
            return {"success": False, "message": error, "action": None}
        
        # Build messages — only inject live DB data if the message is a business query
        system_context = self.get_system_context(
            include_live_data=self._needs_business_context(user_message)
        )
        messages = [
            {"role": "system", "content": system_context},
            {"role": "user", "content": user_message}
        ]

        # Add conversation history if available
        if chat_id:
            history = self._get_conversation_history(chat_id, limit=3)
            if history:
                # Insert history before user message
                for hist in history:
                    messages.insert(-1, {"role": hist['role'], "content": hist['content']})

        # Token guard: tools schema ~1500 tokens, system ~120, leave 512 for reply.
        # Hard cap input at 5500 tokens → ~16500 chars at 3 chars/token.
        TOKEN_LIMIT = 5500
        CHARS_PER_TOKEN = 3
        char_budget = TOKEN_LIMIT * CHARS_PER_TOKEN
        total_chars = sum(len(m.get('content') or '') for m in messages)
        # Strip all history first if over budget
        if total_chars > char_budget and len(messages) > 2:
            messages = [messages[0], messages[-1]]
            total_chars = sum(len(m.get('content') or '') for m in messages)
        # Trim individual history messages if still over
        while total_chars > char_budget and len(messages) > 2:
            removed = messages.pop(1)
            total_chars -= len(removed.get('content') or '')
        # Truncate user message as last resort
        if total_chars > char_budget:
            max_user_chars = char_budget - len(messages[0].get('content') or '')
            if max_user_chars > 200:
                messages[-1]['content'] = messages[-1]['content'][:max_user_chars]
        
        try:
            # Call AI based on provider - use tools for Groq
            if self.ai_provider == 'groq':
                result = self._call_groq_with_tools(messages)
                response = result['content']
                tool_calls = result.get('tool_calls')
                
                # Process tool calls if any
                action_result = None
                if tool_calls:
                    for tool_call in tool_calls:
                        func_name = tool_call.function.name
                        func_args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
                        
                        # Execute the tool
                        action_result = self._execute_tool(func_name, func_args)
                        
                        # Format and append result
                        formatted = self._format_tool_result(func_name, action_result)
                        response += formatted
                
            elif self.ai_provider == 'anthropic':
                response = self._call_anthropic(messages)
                action_result = None
            else:
                response = self._call_openai(messages)
                action_result = None
            
            # For non-Groq providers, try to extract action from response text
            if self.ai_provider != 'groq':
                action = self._extract_action(response)
                if action:
                    action_result = self._execute_action(action)
                    if action_result.get('success'):
                        response += f"\n\n✅ Action completed: {action_result.get('message', 'Done')}"
                    else:
                        response += f"\n\n❌ Action failed: {action_result.get('message', 'Error')}"
            
            # Save conversation
            if chat_id:
                self._save_conversation(chat_id, user_message, response)
            
            return {
                "success": True,
                "message": response,
                "action": None,
                "action_result": action_result
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"AI Error: {str(e)}",
                "action": None
            }
    
    def _execute_tool(self, tool_name: str, params: Dict) -> Dict[str, Any]:
        """Execute a tool/function by name"""
        try:
            if tool_name == 'search_requests':
                return self._action_search_requests(params)
            elif tool_name == 'update_request_status':
                return self._action_update_status(params)
            elif tool_name == 'add_request_note':
                return self._action_add_note(params)
            elif tool_name == 'get_request_details':
                return self._action_get_request(params)
            elif tool_name == 'get_service_info':
                return self._action_get_service(params)
            elif tool_name == 'get_daily_report':
                return self._action_daily_report()
            elif tool_name == 'get_pending_requests':
                return self._action_pending_requests()
            elif tool_name == 'get_revenue_report':
                return self._action_get_revenue_report(params)
            elif tool_name == 'get_top_services':
                return self._action_get_top_services(params)
            elif tool_name == 'send_customer_email':
                return self._action_send_customer_email(params)
            elif tool_name == 'get_analytics_summary':
                return self._action_get_analytics_summary(params)
            elif tool_name == 'toggle_service':
                return self._action_toggle_service(params)
            elif tool_name == 'list_services':
                return self._action_list_services(params)
            elif tool_name == 'get_customer_history':
                return self._action_get_customer_history(params)
            else:
                return {"success": False, "message": f"Unknown tool: {tool_name}"}
        except Exception as e:
            return {"success": False, "message": f"Tool error: {str(e)}"}
    
    def _extract_action(self, response: str) -> Optional[Dict]:
        """Extract action JSON from response"""
        # Look for ```action ... ``` block
        pattern = r'```action\s*\n?(.*?)\n?```'
        match = re.search(pattern, response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                return None
        return None
    
    def _format_tool_result(self, tool_name: str, result: Dict) -> str:
        """Format tool results for nice Telegram display"""
        if not result:
            return f"\n\n❌ Tool returned no result"
        
        if not result.get('success'):
            return f"\n\n❌ {result.get('message', 'Action failed')}"
        
        data = result.get('data')
        msg = result.get('message', 'Done')
        
        # Format based on tool type
        try:
            if tool_name == 'get_pending_requests' or tool_name == 'search_requests':
                return self._format_request_list(data, msg)
            elif tool_name == 'get_request_details':
                return self._format_request_details(data)
            elif tool_name == 'get_daily_report':
                return self._format_daily_report(data)
            elif tool_name == 'get_revenue_report':
                return self._format_revenue_report(data)
            elif tool_name == 'get_top_services':
                return self._format_top_services(data)
            elif tool_name == 'list_services':
                return self._format_services_list(data)
            elif tool_name == 'get_customer_history':
                return self._format_request_list(data, msg)
            elif tool_name == 'get_analytics_summary':
                return self._format_analytics(data)
            else:
                # Generic formatting for other tools
                return f"\n\n✅ {msg}"
        except Exception as e:
            # Fallback if formatting fails
            return f"\n\n✅ {msg}\n(Data: {str(data)[:200]}...)" if data else f"\n\n✅ {msg}"
    
    def _to_dict(self, row):
        """Convert database row to regular dict (handles RealDictRow, etc)"""
        if row is None:
            return {}
        if isinstance(row, dict):
            return dict(row)
        # Try to convert row-like objects
        try:
            return dict(row)
        except:
            return {}
    
    def _format_request_list(self, requests: list, title: str) -> str:
        """Format a list of requests nicely"""
        if not requests:
            return "\n\n📋 No requests found"
        
        output = f"\n\n📋 *{title}*\n"
        output += "━" * 25 + "\n"
        
        for i, row in enumerate(requests, 1):
            req = self._to_dict(row)
            ref = str(req.get('ref_id', 'N/A'))[:10]
            name = str(req.get('name', 'Unknown'))[:20]
            req_type = req.get('request_type', '')
            
            # Get service or job position
            service = ''
            if req.get('service_name'):
                service = str(req.get('service_name'))[:25]
            elif req.get('job_position'):
                service = str(req.get('job_position'))[:25]
            
            created = req.get('created_at')
            
            # Format date
            date_str = ""
            if created:
                if hasattr(created, 'strftime'):
                    date_str = created.strftime('%d/%m')
                else:
                    date_str = str(created)[:10]
            
            # Type emoji
            type_emoji = {'service': '🧹', 'job': '💼', 'general': '📩', 'quote': '💰'}.get(req_type, '📌')
            
            output += f"{i}. {type_emoji} `{ref}`\n"
            output += f"   👤 {name}"
            if date_str:
                output += f" • 📅 {date_str}"
            output += "\n"
            if service:
                output += f"   📦 {service}\n"
        
        output += "━" * 25 + "\n"
        output += f"💡 _Reply with a number to see details_"
        return output
    
    def _format_request_details(self, row) -> str:
        """Format single request details nicely"""
        if not row:
            return "\n\n❌ Request not found"
        
        req = self._to_dict(row)
        ref = req.get('ref_id', 'N/A')
        status = req.get('status', 'unknown')
        status_emoji = {'pending': '🟡', 'in_progress': '🔵', 'completed': '🟢', 'survey_needed': '🟠', 'cancelled': '⚪'}.get(status, '⚪')
        
        output = f"\n\n📄 *Request Details*\n"
        output += "━" * 25 + "\n\n"
        
        # Header
        output += f"🔖 *Ref:* `{ref}`\n"
        output += f"📊 *Status:* {status_emoji} {status.replace('_', ' ').title()}\n\n"
        
        # Customer Info
        output += "👤 *CUSTOMER*\n"
        output += f"   Name: {req.get('name', 'N/A')}\n"
        if req.get('email'):
            output += f"   Email: {req.get('email')}\n"
        if req.get('phone'):
            output += f"   Phone: {req.get('phone')}\n"
        
        # Request Type & Service
        req_type = req.get('request_type', 'general')
        type_emoji = {'service': '🧹', 'job': '💼', 'general': '📩', 'quote': '💰'}.get(req_type, '📌')
        output += f"\n{type_emoji} *TYPE:* {req_type.title()}\n"
        
        if req.get('service_name'):
            output += f"   Service: {req.get('service_name')}\n"
        if req.get('job_position'):
            output += f"   Position: {req.get('job_position')}\n"
        
        # Parse message for booking details
        message = req.get('message', '')
        if message:
            output += "\n📝 *BOOKING DETAILS*\n"
            
            # Extract key info from message
            lines = message.split('\n')
            selections = []
            for line in lines:
                line = line.strip()
                if line.startswith('- ') and '£' in line:
                    # Service selection line
                    selections.append(line[2:])
                elif 'Preferred date:' in line:
                    output += f"   📅 Date: {line.split(':')[1].strip()}\n"
                elif 'Preferred time:' in line:
                    output += f"   🕐 Time: {line.split(':')[1].strip()}\n"
                elif 'Address:' in line:
                    addr = line.replace('Address:', '').strip()
                    output += f"   📍 Address: {addr}\n"
                elif 'Estimated total:' in line:
                    total = line.split(':')[1].strip()
                    output += f"   💰 *Total: {total}*\n"
                elif 'Notes:' in line:
                    notes = line.replace('Notes:', '').strip()
                    if notes:
                        output += f"   📋 Notes: {notes}\n"
            
            if selections:
                output += "\n   *Selected Services:*\n"
                for sel in selections[:5]:  # Limit to 5 for readability
                    # Clean up the selection text
                    sel_short = sel[:60] + "..." if len(sel) > 60 else sel
                    output += f"   • {sel_short}\n"
                if len(selections) > 5:
                    output += f"   _...and {len(selections) - 5} more_\n"
        
        # Dates
        output += "\n📅 *TIMELINE*\n"
        created = req.get('created_at')
        if created:
            if hasattr(created, 'strftime'):
                output += f"   Created: {created.strftime('%d %b %Y, %H:%M')}\n"
            else:
                output += f"   Created: {str(created)[:16]}\n"
        
        # Admin notes
        if req.get('admin_notes'):
            output += f"\n📌 *ADMIN NOTES*\n   {req.get('admin_notes')[:200]}\n"
        
        output += "\n" + "━" * 25
        output += f"\n💡 _Quick actions: Mark completed, Add note, Email customer_"
        
        return output
    
    def _format_daily_report(self, data) -> str:
        """Format daily report"""
        if not data:
            return "\n\n📊 No report data available"
        
        data = self._to_dict(data) if not isinstance(data, dict) else data
        output = "\n\n📊 *Daily Business Report*\n"
        output += "━" * 25 + "\n\n"
        
        output += f"📅 Date: {data.get('date', 'Today')}\n\n"
        
        # Today's breakdown
        breakdown = data.get('today_breakdown', [])
        if breakdown:
            output += "📈 *Today's Activity*\n"
            for item in breakdown:
                item = self._to_dict(item)
                output += f"   • {item.get('request_type', 'Unknown').title()}: {item.get('count', 0)} ({item.get('status', '')})\n"
        
        output += f"\n🔔 *Pending:* {data.get('pending_total', 0)} requests need attention\n"
        output += f"📆 *This Week:* {data.get('weekly_requests', 0)} total requests\n"
        
        return output
    
    def _format_revenue_report(self, data) -> str:
        """Format revenue report"""
        if not data:
            return "\n\n💰 No revenue data available"
        
        data = self._to_dict(data) if not isinstance(data, dict) else data
        output = "\n\n💰 *Revenue Report*\n"
        output += "━" * 25 + "\n\n"
        
        output += f"📅 Period: Last {data.get('period_days', 30)} days\n\n"
        output += f"💵 *Total Revenue:* £{data.get('total_revenue', 0):,.2f}\n"
        output += f"✅ *Completed Jobs:* {data.get('completed_jobs', 0)}\n"
        output += f"📊 *Avg Job Value:* £{data.get('average_job_value', 0):,.2f}\n"
        output += f"📈 *Today:* £{data.get('today_revenue', 0):,.2f}\n"
        
        top = data.get('top_services', [])
        if top:
            output += "\n🏆 *Top Services*\n"
            for i, row in enumerate(top, 1):
                svc = self._to_dict(row)
                output += f"   {i}. {svc.get('name', 'Unknown')[:25]}\n"
                output += f"      £{svc.get('revenue', 0):,.2f} • {svc.get('jobs', 0)} jobs\n"
        
        return output
    
    def _format_top_services(self, services: list) -> str:
        """Format top services list"""
        if not services:
            return "\n\n📊 No service data available"
        
        output = "\n\n🏆 *Top Performing Services*\n"
        output += "━" * 25 + "\n\n"
        
        for i, row in enumerate(services, 1):
            svc = self._to_dict(row)
            medal = {1: '🥇', 2: '🥈', 3: '🥉'}.get(i, f'{i}.')
            output += f"{medal} *{svc.get('name', 'Unknown')}*\n"
            output += f"   📋 Requests: {svc.get('requests', 0)}\n"
            output += f"   ✅ Completed: {svc.get('completed', 0)} ({svc.get('completion_rate', 0)}%)\n"
            output += f"   💰 Revenue: £{svc.get('revenue', 0):,.2f}\n\n"
        
        return output
    
    def _format_services_list(self, services: list) -> str:
        """Format services list"""
        if not services:
            return "\n\n🧹 No services configured"
        
        output = "\n\n🧹 *Services List*\n"
        output += "━" * 25 + "\n\n"
        
        for row in services:
            svc = self._to_dict(row)
            status = "✅" if svc.get('is_active') else "⏸️"
            price = f"£{svc.get('price')}" if svc.get('price') else "Quote"
            output += f"{status} *{svc.get('name', svc.get('title', 'Unknown'))}*\n"
            output += f"   ID: {svc.get('id')} • Price: {price}\n\n"
        
        return output
    
    def _format_analytics(self, data) -> str:
        """Format analytics summary"""
        if not data:
            return "\n\n📈 No analytics data available"
        
        data = self._to_dict(data) if not isinstance(data, dict) else data
        output = "\n\n📈 *Analytics Summary*\n"
        output += "━" * 25 + "\n\n"
        
        output += f"📅 Period: Last {data.get('period_days', 7)} days\n\n"
        output += f"👁️ *Website Visits:* {data.get('visits', 0):,}\n"
        output += f"🔍 *Service Views:* {data.get('service_views', 0):,}\n"
        output += f"📝 *Total Requests:* {data.get('total_requests', 0)}\n"
        output += f"📊 *Conversion Rate:* {data.get('conversion_rate', 0)}%\n\n"
        
        output += "*Request Breakdown*\n"
        output += f"   🟡 Pending: {data.get('pending', 0)}\n"
        output += f"   🔵 In Progress: {data.get('in_progress', 0)}\n"
        output += f"   🟢 Completed: {data.get('completed', 0)}\n"
        output += f"   ⚪ Cancelled: {data.get('cancelled', 0)}\n"
        
        return output

    def _execute_action(self, action: Dict) -> Dict[str, Any]:
        """Execute an action and return result"""
        action_name = action.get('action')
        params = action.get('params', {})
        
        try:
            if action_name == 'search_requests':
                return self._action_search_requests(params)
            elif action_name == 'update_request_status':
                return self._action_update_status(params)
            elif action_name == 'add_request_note':
                return self._action_add_note(params)
            elif action_name == 'get_request_details':
                return self._action_get_request(params)
            elif action_name == 'get_service_info':
                return self._action_get_service(params)
            elif action_name == 'get_daily_report':
                return self._action_daily_report()
            elif action_name == 'get_pending_requests':
                return self._action_pending_requests()
            else:
                return {"success": False, "message": f"Unknown action: {action_name}"}
        except Exception as e:
            return {"success": False, "message": str(e)}
    
    def _action_search_requests(self, params: Dict) -> Dict:
        """Search requests by query"""
        query = params.get('query', '')
        status = params.get('status')
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        sql = """
            SELECT id, ref_id, name, email, phone, request_type, status, 
                   service_name, job_position, created_at
            FROM requests
            WHERE (name LIKE %s OR email LIKE %s OR ref_id LIKE %s OR phone LIKE %s)
        """
        search_term = f"%{query}%"
        params_list = [search_term, search_term, search_term, search_term]
        
        if status:
            sql += " AND status = %s"
            params_list.append(status)
        
        sql += " ORDER BY created_at DESC LIMIT 50"  # Increased limit for complete lists
        
        cursor.execute(sql, params_list)
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "message": f"Found {len(results)} requests",
            "data": results
        }
    
    def _action_update_status(self, params: Dict) -> Dict:
        """Update request status and send email notification"""
        ref_id = params.get('ref_id')
        new_status = params.get('status')
        
        valid_statuses = {'pending', 'in_progress', 'completed', 'cancelled', 'survey_needed'}
        if new_status not in valid_statuses:
            return {"success": False, "message": f"Invalid status. Use: {valid_statuses}"}
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get current status before update
        cursor.execute("SELECT * FROM requests WHERE ref_id = %s", (ref_id,))
        request_row = cursor.fetchone()
        
        if not request_row:
            cursor.close()
            conn.close()
            return {"success": False, "message": f"Request {ref_id} not found"}
        
        previous_status = request_row.get('status')
        
        # Update the status
        cursor.execute(
            "UPDATE requests SET status = %s, updated_at = NOW() WHERE ref_id = %s",
            (new_status, ref_id)
        )
        conn.commit()
        
        # Fetch updated request for email
        cursor.execute("SELECT * FROM requests WHERE ref_id = %s", (ref_id,))
        updated_request = cursor.fetchone()
        cursor.close()
        conn.close()
        
        # Send email notification
        email_result = {'admin_sent': False, 'user_sent': False}
        try:
            # Local import to avoid circular imports
            from app import send_status_update_notifications, app as flask_app
            
            with flask_app.app_context():
                email_result = send_status_update_notifications(updated_request, previous_status)
        except Exception as e:
            print(f"EMAIL ERROR in AI status update: {str(e)}")
        
        # Build response message
        status_msg = f"Request {ref_id} updated to {new_status}"
        
        if email_result.get('user_sent'):
            email_msg = "📧 Email notification sent to client"
        elif email_result.get('admin_sent'):
            email_msg = "📧 Admin notified (no client email on file)"
        else:
            email_msg = "⚠️ Status updated but email notification failed"
        
        return {
            "success": True, 
            "message": f"{status_msg}. {email_msg}",
            "data": {
                "ref_id": ref_id,
                "previous_status": previous_status,
                "new_status": new_status,
                "email_sent_to_user": email_result.get('user_sent', False),
                "email_sent_to_admin": email_result.get('admin_sent', False)
            }
        }
    
    def _action_add_note(self, params: Dict) -> Dict:
        """Add admin note to request"""
        ref_id = params.get('ref_id')
        note = params.get('note', '')
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get existing notes
        cursor.execute("SELECT admin_notes FROM requests WHERE ref_id = %s", (ref_id,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return {"success": False, "message": f"Request {ref_id} not found"}
        
        # Append note with timestamp
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        existing = row['admin_notes'] or ''
        new_notes = f"{existing}\n[{timestamp} via AI] {note}".strip()
        
        cursor.execute(
            "UPDATE requests SET admin_notes = %s, updated_at = NOW() WHERE ref_id = %s",
            (new_notes, ref_id)
        )
        conn.commit()
        cursor.close()
        conn.close()
        
        return {"success": True, "message": f"Note added to request {ref_id}"}
    
    def _action_get_request(self, params: Dict) -> Dict:
        """Get request details"""
        ref_id = params.get('ref_id')
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM requests WHERE ref_id = %s", (ref_id,))
        request = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if request:
            return {"success": True, "message": "Request found", "data": request}
        return {"success": False, "message": f"Request {ref_id} not found"}
    
    def _action_get_service(self, params: Dict) -> Dict:
        """Get service information"""
        service_id = params.get('service_id')
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM services WHERE id = %s", (service_id,))
        service = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if service:
            return {"success": True, "message": "Service found", "data": service}
        return {"success": False, "message": f"Service {service_id} not found"}
    
    def _action_daily_report(self) -> Dict:
        """Generate daily report"""
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        start_today, end_today = self._day_bounds_utc()
        cutoff_week = datetime.utcnow() - timedelta(days=7)
        
        # Today's requests
        cursor.execute("""
            SELECT request_type, status, COUNT(*) as count
            FROM requests
            WHERE created_at >= %s AND created_at < %s
            GROUP BY request_type, status
        """, (start_today, end_today))
        today_breakdown = cursor.fetchall()
        
        # Pending items
        cursor.execute("SELECT COUNT(*) as count FROM requests WHERE status = 'pending'")
        pending = cursor.fetchone()['count']
        
        # This week's total
        cursor.execute("""
            SELECT COUNT(*) as count FROM requests
            WHERE created_at >= %s
        """, (cutoff_week,))
        weekly = cursor.fetchone()['count']
        
        cursor.close()
        conn.close()
        
        report = {
            "date": datetime.now().strftime('%Y-%m-%d'),
            "today_breakdown": today_breakdown,
            "pending_total": pending,
            "weekly_requests": weekly
        }
        
        return {"success": True, "message": "Daily report generated", "data": report}
    
    def _action_pending_requests(self) -> Dict:
        """Get all pending requests"""
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, ref_id, name, request_type, service_name, job_position, created_at
            FROM requests
            WHERE status = 'pending'
            ORDER BY created_at ASC
        """)
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "message": f"{len(results)} pending requests",
            "data": results
        }
    
    def _action_get_revenue_report(self, params: Dict = None) -> Dict:
        """Get revenue report for specified period"""
        days = params.get('days', 30) if params else 30
        cutoff = datetime.utcnow() - timedelta(days=days)
        start_today, end_today = self._day_bounds_utc()
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Revenue stats from service_requests
        cursor.execute("""
            SELECT 
                SUM(CASE WHEN status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as total_revenue,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed_jobs,
                AVG(CASE WHEN status = 'completed' THEN COALESCE(total_price, 0) END) as avg_job_value
            FROM service_requests
            WHERE updated_at >= %s
        """, (cutoff,))
        stats = cursor.fetchone()
        
        # Today's revenue
        cursor.execute("""
            SELECT SUM(COALESCE(total_price, 0)) as today_revenue
            FROM service_requests
            WHERE status = 'completed' AND updated_at >= %s AND updated_at < %s
        """, (start_today, end_today))
        today = cursor.fetchone()
        
        # Top earning services from service_request_items
        cursor.execute("""
            SELECT s.name as service_name, SUM(COALESCE(sri.price, 0)) as revenue, COUNT(DISTINCT sr.id) as jobs
            FROM service_requests sr
            JOIN service_request_items sri ON sr.id = sri.service_request_id
            JOIN services s ON sri.service_id = s.id
            WHERE sr.status = 'completed' AND sr.updated_at >= %s
            GROUP BY s.id, s.name
            ORDER BY revenue DESC
            LIMIT 5
        """, (cutoff,))
        top_services = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "message": f"Revenue report for last {days} days",
            "data": {
                "period_days": days,
                "total_revenue": float(stats['total_revenue'] or 0),
                "completed_jobs": stats['completed_jobs'] or 0,
                "average_job_value": round(float(stats['avg_job_value'] or 0), 2),
                "today_revenue": float(today['today_revenue'] or 0),
                "top_services": [{
                    "name": s['service_name'],
                    "revenue": float(s['revenue']),
                    "jobs": s['jobs']
                } for s in top_services]
            }
        }
    
    def _action_get_analytics_summary(self, params: Dict = None) -> Dict:
        """Get analytics summary with conversion metrics"""
        days = params.get('days', 7) if params else 7
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Traffic stats
        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN event_type = 'visit' THEN 1 END) as visits,
                COUNT(CASE WHEN event_type = 'service_view' THEN 1 END) as service_views
            FROM analytics
            WHERE created_at >= %s
        """, (cutoff,))
        traffic = cursor.fetchone()
        
        # Request stats
        cursor.execute("""
            SELECT 
                COUNT(*) as total_requests,
                COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending,
                COUNT(CASE WHEN status = 'in_progress' THEN 1 END) as in_progress,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                COUNT(CASE WHEN status = 'cancelled' THEN 1 END) as cancelled
            FROM requests
            WHERE created_at >= %s
        """, (cutoff,))
        requests_stats = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        # Calculate conversion rate
        visits = traffic['visits'] or 0
        total_req = requests_stats['total_requests'] or 0
        conversion = round((total_req / visits * 100) if visits > 0 else 0, 1)
        
        return {
            "success": True,
            "message": f"Analytics for last {days} days",
            "data": {
                "period_days": days,
                "visits": visits,
                "service_views": traffic['service_views'] or 0,
                "total_requests": total_req,
                "pending": requests_stats['pending'] or 0,
                "in_progress": requests_stats['in_progress'] or 0,
                "completed": requests_stats['completed'] or 0,
                "cancelled": requests_stats['cancelled'] or 0,
                "conversion_rate": conversion
            }
        }
    
    def _action_get_top_services(self, params: Dict = None) -> Dict:
        """Get top performing services"""
        days = params.get('days', 30) if params else 30
        limit = params.get('limit', 5) if params else 5
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT 
                s.name as service_name,
                COUNT(DISTINCT sr.id) as total_requests,
                COUNT(DISTINCT CASE WHEN sr.status = 'completed' THEN sr.id END) as completed,
                SUM(CASE WHEN sr.status = 'completed' THEN COALESCE(sri.price, 0) ELSE 0 END) as revenue
            FROM service_requests sr
            JOIN service_request_items sri ON sr.id = sri.service_request_id
            JOIN services s ON sri.service_id = s.id
            WHERE sr.created_at >= %s
            GROUP BY s.id, s.name
            ORDER BY total_requests DESC
            LIMIT %s
        """, (cutoff, limit))
        services = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "message": f"Top {limit} services in last {days} days",
            "data": [{
                "name": s['service_name'],
                "requests": s['total_requests'],
                "completed": s['completed'],
                "revenue": float(s['revenue'] or 0),
                "completion_rate": round((s['completed'] / s['total_requests'] * 100) if s['total_requests'] > 0 else 0, 1)
            } for s in services]
        }
    
    def _action_send_customer_email(self, params: Dict) -> Dict:
        """Send email to customer about their request"""
        ref_id = params.get('ref_id')
        subject = params.get('subject', '')
        message = params.get('message', '')
        
        if not ref_id or not subject or not message:
            return {"success": False, "message": "ref_id, subject, and message are required"}
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM requests WHERE ref_id = %s", (ref_id,))
        request_row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not request_row:
            return {"success": False, "message": f"Request {ref_id} not found"}
        
        customer_email = request_row.get('email')
        if not customer_email:
            return {"success": False, "message": "No email address on file for this customer"}
        
        try:
            from app import fetch_email_settings, send_email_via_settings, app as flask_app
            
            with flask_app.app_context():
                settings = fetch_email_settings()
                
                # Build HTML email
                html_body = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                    <h2 style="color: #333;">Done-Well Cleaners</h2>
                    <p>Dear {request_row.get('name', 'Customer')},</p>
                    <div style="background: #f9f9f9; padding: 15px; border-radius: 8px; margin: 20px 0;">
                        {message.replace(chr(10), '<br>')}
                    </div>
                    <p>Reference: <strong>{ref_id}</strong></p>
                    <hr style="border: none; border-top: 1px solid #eee;">
                    <p style="color: #666; font-size: 12px;">Done-Well Cleaners Team</p>
                </div>
                """
                
                success = send_email_via_settings(
                    subject=subject,
                    html_body=html_body,
                    text_body=message,
                    recipients=[customer_email],
                    settings=settings,
                    reply_to=settings.get('reply_to'),
                    error_context='ai_customer_email',
                    request_id=request_row.get('id')
                )
                
                if success:
                    return {"success": True, "message": f"Email sent to {customer_email}"}
                return {"success": False, "message": "Email failed to send - check email settings"}
                
        except Exception as e:
            return {"success": False, "message": f"Email error: {str(e)}"}
    
    def _action_toggle_service(self, params: Dict) -> Dict:
        """Enable or disable a service"""
        service_id = params.get('service_id')
        active = params.get('active', True)
        
        if not service_id:
            return {"success": False, "message": "service_id is required"}
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Check if service exists
        cursor.execute("SELECT id, name FROM services WHERE id = %s", (service_id,))
        service = cursor.fetchone()
        
        if not service:
            cursor.close()
            conn.close()
            return {"success": False, "message": f"Service {service_id} not found"}
        
        # Update status
        cursor.execute(
            "UPDATE services SET is_active = %s WHERE id = %s",
            (1 if active else 0, service_id)
        )
        conn.commit()
        cursor.close()
        conn.close()
        
        status = "enabled" if active else "disabled"
        return {
            "success": True,
            "message": f"Service '{service['name']}' has been {status}",
            "data": {"service_id": service_id, "name": service['name'], "active": active}
        }
    
    def _action_list_services(self, params: Dict = None) -> Dict:
        """List all services"""
        active_only = params.get('active_only', False) if params else False
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        sql = "SELECT id, name, title, price, is_active FROM services"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY name"
        
        cursor.execute(sql)
        services = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "message": f"Found {len(services)} services",
            "data": [{
                "id": s['id'],
                "name": s['name'] or s['title'],
                "price": float(s['price']) if s['price'] else None,
                "active": bool(s['is_active'])
            } for s in services]
        }
    
    def _action_get_customer_history(self, params: Dict) -> Dict:
        """Get all requests from a customer"""
        email = params.get('email', '').strip()
        phone = params.get('phone', '').strip()
        
        if not email and not phone:
            return {"success": False, "message": "Either email or phone is required"}
        
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        conditions = []
        values = []
        if email:
            conditions.append("email = %s")
            values.append(email)
        if phone:
            conditions.append("phone = %s")
            values.append(phone)
        
        where_clause = " OR ".join(conditions)
        
        cursor.execute(f"""
            SELECT ref_id, name, email, phone, request_type, status, service_name, created_at
            FROM requests
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT 20
        """, values)
        requests = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not requests:
            return {"success": False, "message": "No requests found for this customer"}
        
        return {
            "success": True,
            "message": f"Found {len(requests)} requests for this customer",
            "data": requests
        }
    
    def _get_conversation_history(self, chat_id: str, limit: int = 5) -> List[Dict]:
        """Get recent conversation history"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT role, content FROM ai_conversations
                WHERE chat_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (chat_id, limit * 2))  # *2 for user+assistant pairs
            history = cursor.fetchall()
            cursor.close()
            conn.close()
            return list(reversed(history))
        except:
            return []
    
    def _save_conversation(self, chat_id: str, user_msg: str, assistant_msg: str):
        """Save conversation to database"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ai_conversations (chat_id, role, content)
                VALUES (%s, 'user', %s), (%s, 'assistant', %s)
            """, (chat_id, user_msg, chat_id, assistant_msg))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Failed to save conversation: {e}")
    
    def get_quick_stats(self) -> Dict[str, Any]:
        """Get quick stats without AI - for dashboard"""
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        start_today, end_today = self._day_bounds_utc()
        cutoff_week = datetime.utcnow() - timedelta(days=7)
        
        stats = {}
        
        # Pending requests
        cursor.execute("SELECT COUNT(*) as c FROM requests WHERE status = 'pending'")
        stats['pending'] = cursor.fetchone()['c']
        
        # Today's requests
        cursor.execute(
            "SELECT COUNT(*) as c FROM requests WHERE created_at >= %s AND created_at < %s",
            (start_today, end_today),
        )
        stats['today'] = cursor.fetchone()['c']
        
        # This week
        cursor.execute("""
            SELECT COUNT(*) as c FROM requests 
            WHERE created_at >= %s
        """, (cutoff_week,))
        stats['week'] = cursor.fetchone()['c']
        
        # Conversion rate (completed / total)
        cursor.execute("SELECT COUNT(*) as c FROM requests WHERE status = 'completed'")
        completed = cursor.fetchone()['c']
        cursor.execute("SELECT COUNT(*) as c FROM requests")
        total = cursor.fetchone()['c']
        stats['conversion_rate'] = round((completed / total * 100) if total > 0 else 0, 1)
        
        cursor.close()
        conn.close()
        
        return stats


# Singleton instance - will be initialized with db_config from app.py
_assistant_instance = None

def get_assistant(db_config: Dict[str, Any] = None) -> AIAssistant:
    """Get or create AI assistant instance"""
    global _assistant_instance
    if _assistant_instance is None and db_config:
        _assistant_instance = AIAssistant(db_config)
    return _assistant_instance

def init_assistant(db_config: Dict[str, Any]):
    """Initialize the assistant with database config"""
    global _assistant_instance
    _assistant_instance = AIAssistant(db_config)
    return _assistant_instance
