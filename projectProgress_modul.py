from depedencies import *
from internal_assistant_core import settings, llm
import msal
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
import json
from pydantic import BaseModel, Field
import secrets
import base64
import hashlib
import urllib.parse

# ============================================
# CENTRALIZED TOKEN MANAGEMENT (UNCHANGED)
# ============================================

class TokenManager:
    _instance = None
    _tokens: Dict[str, dict] = {}
    _pkce_data: Dict[str, dict] = {}
    
    def _new_(cls):
        if cls._instance is None:
            cls.instance = super(TokenManager, cls).new_(cls)
        return cls._instance
    
    def set_token(self, user_id: str, token_data: dict):
        self._tokens[user_id] = token_data
    
    def get_token(self, user_id: str = "current_user") -> Optional[dict]:
        return self._tokens.get(user_id)
    
    def clear_token(self, user_id: str = "current_user"):
        if user_id in self._tokens:
            del self._tokens[user_id]
        if user_id in self._pkce_data:
            del self._pkce_data[user_id]
    
    def has_token(self, user_id: str = "current_user") -> bool:
        token_data = self._tokens.get(user_id)
        return token_data is not None and "access_token" in token_data
    
    def set_pkce_data(self, user_id: str, pkce_data: dict):
        self._pkce_data[user_id] = pkce_data
    
    def get_pkce_data(self, user_id: str = "current_user") -> Optional[dict]:
        return self._pkce_data.get(user_id)
    
    def clear_pkce_data(self, user_id: str = "current_user"):
        if user_id in self._pkce_data:
            del self._pkce_data[user_id]

token_manager = TokenManager()

# ============================================
# AUTHENTICATION FUNCTIONS (UNCHANGED)
# ============================================

def generate_pkce_params() -> Dict[str, str]:
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip('=')
    
    return {
        'code_verifier': code_verifier,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256'
    }

def build_auth_url() -> str:
    pkce_params = generate_pkce_params()
    session_key = "current_user"
    token_manager.set_pkce_data(session_key, pkce_params)
    
    state = secrets.token_urlsafe(32)
    auth_endpoint = f"https://login.microsoftonline.com/{settings.MS_TENANT_ID}/oauth2/v2.0/authorize"
    
    scopes = [
        "https://graph.microsoft.com/User.Read",
        "https://graph.microsoft.com/Tasks.Read",
        "https://graph.microsoft.com/Group.Read.All"
    ]
    
    params = {
        'client_id': settings.MS_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': 'http://localhost:8001/project/auth/callback',
        'scope': ' '.join(scopes),
        'state': state,
        'code_challenge': pkce_params['code_challenge'],
        'code_challenge_method': pkce_params['code_challenge_method'],
        'response_mode': 'query'
    }
    
    pkce_params['state'] = state
    token_manager.set_pkce_data(session_key, pkce_params)
    
    auth_url = f"{auth_endpoint}?{urllib.parse.urlencode(params)}"
    return auth_url

def exchange_code_for_token(auth_code: str, state: str = None) -> Optional[dict]:
    try:
        session_key = "current_user"
        pkce_data = token_manager.get_pkce_data(session_key)
        
        if not pkce_data:
            raise Exception("PKCE data not found. Please restart the authentication process.")
        
        if state and pkce_data.get('state') != state:
            raise Exception("State validation failed. Possible CSRF attack.")
        
        token_endpoint = f"https://login.microsoftonline.com/{settings.MS_TENANT_ID}/oauth2/v2.0/token"
        
        scopes = [
            "https://graph.microsoft.com/User.Read",
            "https://graph.microsoft.com/Tasks.Read",
            "https://graph.microsoft.com/Group.Read.All"
        ]
        
        token_data = {
            'client_id': settings.MS_CLIENT_ID,
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': 'http://localhost:8001/project/auth/callback',
            'code_verifier': pkce_data['code_verifier'],
            'scope': ' '.join(scopes)
        }
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'http://localhost:8001'
        }
        
        response = requests.post(token_endpoint, data=token_data, headers=headers)
        
        if response.status_code == 200:
            token_response = response.json()
            token_manager.clear_pkce_data(session_key)
            return token_response
        else:
            error_response = response.json() if response.content else {}
            error_desc = error_response.get('error_description', 'Unknown error')
            error_code = error_response.get('error', 'Unknown')
            raise Exception(f"OAuth error ({error_code}): {error_desc}")
            
    except Exception as e:
        raise

def get_user_token(user_id: str = "current_user") -> str:
    token_data = token_manager.get_token(user_id)
    if not token_data:
        raise Exception("User belum login. Silakan login terlebih dahulu melalui /project/login endpoint")
    return token_data["access_token"]

def is_user_authenticated(user_id: str = "current_user") -> bool:
    return token_manager.has_token(user_id)

def get_login_status(user_id: str = "current_user") -> str:
    if is_user_authenticated(user_id):
        try:
            url = "https://graph.microsoft.com/v1.0/me"
            response_data = make_authenticated_request(url, user_id)
            display_name = response_data.get('displayName', 'Unknown')
            email = response_data.get('mail') or response_data.get('userPrincipalName', 'No email')
            return f"✅ Logged in as: {display_name} ({email})"
        except Exception as e:
            return f"❌ Authentication error: {str(e)}"
    else:
        return "❌ Not logged in. Please click 'Login untuk Project Management' button."

def set_user_token(token_data: dict, user_id: str = "current_user"):
    token_manager.set_token(user_id, token_data)

def clear_user_token(user_id: str = "current_user"):
    token_manager.clear_token(user_id)

# ============================================
# CORE GRAPH API REQUEST HANDLER
# ============================================

def make_authenticated_request(url: str, user_id: str = "current_user", method: str = "GET", data: dict = None):
    """Generic handler untuk semua Graph API requests"""
    if not is_user_authenticated(user_id):
        raise Exception("User not authenticated. Please login first.")
    
    token = get_user_token(user_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": "http://localhost:8001"
    }
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=data)
        else:
            response = requests.request(method, url, headers=headers, json=data)
        
        if response.status_code >= 400:
            error_detail = "Unknown error"
            try:
                error_json = response.json()
                error_detail = error_json.get('error', {}).get('message', str(error_json))
            except:
                error_detail = response.text
            raise Exception(f"HTTP {response.status_code}: {error_detail}")
        
        response.raise_for_status()
        return response.json()
        
    except requests.exceptions.RequestException as e:
        raise Exception(f"Network error: {str(e)}")

# ============================================
# DYNAMIC GRAPH API TOOLS FOR LLM
# ============================================

def graph_get_user_groups(user_id: str = "current_user") -> str:
    """
    Tool: Get all Microsoft 365 groups that the user is a member of.
    Returns JSON string with group information.
    """
    try:
        url = "https://graph.microsoft.com/v1.0/me/memberOf"
        response_data = make_authenticated_request(url, user_id)
        
        groups = response_data.get("value", [])
        groups_filtered = [g for g in groups if g.get("@odata.type") == "#microsoft.graph.group"]
        
        result = {
            "success": True,
            "total_groups": len(groups_filtered),
            "groups": [
                {
                    "id": g.get("id"),
                    "displayName": g.get("displayName"),
                    "description": g.get("description"),
                    "mail": g.get("mail")
                }
                for g in groups_filtered
            ]
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

def graph_get_plans_from_group(group_id: str, user_id: str = "current_user") -> str:
    """
    Tool: Get all Planner plans from a specific group.
    Returns JSON string with plan information.
    """
    try:
        url = f"https://graph.microsoft.com/v1.0/groups/{group_id}/planner/plans"
        response_data = make_authenticated_request(url, user_id)
        
        plans = response_data.get("value", [])
        
        result = {
            "success": True,
            "group_id": group_id,
            "total_plans": len(plans),
            "plans": [
                {
                    "id": p.get("id"),
                    "title": p.get("title"),
                    "createdDateTime": p.get("createdDateTime"),
                    "owner": p.get("owner")
                }
                for p in plans
            ]
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

def graph_get_all_plans(user_id: str = "current_user") -> str:
    """
    Tool: Get ALL plans from ALL groups user is member of.
    This is useful when you don't know which group contains the plan.
    Returns JSON string with all plans.
    """
    try:
        # First get all groups
        groups_response = json.loads(graph_get_user_groups(user_id))
        if not groups_response.get("success"):
            return json.dumps({"success": False, "error": "Failed to get groups"})
        
        all_plans = []
        groups = groups_response.get("groups", [])
        
        for group in groups:
            group_id = group.get("id")
            group_name = group.get("displayName")
            
            try:
                plans_response = json.loads(graph_get_plans_from_group(group_id, user_id))
                if plans_response.get("success"):
                    plans = plans_response.get("plans", [])
                    for plan in plans:
                        plan["groupName"] = group_name
                        plan["groupId"] = group_id
                        all_plans.append(plan)
            except:
                continue
        
        result = {
            "success": True,
            "total_plans": len(all_plans),
            "total_groups": len(groups),
            "plans": all_plans
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

def graph_get_plan_tasks(plan_id: str, user_id: str = "current_user") -> str:
    """
    Tool: Get all tasks from a specific plan.
    Returns JSON string with detailed task information.
    """
    try:
        url = f"https://graph.microsoft.com/v1.0/planner/plans/{plan_id}/tasks"
        response_data = make_authenticated_request(url, user_id)
        
        tasks = response_data.get("value", [])
        
        # Parse and enrich task data
        enriched_tasks = []
        for task in tasks:
            enriched_tasks.append({
                "id": task.get("id"),
                "title": task.get("title"),
                "percentComplete": task.get("percentComplete", 0),
                "priority": task.get("priority", 5),
                "dueDateTime": task.get("dueDateTime"),
                "createdDateTime": task.get("createdDateTime"),
                "bucketId": task.get("bucketId"),
                "assignedTo": len(task.get("assignments", {})),
                "hasDescription": bool(task.get("hasDescription")),
                "checklistItemCount": task.get("checklistItemCount", 0),
                "completedChecklistItemCount": task.get("completedChecklistItemCount", 0)
            })
        
        result = {
            "success": True,
            "plan_id": plan_id,
            "total_tasks": len(tasks),
            "tasks": enriched_tasks
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

def graph_get_plan_buckets(plan_id: str, user_id: str = "current_user") -> str:
    """
    Tool: Get all buckets (task containers) from a specific plan.
    Buckets are used to organize tasks into categories/phases.
    Returns JSON string with bucket information.
    """
    try:
        url = f"https://graph.microsoft.com/v1.0/planner/plans/{plan_id}/buckets"
        response_data = make_authenticated_request(url, user_id)
        
        buckets = response_data.get("value", [])
        
        result = {
            "success": True,
            "plan_id": plan_id,
            "total_buckets": len(buckets),
            "buckets": [
                {
                    "id": b.get("id"),
                    "name": b.get("name"),
                    "orderHint": b.get("orderHint")
                }
                for b in buckets
            ]
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

def graph_get_task_details(task_id: str, user_id: str = "current_user") -> str:
    """
    Tool: Get detailed information about a specific task.
    Returns JSON string with full task details including description.
    """
    try:
        url = f"https://graph.microsoft.com/v1.0/planner/tasks/{task_id}"
        response_data = make_authenticated_request(url, user_id)
        
        # Also get task details (description)
        details_url = f"https://graph.microsoft.com/v1.0/planner/tasks/{task_id}/details"
        try:
            details_data = make_authenticated_request(details_url, user_id)
            description = details_data.get("description", "")
        except:
            description = ""
        
        result = {
            "success": True,
            "task": {
                "id": response_data.get("id"),
                "title": response_data.get("title"),
                "percentComplete": response_data.get("percentComplete", 0),
                "priority": response_data.get("priority", 5),
                "dueDateTime": response_data.get("dueDateTime"),
                "startDateTime": response_data.get("startDateTime"),
                "completedDateTime": response_data.get("completedDateTime"),
                "bucketId": response_data.get("bucketId"),
                "planId": response_data.get("planId"),
                "description": description,
                "assignments": response_data.get("assignments", {}),
                "checklistItemCount": response_data.get("checklistItemCount", 0),
                "completedChecklistItemCount": response_data.get("completedChecklistItemCount", 0)
            }
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

# ============================================
# INTELLIGENT PROJECT QUERY PROCESSOR
# ============================================

def intelligent_project_query(user_query: str, user_id: str = "current_user") -> str:
    """
    Main entry point: Process user query dynamically using LLM with Graph API tools.
    LLM will decide which Graph API calls to make based on the question.
    """
    try:
        from internal_assistant_core import memory_manager
    except:
        memory_manager = None
    
    if not is_user_authenticated(user_id):
        return "🔒 Anda belum login ke Microsoft. Silakan login terlebih dahulu untuk mengakses data project."
    
    try:
        # Get conversation context from memory
        project_context = ""
        if memory_manager:
            try:
                project_context = memory_manager.get_conversation_context(
                    user_id, 
                    max_tokens=600,
                    module="project"
                )
            except Exception as e:
                print(f"[PROJECT MEMORY] Error: {e}")
        
        # Build dynamic prompt for LLM
        current_datetime = datetime.now(timezone.utc).isoformat()
        
        system_prompt = f"""You are Smart Project Assistant - an intelligent, friendly Microsoft Planner assistant with personality and memory.

PERSONALITY & INTERACTION:
- You are professional yet warm and personable
- You remember user's name and previous conversations
- You can handle casual chat, greetings, and general questions
- You build rapport while staying focused on helping with project management
- Use natural, conversational Indonesian language
- Show enthusiasm when appropriate with emojis (but don't overuse them)

PRIMARY MISSION: Microsoft Planner Project Management
You have DIRECT ACCESS to Graph API for real-time project data analysis.

User Query: "{user_query}"
"""
        
        if project_context:
            system_prompt += f"""
CONVERSATION HISTORY:
{project_context}

IMPORTANT: Use this context to:
- Remember the user's name if they introduced themselves
- Reference previous discussions about projects
- Build on earlier conversations naturally
- Show continuity in your assistance
"""
        
        system_prompt += f"""

RESPONSE GUIDELINES:

1. For GENERAL QUESTIONS (greetings, introductions, casual chat):
   - Respond warmly and naturally
   - If user introduces their name, remember it and use it
   - Examples:
     * "Hai" → "Halo! Senang bisa membantu Anda. 😊 Saya Smart Project Assistant, siap membantu mengelola project Anda di Microsoft Planner. Ada yang bisa saya bantu hari ini?"
     * "Nama saya [X]" → "Senang berkenalan dengan Anda, [X]! 😊 Saya di sini untuk membantu mengelola project Anda. Ingat, One Team One Solution! Ada project yang ingin kita review bersama?"
     * "Apa kabar?" → "Kabar baik! Saya siap membantu Anda mengoptimalkan project management. 😊 Bagaimana dengan project Anda hari ini?"
   
2. For OFF-TOPIC QUESTIONS (not related to project management):
   - Answer briefly and politely
   - Gently redirect to your primary function
   - Example: "Itu pertanyaan menarik! Tapi keahlian utama saya adalah project management di Microsoft Planner. 😊 Ingat, One Team One Solution! Ada project yang ingin kita bahas? Saya bisa bantu analisis progress, cek task overdue, atau bandingkan beberapa project."

3. For PROJECT-RELATED QUESTIONS:
   - Use the Graph API tools to get real-time data
   - Provide detailed, actionable insights
   - Highlight issues and opportunities proactively

AVAILABLE GRAPH API TOOLS:
1. graph_get_all_plans() - Get ALL plans from all groups
2. graph_get_user_groups() - Get user's Microsoft 365 groups
3. graph_get_plans_from_group(group_id) - Get plans from specific group
4. graph_get_plan_tasks(plan_id) - Get tasks from a plan
5. graph_get_plan_buckets(plan_id) - Get buckets (task categories) from a plan
6. graph_get_task_details(task_id) - Get detailed info about specific task

PROJECT QUERY APPROACH:
1. Understand what user is asking
2. Determine if tools are needed (for project data) or just conversation
3. If tools needed: Call appropriate Graph API tools
4. Analyze data intelligently
5. Provide clear, actionable answer

EXAMPLES:

Query: "Hai, nama saya Budi"
Response: "Halo Budi! Senang berkenalan dengan Anda. 😊 Saya Smart Project Assistant, siap membantu mengelola project Anda di Microsoft Planner. Ingat, One Team One Solution! Ada project yang ingin kita review hari ini?"
[NO TOOLS NEEDED]

Query: "Gimana cuaca hari ini?"
Response: "Saya tidak punya akses ke data cuaca, tapi saya ahli dalam project management! 😊 Ingat, One Team One Solution! Bagaimana kalau kita fokus ke project Anda? Ada yang perlu di-review?"
[NO TOOLS NEEDED]

Query: "List all my projects"
Response: [CALL graph_get_all_plans() → Analyze → Present nicely]
[TOOLS NEEDED]

Query: "Progress project Website gimana?"
Response: [CALL graph_get_all_plans() → Find "Website" → CALL graph_get_plan_tasks() → Calculate progress → Report]
[TOOLS NEEDED]

Query: "Ada task yang overdue ga?"
Response: [CALL graph_get_all_plans() → For each plan CALL graph_get_plan_tasks() → Filter overdue → List them]
[TOOLS NEEDED]

CRITICAL GUIDELINES:
- Current datetime for overdue calculation: {current_datetime}
- Be FLEXIBLE with project/task names (handle typos, variations)
- Always check if data retrieval was successful (check "success": true in JSON)
- If project not found, list available projects
- Provide actionable insights, not just raw data
- Use natural, conversational Indonesian
- Highlight urgent issues with appropriate emojis (⚠ 🔴 ⏰)
- Show enthusiasm for good progress with positive emojis (✅ 🎉 👍)
- Be accurate with numbers and dates
- Reference user's name if you know it
- Build rapport while staying helpful

REMEMBER: You're not just a data retriever - you're an intelligent assistant who:
- Builds relationships through memory and personality
- Understands context from conversation history
- Provides strategic insights, not just information
- Guides users to better project management
- Represents "One Team One Solution" spirit

Now process the user's query intelligently!
"""
        
        # Rest of the code remains the same...
        from langchain.agents import AgentExecutor, create_openai_functions_agent
        from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
        from langchain.tools import StructuredTool

        class NoArgsInput(BaseModel):
            """Empty input schema for tools without parameters"""
            pass

        class GroupInput(BaseModel):
            """Input schema for group-related operations"""
            group_id: str = Field(description="The ID of the group")

        class PlanInput(BaseModel):
            """Input schema for plan-related operations"""
            plan_id: str = Field(description="The ID of the plan")

        class TaskInput(BaseModel):
            """Input schema for task-related operations"""
            task_id: str = Field(description="The ID of the task")
        
        tools = [
            StructuredTool.from_function(
                name="graph_get_all_plans",
                description="Get ALL Planner plans from all groups the user is a member of. Use this to discover available projects.",
                func=lambda: graph_get_all_plans(user_id),
                args_schema=NoArgsInput
            ),
            StructuredTool.from_function(
                name="graph_get_user_groups",
                description="Get all Microsoft 365 groups the user is a member of.",
                func=lambda: graph_get_user_groups(user_id),
                args_schema=NoArgsInput
            ),
            StructuredTool.from_function(
                name="graph_get_plans_from_group",
                description="Get all Planner plans from a specific group. Requires group_id.",
                func=lambda group_id: graph_get_plans_from_group(group_id, user_id),
                args_schema=GroupInput
            ),
            StructuredTool.from_function(
                name="graph_get_plan_tasks",
                description="Get all tasks from a specific plan. Requires plan_id. Returns task list with completion percentages, due dates, priorities.",
                func=lambda plan_id: graph_get_plan_tasks(plan_id, user_id),
                args_schema=PlanInput
            ),
            StructuredTool.from_function(
                name="graph_get_plan_buckets",
                description="Get all buckets (task categories/phases) from a plan. Requires plan_id.",
                func=lambda plan_id: graph_get_plan_buckets(plan_id, user_id),
                args_schema=PlanInput
            ),
            StructuredTool.from_function(
                name="graph_get_task_details",
                description="Get detailed information about a specific task including description. Requires task_id.",
                func=lambda task_id: graph_get_task_details(task_id, user_id),
                args_schema=TaskInput
            )
        ]
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad")
        ])
        
        agent = create_openai_functions_agent(llm, tools, prompt)
        agent_executor = AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=True,
            max_iterations=10,
            return_intermediate_steps=True
        )
        
        result = agent_executor.invoke({"input": user_query})
        answer = result.get("output", "Maaf, saya tidak bisa memproses permintaan Anda.")
        
        # Save to memory
        if memory_manager:
            try:
                memory_manager.add_message(user_id, "user", user_query, module="project")
                memory_manager.add_message(
                    user_id,
                    "assistant",
                    answer,
                    metadata={"type": "dynamic_project_query"},
                    module="project"
                )
            except Exception as e:
                print(f"[PROJECT MEMORY] Error saving: {e}")
        
        return answer
        
    except Exception as e:
        error_msg = str(e)
        if "authentication" in error_msg.lower():
            return "🔒 Authentication error. Silakan login kembali."
        return f"❌ Error: {error_msg}"
    
# ============================================
# LANGCHAIN TOOL DEFINITIONS
# ============================================

class ProjectQueryInput(BaseModel):
    query: str = Field(description="The user's full natural language query about projects, tasks, or anything related to Microsoft Planner")

# Main tool for agent to use
project_tool = StructuredTool.from_function(
    name="intelligent_project_query",
    description="DYNAMIC PROJECT TOOL: Use this for ANY question about Microsoft Planner projects. The tool uses AI to dynamically access Graph API and retrieve exactly what's needed to answer the question. Works for: listing projects, checking progress, finding tasks, comparing projects, analyzing data, etc. REQUIRES USER LOGIN.",
    func=lambda query: intelligent_project_query(query, "current_user"),
    args_schema=ProjectQueryInput,
)

# Backward compatibility aliases
project_detail_tool = project_tool
project_list_tool = project_tool
portfolio_analysis_tool = project_tool

# Backward compatibility functions
def process_project_query(user_query: str, user_id: str = "current_user") -> str:
    return intelligent_project_query(user_query, user_id)

def list_all_projects(user_id: str = "current_user") -> str:
    return intelligent_project_query("List all my projects", user_id)

def get_project_progress(project_name: str, user_id: str = "current_user") -> str:
    return intelligent_project_query(f"What is the progress of {project_name}?", user_id)