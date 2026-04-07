"""
Enhanced Telegram Bot with AI Integration for Cleaning Service
Supports both command-based and AI-powered natural language interactions
"""

import os
import json
import time
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any, Callable

# DB drivers
try:
    import mysql.connector
except ImportError:
    mysql_connector = None

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TelegramBotAI:
    """Enhanced Telegram bot with AI capabilities"""
    
    def __init__(self, db_config: Dict[str, Any]):
        self.db_config = db_config
        self.db_engine = (db_config.get('engine') or 'mysql').strip().lower()
        self.bot_token = None
        self.chat_id = None
        self.is_active = False
        self.ai_enabled = False
        self.allowed_chat_ids = set()
        self.commands = {}
        self.polling_thread = None
        self.should_stop = False
        self.last_update_id = 0
        
        self._register_default_commands()
        self._load_settings()
    
    def _get_db_connection(self):
        """Get database connection based on engine"""
        if self.db_engine == 'postgres':
            dsn = (self.db_config.get('postgres_url') or '').strip()
            if not dsn:
                raise ValueError('Postgres engine selected but postgres_url is not configured')
            if psycopg2 is None:
                raise RuntimeError('psycopg2 is not installed')

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

        # MySQL connection
        if mysql_connector is None:
            raise RuntimeError('mysql-connector-python is not installed')
        mysql_config = {k: v for k, v in self.db_config.items() if k not in ('engine', 'postgres_url')}
        return mysql.connector.connect(**mysql_config)
    
    def _load_settings(self):
        """Load bot settings from database"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Load Telegram settings
            cursor.execute("""
                SELECT bot_token, chat_id, is_active 
                FROM telegram_settings WHERE id = 1
            """)
            tg_settings = cursor.fetchone()
            
            if tg_settings:
                self.bot_token = tg_settings.get('bot_token', '')
                self.chat_id = tg_settings.get('chat_id', '')
                self.is_active = bool(tg_settings.get('is_active', 0))
            
            # Load AI settings
            cursor.execute("""
                SELECT telegram_ai_enabled, allowed_chat_ids 
                FROM ai_settings WHERE id = 1
            """)
            ai_settings = cursor.fetchone()
            
            if ai_settings:
                self.ai_enabled = bool(ai_settings.get('telegram_ai_enabled', 0))
                allowed = ai_settings.get('allowed_chat_ids', '') or ''
                self.allowed_chat_ids = set(
                    cid.strip() for cid in allowed.split(',') if cid.strip()
                )
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to load bot settings: {e}")
    
    def reload_settings(self):
        """Reload settings from database"""
        self._load_settings()
    
    def _register_default_commands(self):
        """Register built-in commands"""
        self.register_command('start', self._cmd_start, 'Start the bot')
        self.register_command('help', self._cmd_help, 'Show available commands')
        self.register_command('status', self._cmd_status, 'Get system status')
        self.register_command('pending', self._cmd_pending, 'List pending requests')
        self.register_command('today', self._cmd_today, 'Today\'s summary')
        self.register_command('search', self._cmd_search, 'Search requests')
        self.register_command('request', self._cmd_request, 'Get request details')
        self.register_command('update', self._cmd_update, 'Update request status')
        self.register_command('ai', self._cmd_ai, 'Ask AI assistant')
        self.register_command('menu', self._cmd_menu, 'Show quick action menu')
    
    def register_command(self, name: str, handler: Callable, description: str = ''):
        """Register a command handler"""
        self.commands[name.lower()] = {
            'handler': handler,
            'description': description
        }
    
    def is_allowed_chat(self, chat_id: str) -> bool:
        """Check if chat is allowed to use the bot"""
        if not self.allowed_chat_ids:
            return True  # Allow all if no restrictions
        return str(chat_id) in self.allowed_chat_ids
    
    def send_message(self, chat_id: str, text: str, parse_mode: str = 'HTML', reply_markup: dict = None) -> bool:
        """Send a message to a chat with optional inline keyboard"""
        if not self.bot_token or not REQUESTS_AVAILABLE:
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': text[:4096],  # Telegram limit
                'parse_mode': parse_mode
            }
            if reply_markup:
                payload['reply_markup'] = json.dumps(reply_markup)
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False
    
    def send_message_with_buttons(self, chat_id: str, text: str, buttons: list, parse_mode: str = 'HTML') -> bool:
        """Send a message with inline keyboard buttons"""
        reply_markup = {
            'inline_keyboard': buttons
        }
        return self.send_message(chat_id, text, parse_mode, reply_markup)
    
    def process_message(self, message: Dict) -> Optional[str]:
        """Process an incoming message and return response"""
        chat_id = str(message.get('chat', {}).get('id', ''))
        text = message.get('text', '').strip()
        username = message.get('from', {}).get('username', 'Unknown')
        
        if not text or not chat_id:
            return None
        
        # Log the command
        self._log_command(chat_id, username, text)
        
        # Check if allowed
        if not self.is_allowed_chat(chat_id):
            return "⛔ You are not authorized to use this bot."
        
        # Check for command
        if text.startswith('/'):
            return self._handle_command(chat_id, text, username)
        
        # If AI is enabled, treat as AI query
        if self.ai_enabled:
            return self._handle_ai_query(chat_id, text)
        
        return "Use /help to see available commands, or enable AI mode for natural language."
    
    def _handle_command(self, chat_id: str, text: str, username: str) -> str:
        """Handle a command"""
        parts = text[1:].split(maxsplit=1)  # Remove / and split
        command = parts[0].lower().split('@')[0]  # Handle @botname
        args = parts[1] if len(parts) > 1 else ''
        
        if command in self.commands:
            try:
                return self.commands[command]['handler'](chat_id, args, username)
            except Exception as e:
                import traceback as _tb
                logger.error(f"Command error: {e}\n{_tb.format_exc()}")
                return f"❌ Error executing command: {str(e)}"
        
        return f"❓ Unknown command: /{command}\nUse /help to see available commands."
    
    def _handle_ai_query(self, chat_id: str, query: str) -> str:
        """Handle AI query"""
        try:
            from ai_assistant import get_assistant
            assistant = get_assistant()
            
            if not assistant:
                return "❌ AI Assistant not configured."
            
            ready, error = assistant.is_ready()
            if not ready:
                return f"❌ {error}"
            
            result = assistant.process_message(query, f"telegram_{chat_id}")
            
            if result.get('success'):
                return result.get('message', 'No response')
            else:
                return f"❌ {result.get('message', 'AI error')}"
                
        except Exception as e:
            import traceback as _tb
            tb_str = _tb.format_exc()
            logger.error(f"AI query error: {e}\n{tb_str}")
            # Surface the first informative line to the user
            err_line = next((l.strip() for l in tb_str.splitlines() if l.strip() and not l.startswith('Traceback')), str(e))
            return f"❌ AI error: {err_line}"
    
    def _log_command(self, chat_id: str, username: str, text: str):
        """Log command to database"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO telegram_command_logs 
                (chat_id, username, command, message_text) 
                VALUES (%s, %s, %s, %s)
            """, (chat_id, username, text.split()[0] if text else '', text[:1000]))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to log command: {e}")
    
    # =====================
    # COMMAND HANDLERS
    # =====================
    
    def _cmd_start(self, chat_id: str, args: str, username: str) -> str:
        """Start command"""
        return f"""👋 Welcome to Done-Well Cleaners Bot!

I can help you manage your cleaning service business.

<b>Your Chat ID:</b> <code>{chat_id}</code>

Use /help to see available commands.
{"🤖 AI Mode is enabled - just type naturally!" if self.ai_enabled else ""}"""
    
    def _cmd_help(self, chat_id: str, args: str, username: str) -> str:
        """Help command"""
        lines = ["<b>📋 Available Commands:</b>\n"]
        
        for cmd, info in self.commands.items():
            desc = info.get('description', 'No description')
            lines.append(f"/{cmd} - {desc}")
        
        if self.ai_enabled:
            lines.append("\n<b>🤖 AI Mode Active</b>")
            lines.append("You can also type questions naturally:")
            lines.append("• \"How many pending requests?\"")
            lines.append("• \"Show today's summary\"")
            lines.append("• \"Search for John's request\"")
        
        return "\n".join(lines)
    
    def _cmd_status(self, chat_id: str, args: str, username: str) -> str:
        """System status"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get request counts - Postgres compatible
            cursor.execute("""
                SELECT 
                    COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending,
                    COUNT(CASE WHEN status = 'in_progress' THEN 1 END) as in_progress,
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                    COUNT(CASE WHEN status = 'survey_needed' THEN 1 END) as survey_needed
                FROM requests
            """)
            stats = cursor.fetchone()
            
            # Today's count - use CURRENT_DATE for Postgres compatibility
            cursor.execute("""
                SELECT COUNT(*) as today FROM requests 
                WHERE DATE(created_at) = CURRENT_DATE
            """)
            today = cursor.fetchone()['today']
            
            cursor.close()
            conn.close()
            
            return f"""📊 <b>System Status</b>

<b>Requests Overview:</b>
🟡 Pending: {stats.get('pending', 0) or 0}
🔵 In Progress: {stats.get('in_progress', 0) or 0}
🟢 Completed: {stats.get('completed', 0) or 0}
🔴 Survey Needed: {stats.get('survey_needed', 0) or 0}

📅 Today's Requests: {today}
🤖 AI Enabled: {'Yes' if self.ai_enabled else 'No'}"""
            
        except Exception as e:
            return f"❌ Failed to get status: {str(e)}"
    
    def _cmd_pending(self, chat_id: str, args: str, username: str) -> str:
        """List pending requests"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT ref_id, name, request_type, service_name, created_at
                FROM requests
                WHERE status = 'pending'
                ORDER BY created_at DESC
                LIMIT 10
            """)
            requests_list = cursor.fetchall()
            cursor.close()
            conn.close()
            
            if not requests_list:
                return "✅ No pending requests!"
            
            lines = [f"📋 <b>Pending Requests ({len(requests_list)})</b>\n"]
            for req in requests_list:
                created = req['created_at'].strftime('%d/%m %H:%M') if req['created_at'] else 'N/A'
                type_emoji = {'service': '🧹', 'job': '💼', 'general': '📩'}.get(req['request_type'], '📄')
                detail = req['service_name'] or req['request_type']
                lines.append(f"{type_emoji} <code>{req['ref_id']}</code>")
                lines.append(f"   {req['name']} - {detail}")
                lines.append(f"   📅 {created}\n")
            
            return "\n".join(lines)
            
        except Exception as e:
            return f"❌ Error: {str(e)}"
    
    def _cmd_today(self, chat_id: str, args: str, username: str) -> str:
        """Today's summary"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT 
                    request_type,
                    COUNT(*) as count
                FROM requests
                WHERE DATE(created_at) = CURRENT_DATE
                GROUP BY request_type
            """)
            by_type = {row['request_type']: row['count'] for row in cursor.fetchall()}
            
            cursor.execute("""
                SELECT 
                    status,
                    COUNT(*) as count
                FROM requests
                WHERE DATE(updated_at) = CURRENT_DATE
                GROUP BY status
            """)
            by_status = {row['status']: row['count'] for row in cursor.fetchall()}
            
            cursor.close()
            conn.close()
            
            total = sum(by_type.values())
            
            return f"""📅 <b>Today's Summary</b>
Date: {datetime.now().strftime('%d %B %Y')}

<b>New Requests:</b> {total}
🧹 Service: {by_type.get('service', 0)}
💼 Jobs: {by_type.get('job', 0)}
📩 General: {by_type.get('general', 0)}

<b>Status Updates:</b>
✅ Completed: {by_status.get('completed', 0)}
🔄 In Progress: {by_status.get('in_progress', 0)}
❌ Cancelled: {by_status.get('cancelled', 0)}"""
            
        except Exception as e:
            return f"❌ Error: {str(e)}"
    
    def _cmd_search(self, chat_id: str, args: str, username: str) -> str:
        """Search requests"""
        if not args:
            return "Usage: /search <name or email or ref_id>"
        
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(dictionary=True)
            search = f"%{args}%"
            cursor.execute("""
                SELECT ref_id, name, email, request_type, status, created_at
                FROM requests
                WHERE name LIKE %s OR email LIKE %s OR ref_id LIKE %s
                ORDER BY created_at DESC
                LIMIT 5
            """, (search, search, search))
            results = cursor.fetchall()
            cursor.close()
            conn.close()
            
            if not results:
                return f"🔍 No requests found for: {args}"
            
            lines = [f"🔍 <b>Search Results for '{args}'</b>\n"]
            for req in results:
                status_emoji = {
                    'pending': '🟡', 'in_progress': '🔵', 
                    'completed': '🟢', 'cancelled': '⚪', 'survey_needed': '🔴'
                }.get(req['status'], '⚪')
                created = req['created_at'].strftime('%d/%m/%y') if req['created_at'] else ''
                lines.append(f"{status_emoji} <code>{req['ref_id']}</code> - {req['name']}")
                lines.append(f"   {req['request_type']} | {req['status']} | {created}\n")
            
            return "\n".join(lines)
            
        except Exception as e:
            return f"❌ Search error: {str(e)}"
    
    def _cmd_request(self, chat_id: str, args: str, username: str) -> str:
        """Get request details"""
        if not args:
            return "Usage: /request <REF-ID>"
        
        ref_id = args.strip().upper()
        
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM requests WHERE ref_id = %s", (ref_id,))
            req = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if not req:
                return f"❌ Request not found: {ref_id}"
            
            created = req['created_at'].strftime('%d/%m/%Y %H:%M') if req['created_at'] else 'N/A'
            
            return f"""📄 <b>Request Details</b>

<b>Ref ID:</b> <code>{req['ref_id']}</code>
<b>Status:</b> {req['status']}
<b>Type:</b> {req['request_type']}

<b>Customer:</b>
👤 {req['name']}
📧 {req['email'] or 'N/A'}
📱 {req['phone'] or 'N/A'}

<b>Details:</b>
{req.get('service_name') or req.get('job_position') or 'General inquiry'}

<b>Message:</b>
{(req.get('message') or 'No message')[:300]}

📅 Created: {created}

Use /update {ref_id} <status> to update."""
            
        except Exception as e:
            return f"❌ Error: {str(e)}"
    
    def _cmd_update(self, chat_id: str, args: str, username: str) -> str:
        """Update request status"""
        if not args:
            return "Usage: /update <REF-ID> <status>\nStatuses: pending, in_progress, completed, cancelled, survey_needed"
        
        parts = args.split()
        if len(parts) < 2:
            return "Usage: /update <REF-ID> <status>"
        
        ref_id = parts[0].upper()
        new_status = parts[1].lower()
        
        valid_statuses = {'pending', 'in_progress', 'completed', 'cancelled', 'survey_needed'}
        if new_status not in valid_statuses:
            return f"❌ Invalid status. Use: {', '.join(valid_statuses)}"
        
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE requests SET status = %s, updated_at = NOW() WHERE ref_id = %s",
                (new_status, ref_id)
            )
            affected = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            
            if affected:
                return f"✅ Request {ref_id} updated to: {new_status}"
            return f"❌ Request not found: {ref_id}"
            
        except Exception as e:
            return f"❌ Update error: {str(e)}"
    
    def _cmd_ai(self, chat_id: str, args: str, username: str) -> str:
        """AI query command"""
        if not args:
            return "Usage: /ai <your question>\nExample: /ai How many bookings today?"
        
        if not self.ai_enabled:
            return "❌ AI mode is not enabled. Enable it in Admin > AI Settings."
        
        return self._handle_ai_query(chat_id, args)
    
    def _cmd_menu(self, chat_id: str, args: str, username: str) -> str:
        """Show quick action menu with inline buttons"""
        buttons = [
            [
                {'text': '📊 Status', 'callback_data': 'cmd_status'},
                {'text': '📅 Today', 'callback_data': 'cmd_today'}
            ],
            [
                {'text': '🟡 Pending', 'callback_data': 'cmd_pending'},
                {'text': '💰 Revenue', 'callback_data': 'ai_revenue'}
            ],
            [
                {'text': '🏆 Top Services', 'callback_data': 'ai_top_services'},
                {'text': '📈 Analytics', 'callback_data': 'ai_analytics'}
            ],
            [
                {'text': '🤖 Ask AI', 'callback_data': 'ai_help'}
            ]
        ]
        
        self.send_message_with_buttons(
            chat_id,
            "📋 <b>Quick Actions Menu</b>\n\nTap a button or just type naturally:",
            buttons
        )
        return None  # Message already sent
    
    def handle_callback_query(self, callback_query: Dict) -> Optional[str]:
        """Handle inline button callbacks"""
        callback_id = callback_query.get('id')
        chat_id = str(callback_query.get('message', {}).get('chat', {}).get('id', ''))
        data = callback_query.get('data', '')
        username = callback_query.get('from', {}).get('username', 'Unknown')
        
        # Acknowledge the callback
        self._answer_callback(callback_id)
        
        response = None
        
        # Handle command callbacks
        if data == 'cmd_status':
            response = self._cmd_status(chat_id, '', username)
        elif data == 'cmd_today':
            response = self._cmd_today(chat_id, '', username)
        elif data == 'cmd_pending':
            response = self._cmd_pending(chat_id, '', username)
        elif data == 'ai_revenue':
            response = self._handle_ai_query(chat_id, "Show me the revenue report for the last 30 days")
        elif data == 'ai_top_services':
            response = self._handle_ai_query(chat_id, "What are the top performing services this month?")
        elif data == 'ai_analytics':
            response = self._handle_ai_query(chat_id, "Give me an analytics summary for the last 7 days")
        elif data == 'ai_help':
            response = "🤖 <b>AI Mode Active!</b>\n\nJust type your question naturally:\n\n• \"How are we doing today?\"\n• \"Search for John Smith\"\n• \"Mark ABC123 as completed\"\n• \"Email the customer about REF-XYZ\"\n• \"Show customer history for john@email.com\""
        elif data.startswith('update_'):
            # Handle status update callbacks like update_REF123_completed
            parts = data.split('_')
            if len(parts) >= 3:
                ref_id = parts[1]
                new_status = parts[2]
                response = self._cmd_update(chat_id, f"{ref_id} {new_status}", username)
        
        if response:
            self.send_message(chat_id, response)
        
        return response
    
    def _answer_callback(self, callback_id: str):
        """Answer callback query to remove loading state"""
        if not self.bot_token or not REQUESTS_AVAILABLE:
            return
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery"
            requests.post(url, json={'callback_query_id': callback_id}, timeout=5)
        except Exception:
            pass
    
    # =====================
    # POLLING & WEBHOOKS
    # =====================
    
    def start_polling(self, interval: float = 1.0):
        """Start polling for updates in a background thread"""
        if not self.bot_token or not REQUESTS_AVAILABLE:
            logger.error("Cannot start polling: missing bot token or requests library")
            return False
        
        self.should_stop = False
        self.polling_thread = threading.Thread(
            target=self._polling_loop, 
            args=(interval,),
            daemon=True
        )
        self.polling_thread.start()
        logger.info("Telegram bot polling started")
        return True
    
    def stop_polling(self):
        """Stop the polling thread"""
        self.should_stop = True
        if self.polling_thread:
            self.polling_thread.join(timeout=5)
        logger.info("Telegram bot polling stopped")
    
    def _polling_loop(self, interval: float):
        """Main polling loop"""
        while not self.should_stop:
            try:
                updates = self._get_updates()
                for update in updates:
                    self._process_update(update)
            except Exception as e:
                logger.error(f"Polling error: {e}")
            time.sleep(interval)
    
    def _get_updates(self) -> list:
        """Get updates from Telegram"""
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
            params = {
                'offset': self.last_update_id + 1,
                'timeout': 30
            }
            response = requests.get(url, params=params, timeout=35)
            data = response.json()
            
            if data.get('ok'):
                return data.get('result', [])
            return []
        except Exception as e:
            logger.error(f"Failed to get updates: {e}")
            return []
    
    def _process_update(self, update: Dict):
        """Process a single update"""
        update_id = update.get('update_id', 0)
        if update_id > self.last_update_id:
            self.last_update_id = update_id
        
        # Handle callback queries (button presses)
        callback_query = update.get('callback_query')
        if callback_query:
            self.handle_callback_query(callback_query)
            return
        
        # Handle regular messages
        message = update.get('message')
        if message:
            chat_id = str(message.get('chat', {}).get('id', ''))
            response = self.process_message(message)
            if response:
                self.send_message(chat_id, response)
    
    def handle_webhook(self, update_data: Dict) -> Optional[str]:
        """Handle webhook update (for use with Flask)"""
        # Handle callback queries
        callback_query = update_data.get('callback_query')
        if callback_query:
            return self.handle_callback_query(callback_query)
        
        # Handle messages
        message = update_data.get('message')
        if message:
            chat_id = str(message.get('chat', {}).get('id', ''))
            response = self.process_message(message)
            if response:
                self.send_message(chat_id, response)
            return response
        return None


# Singleton instance
_bot_instance = None

def get_telegram_bot(db_config: Dict[str, Any] = None) -> TelegramBotAI:
    """Get or create Telegram bot instance"""
    global _bot_instance
    if _bot_instance is None and db_config:
        _bot_instance = TelegramBotAI(db_config)
    return _bot_instance

def init_telegram_bot(db_config: Dict[str, Any]) -> TelegramBotAI:
    """Initialize the Telegram bot"""
    global _bot_instance
    _bot_instance = TelegramBotAI(db_config)
    return _bot_instance
