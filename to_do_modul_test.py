import os
import requests
import json
from urllib.parse import urlencode
from internal_assistant_core import settings, llm, memory_manager
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from langchain.tools import Tool
from langchain.agents import AgentExecutor, create_react_agent
from langchain.prompts import PromptTemplate
from langchain.schema import HumanMessage, SystemMessage

# ====================
# Microsoft To-Do Configuration (Delegated Permissions)
# ====================

CLIENT_ID = settings.MS_CLIENT_ID
CLIENT_SECRET = settings.MS_CLIENT_SECRET
TENANT_ID = settings.MS_TENANT_ID
REDIRECT_URI = os.getenv("AZURE_REDIRECT_URI", "http://localhost:8001/auth/callback")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["Tasks.Read", "Tasks.ReadWrite"]

# Token cache (in-memory for demo)
_token_cache = {}

# ====================
# Authentication Functions
# ====================

def build_auth_url():
    """Build Microsoft login URL (Authorization Code Flow)"""
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "response_mode": "query",
        "scope": " ".join(SCOPES),
        "state": "12345",
    }
    return f"{AUTHORITY}/oauth2/v2.0/authorize?{urlencode(params)}"

def exchange_code_for_token(code: str):
    """Exchange authorization code for access token"""
    url = f"{AUTHORITY}/oauth2/v2.0/token"
    data = {
        "client_id": CLIENT_ID,
        "scope": " ".join(SCOPES),
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "client_secret": CLIENT_SECRET,
    }
    resp = requests.post(url, data=data)
    if resp.status_code != 200:
        raise Exception(f"Failed to exchange code: {resp.text}")
    
    token_data = resp.json()
    token_data["received_at"] = datetime.now().isoformat()
    _token_cache["token"] = token_data
    return token_data

def is_token_expired(token_data: dict) -> bool:
    """Check if token is expired"""
    try:
        if "expires_in" not in token_data or "received_at" not in token_data:
            return True
        
        received_at = datetime.fromisoformat(token_data["received_at"])
        expires_in = token_data["expires_in"]
        expiry_time = received_at + timedelta(seconds=expires_in)
        
        # 5 minute buffer before expiry
        return datetime.now() >= (expiry_time - timedelta(minutes=5))
    except Exception:
        return True

def refresh_token_if_needed():
    """Refresh token if needed"""
    if "token" not in _token_cache:
        return False
    
    token_data = _token_cache["token"]
    
    if not is_token_expired(token_data):
        return True
    
    if "refresh_token" not in token_data:
        _token_cache.clear()
        return False
    
    try:
        url = f"{AUTHORITY}/oauth2/v2.0/token"
        data = {
            "client_id": CLIENT_ID,
            "scope": " ".join(SCOPES),
            "refresh_token": token_data["refresh_token"],
            "grant_type": "refresh_token",
            "client_secret": CLIENT_SECRET,
        }
        resp = requests.post(url, data=data)
        
        if resp.status_code != 200:
            _token_cache.clear()
            return False
        
        new_token = resp.json()
        new_token["received_at"] = datetime.now().isoformat()
        _token_cache["token"] = new_token
        return True
        
    except Exception:
        _token_cache.clear()
        return False

def get_current_token():
    """Get current access token, refresh if needed"""
    if not refresh_token_if_needed():
        raise Exception("Token expired or not logged in. Please login again.")
    return _token_cache["token"]["access_token"]

def is_user_logged_in() -> bool:
    """Check if user is logged in with valid token"""
    try:
        return refresh_token_if_needed()
    except Exception:
        return False

def get_login_status() -> str:
    """Get login status as string"""
    try:
        if is_user_logged_in():
            token_data = _token_cache["token"]
            received_at = datetime.fromisoformat(token_data["received_at"])
            expires_in = token_data["expires_in"]
            expiry_time = received_at + timedelta(seconds=expires_in)
            
            return f"✅ Login active. Token valid until: {expiry_time.strftime('%H:%M:%S')}"
        else:
            return "❌ Not logged in or token expired."
    except Exception as e:
        return f"❌ Error checking status: {str(e)}"

# ====================
# Microsoft Graph API Core Functions
# ====================

def graph_api_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Generic Graph API request handler"""
    try:
        access_token = get_current_token()
        url = f"https://graph.microsoft.com/v1.0{endpoint}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        if method == "GET":
            resp = requests.get(url, headers=headers)
        elif method == "POST":
            resp = requests.post(url, headers=headers, json=data)
        elif method == "PATCH":
            resp = requests.patch(url, headers=headers, json=data)
        elif method == "DELETE":
            resp = requests.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        resp.raise_for_status()
        
        # Return empty dict for DELETE or if no content
        if method == "DELETE" or resp.status_code == 204:
            return {"success": True}
        
        return resp.json()
        
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            _token_cache.clear()
            raise Exception("Token expired. Please login again.")
        raise Exception(f"API Error: {e.response.status_code} - {e.response.text}")

# ====================
# LangChain Tool Functions (Dynamic API Access)
# ====================

def tool_get_all_lists(query: str = "") -> str:
    """Get all To-Do lists for the user. Use this to see available lists."""
    try:
        result = graph_api_request("/me/todo/lists")
        lists = result.get("value", [])
        
        if not lists:
            return "No To-Do lists found."
        
        output = ["Available To-Do Lists:"]
        for lst in lists:
            list_id = lst.get("id", "")
            display_name = lst.get("displayName", "Unnamed")
            is_default = lst.get("isOwner", False)
            output.append(f"- {display_name} (ID: {list_id}) {'[DEFAULT]' if is_default else ''}")
        
        return "\n".join(output)
    except Exception as e:
        return f"Error getting lists: {str(e)}"

def tool_get_tasks_from_list(list_id: str) -> str:
    """Get all tasks from a specific list. Provide the list_id."""
    try:
        if not list_id or not list_id.strip():
            return "Error: list_id is required"
        
        result = graph_api_request(f"/me/todo/lists/{list_id}/tasks")
        tasks = result.get("value", [])
        
        if not tasks:
            return f"No tasks found in list {list_id}"
        
        output = [f"Tasks in list {list_id}:"]
        for task in tasks:
            title = task.get("title", "Untitled")
            task_id = task.get("id", "")
            status = task.get("status", "notStarted")
            due_date = task.get("dueDateTime", {}).get("dateTime", "No deadline")
            importance = task.get("importance", "normal")
            
            status_icon = "✅" if status == "completed" else "⏳"
            priority_icon = "🔴" if importance == "high" else ""
            
            output.append(f"- {status_icon} {priority_icon} {title} (ID: {task_id}, Status: {status}, Due: {due_date})")
        
        return "\n".join(output)
    except Exception as e:
        return f"Error getting tasks: {str(e)}"

def tool_get_all_tasks() -> str:
    """Get ALL tasks from ALL lists. Use this for comprehensive task overview."""
    try:
        # First get all lists
        lists_result = graph_api_request("/me/todo/lists")
        lists = lists_result.get("value", [])
        
        if not lists:
            return "No To-Do lists found."
        
        all_tasks_info = []
        current_date = datetime.now().date()
        
        for lst in lists:
            list_id = lst.get("id")
            list_name = lst.get("displayName", "Unnamed")
            
            # Get tasks for this list
            tasks_result = graph_api_request(f"/me/todo/lists/{list_id}/tasks")
            tasks = tasks_result.get("value", [])
            
            for task in tasks:
                task_info = {
                    "list_name": list_name,
                    "list_id": list_id,
                    "task_id": task.get("id"),
                    "title": task.get("title", "Untitled"),
                    "status": task.get("status", "notStarted"),
                    "importance": task.get("importance", "normal"),
                    "body": task.get("body", {}).get("content", ""),
                    "created": task.get("createdDateTime", ""),
                    "last_modified": task.get("lastModifiedDateTime", "")
                }
                
                # Parse due date
                due_info = task.get("dueDateTime")
                if due_info:
                    due_date_str = due_info.get("dateTime", "")
                    try:
                        due_date = datetime.fromisoformat(due_date_str.replace('Z', '+00:00')).date()
                        days_diff = (due_date - current_date).days
                        
                        task_info["due_date"] = due_date.isoformat()
                        task_info["days_until_due"] = days_diff
                        
                        if days_diff < 0:
                            task_info["due_status"] = f"OVERDUE ({abs(days_diff)} days)"
                        elif days_diff == 0:
                            task_info["due_status"] = "DUE TODAY"
                        elif days_diff == 1:
                            task_info["due_status"] = "DUE TOMORROW"
                        else:
                            task_info["due_status"] = f"Due in {days_diff} days"
                    except Exception:
                        task_info["due_date"] = due_date_str
                        task_info["due_status"] = "Unknown"
                else:
                    task_info["due_date"] = None
                    task_info["due_status"] = "No deadline"
                
                all_tasks_info.append(task_info)
        
        if not all_tasks_info:
            return "No tasks found across all lists."
        
        # Format output
        output = [f"Total tasks found: {len(all_tasks_info)}\n"]
        
        # Group by list
        from itertools import groupby
        all_tasks_info.sort(key=lambda x: x["list_name"])
        
        for list_name, tasks_group in groupby(all_tasks_info, key=lambda x: x["list_name"]):
            tasks_list = list(tasks_group)
            output.append(f"\n📋 {list_name} ({len(tasks_list)} tasks):")
            
            for task in tasks_list:
                status_icon = "✅" if task["status"] == "completed" else "⏳"
                priority_icon = "🔴" if task["importance"] == "high" else ""
                
                task_line = f"  {status_icon} {priority_icon} {task['title']}"
                
                if task["due_status"] != "No deadline":
                    task_line += f" | {task['due_status']}"
                
                output.append(task_line)
        
        return "\n".join(output)
    except Exception as e:
        return f"Error getting all tasks: {str(e)}"

def tool_get_task_details(list_id: str, task_id: str) -> str:
    """Get detailed information about a specific task. Provide list_id and task_id."""
    try:
        if not list_id or not task_id:
            return "Error: Both list_id and task_id are required"
        
        result = graph_api_request(f"/me/todo/lists/{list_id}/tasks/{task_id}")
        
        output = ["Task Details:"]
        output.append(f"Title: {result.get('title', 'Untitled')}")
        output.append(f"Status: {result.get('status', 'notStarted')}")
        output.append(f"Importance: {result.get('importance', 'normal')}")
        
        body = result.get("body", {}).get("content", "")
        if body:
            output.append(f"Description: {body}")
        
        due_date = result.get("dueDateTime", {}).get("dateTime", "")
        if due_date:
            output.append(f"Due Date: {due_date}")
        
        output.append(f"Created: {result.get('createdDateTime', 'Unknown')}")
        output.append(f"Last Modified: {result.get('lastModifiedDateTime', 'Unknown')}")
        
        # Check for linked resources
        if result.get("hasAttachments"):
            output.append("Has attachments: Yes")
        
        return "\n".join(output)
    except Exception as e:
        return f"Error getting task details: {str(e)}"

def tool_create_task(list_id: str, title: str, body: str = "", due_date: str = "", importance: str = "normal") -> str:
    """
    Create a new task in a specific list.
    Args:
        list_id: ID of the list to add task to
        title: Task title (required)
        body: Task description (optional)
        due_date: Due date in ISO format YYYY-MM-DD (optional)
        importance: Task importance - 'low', 'normal', or 'high' (optional, default: normal)
    """
    try:
        if not list_id or not title:
            return "Error: list_id and title are required"
        
        task_data = {
            "title": title.strip(),
            "importance": importance if importance in ["low", "normal", "high"] else "normal"
        }
        
        if body and body.strip():
            task_data["body"] = {
                "content": body.strip(),
                "contentType": "text"
            }
        
        if due_date and due_date.strip():
            # Try to parse and validate date
            try:
                parsed_date = datetime.fromisoformat(due_date.strip())
                task_data["dueDateTime"] = {
                    "dateTime": parsed_date.isoformat(),
                    "timeZone": "UTC"
                }
            except ValueError:
                return f"Error: Invalid due_date format. Use YYYY-MM-DD or ISO format. Got: {due_date}"
        
        result = graph_api_request(f"/me/todo/lists/{list_id}/tasks", method="POST", data=task_data)
        
        return f"✅ Task created successfully!\nTask ID: {result.get('id')}\nTitle: {result.get('title')}"
    except Exception as e:
        return f"Error creating task: {str(e)}"

def tool_update_task(list_id: str, task_id: str, title: str = "", body: str = "", due_date: str = "", importance: str = "", status: str = "") -> str:
    """
    Update an existing task.
    Args:
        list_id: List ID
        task_id: Task ID
        title: New title (optional)
        body: New description (optional)
        due_date: New due date in ISO format (optional)
        importance: New importance level (optional)
        status: New status - 'notStarted', 'inProgress', 'completed' (optional)
    """
    try:
        if not list_id or not task_id:
            return "Error: list_id and task_id are required"
        
        task_data = {}
        
        if title and title.strip():
            task_data["title"] = title.strip()
        
        if body and body.strip():
            task_data["body"] = {
                "content": body.strip(),
                "contentType": "text"
            }
        
        if due_date and due_date.strip():
            try:
                parsed_date = datetime.fromisoformat(due_date.strip())
                task_data["dueDateTime"] = {
                    "dateTime": parsed_date.isoformat(),
                    "timeZone": "UTC"
                }
            except ValueError:
                return f"Error: Invalid due_date format: {due_date}"
        
        if importance and importance in ["low", "normal", "high"]:
            task_data["importance"] = importance
        
        if status and status in ["notStarted", "inProgress", "completed"]:
            task_data["status"] = status
        
        if not task_data:
            return "Error: No update fields provided"
        
        result = graph_api_request(f"/me/todo/lists/{list_id}/tasks/{task_id}", method="PATCH", data=task_data)
        
        return f"✅ Task updated successfully!\nTask: {result.get('title')}"
    except Exception as e:
        return f"Error updating task: {str(e)}"

def tool_complete_task(list_id: str, task_id: str) -> str:
    """Mark a task as completed. Provide list_id and task_id."""
    try:
        if not list_id or not task_id:
            return "Error: list_id and task_id are required"
        
        task_data = {"status": "completed"}
        result = graph_api_request(f"/me/todo/lists/{list_id}/tasks/{task_id}", method="PATCH", data=task_data)
        
        return f"✅ Task completed!\nTask: {result.get('title')}"
    except Exception as e:
        return f"Error completing task: {str(e)}"

def tool_delete_task(list_id: str, task_id: str) -> str:
    """Delete a task permanently. Provide list_id and task_id."""
    try:
        if not list_id or not task_id:
            return "Error: list_id and task_id are required"
        
        graph_api_request(f"/me/todo/lists/{list_id}/tasks/{task_id}", method="DELETE")
        
        return f"✅ Task deleted successfully (List: {list_id}, Task: {task_id})"
    except Exception as e:
        return f"Error deleting task: {str(e)}"

def tool_search_tasks(query: str) -> str:
    """
    Search for tasks by title across all lists. Provide search query.
    Returns matching tasks with their IDs and list information.
    """
    try:
        if not query or not query.strip():
            return "Error: Search query is required"
        
        # Get all tasks first
        lists_result = graph_api_request("/me/todo/lists")
        lists = lists_result.get("value", [])
        
        if not lists:
            return "No lists found."
        
        query_lower = query.lower().strip()
        matches = []
        
        for lst in lists:
            list_id = lst.get("id")
            list_name = lst.get("displayName", "Unnamed")
            
            tasks_result = graph_api_request(f"/me/todo/lists/{list_id}/tasks")
            tasks = tasks_result.get("value", [])
            
            for task in tasks:
                title = task.get("title", "")
                if query_lower in title.lower():
                    matches.append({
                        "list_name": list_name,
                        "list_id": list_id,
                        "task_id": task.get("id"),
                        "title": title,
                        "status": task.get("status", "notStarted")
                    })
        
        if not matches:
            return f"No tasks found matching '{query}'"
        
        output = [f"Found {len(matches)} task(s) matching '{query}':\n"]
        for match in matches:
            status_icon = "✅" if match["status"] == "completed" else "⏳"
            output.append(f"{status_icon} {match['title']}")
            output.append(f"   List: {match['list_name']}")
            output.append(f"   IDs: list_id={match['list_id']}, task_id={match['task_id']}\n")
        
        return "\n".join(output)
    except Exception as e:
        return f"Error searching tasks: {str(e)}"

# ====================
# LangChain Tools Setup
# ====================

def create_todo_tools():
    """Create LangChain tools for To-Do operations"""
    tools = [
        Tool(
            name="get_all_lists",
            func=lambda query="": tool_get_all_lists(query),  # Add lambda wrapper
            description="Get all To-Do lists. Use this first to see available lists and their IDs."
        ),
        Tool(
            name="get_tasks_from_list",
            func=tool_get_tasks_from_list,
            description="Get all tasks from a specific list. Input: list_id (string)"
        ),
        Tool(
            name="get_all_tasks",
            func=lambda query="": tool_get_all_tasks(),  # 👈 FIX: Add lambda wrapper
            description="Get ALL tasks from ALL lists with comprehensive details including due dates, status, priority. Best for overview and analysis."
        ),
        Tool(
            name="get_task_details",
            func=lambda input_str: tool_get_task_details(*input_str.split(",")),
            description="Get detailed info about a specific task. Input: 'list_id,task_id'"
        ),
        Tool(
            name="create_task",
            func=lambda input_str: tool_create_task(*input_str.split("|")),
            description="Create new task. Input: 'list_id|title|body|due_date|importance' (body, due_date, importance are optional, separate with |)"
        ),
        Tool(
            name="update_task",
            func=lambda input_str: tool_update_task(*input_str.split("|")),
            description="Update task. Input: 'list_id|task_id|title|body|due_date|importance|status' (all except IDs optional, separate with |)"
        ),
        Tool(
            name="complete_task",
            func=lambda input_str: tool_complete_task(*input_str.split(",")),
            description="Mark task as completed. Input: 'list_id,task_id'"
        ),
        Tool(
            name="delete_task",
            func=lambda input_str: tool_delete_task(*input_str.split(",")),
            description="Delete task permanently. Input: 'list_id,task_id'"
        ),
        Tool(
            name="search_tasks",
            func=tool_search_tasks,
            description="Search tasks by title across all lists. Input: search query (string)"
        )
    ]
    return tools

# ====================
# Agent Creation
# ====================

def create_todo_agent():
    """Create a ReAct agent for To-Do management"""
    tools = create_todo_tools()
    
    # Enhanced prompt template for To-Do assistant
    template = """You are a Smart Microsoft To-Do Assistant with direct access to Microsoft Graph API.

Current Date: {current_date}
User Timezone: Asia/Jakarta

You have access to these tools to interact with Microsoft To-Do:
{tools}

Tool Names: {tool_names}

IMPORTANT INSTRUCTIONS:
1. When user asks about tasks, use get_all_tasks for comprehensive overview
2. Always get list IDs first using get_all_lists before creating/updating tasks
3. For searches, use search_tasks to find tasks by title
4. Be proactive - if user wants to complete a task but didn't provide IDs, search for it first
5. Format dates as YYYY-MM-DD when creating/updating tasks
6. Provide helpful, actionable responses in Indonesian

Use this format:

Question: the input question you must answer
Thought: think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer in Indonesian with helpful formatting

Begin!

Question: {input}
Thought: {agent_scratchpad}"""

    prompt = PromptTemplate(
        input_variables=["input", "agent_scratchpad", "tools", "tool_names", "current_date"],
        template=template
    )
    
    # Create agent
    agent = create_react_agent(llm, tools, prompt)
    
    # Create executor with verbose output
    agent_executor = AgentExecutor.from_agent_and_tools(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=10,
        max_execution_time=60,
        handle_parsing_errors=True,
        return_intermediate_steps=True
    )
    
    return agent_executor

# ====================
# Main Query Processing Function
# ====================

def process_todo_query_advanced(query: str, token: dict, user_id: str = "current_user") -> str:
    """
    Process To-Do query using dynamic agent with conversation memory.
    Module: "todo" (separated from rag and project)
    """
    if not query.strip():
        return """📝 **Selamat datang di Smart To-Do Assistant!**

Saya adalah asisten AI dengan akses langsung ke Microsoft To-Do Anda. Saya bisa:

**📋 Melihat & Menganalisis:**
• "Tampilkan semua task saya"
• "Task apa yang deadline hari ini?"
• "Ada berapa task yang belum selesai?"
• "Analisis produktivitas minggu ini"
• "Task mana yang overdue?"

**➕ Membuat Task Baru:**
• "Buatkan task: Review laporan keuangan"
• "Tambah task meeting client besok jam 2"
• "Buat reminder call vendor deadline 5 September"

**✅ Menyelesaikan Task:**
• "Tandai task 'Meeting pagi' selesai"
• "Complete task review document"

**✏️ Update Task:**
• "Ubah deadline task meeting jadi besok"
• "Update deskripsi task review"

**🔍 Cari Task:**
• "Cari task tentang client"
• "Ada task apa yang berisi kata 'report'?"

Tanyakan apa saja - saya akan mengakses data To-Do Anda secara real-time! 🚀"""
    
    try:
        # Check authentication
        if not is_user_logged_in():
            return "❌ **Belum login ke Microsoft To-Do.**\n\nSilakan login terlebih dahulu dengan klik tombol '🔑 Login ke Microsoft'."
        
        # Get conversation memory (separated by module)
        memory_context = ""
        if memory_manager:
            try:
                memory_context = memory_manager.get_conversation_context(
                    user_id,
                    max_tokens=600,
                    module="todo"  # Separated module
                )
                if memory_context:
                    print(f"[TODO AGENT] Retrieved conversation history")
            except Exception as e:
                print(f"[TODO AGENT] Memory error: {e}")
        
        # Create agent
        agent = create_todo_agent()
        
        # Prepare input with context
        agent_input = {
            "input": query,
            "current_date": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Add memory context if available
        if memory_context:
            agent_input["input"] = f"""Previous conversation context:
{memory_context}

Current user query: {query}

Consider the conversation history when responding. User might reference previous discussions."""
        
        # Execute agent
        result = agent.invoke(agent_input)
        
        # Extract answer
        answer = result.get("output", "")
        
        # Save to memory
        if memory_manager:
            try:
                memory_manager.add_message(
                    user_id,
                    "user",
                    query,
                    module="todo"
                )
                memory_manager.add_message(
                    user_id,
                    "assistant",
                    answer,
                    metadata={
                        "type": "todo_agent",
                        "tools_used": len(result.get("intermediate_steps", []))
                    },
                    module="todo"
                )
                print(f"[TODO AGENT] Saved to separated memory")
            except Exception as e:
                print(f"[TODO AGENT] Memory save error: {e}")
        
        return answer
        
    except Exception as e:
        error_msg = str(e)
        if "authentication" in error_msg.lower() or "401" in error_msg:
            return f"❌ **Authentication Error:** {error_msg}\n\nSilakan coba login ulang."
        return f"❌ **Error:** {error_msg}\n\nCoba refresh atau login ulang jika masalah berlanjut."

# ====================
# Backward Compatibility Functions (for existing code)
# ====================

# Keep these for any existing code that might call them directly
def get_todo_lists(token: dict = None):
    """Legacy function - redirects to tool"""
    return tool_get_all_lists()

def get_todo_tasks(token: dict, list_id: str):
    """Legacy function - redirects to tool"""
    return json.loads(tool_get_tasks_from_list(list_id))

def get_all_tasks():
    """Legacy function - returns structured data"""
    try:
        result = tool_get_all_tasks()
        # Parse the string output back to structured format if needed
        return result
    except Exception as e:
        raise Exception(f"Error getting all tasks: {str(e)}")

def create_todo_task(list_id: str, title: str, body: str = "", due_date: str = None):
    """Legacy function - redirects to tool"""
    return tool_create_task(list_id, title, body, due_date or "")

def complete_todo_task(list_id: str, task_id: str):
    """Legacy function - redirects to tool"""
    return tool_complete_task(list_id, task_id)

def update_todo_task(list_id: str, task_id: str, title: str = None, body: str = None, due_date: str = None):
    """Legacy function - redirects to tool"""
    return tool_update_task(list_id, task_id, title or "", body or "", due_date or "")

# ====================
# Helper Functions for UI
# ====================

def get_todo_summary() -> str:
    """Get a quick summary of tasks for UI display"""
    try:
        if not is_user_logged_in():
            return "Not logged in"
        
        # Use the tool to get all tasks
        result = tool_get_all_tasks()
        return result
    except Exception as e:
        return f"Error: {str(e)}"

def get_smart_suggestions() -> str:
    """Generate smart suggestions based on current tasks"""
    try:
        if not is_user_logged_in():
            return "Please login first to get suggestions"
        
        # Get all tasks
        lists_result = graph_api_request("/me/todo/lists")
        lists = lists_result.get("value", [])
        
        current_date = datetime.now().date()
        total_tasks = 0
        overdue_tasks = 0
        today_tasks = 0
        
        for lst in lists:
            list_id = lst.get("id")
            tasks_result = graph_api_request(f"/me/todo/lists/{list_id}/tasks")
            tasks = tasks_result.get("value", [])
            
            for task in tasks:
                if task.get("status") == "completed":
                    continue
                
                total_tasks += 1
                
                due_info = task.get("dueDateTime")
                if due_info:
                    try:
                        due_date_str = due_info.get("dateTime", "")
                        due_date = datetime.fromisoformat(due_date_str.replace('Z', '+00:00')).date()
                        
                        if due_date < current_date:
                            overdue_tasks += 1
                        elif due_date == current_date:
                            today_tasks += 1
                    except Exception:
                        pass
        
        suggestions = []
        
        if overdue_tasks > 0:
            suggestions.append(f"⚠️ You have {overdue_tasks} overdue task(s) - prioritize these!")
        
        if today_tasks > 0:
            suggestions.append(f"📅 {today_tasks} task(s) due today - don't forget!")
        
        if total_tasks == 0:
            suggestions.append("✨ No pending tasks - great job!")
        elif total_tasks > 10:
            suggestions.append(f"📊 You have {total_tasks} pending tasks - consider prioritizing")
        
        if not suggestions:
            suggestions.append("🎯 Keep up the good work managing your tasks!")
        
        return "\n".join(suggestions)
        
    except Exception as e:
        return f"Error generating suggestions: {str(e)}"