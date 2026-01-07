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
        """Define the tools/functions available for the AI to call"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_requests",
                    "description": "Search for customer requests by name, email, reference ID, or status",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search term - can be customer name, email, or reference ID"
                            },
                            "status": {
                                "type": ["string", "null"],
                                "enum": ["pending", "in_progress", "completed", "cancelled", "survey_needed", None],
                                "description": "Optional status filter"
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "update_request_status",
                    "description": "Update the status of a request",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ref_id": {
                                "type": "string",
                                "description": "The request reference ID (e.g., REQ-XXXXXX)"
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed", "cancelled", "survey_needed"],
                                "description": "The new status"
                            }
                        },
                        "required": ["ref_id", "status"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "add_request_note",
                    "description": "Add an admin note to a request",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ref_id": {
                                "type": "string",
                                "description": "The request reference ID"
                            },
                            "note": {
                                "type": "string",
                                "description": "The note text to add"
                            }
                        },
                        "required": ["ref_id", "note"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_request_details",
                    "description": "Get full details of a specific request",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ref_id": {
                                "type": "string",
                                "description": "The request reference ID"
                            }
                        },
                        "required": ["ref_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_daily_report",
                    "description": "Get today's business report including request counts and status breakdown",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_pending_requests",
                    "description": "Get all pending requests that need attention",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_revenue_report",
                    "description": "Get revenue report for a specified period",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "days": {
                                "type": "integer",
                                "description": "Number of days to include in report (default 30)"
                            }
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_top_services",
                    "description": "Get the top performing services",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "days": {
                                "type": "integer",
                                "description": "Number of days to analyze (default 30)"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Number of services to return (default 5)"
                            }
                        },
                        "required": []
                    }
                }
            }
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
        
        if self.ai_provider == 'openai' and not OPENAI_AVAILABLE:
            return False, "OpenAI package not installed. Run: pip install openai"
        
        if self.ai_provider == 'anthropic' and not ANTHROPIC_AVAILABLE:
            return False, "Anthropic package not installed. Run: pip install anthropic"
        
        return True, "Ready"
    
    def get_system_context(self) -> str:
        """Build system context with current data"""
        conn = self._get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get request summary
        cursor.execute("""
            SELECT 
                status,
                COUNT(*) as count
            FROM requests
            GROUP BY status
        """)
        request_stats = {row['status']: row['count'] for row in cursor.fetchall()}
        
        # Get today's stats
        start_today, end_today = self._day_bounds_utc()
        cursor.execute("""
            SELECT 
                request_type,
                COUNT(*) as count
            FROM requests
            WHERE created_at >= %s AND created_at < %s
            GROUP BY request_type
        """, (start_today, end_today))
        today_stats = {row['request_type']: row['count'] for row in cursor.fetchall()}
        
        # Get services list
        cursor.execute("SELECT id, name, title, price, is_active FROM services")
        services = cursor.fetchall()
        
        # Get recent requests
        cursor.execute("""
            SELECT id, ref_id, name, request_type, status, service_name, created_at
            FROM requests
            ORDER BY created_at DESC
            LIMIT 10
        """)
        recent_requests = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        # Format context
        context = f"""You are an AI assistant for a cleaning service business called "Done-Well Cleaners".
You help the admin manage requests, services, and get insights about the business.

CURRENT DATE: {datetime.now().strftime('%Y-%m-%d %H:%M')}

REQUEST STATUS SUMMARY:
- Pending: {request_stats.get('pending', 0)}
- In Progress: {request_stats.get('in_progress', 0)}
- Completed: {request_stats.get('completed', 0)}
- Cancelled: {request_stats.get('cancelled', 0)}
- Survey Needed: {request_stats.get('survey_needed', 0)}

TODAY'S ACTIVITY:
- Service Requests: {today_stats.get('service', 0)}
- Job Applications: {today_stats.get('job', 0)}
- General Inquiries: {today_stats.get('general', 0)}

SERVICES AVAILABLE ({len(services)} total):
"""
        for svc in services[:10]:
            status = "Active" if svc['is_active'] else "Inactive"
            price = f"£{svc['price']}" if svc['price'] else "Variable"
            context += f"- [{svc['id']}] {svc['name'] or svc['title']} ({status}, {price})\n"
        
        context += f"\nRECENT REQUESTS (last 10):\n"
        for req in recent_requests:
            created = req['created_at'].strftime('%Y-%m-%d %H:%M') if req['created_at'] else 'N/A'
            context += f"- [{req['ref_id']}] {req['name']} - {req['request_type']} - {req['status']} ({created})\n"
        
        context += """
CAPABILITIES:
1. Get statistics and summaries
2. Search for specific requests by name, email, ref_id, or status
3. Update request status (pending, in_progress, completed, cancelled, survey_needed)
4. Add admin notes to requests
5. Get service information
6. Provide business insights

When asked to perform an action, respond with a JSON block if an action is needed:
```action
{"action": "action_name", "params": {...}}
```

Available actions:
- search_requests: {"query": "search term", "status": "optional status filter"}
- update_request_status: {"ref_id": "REQ-XXX", "status": "new_status"}
- add_request_note: {"ref_id": "REQ-XXX", "note": "note text"}
- get_request_details: {"ref_id": "REQ-XXX"}
- get_service_info: {"service_id": 1}
- get_daily_report: {}
- get_pending_requests: {}

Always be helpful, concise, and professional. If you can't perform an action, explain why.
"""
        return context
    
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
            "max_completion_tokens": self.max_tokens,
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
    
    def _call_openai(self, messages: List[Dict]) -> str:
        """Call OpenAI API"""
        if not OPENAI_AVAILABLE:
            return "OpenAI not available"
        
        client = openai.OpenAI(api_key=self.api_key)
        response = client.chat.completions.create(
            model=self.model or "gpt-4o-mini",
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature
        )
        return response.choices[0].message.content
    
    def _call_anthropic(self, messages: List[Dict]) -> str:
        """Call Anthropic Claude API"""
        if not ANTHROPIC_AVAILABLE:
            return "Anthropic not available"
        
        client = anthropic.Anthropic(api_key=self.api_key)
        
        # Extract system message
        system_msg = ""
        chat_messages = []
        for msg in messages:
            if msg['role'] == 'system':
                system_msg = msg['content']
            else:
                chat_messages.append(msg)
        
        response = client.messages.create(
            model=self.model or "claude-3-haiku-20240307",
            max_tokens=1500,
            system=system_msg,
            messages=chat_messages
        )
        return response.content[0].text
    
    def process_message(self, user_message: str, chat_id: str = None) -> Dict[str, Any]:
        """Process a user message and return response with any actions"""
        ready, error = self.is_ready()
        if not ready:
            return {"success": False, "message": error, "action": None}
        
        # Build messages
        system_context = self.get_system_context()
        messages = [
            {"role": "system", "content": system_context},
            {"role": "user", "content": user_message}
        ]
        
        # Add conversation history if available
        if chat_id:
            history = self._get_conversation_history(chat_id, limit=5)
            if history:
                # Insert history before user message
                for hist in history:
                    messages.insert(-1, {"role": hist['role'], "content": hist['content']})
        
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
                        
                        # Append result to response
                        if action_result.get('success'):
                            result_data = action_result.get('data', action_result.get('message', 'Done'))
                            response += f"\n\n✅ {action_result.get('message', 'Action completed')}"
                            if isinstance(result_data, list):
                                for item in result_data[:5]:  # Limit to 5 items
                                    if isinstance(item, dict):
                                        response += f"\n• {item.get('ref_id', item.get('name', str(item)))}"
                                        if 'name' in item and 'ref_id' in item:
                                            response += f" - {item['name']}"
                                        if 'status' in item:
                                            response += f" ({item['status']})"
                            elif isinstance(result_data, dict):
                                for key, val in result_data.items():
                                    if not key.startswith('_'):
                                        response += f"\n• {key}: {val}"
                        else:
                            response += f"\n\n❌ {action_result.get('message', 'Action failed')}"
                
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
        
        sql += " ORDER BY created_at DESC LIMIT 10"
        
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
