from depedencies import *
from fastapi import File, UploadFile, Form


# Import enhanced tools dan update agent creation
from internal_assistant_core import (
    get_or_create_agent, settings, 
    blob_container,
    qdrant_client  # 👈 BARU: Impor klien Qdrant
)

# Modul RAG (answering & indexing) - UNCHANGED
from rag_modul import (
    rag_answer, process_and_index_docs
)

# Enhanced project tools - NOW WITH SPA SUPPORT
from projectProgress_modul import (
    # Authentication functions
    build_auth_url as project_build_auth_url,
    exchange_code_for_token as project_exchange_code_for_token,
    is_user_authenticated as project_is_user_authenticated,
    get_login_status as project_get_login_status,
    set_user_token, 
    clear_user_token,
    token_manager,
    
    # HANYA 1 TOOL YANG DIGUNAKAN - Dynamic tool
    project_tool,
    
    # Backward compatibility functions (optional)
    intelligent_project_query,
    process_project_query,
    list_all_projects
)


# Modul To Do - UNCHANGED
from to_do_modul_test import (
    build_auth_url,
    exchange_code_for_token,
    is_user_logged_in,
    get_login_status,
    process_todo_query_advanced,  # This is the main function now (agent-based)
    get_smart_suggestions          # New helper function
)

# 👇 DIUBAH: Impor nama fungsi baru dari documentManagement
from documentManagement import (
    upload_file_to_blob,
    batch_upload_files,
    process_and_index_documents,
    upload_and_index_complete,
    list_documents_in_blob,
    delete_document_complete,     # (Signature diubah)
    batch_delete_documents,       # (Signature diubah)
    inspect_qdrant_collection_sample, # (Nama baru)
    get_qdrant_collection_info,     # (Nama baru)
    rebuild_qdrant_index            # (Nama baru)
)

from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import webbrowser
import threading
from internal_assistant_core import memory_manager

# FastAPI App & Schemas
app = FastAPI(title="Internal Assistant – LangChain + Azure + UI + Document Management")

# Enable CORS untuk SPA compatibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8001", "http://127.0.0.1:8001"],  # Specific origins for SPA
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"]
)

class ChatRequest(BaseModel):
    user_id: str
    message: str

class ChatResponse(BaseModel):
    answer: str
    tool_calls: Optional[List[Dict[str, Any]]] = None

class IndexRequest(BaseModel):
    prefix: str = "sop/"

class DocumentDeleteRequest(BaseModel):
    blob_names: List[str]

# Enhanced System Prompt untuk lebih smart project handling
ENHANCED_SYSTEM_PROMPT = """
You are the company's Internal Assistant with advanced project management capabilities. You can:

1) **RAG Q&A Internal (qna_internal)** – Jawab pertanyaan policy/SOP, handbook dari dokumen internal, disini intinya adalah semua document yang hubungannya dengan internal company, dan anda harus menjawabnya sesuai dengan pertanyaan dari user. Jika konteks berasal dari beberapa potongan, gabungkan untuk menyusun jawaban lengkap.

2) **Dynamic Project Intelligence (intelligent_project_query)** – Akses LANGSUNG ke Microsoft Planner melalui Graph API. Tool ini DINAMIS dan bisa handle SEMUA jenis pertanyaan tentang project tanpa perlu pre-defined functions:
   - List semua projects dari berbagai groups
   - Analisis progress project tertentu
   - Cari task spesifik dan detailnya
   - Bandingkan multiple projects
   - Portfolio analysis
   - Overdue tasks identification
   - Custom queries sesuai kebutuhan user

3) **Smart To-Do Management** – AI Agent dengan akses langsung ke Microsoft To-Do via Graph API:
   - LangChain Agent yang bisa execute tools secara dinamis
   - Search, create, update, complete, delete tasks
   - Analisis produktivitas dan generate insights
   - Natural language processing untuk semua operations
   - Tidak perlu format khusus - tanya apa saja
4) **Template Documents** – Ambil template dokumen
5) **Notifications** – Kirim notifikasi/pengingat
6) **Document Management** - Upload, list, delete, and manage documents in Azure Blob Storage

**DYNAMIC PROJECT INTELLIGENCE:**
Tool ini menggunakan AI Agent yang bisa memutuskan sendiri Graph API calls apa yang diperlukan berdasarkan pertanyaan user. Tidak ada batasan pre-defined - LLM akan:
- Menentukan data apa yang dibutuhkan
- Memanggil Graph API yang sesuai
- Menganalisis raw data
- Memberikan jawaban yang relevan

Contoh queries yang bisa dihandle:
- "List all my projects" → AI akan panggil graph_get_all_plans()
- "Progress project Alpha?" → AI akan cari plan Alpha, ambil tasks, hitung completion
- "Which tasks overdue in Project Beta?" → AI akan filter tasks berdasarkan due date
- "Compare Project A vs B" → AI akan ambil data kedua project dan bandingkan
- "Detail task 'Design Phase' in Project Gamma" → AI akan cari task spesifik
- Dan SEMUA variasi pertanyaan lainnya

**AUTHENTICATION NOTE:**
- Project features require Microsoft login via delegated permissions with PKCE for SPA
- If user asks about projects but not authenticated, inform them to login first

**USAGE GUIDELINES:**
- Untuk SEMUA pertanyaan project, gunakan intelligent_project_query tool dengan query lengkap user
- Tool akan secara otomatis menentukan approach terbaik
- Tidak perlu kategorisasi manual (list vs detail vs compare) - AI handle semua
- Selalu berikan insight yang actionable dan highlight masalah penting

**RESPONSE STYLE:**
- Professional namun friendly
- Gunakan format yang clear dengan bullet points atau sections
- Highlight urgent items dengan emoji peringatan
- Berikan next steps recommendations
- Jawab dalam bahasa Indonesia kecuali diminta otherwise

Gunakan tools secara selektif dan berikan jawaban yang komprehensif namun tidak berlebihan.
"""

@app.get("/health")
def health():
    return {"ok": True, "service": "Internal Assistant – LangChain + Azure + UI + Document Management (SPA Compatible)"}

#Memory endpoints
@app.get("/memory/history/{user_id}")
def get_conversation_history(user_id: str, limit: int = 20):
    """Get conversation history for a user"""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory system not available")
    
    try:
        history = memory_manager.get_recent_history(user_id, limit=limit)
        return {
            "user_id": user_id,
            "message_count": len(history),
            "history": history
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving history: {str(e)}")

@app.delete("/memory/session/{user_id}")
def clear_user_session(user_id: str):
    """Clear Redis cache for user session"""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory system not available")
    
    try:
        memory_manager.clear_session(user_id)
        return {
            "status": "success",
            "message": f"Session cleared for user: {user_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error clearing session: {str(e)}")

@app.get("/memory/stats/{user_id}")
def get_user_memory_stats(user_id: str):
    """Get conversation statistics for a user"""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory system not available")
    
    try:
        stats = memory_manager.get_user_statistics(user_id)
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting statistics: {str(e)}")
    
#end memory endpoints

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        agent = get_or_create_agent(req.user_id)
        
        # Update system prompt with enhanced version
        from langchain.schema import SystemMessage
        agent.agent.llm_chain.prompt.messages[0] = SystemMessage(content=ENHANCED_SYSTEM_PROMPT)
        
        # Process query
        result = agent.invoke({"input": req.message})
        answer = result.get("output", "")
        steps = result.get("intermediate_steps", [])
        
        # Serialize tool calls for debugging
        serialized_steps = []
        for s in steps:
            try:
                action, observation = s
                serialized_steps.append({
                    "tool": getattr(action, "tool", None),
                    "tool_input": getattr(action, "tool_input", None),
                    "log": getattr(action, "log", None),
                    "observation": observation,
                })
            except Exception:
                pass
                
        return ChatResponse(answer=answer, tool_calls=serialized_steps)
        
    except Exception as e:
        if settings.debug:
            raise
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/index")
def admin_index(req: IndexRequest):
    return process_and_index_docs(prefix=req.prefix)

# =====================================================
# NEW: DOCUMENT MANAGEMENT ENDPOINTS
# =====================================================

@app.get("/documents")
def list_documents(prefix: str = "sop/"):
    """List all documents in blob storage with metadata"""
    try:
        documents = list_documents_in_blob(prefix, blob_container)
        return {
            "success": True,
            "prefix": prefix,
            "documents": documents,
            "total_documents": len(documents)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing documents: {str(e)}")

@app.post("/documents/upload")
def upload_documents(files: List[UploadFile] = File(...), prefix: str = Form("sop/")):
    """Upload multiple documents to blob storage and index them"""
    try:
        # Convert UploadFile objects to temporary files for processing
        temp_files = []
        for file in files:
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as temp_file:
                content = file.file.read()
                temp_file.write(content)
                temp_file.flush()
                
                # Create a file-like object with name attribute
                class TempFileWithName:
                    def __init__(self, path, filename):
                        self.name = path
                        self.filename = filename
                
                temp_files.append(TempFileWithName(temp_file.name, file.filename))
        
        # Use the enhanced upload and index function
        result = upload_and_index_complete(temp_files, prefix, blob_container, settings)
        
        # Cleanup temp files
        for temp_file in temp_files:
            try:
                os.unlink(temp_file.name)
            except:
                pass
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error uploading documents: {str(e)}")

@app.delete("/documents")
def delete_documents(request: DocumentDeleteRequest):
    """Delete multiple documents from both blob storage and search index"""
    try:
        result = batch_delete_documents(request.blob_names, blob_container, settings,qdrant_client)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting documents: {str(e)}")

@app.delete("/documents/{blob_name:path}")
def delete_single_document(blob_name: str):
    """Delete single document from both blob storage and search index"""
    try:
        result = delete_document_complete(blob_name, blob_container, settings,qdrant_client)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting document: {str(e)}")

@app.get("/documents/inspect")
def inspect_documents(blob_name: Optional[str] = None, prefix: str = "sop/"):
    """Inspect search index structure and find documents (debugging tool)"""
    try:
        result = inspect_qdrant_collection_sample(qdrant_client, blob_name)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error inspecting index: {str(e)}")

@app.get("/documents/schema")
def get_index_schema():
    """Get search index schema information"""
    try:
        schema_info = get_qdrant_collection_info(settings,qdrant_client)
        return schema_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting schema: {str(e)}")

@app.post("/documents/reindex")
def reindex_documents(prefix: str = "sop/"):
    """Re-index all documents from blob storage"""
    try:
        result = process_and_index_documents(prefix, blob_container, settings)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reindexing documents: {str(e)}")

# =====================================================
# PROJECT AUTHENTICATION ENDPOINTS - FIXED FOR SPA
# =====================================================

@app.get("/project/login")
def project_login():
    """Redirect user ke Microsoft login page untuk project access (SPA dengan PKCE)."""
    try:
        # Generate auth URL with PKCE parameters for SPA
        auth_url = project_build_auth_url()
        return RedirectResponse(auth_url)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error building auth URL: {str(e)}")

@app.get("/project/auth/callback")
def project_auth_callback(
    code: Optional[str] = None, 
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None
):
    """
    Enhanced callback untuk SPA dengan robust error handling dan PKCE support.
    Menangani berbagai error scenarios termasuk SPA-specific issues.
    """
    
    # Handle OAuth errors dari Microsoft
    if error:
        error_details = f"Microsoft OAuth Error: {error}"
        if error_description:
            error_details += f" - {error_description}"
        
        # Specific handling untuk SPA dan PKCE errors
        if "Single-Page Application" in str(error_description):
            error_details += "\n\nSPA Authentication Issue: This error occurs when there's a mismatch in client configuration or request origin. The application is configured correctly for SPA with PKCE."
        elif "PKCE" in str(error_description):
            error_details += "\n\nPKCE (Proof Key for Code Exchange) Issue: Please try the following:\n1. Clear your browser cache\n2. Try logging in again\n3. If the issue persists, check the application configuration."
        
        return HTMLResponse(f"""
            <html>
                <head>
                    <title>SPA Authentication Failed</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; }}
                        .container {{ background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                        .error {{ color: #d32f2f; margin-bottom: 20px; background: #fff5f5; padding: 15px; border-radius: 4px; }}
                        .retry-btn {{ background: #1976d2; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block; margin-top: 15px; }}
                        .spa-info {{ background: #e3f2fd; padding: 15px; border-radius: 4px; margin: 15px 0; border-left: 4px solid #1976d2; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>🔒 SPA Authentication Failed</h1>
                        <div class="error">{error_details}</div>
                        <div class="spa-info">
                            <strong>SPA Configuration:</strong> This application is configured as a Single-Page Application (SPA) with PKCE security. 
                            Make sure you're accessing from the correct origin (http://localhost:8001).
                        </div>
                        <p>You can try logging in again or close this window and return to the main application.</p>
                        <a href="/project/login" class="retry-btn">Try Login Again</a>
                    </div>
                </body>
            </html>
        """, status_code=400)

    # Handle missing authorization code
    if not code:
        return HTMLResponse("""
            <html>
                <head>
                    <title>SPA Authentication Cancelled</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; }}
                        .container {{ background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>❌ SPA Authentication Cancelled</h1>
                        <p>Authorization code not found. The login process may have been cancelled or interrupted.</p>
                        <p>Please close this window and try logging in again from the main application.</p>
                        <a href="/project/login" style="background: #1976d2; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px;">Try Again</a>
                    </div>
                </body>
            </html>
        """, status_code=400)

    # Process successful authorization code for SPA
    try:
        # Exchange code for token with PKCE for SPA - pass the state for validation
        token = project_exchange_code_for_token(code, state)
        
        if not token:
            raise HTTPException(status_code=400, detail="Failed to exchange authorization code for access token in SPA flow")
        
        # Store token menggunakan centralized token manager
        set_user_token(token, "current_user")
        
        # Return success page dengan auto-close functionality for SPA
        return HTMLResponse("""
            <html>
                <head>
                    <title>SPA Login Successful</title>
                    <style>
                        body { 
                            font-family: Arial, sans-serif; 
                            margin: 40px; 
                            background-color: #e8f5e8; 
                            text-align: center;
                        }
                        .container { 
                            background: white; 
                            padding: 30px; 
                            border-radius: 8px; 
                            box-shadow: 0 2px 10px rgba(0,0,0,0.1); 
                            max-width: 500px;
                            margin: 0 auto;
                        }
                        .success { color: #2e7d32; font-size: 18px; margin-bottom: 20px; }
                        .close-btn { 
                            background: #4caf50; 
                            color: white; 
                            padding: 12px 24px; 
                            border: none; 
                            border-radius: 4px; 
                            cursor: pointer; 
                            font-size: 16px;
                            margin-top: 15px;
                        }
                        .spa-badge {
                            background: #1976d2;
                            color: white;
                            padding: 5px 10px;
                            border-radius: 12px;
                            font-size: 12px;
                            display: inline-block;
                            margin: 10px 0;
                        }
                    </style>
                    <script>
                        // Auto close window setelah 3 detik
                        setTimeout(function() {
                            window.close();
                        }, 3000);
                        
                        function closeWindow() {
                            window.close();
                        }
                    </script>
                </head>
                <body>
                    <div class="container">
                        <h1>✅ SPA Login Successful!</h1>
                        <div class="spa-badge">Single-Page Application</div>
                        <div class="success">
                            You have successfully logged in to Microsoft Project Management using SPA with PKCE security.
                        </div>
                        <p>You can now access project data and features securely.</p>
                        <p><small>This window will close automatically in 3 seconds...</small></p>
                        <button class="close-btn" onclick="closeWindow()">Close Window</button>
                    </div>
                </body>
            </html>
        """)
        
    except Exception as e:
        error_message = str(e)
        
        # Enhanced error handling untuk SPA-specific issues
        if "Single-Page Application" in error_message:
            error_details = f"SPA Token Exchange Error: {error_message}\n\nThis occurs when the token request doesn't match SPA configuration. Please ensure:\n1. The application is registered as SPA in Azure\n2. PKCE parameters are correctly generated\n3. Origin header matches the registered redirect URI"
        elif "PKCE" in error_message or "code_verifier" in error_message:
            error_details = f"PKCE Verification Failed: {error_message}\n\nThis is likely due to a session mismatch in SPA flow. Please try:\n1. Starting a fresh login process\n2. Clearing browser cache if the issue persists\n3. Ensure cookies are enabled"
        elif "invalid_grant" in error_message:
            error_details = f"Authorization Grant Invalid: The authorization code may have expired or already been used. Please try logging in again."
        elif "invalid_client" in error_message:
            error_details = f"Client Configuration Error: There may be an issue with the SPA application configuration. Please contact support."
        else:
            error_details = f"SPA Authentication Error: {error_message}"
        
        return HTMLResponse(f"""
            <html>
                <head>
                    <title>SPA Authentication Error</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; margin: 40px; background-color: #ffeaa7; }}
                        .container {{ background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                        .error {{ color: #d63031; background: #fff5f5; padding: 15px; border-radius: 4px; margin: 15px 0; }}
                        .spa-note {{ background: #dbeafe; padding: 15px; border-radius: 4px; margin: 15px 0; border-left: 4px solid #3b82f6; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>⚠️ SPA Authentication Error</h1>
                        <div class="error">{error_details}</div>
                        <div class="spa-note">
                            <strong>Note:</strong> This application uses Single-Page Application (SPA) authentication with PKCE for enhanced security.
                        </div>
                        <p>Please try logging in again. If the problem persists, please contact support.</p>
                        <a href="/project/login" style="background: #0984e3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px;">Retry Login</a>
                    </div>
                </body>
            </html>
        """, status_code=500)

@app.get("/project/status")
def project_auth_status():
    """Check current project authentication status dengan enhanced info untuk SPA"""
    try:
        is_authenticated = project_is_user_authenticated("current_user")
        status_message = project_get_login_status("current_user")
        
        return {
            "authenticated": is_authenticated,
            "status": status_message,
            "login_url": "/project/login" if not is_authenticated else None,
            "client_type": "Single-Page Application (SPA)",
            "security": "PKCE Enhanced",
            "features_available": [
                "Project Progress Analysis",
                "Multi-Project Comparison", 
                "Portfolio Overview",
                "Task Management Insights"
            ] if is_authenticated else [],
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "authenticated": False,
            "status": f"Error checking authentication status: {str(e)}",
            "login_url": "/project/login",
            "client_type": "Single-Page Application (SPA)",
            "error": str(e)
        }

@app.get("/project/logout")
def project_logout():
    """Logout and clear project authentication for SPA"""
    try:
        clear_user_token("current_user")
        return {
            "status": "success",
            "message": "Successfully logged out from SPA project management",
            "client_type": "Single-Page Application",
            "login_url": "/project/login"
        }
    except Exception as e:
        return {
            "status": "error", 
            "message": f"Error during SPA logout: {str(e)}"
        }

# Enhanced project endpoints untuk direct API access dengan SPA support
@app.get("/projects")
def get_all_projects():
    """Enhanced API endpoint untuk mendapatkan list semua projects"""
    try:
        if not project_is_user_authenticated("current_user"):
            return {
                "error": "Authentication required",
                "message": "Please login first via /project/login",
                "login_url": "/project/login",
                "authenticated": False,
                "client_type": "Single-Page Application (SPA)"
            }
        
        # Gunakan dynamic query
        result = intelligent_project_query("List all my projects with their groups and basic info", "current_user")
        
        return {
            "status": "success",
            "result": result,
            "authenticated": True,
            "client_type": "SPA",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "error": f"Error fetching projects: {str(e)}",
            "authenticated": project_is_user_authenticated("current_user"),
            "client_type": "SPA",
            "login_url": "/project/login" if not project_is_user_authenticated("current_user") else None
        }

@app.get("/projects/{project_name}")
def get_project_detail(project_name: str):
    """Enhanced API endpoint untuk detail project tertentu"""
    try:
        if not project_is_user_authenticated("current_user"):
            return {
                "error": "Authentication required", 
                "message": "Please login first via /project/login",
                "login_url": "/project/login",
                "authenticated": False,
                "client_type": "Single-Page Application (SPA)"
            }
        
        # Gunakan dynamic query
        result = intelligent_project_query(
            f"Give me detailed progress analysis of project {project_name} including tasks, completion rate, and any issues",
            "current_user"
        )
        
        return {
            "status": "success",
            "project_detail": result,
            "project_name": project_name,
            "authenticated": True,
            "client_type": "SPA",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "error": f"Error fetching project detail: {str(e)}",
            "project_name": project_name,
            "authenticated": project_is_user_authenticated("current_user"),
            "client_type": "SPA",
            "login_url": "/project/login" if not project_is_user_authenticated("current_user") else None
        }


# =====================================================
# EXISTING TO-DO ENDPOINTS - UNCHANGED but keep separate tokens
# =====================================================

# Keep separate token storage for todo (if needed)
user_todo_tokens: Dict[str, dict] = {}

@app.get("/login")
def login():
    """Redirect user ke Microsoft login page (delegated) - FOR TODO."""
    auth_url = build_auth_url()
    return RedirectResponse(auth_url)

@app.get("/auth/callback")
def auth_callback(code: str, state: Optional[str] = None):
    """Callback after user login - exchange code for token (FOR TODO)"""
    try:
        token = exchange_code_for_token(code)
        if not token:
            raise HTTPException(status_code=400, detail="Failed to exchange code for token")
        
        # Token is now stored internally in to_do_modul_test._token_cache
        # No need to store separately
        
        return HTMLResponse("""
            <html>
                <head>
                    <title>Login Successful</title>
                    <style>
                        body { 
                            font-family: Arial, sans-serif; 
                            margin: 40px; 
                            background-color: #e8f5e8; 
                            text-align: center;
                        }
                        .container { 
                            background: white; 
                            padding: 30px; 
                            border-radius: 8px; 
                            box-shadow: 0 2px 10px rgba(0,0,0,0.1); 
                            max-width: 500px;
                            margin: 0 auto;
                        }
                        .success { color: #2e7d32; font-size: 18px; margin-bottom: 20px; }
                        .close-btn { 
                            background: #4caf50; 
                            color: white; 
                            padding: 12px 24px; 
                            border: none; 
                            border-radius: 4px; 
                            cursor: pointer; 
                            font-size: 16px;
                            margin-top: 15px;
                        }
                    </style>
                    <script>
                        setTimeout(function() { window.close(); }, 3000);
                        function closeWindow() { window.close(); }
                    </script>
                </head>
                <body>
                    <div class="container">
                        <h1>✅ Login Successful!</h1>
                        <div class="success">
                            You have successfully logged in to Microsoft To-Do.
                        </div>
                        <p>You can now access your tasks and use the Smart To-Do Assistant.</p>
                        <p><small>This window will close automatically in 3 seconds...</small></p>
                        <button class="close-btn" onclick="closeWindow()">Close Window</button>
                    </div>
                </body>
            </html>
        """)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error during login: {str(e)}")

# ====================
# Gradio UI Functions - UPDATED WITH DOCUMENT MANAGEMENT
# ====================

def _detect_mime(path: str) -> str:
    ext = (os.path.splitext(path)[1] or "").lower()
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".txt": "text/plain",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(ext, "application/octet-stream")

def ui_upload_and_index(files: List, prefix: str):
    """Enhanced upload function using document management module"""
    if not prefix:
        prefix = "sop/"
    if not prefix.endswith("/"):
        prefix += "/"

    if not files:
        return json.dumps({"error": "No files provided"}, indent=2, ensure_ascii=False)

    try:
        # Use the enhanced document management function
        result = upload_and_index_complete(files, prefix, blob_container, settings)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Upload failed: {str(e)}"}, indent=2, ensure_ascii=False)

def ui_list_documents(prefix: str = "sop/"):
    """List all documents in blob storage"""
    try:
        documents = list_documents_in_blob(prefix, blob_container)
        return json.dumps({
            "prefix": prefix,
            "total_documents": len(documents),
            "documents": documents
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error listing documents: {str(e)}"

def ui_delete_documents(blob_names_text: str):
    """Delete documents from comma-separated list"""
    try:
        if not blob_names_text.strip():
            return "Please provide blob names (comma-separated)"
        
        blob_names = [name.strip() for name in blob_names_text.split(",") if name.strip()]
        result = batch_delete_documents(blob_names, blob_container, settings,qdrant_client)
        
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error deleting documents: {str(e)}"

def ui_inspect_index(blob_name: str = ""):
    """Inspect search index for debugging"""
    try:
        result = inspect_qdrant_collection_sample(settings,qdrant_client,blob_name if blob_name else None)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error inspecting index: {str(e)}"

def ui_get_schema():
    """Get search index schema"""
    try:
        schema_info = get_qdrant_collection_info(settings,qdrant_client)
        return json.dumps(schema_info, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error getting schema: {str(e)}"

def ui_rag_chat(message: str, history: List[Dict[str, str]]):
    """Updated RAG chat dengan memory - extract user_id dari session atau gunakan default"""
    try:
        # OPTION 1: Use Gradio's built-in user tracking if available
        # user_id = gr.Request.username if hasattr(gr, 'Request') else "gradio_user"
        
        # OPTION 2: Simple static user for demo (bisa diganti dengan session tracking)
        user_id = "gradio_user"  # Bisa diupgrade ke proper session management
        
        # Call rag_answer dengan user_id
        answer = rag_answer(message, user_id=user_id)
        return answer
    except Exception as e:
        return f"Terjadi error saat RAG: {e}"

def ui_project_progress(project_name: str):
    """Simple project progress check using dynamic query"""
    try:
        return intelligent_project_query(
            f"What is the current progress of {project_name}? Include completion percentage, task status, and any overdue items.",
            "current_user"
        )
    except Exception as e:
        return f"Terjadi error saat ambil progress project: {e}"

def ui_project_smart_chat(message: str, history: List[List[str]]):
    """Enhanced project chat dengan dynamic AI processing"""
    try:
        if not message.strip():
            return """🚀 **Selamat datang di Dynamic Project Assistant!**

Saya memiliki akses LANGSUNG ke Microsoft Planner API dan bisa menjawab APAPUN tentang projects Anda:

**📊 Contoh Pertanyaan:**
- "Show all my projects"
- "Progress of Project Website?"
- "Which tasks are overdue in Project Alpha?"
- "Compare Project A and Project B"
- "Detail about the 'Design Phase' task"
- "Which project needs urgent attention?"
- "List tasks assigned to me"
- "Projects with completion rate below 50%"

**🎯 Keunggulan:**
✅ Tidak perlu format khusus - tanya apa saja
✅ AI otomatis akses data yang diperlukan
✅ Mendukung typo dan bahasa campuran
✅ Bisa reference previous conversations

⚠️ **PERLU LOGIN:** Fitur project memerlukan autentikasi Microsoft SPA.
Klik tombol '🔑 Login untuk Project Management' jika belum login.

Coba tanyakan sesuatu! 🤖"""
        
        # Check authentication
        if not project_is_user_authenticated("current_user"):
            return """🔒 **SPA Authentication Required**

Untuk mengakses data Microsoft Planner, Anda perlu login terlebih dahulu.

**Cara Login (SPA Mode):**
1. Klik tombol '🔑 Login untuk Project Management'
2. Login dengan akun Microsoft Anda
3. Berikan izin akses untuk membaca data Planner
4. Kembali ke sini dan coba query Anda lagi

Silakan login terlebih dahulu untuk melanjutkan."""
        
        # Process dengan dynamic query - AI yang handle semuanya
        response = intelligent_project_query(message, "current_user")
        return response
        
    except Exception as e:
        error_msg = str(e)
        if "authentication" in error_msg.lower():
            return f"❌ **Authentication Error:** {error_msg}\n\nSilakan coba login ulang."
        return f"❌ **Error:** {error_msg}"

def ui_project_login():
    """Login ke Microsoft untuk project access dengan SPA + PKCE support"""
    try:
        # URL ini harus sesuai dengan yang di-handle oleh FastAPI backend
        login_url = "http://127.0.0.1:8001/project/login"
        
        # Buka browser dalam thread terpisah
        def open_browser():
            webbrowser.open(login_url)
        
        threading.Thread(target=open_browser, daemon=True).start()
        return "🔗 Browser akan terbuka untuk login Microsoft Project dengan SPA + PKCE security. Setelah login, kembali ke sini dan klik 'Refresh Status'."
    except Exception as e:
        return f"❌ Error membuka login: {str(e)}"

def ui_project_check_status():
    """Check authentication status untuk project dengan SPA enhanced info"""
    try:
        status = project_get_login_status("current_user")
        if project_is_user_authenticated("current_user"):
            return f"{status}\n\n✅ **SPA Authentication:** Secure connection established\n🔐 **PKCE Security:** Active and validated\n🌐 **Client Type:** Single-Page Application"
        else:
            return f"{status}\n\n💡 **Tip:** Setelah login, pastikan untuk memberikan consent untuk semua permissions yang diminta.\n🔒 **Security:** SPA dengan PKCE protection"
    except Exception as e:
        return f"❌ Error check status: {str(e)}"

def ui_get_project_suggestions():
    """Generate smart suggestions dengan dynamic capabilities"""
    try:
        if not project_is_user_authenticated("current_user"):
            return """🔒 **SPA Login Required**

Silakan login terlebih dahulu untuk mendapatkan project suggestions."""
        
        suggestions = """💡 **Dynamic Project Assistant - Sample Queries:**

**🎯 Basic Queries:**
- "List all my projects"
- "What projects am I working on?"
- "Show me all groups and their plans"

**📈 Progress & Analysis:**
- "Progress of [Project Name]"
- "Which project is behind schedule?"
- "Show me projects with completion rate below 70%"
- "What's the overall portfolio health?"

**⚠️ Issue Detection:**
- "Which tasks are overdue across all projects?"
- "Show me high priority tasks"
- "Projects that need immediate attention"
- "Bottlenecks in Project Alpha"

**🔍 Specific Queries:**
- "Detail about task 'Design Phase' in Project Beta"
- "Who is assigned to [Task Name]?"
- "List all tasks in the 'Development' bucket"
- "What are the next milestones?"

**⚖️ Comparisons:**
- "Compare Project A and Project B"
- "Which project is more complete?"
- "Rank all projects by progress"

**🔐 Secure:** All data accessed via SPA + PKCE secured Microsoft Graph API

💬 **Just ask in natural language - AI will figure out what to do!**
"""
        return suggestions
        
    except Exception as e:
        return f"Error generating suggestions: {str(e)}"
    
# =======================================
# ====  TO DO UI Functions - UNCHANGED  =====
# =======================================

def ui_login_to_microsoft():
    """Buka browser ke login Microsoft dan return status"""
    try:
        auth_url = build_auth_url()
        # Buka browser dalam thread terpisah
        def open_browser():
            webbrowser.open(auth_url)
        
        threading.Thread(target=open_browser, daemon=True).start()
        return "🔗 Browser akan terbuka untuk login Microsoft. Setelah login, kembali ke sini dan klik 'Refresh Status'."
    except Exception as e:
        return f"❌ Error membuka login: {str(e)}"

def ui_check_login_status():
    """Check apakah user sudah login"""
    try:
        return get_login_status()
    except Exception as e:
        return f"❌ Error check status: {str(e)}"

def ui_todo_chat(message: str, history: List[List[str]]):
    """
    Main function for chat with To-Do using dynamic LLM Agent.
    Now uses agent-based system with direct Graph API access.
    """
    try:
        # Check login status first
        if not is_user_logged_in():
            return "❌ **Belum login ke Microsoft To-Do.**\n\nSilakan login terlebih dahulu dengan klik tombol '🔑 Login ke Microsoft' di atas."
        
        if not message.strip():
            return process_todo_query_advanced("", None, "current_user")
        
        user_id = "current_user"
        
        # Process dengan dynamic agent - no need to pass token anymore
        # The agent will get token automatically from internal cache
        response = process_todo_query_advanced(message, None, user_id)
        return response
        
    except Exception as e:
        error_msg = str(e)
        if "authentication" in error_msg.lower() or "token" in error_msg.lower():
            return f"❌ **Authentication Error:** {error_msg}\n\nSilakan coba login ulang."
        return f"❌ **Error:** {error_msg}\n\nSilakan coba lagi atau refresh status login Anda."

def ui_todo_examples():
    """Return example queries for the dynamic agent"""
    examples = [
        "Tampilkan semua task saya",
        "Task apa yang deadline hari ini?",
        "Buatkan task: Review laporan keuangan deadline besok",
        "Tandai task 'Meeting pagi' sebagai selesai",
        "Cari task tentang client",
        "Update deadline task presentation jadi minggu depan",
        "Task mana yang sudah overdue?",
        "Analisis produktivitas saya minggu ini",
        "Berapa task yang belum selesai?",
        "Ada task apa saja dengan priority tinggi?",
        "Buatkan task meeting dengan client besok jam 2 PM",
        "Delete task yang sudah tidak relevan"
    ]
    return "\n".join([f"• {ex}" for ex in examples])

def ui_get_smart_suggestions():
    """Generate smart suggestions using the new helper function"""
    try:
        if not is_user_logged_in():
            return "Silakan login terlebih dahulu untuk mendapatkan suggestions."
        
        # Use the new helper function from to_do_modul_test
        suggestions = get_smart_suggestions()
        
        additional_tips = """

💡 **Tips Menggunakan Smart To-Do Assistant:**

**Natural Language Commands:**
• "Buatkan task..." untuk create
• "Tandai task ... selesai" untuk complete
• "Cari task tentang..." untuk search
• "Tampilkan semua task" untuk overview
• "Update task ... deadline jadi..." untuk modify

**AI Capabilities:**
✨ Akses langsung ke Microsoft To-Do API
✨ Search tasks across all lists
✨ Analisis produktivitas otomatis
✨ Smart task matching (typo tolerant)
✨ Context-aware dari conversation history

Tanyakan apa saja dalam bahasa natural - AI akan mengerti! 🚀"""

        return suggestions + additional_tips
        
    except Exception as e:
        return f"Error generating suggestions: {str(e)}"
    
# =====================================================
# NEW: Memory Management UI Tab
# =====================================================
def ui_get_history(user_id: str, module: str = "rag"):
    """Get and display conversation history for specific module"""
    if not memory_manager:
        return "Memory system not available"
    
    try:
        history = memory_manager.get_recent_history(user_id, limit=50, module=module)
        
        if not history:
            return f"No conversation history found for user: {user_id} in module: {module}"
        
        formatted = []
        for msg in history:
            role = msg["role"].upper()
            content = msg["content"]
            timestamp = msg.get("timestamp", "N/A")
            formatted.append(f"[{timestamp}] {role}:\n{content}\n")
        
        return "\n".join(formatted)
    except Exception as e:
        return f"Error: {str(e)}"

def ui_clear_session(user_id: str, module: str = "rag"):
    """Clear user session for specific module"""
    if not memory_manager:
        return "Memory system not available"
    
    try:
        memory_manager.clear_session(user_id, module=module)
        return f"✅ {module.upper()} session cleared for user: {user_id}"
    except Exception as e:
        return f"❌ Error: {str(e)}"

def ui_get_stats(user_id: str, module: Optional[str] = None):
    """Get user statistics for specific module or all modules"""
    if not memory_manager:
        return "Memory system not available"
    
    try:
        stats = memory_manager.get_user_statistics(user_id, module=module)
        return json.dumps(stats, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

# ====================
# Gradio UI (WITH ENHANCED DOCUMENT MANAGEMENT)
# ====================

with gr.Blocks(
    title="Internal Assistant Platform",
    theme=gr.themes.Soft(
        primary_hue="slate",
        secondary_hue="slate",
        neutral_hue="slate",
        spacing_size="lg",
        radius_size="lg",
    ),
    css="""
        /* Smooth Modern Elegant Design - Adaptive Theme */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        
        :root {
            --bg-primary: #ffffff;
            --bg-secondary: #f8f9fa;
            --bg-tertiary: #f1f3f5;
            --text-primary: #1a1a1a;
            --text-secondary: #6c757d;
            --text-tertiary: #adb5bd;
            --border-color: #e9ecef;
            --accent-color: #495057;
            --accent-hover: #343a40;
            --shadow-sm: 0 1px 3px rgba(0,0,0,0.04);
            --shadow-md: 0 4px 12px rgba(0,0,0,0.06);
            --shadow-lg: 0 10px 30px rgba(0,0,0,0.08);
        }
        
        @media (prefers-color-scheme: dark) {
            :root {
                --bg-primary: #1a1a1a;
                --bg-secondary: #252525;
                --bg-tertiary: #2d2d2d;
                --text-primary: #e9ecef;
                --text-secondary: #adb5bd;
                --text-tertiary: #6c757d;
                --border-color: #343a40;
                --accent-color: #adb5bd;
                --accent-hover: #dee2e6;
                --shadow-sm: 0 1px 3px rgba(0,0,0,0.2);
                --shadow-md: 0 4px 12px rgba(0,0,0,0.3);
                --shadow-lg: 0 10px 30px rgba(0,0,0,0.4);
            }
        }
        
        .gradio-container {
            max-width: 1600px !important;
            margin: 0 auto !important;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
            background: var(--bg-secondary) !important;
            padding: 2rem 1.5rem !important;
        }
        
        /* Smooth Header with Gradient */
        .smooth-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 50%, #f093fb 100%);
            padding: 3.5rem 3rem;
            border-radius: 24px;
            margin-bottom: 2rem;
            color: white;
            position: relative;
            overflow: hidden;
            box-shadow: var(--shadow-lg);
        }
        
        .smooth-header::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(135deg, rgba(255,255,255,0.1) 0%, transparent 100%);
            pointer-events: none;
        }
        
        .smooth-header h1 {
            margin: 0;
            font-size: 2.25rem;
            font-weight: 600;
            letter-spacing: -0.02em;
            line-height: 1.2;
            position: relative;
            z-index: 1;
        }
        
        .smooth-header p {
            margin: 0.75rem 0 0 0;
            opacity: 0.95;
            font-size: 1.05rem;
            font-weight: 400;
            position: relative;
            z-index: 1;
        }
        
        /* Smooth Tabs */
        .tab-nav button {
            background: transparent !important;
            border: none !important;
            color: var(--text-secondary) !important;
            font-weight: 500 !important;
            padding: 1rem 1.5rem !important;
            border-radius: 16px !important;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
            margin: 0 0.25rem !important;
        }
        
        .tab-nav button:hover {
            color: var(--text-primary) !important;
            background: var(--bg-tertiary) !important;
        }
        
        .tab-nav button.selected {
            color: var(--text-primary) !important;
            background: var(--bg-primary) !important;
            font-weight: 600 !important;
            box-shadow: var(--shadow-sm) !important;
        }
        
        /* Smooth Cards */
        .smooth-card {
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 2rem;
            margin-bottom: 1.5rem;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: var(--shadow-sm);
        }
        
        .smooth-card:hover {
            transform: translateY(-4px);
            box-shadow: var(--shadow-md);
            border-color: var(--accent-color);
        }
        
        .smooth-card h3 {
            margin: 0 0 0.5rem 0;
            color: var(--text-primary);
            font-size: 1.375rem;
            font-weight: 600;
            letter-spacing: -0.01em;
        }
        
        .smooth-card p {
            margin: 0;
            color: var(--text-secondary);
            font-size: 0.9375rem;
            line-height: 1.6;
        }
        
        /* Smooth Alert */
        .smooth-alert {
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-left: 4px solid var(--accent-color);
            padding: 1.25rem 1.5rem;
            border-radius: 16px;
            margin: 1.5rem 0;
            color: var(--text-primary);
            line-height: 1.6;
            box-shadow: var(--shadow-sm);
        }
        
        .smooth-alert-warning {
            background: #fff8f0;
            border-left-color: #fb923c;
            color: #9a3412;
        }
        
        @media (prefers-color-scheme: dark) {
            .smooth-alert-warning {
                background: #2d2416;
                color: #fdba74;
            }
        }
        
        .smooth-alert-info {
            background: #f0f9ff;
            border-left-color: #3b82f6;
            color: #1e40af;
        }
        
        @media (prefers-color-scheme: dark) {
            .smooth-alert-info {
                background: #1e2a3a;
                color: #93c5fd;
            }
        }
        
        .smooth-alert-success {
            background: #f0fdf4;
            border-left-color: #22c55e;
            color: #166534;
        }
        
        @media (prefers-color-scheme: dark) {
            .smooth-alert-success {
                background: #1a2e1f;
                color: #86efac;
            }
        }
        
        /* Smooth Buttons */
        button {
            border-radius: 12px !important;
            font-weight: 500 !important;
            letter-spacing: 0.01em !important;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
            border: none !important;
            padding: 0.875rem 1.75rem !important;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
            color: white !important;
            box-shadow: 0 4px 14px rgba(102, 126, 234, 0.3) !important;
        }
        
        .btn-primary:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4) !important;
        }
        
        .btn-secondary {
            background: var(--bg-primary) !important;
            color: var(--text-primary) !important;
            border: 2px solid var(--border-color) !important;
            box-shadow: var(--shadow-sm) !important;
        }
        
        .btn-secondary:hover {
            border-color: var(--accent-color) !important;
            transform: translateY(-1px) !important;
            box-shadow: var(--shadow-md) !important;
        }
        
        .btn-danger {
            background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%) !important;
            color: white !important;
            box-shadow: 0 4px 14px rgba(239, 68, 68, 0.3) !important;
        }
        
        .btn-danger:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 6px 20px rgba(239, 68, 68, 0.4) !important;
        }
        
        /* Smooth Inputs */
        label {
            color: var(--text-primary) !important;
            font-weight: 500 !important;
            font-size: 0.875rem !important;
            margin-bottom: 0.5rem !important;
        }
        
        input, textarea, select {
            background: var(--bg-primary) !important;
            border: 2px solid var(--border-color) !important;
            border-radius: 12px !important;
            padding: 0.875rem 1rem !important;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
            color: var(--text-primary) !important;
        }
        
        input:focus, textarea:focus, select:focus {
            border-color: #667eea !important;
            box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.1) !important;
            outline: none !important;
        }
        
        input::placeholder, textarea::placeholder {
            color: var(--text-tertiary) !important;
        }
        
        /* Smooth Container */
        .gr-box {
            background: var(--bg-primary) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 16px !important;
            box-shadow: var(--shadow-sm) !important;
        }
        
        /* Chat Messages */
        .message {
            background: var(--bg-primary) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 16px !important;
            padding: 1.25rem !important;
            margin: 0.75rem 0 !important;
            box-shadow: var(--shadow-sm) !important;
        }
        
        /* Code Output */
        pre, code {
            background: var(--bg-tertiary) !important;
            color: var(--text-primary) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 12px !important;
            padding: 1rem !important;
            font-family: 'Monaco', 'Menlo', monospace !important;
        }
        
        /* Accordion */
        details {
            background: var(--bg-primary) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 16px !important;
            overflow: hidden !important;
            margin: 1.5rem 0 !important;
            box-shadow: var(--shadow-sm) !important;
        }
        
        summary {
            padding: 1.25rem 1.5rem !important;
            font-weight: 500 !important;
            cursor: pointer !important;
            transition: all 0.2s !important;
            color: var(--text-primary) !important;
            background: var(--bg-secondary) !important;
        }
        
        summary:hover {
            background: var(--bg-tertiary) !important;
        }
        
        /* Badges */
        .smooth-badge {
            display: inline-block;
            padding: 0.375rem 0.875rem;
            border-radius: 20px;
            font-size: 0.8125rem;
            font-weight: 500;
            margin: 0.25rem;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            border: 1px solid var(--border-color);
        }
        
        /* Section Title */
        .section-title {
            font-size: 1.125rem;
            font-weight: 600;
            color: var(--text-primary);
            margin: 2rem 0 1rem 0;
            letter-spacing: -0.01em;
        }
        
        /* File Upload Area */
        .upload-container {
            background: var(--bg-primary) !important;
            border: 2px dashed var(--border-color) !important;
            border-radius: 16px !important;
            padding: 2rem !important;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
        }
        
        .upload-container:hover {
            border-color: #667eea !important;
            background: var(--bg-secondary) !important;
        }
        
        /* Smooth Scrollbar */
        ::-webkit-scrollbar {
            width: 10px;
            height: 10px;
        }
        
        ::-webkit-scrollbar-track {
            background: var(--bg-secondary);
            border-radius: 10px;
        }
        
        ::-webkit-scrollbar-thumb {
            background: var(--border-color);
            border-radius: 10px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: var(--accent-color);
        }
        
        /* Smooth Transitions */
        * {
            transition: background-color 0.3s cubic-bezier(0.4, 0, 0.2, 1),
                        border-color 0.3s cubic-bezier(0.4, 0, 0.2, 1),
                        color 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        /* Remove default shadows */
        .gr-button {
            box-shadow: none !important;
        }
        
        /* Panel styling */
        .gr-panel {
            background: var(--bg-primary) !important;
            border-radius: 16px !important;
            border: 1px solid var(--border-color) !important;
        }
        
        /* Row spacing */
        .gr-row {
            gap: 1.5rem !important;
        }
        
        /* Column spacing */
        .gr-column {
            gap: 1rem !important;
        }
    """
) as ui:
    
    # Header
    gr.HTML("""
        <div class="smooth-header">
            <h1>Internal Assistant Platform</h1>
            <p>AI-Powered Knowledge Management • Smart Project Intelligence • Document Control • Task Automation</p>
        </div>
    """)
    
    # Document Management Tab
    with gr.Tab("Document Management"):
        gr.HTML("""
            <div class="smooth-card">
                <h3>Document Management System</h3>
                <p>Centralized document storage with AI-powered search and intelligent retrieval</p>
            </div>
        """)

        with gr.Tab("Upload & Index"):
            with gr.Row():
                with gr.Column(scale=2):
                    gr.Markdown("### Upload Documents")
                    prefix = gr.Textbox(
                        value="sop/", 
                        label="Storage Path",
                        placeholder="e.g., sop/, policies/, templates/"
                    )
                    files = gr.File(
                        label="Select Files", 
                        file_count="multiple",
                        file_types=[".pdf", ".docx", ".doc", ".txt", ".pptx", ".xlsx", ".jpg", ".jpeg", ".png"]
                    )
                    run_btn = gr.Button("Upload & Index Documents", variant="primary", size="lg")
                
                with gr.Column(scale=1):
                    gr.HTML("""
                        <div class="smooth-card">
                            <h3>Supported Formats</h3>
                            <div class="smooth-badge">PDF</div>
                            <div class="smooth-badge">Word</div>
                            <div class="smooth-badge">PowerPoint</div>
                            <div class="smooth-badge">Excel</div>
                            <div class="smooth-badge">Text</div>
                            <div class="smooth-badge">Images</div>
                            
                            <div style="margin-top: 1.5rem;">
                                <div class="section-title">Processing Steps</div>
                                <ol style="padding-left: 1.25rem; color: var(--text-secondary);">
                                    <li>Upload to Azure Blob</li>
                                    <li>Extract text content</li>
                                    <li>Generate embeddings</li>
                                    <li>Index in Qdrant</li>
                                    <li>Enable AI search</li>
                                </ol>
                            </div>
                        </div>
                    """)
            
            output = gr.Code(label="Upload Results", lines=12, language="json")
            run_btn.click(fn=ui_upload_and_index, inputs=[files, prefix], outputs=[output])

        with gr.Tab("Browse Library"):
            gr.Markdown("### Document Library")
            with gr.Row():
                with gr.Column(scale=3):
                    list_prefix = gr.Textbox(value="sop/", label="Browse Path")
                with gr.Column(scale=1):
                    list_btn = gr.Button("List Documents", variant="primary")
            
            list_output = gr.Code(label="Documents Found", lines=16, language="json")
            list_btn.click(fn=ui_list_documents, inputs=[list_prefix], outputs=[list_output])

        with gr.Tab("Delete Documents"):
            gr.HTML("""
                <div class="smooth-alert smooth-alert-warning">
                    <strong>Warning:</strong> This permanently removes documents from storage and search index. This cannot be undone.
                </div>
            """)
            
            delete_input = gr.Textbox(
                label="Document Names to Delete",
                placeholder="sop/doc1.pdf, sop/policy.docx",
                lines=3
            )
            delete_btn = gr.Button("Delete Documents", variant="stop", size="lg")
            delete_output = gr.Code(label="Deletion Results", lines=12, language="json")
            
            delete_btn.click(fn=ui_delete_documents, inputs=[delete_input], outputs=[delete_output])

        with gr.Tab("Debug Tools"):
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### Index Inspector")
                    inspect_blob = gr.Textbox(label="Document Name (Optional)")
                    inspect_btn = gr.Button("Inspect Index", variant="secondary")
                    inspect_output = gr.Code(label="Results", lines=10, language="json")
                    
                with gr.Column():
                    gr.Markdown("### Index Schema")
                    schema_btn = gr.Button("View Schema", variant="secondary")
                    schema_output = gr.Code(label="Schema Info", lines=10, language="json")
            
            inspect_btn.click(fn=ui_inspect_index, inputs=[inspect_blob], outputs=[inspect_output])
            schema_btn.click(fn=ui_get_schema, inputs=[], outputs=[schema_output])

        with gr.Tab("Reindex"):
            gr.HTML("""
                <div class="smooth-card">
                    <h3>Rebuild Search Index</h3>
                    <p>Reprocess all documents and rebuild the search index</p>
                </div>
            """)
            
            reindex_prefix = gr.Textbox(value="sop/", label="Path to Reindex")
            reindex_btn = gr.Button("Start Reindexing", variant="primary", size="lg")
            reindex_output = gr.Code(label="Progress", lines=10, language="json")
            
            reindex_btn.click(
                fn=lambda prefix: json.dumps(process_and_index_documents(prefix, blob_container, settings), indent=2, ensure_ascii=False),
                inputs=[reindex_prefix], 
                outputs=[reindex_output]
            )

    # Knowledge Chat Tab
    with gr.Tab("Knowledge Chat"):
        gr.HTML("""
            <div class="smooth-card">
                <h3>AI Knowledge Assistant</h3>
                <p>Ask questions about your indexed documents using natural language</p>
            </div>
        """)
        
        chat = gr.ChatInterface(
            fn=ui_rag_chat,
            textbox=gr.Textbox(placeholder="Ask about policies, procedures, or any content...", container=False),
            examples=[
                "What is our vacation policy?",
                "Explain the onboarding process",
                "What are the safety procedures?",
                "How do I submit expenses?"
            ]
        )

    # Project Management Tab
    with gr.Tab("Project Management"):
        gr.HTML("""
            <div class="smooth-card">
                <h3>Smart Project Intelligence</h3>
                <p>AI-powered project insights from Microsoft Planner with enterprise security</p>
            </div>
        """)

        with gr.Row():
            with gr.Column(scale=3):
                project_login_status = gr.Textbox(
                    label="Authentication Status", 
                    value="Checking...", 
                    interactive=False,
                    lines=2
                )
            with gr.Column(scale=1):
                project_login_btn = gr.Button("Login to Microsoft", variant="primary", size="lg")
                with gr.Row():
                    project_refresh_btn = gr.Button("Refresh", variant="secondary", size="sm")
                    project_logout_btn = gr.Button("Logout", variant="secondary", size="sm")

        gr.HTML("""
            <div class="smooth-alert smooth-alert-info">
                <strong>Enhanced Security:</strong> SPA architecture with PKCE for OAuth 2.0
            </div>
        """)

        with gr.Row():
            with gr.Column(scale=3):
                gr.Markdown("### Project Assistant")
                
                project_chat = gr.ChatInterface(
                    fn=ui_project_smart_chat,
                    textbox=gr.Textbox(
                        placeholder="e.g., 'Analyze Project Alpha' or 'Which projects are at risk?'", 
                        container=False
                    ),
                    examples=[
                        "Show all my projects",
                        "Progress of Project Website",
                        "Which tasks are overdue?",
                        "Compare Project A and B",
                        "Detail about Design Phase task",
                        "Projects needing urgent attention"
                    ]
                )
            
            with gr.Column(scale=1):
                gr.Markdown("### Quick Actions")
                quick_project_btn1 = gr.Button("📋 All Projects", variant="secondary", size="sm")
                quick_project_btn2 = gr.Button("📊 Portfolio Health", variant="secondary", size="sm")
                quick_project_btn3 = gr.Button("⚠️ Overdue Tasks", variant="secondary", size="sm")
                quick_project_btn4 = gr.Button("🎯 Critical Items", variant="secondary", size="sm")
                quick_project_btn5 = gr.Button("📈 Progress Summary", variant="secondary", size="sm")

        with gr.Accordion("Advanced Features", open=False):
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### Examples")
                    project_examples = gr.Textbox(
                        value="""• "What is Project Alpha status?"
• "Which project is delayed?"
• "Compare website and mobile app"
• "Show overdue tasks"
• "Analyze bottlenecks"
• "Rank by completion rate"
• "Which need resources?"
• "Q4 milestone status"
• "Estimate completion time"
• "Critical path analysis"
• "Risk assessment" """,
                        interactive=False,
                        lines=11,
                        show_label=False
                    )
                
                with gr.Column():
                    gr.Markdown("### AI Capabilities")
                    ai_features = gr.Textbox(
                        value=ui_get_project_suggestions(),
                        interactive=False,
                        lines=11,
                        show_label=False
                    )

        def handle_project_tab_select():
            return ui_project_check_status()

        ui.load(fn=handle_project_tab_select, inputs=None, outputs=[project_login_status])
        project_login_btn.click(fn=ui_project_login, inputs=None, outputs=[project_login_status])
        project_refresh_btn.click(fn=ui_project_check_status, inputs=None, outputs=[project_login_status])

        def handle_logout():
            try:
                clear_user_token("current_user")
                return "Logged out successfully"
            except Exception as e:
                return f"Error: {str(e)}"

        project_logout_btn.click(fn=handle_logout, inputs=None, outputs=[project_login_status])

        quick_project_btn1.click(
            fn=lambda: "List all my projects with their groups", 
            inputs=None, 
            outputs=project_chat.textbox
        )
        quick_project_btn2.click(
            fn=lambda: "Analyze overall portfolio health - show average completion, projects at risk, and recommendations", 
            inputs=None, 
            outputs=project_chat.textbox
        )
        quick_project_btn3.click(
            fn=lambda: "Show all overdue tasks across all projects", 
            inputs=None, 
            outputs=project_chat.textbox
        )
        quick_project_btn4.click(
            fn=lambda: "What are the high priority and urgent items that need attention?", 
            inputs=None, 
            outputs=project_chat.textbox
        )
        quick_project_btn5.click(
            fn=lambda: "Give me a progress summary of all active projects", 
            inputs=None, 
            outputs=project_chat.textbox
        )

    # Simple Project View Tab
    with gr.Tab("Simple Project View"):
        gr.HTML("""
            <div class="smooth-card">
                <h3>Quick Project Status</h3>
                <p>Direct progress check from Microsoft Planner</p>
            </div>
        """)
        
        with gr.Row():
            with gr.Column(scale=2):
                project_name = gr.Textbox(label="Project Name", placeholder="Enter project name...")
            with gr.Column(scale=1):
                run_btn2 = gr.Button("Check Progress", variant="primary", size="lg")
        
        output2 = gr.Textbox(label="Progress Report", lines=15, show_copy_button=True)
        run_btn2.click(fn=ui_project_progress, inputs=[project_name], outputs=[output2])

    # Smart To-Do Tab
    with gr.Tab("Smart To-Do"):
        gr.HTML("""
            <div class="smooth-card">
                <h3>🤖 AI Task Management Agent</h3>
                <p>Dynamic LangChain Agent with direct Microsoft Graph API access</p>
            </div>
        """)

        with gr.Row():
            with gr.Column(scale=3):
                login_status = gr.Textbox(label="Authentication Status", value="Checking...", interactive=False, lines=1)
            with gr.Column(scale=1):
                with gr.Row():
                    login_btn = gr.Button("Login", variant="primary", size="sm")
                    refresh_btn = gr.Button("Refresh", variant="secondary", size="sm")

        gr.HTML("""
            <div class="smooth-alert smooth-alert-success">
                <strong>🚀 Powered by LangChain Agent:</strong> Dynamic tool execution • Natural language • Real-time API access • Conversation memory
            </div>
        """)

        gr.Markdown("### Task Assistant")
        
        todo_chat = gr.ChatInterface(
            fn=ui_todo_chat,
            textbox=gr.Textbox(placeholder="e.g., 'Show today's tasks' or 'Create task: Review report'", container=False),
            examples=[
                "Tampilkan semua task saya",
                "Task apa yang deadline hari ini?",
                "Buatkan task: Review laporan keuangan",
                "Cari task tentang meeting",
                "Tandai task 'Review doc' selesai",
                "Analisis produktivitas minggu ini",
                "Task mana yang overdue?",
                "Update deadline task presentation"
            ]
        )

        with gr.Accordion("Usage Guide", open=False):
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### Examples")
                    examples_text = gr.Textbox(value=ui_todo_examples(), interactive=False, lines=11, show_label=False)
                
                with gr.Column():
                    gr.Markdown("### Suggestions")
                    suggestions_text = gr.Textbox(value=ui_get_smart_suggestions(), interactive=False, lines=11, show_label=False)
    # Tambahkan tab ini di Gradio UI (internal_assistant_app.py)
# Letakkan setelah tab terakhir, sebelum closing with gr.Blocks():

    # Memory Management Tab (NEW)
    # Memory Management Tab (UPDATED WITH MODULE SEPARATION)
    with gr.Tab("Memory Management"):
        gr.HTML("""
            <div class="smooth-card">
                <h3>Conversation Memory</h3>
                <p>Manage conversation history stored in Redis (cache) and Cosmos DB (persistent) - Separated by feature module</p>
            </div>
        """)
        
        with gr.Row():
            user_id_input = gr.Textbox(
                label="User ID",
                placeholder="Enter user ID (e.g., current_user, gradio_user)",
                value="gradio_user"
            )
        
        gr.HTML("""
            <div class="smooth-alert smooth-alert-info">
                <strong>Module Separation:</strong> Each feature (RAG, Project Management, To-Do) has its own separate conversation memory to avoid confusion.
            </div>
        """)
        
        # Sub-tabs for each module
        with gr.Tab("📚 RAG Memory"):
            gr.Markdown("### RAG (Knowledge Chat) Conversation History")
            
            with gr.Tab("View History"):
                with gr.Row():
                    get_history_rag_btn = gr.Button("Get RAG History", variant="primary")
                    refresh_history_rag_btn = gr.Button("Refresh", variant="secondary")
                
                history_rag_output = gr.Textbox(
                    label="RAG Conversation History",
                    lines=20,
                    interactive=False,
                    show_copy_button=True
                )
                
                get_history_rag_btn.click(
                    fn=lambda user_id: ui_get_history(user_id, module="rag"),
                    inputs=[user_id_input],
                    outputs=[history_rag_output]
                )
                
                refresh_history_rag_btn.click(
                    fn=lambda user_id: ui_get_history(user_id, module="rag"),
                    inputs=[user_id_input],
                    outputs=[history_rag_output]
                )
            
            with gr.Tab("Statistics"):
                gr.Markdown("### RAG Module Statistics")
                get_stats_rag_btn = gr.Button("Get RAG Statistics", variant="primary")
                
                stats_rag_output = gr.Code(
                    label="Statistics",
                    language="json",
                    lines=10
                )
                
                get_stats_rag_btn.click(
                    fn=lambda user_id: ui_get_stats(user_id, module="rag"),
                    inputs=[user_id_input],
                    outputs=[stats_rag_output]
                )
            
            with gr.Tab("Clear Session"):
                gr.HTML("""
                    <div class="smooth-alert smooth-alert-warning">
                        <strong>Warning:</strong> This will clear the Redis cache for RAG conversations. 
                        Long-term history in Cosmos DB will remain intact.
                    </div>
                """)
                
                clear_rag_btn = gr.Button("Clear RAG Session Cache", variant="stop", size="lg")
                clear_rag_output = gr.Textbox(label="Result", interactive=False)
                
                clear_rag_btn.click(
                    fn=lambda user_id: ui_clear_session(user_id, module="rag"),
                    inputs=[user_id_input],
                    outputs=[clear_rag_output]
                )
        
        # Project Management Memory Tab
        with gr.Tab("📊 Project Memory"):
            gr.Markdown("### Smart Project Management Conversation History")
            
            with gr.Tab("View History"):
                with gr.Row():
                    get_history_project_btn = gr.Button("Get Project History", variant="primary")
                    refresh_history_project_btn = gr.Button("Refresh", variant="secondary")
                
                history_project_output = gr.Textbox(
                    label="Project Conversation History",
                    lines=20,
                    interactive=False,
                    show_copy_button=True
                )
                
                get_history_project_btn.click(
                    fn=lambda user_id: ui_get_history(user_id, module="project"),
                    inputs=[user_id_input],
                    outputs=[history_project_output]
                )
                
                refresh_history_project_btn.click(
                    fn=lambda user_id: ui_get_history(user_id, module="project"),
                    inputs=[user_id_input],
                    outputs=[history_project_output]
                )
            
            with gr.Tab("Statistics"):
                gr.Markdown("### Project Module Statistics")
                get_stats_project_btn = gr.Button("Get Project Statistics", variant="primary")
                
                stats_project_output = gr.Code(
                    label="Statistics",
                    language="json",
                    lines=10
                )
                
                get_stats_project_btn.click(
                    fn=lambda user_id: ui_get_stats(user_id, module="project"),
                    inputs=[user_id_input],
                    outputs=[stats_project_output]
                )
            
            with gr.Tab("Clear Session"):
                gr.HTML("""
                    <div class="smooth-alert smooth-alert-warning">
                        <strong>Warning:</strong> This will clear the Redis cache for Project Management conversations. 
                        Long-term history in Cosmos DB will remain intact.
                    </div>
                """)
                
                clear_project_btn = gr.Button("Clear Project Session Cache", variant="stop", size="lg")
                clear_project_output = gr.Textbox(label="Result", interactive=False)
                
                clear_project_btn.click(
                    fn=lambda user_id: ui_clear_session(user_id, module="project"),
                    inputs=[user_id_input],
                    outputs=[clear_project_output]
                )
        
        # To-Do Memory Tab
        with gr.Tab("✅ To-Do Memory"):
            gr.Markdown("### Smart To-Do Conversation History")
            
            with gr.Tab("View History"):
                with gr.Row():
                    get_history_todo_btn = gr.Button("Get To-Do History", variant="primary")
                    refresh_history_todo_btn = gr.Button("Refresh", variant="secondary")
                
                history_todo_output = gr.Textbox(
                    label="To-Do Conversation History",
                    lines=20,
                    interactive=False,
                    show_copy_button=True
                )
                
                get_history_todo_btn.click(
                    fn=lambda user_id: ui_get_history(user_id, module="todo"),
                    inputs=[user_id_input],
                    outputs=[history_todo_output]
                )
                
                refresh_history_todo_btn.click(
                    fn=lambda user_id: ui_get_history(user_id, module="todo"),
                    inputs=[user_id_input],
                    outputs=[history_todo_output]
                )
            
            with gr.Tab("Statistics"):
                gr.Markdown("### To-Do Module Statistics")
                get_stats_todo_btn = gr.Button("Get To-Do Statistics", variant="primary")
                
                stats_todo_output = gr.Code(
                    label="Statistics",
                    language="json",
                    lines=10
                )
                
                get_stats_todo_btn.click(
                    fn=lambda user_id: ui_get_stats(user_id, module="todo"),
                    inputs=[user_id_input],
                    outputs=[stats_todo_output]
                )
            
            with gr.Tab("Clear Session"):
                gr.HTML("""
                    <div class="smooth-alert smooth-alert-warning">
                        <strong>Warning:</strong> This will clear the Redis cache for To-Do conversations. 
                        Long-term history in Cosmos DB will remain intact.
                    </div>
                """)
                
                clear_todo_btn = gr.Button("Clear To-Do Session Cache", variant="stop", size="lg")
                clear_todo_output = gr.Textbox(label="Result", interactive=False)
                
                clear_todo_btn.click(
                    fn=lambda user_id: ui_clear_session(user_id, module="todo"),
                    inputs=[user_id_input],
                    outputs=[clear_todo_output]
                )
        
        # All Modules Tab
        with gr.Tab("🔄 All Modules"):
            gr.Markdown("### Combined Statistics & Clear All")
            
            with gr.Tab("All Statistics"):
                gr.Markdown("### Statistics for All Modules")
                get_stats_all_btn = gr.Button("Get All Statistics", variant="primary")
                
                stats_all_output = gr.Code(
                    label="Combined Statistics",
                    language="json",
                    lines=15
                )
                
                get_stats_all_btn.click(
                    fn=lambda user_id: ui_get_stats(user_id, module=None),
                    inputs=[user_id_input],
                    outputs=[stats_all_output]
                )
            
            with gr.Tab("Clear All Sessions"):
                gr.HTML("""
                    <div class="smooth-alert smooth-alert-warning">
                        <strong>⚠️ Warning:</strong> This will clear Redis cache for ALL modules (RAG, Project, To-Do). 
                        Long-term history in Cosmos DB will remain intact. Use with caution!
                    </div>
                """)
                
                clear_all_btn = gr.Button("Clear All Sessions Cache", variant="stop", size="lg")
                clear_all_output = gr.Textbox(label="Result", interactive=False)
                
                def clear_all_sessions(user_id):
                    if not memory_manager:
                        return "Memory system not available"
                    try:
                        memory_manager.clear_session(user_id, module=None)
                        return f"✅ All sessions cleared for user: {user_id}\n- RAG session cleared\n- Project session cleared\n- To-Do session cleared"
                    except Exception as e:
                        return f"❌ Error: {str(e)}"
                
                clear_all_btn.click(
                    fn=clear_all_sessions,
                    inputs=[user_id_input],
                    outputs=[clear_all_output]
                )

        def handle_todo_tab_select():
            return ui_check_login_status()

        ui.load(fn=handle_todo_tab_select, inputs=None, outputs=[login_status])
        login_btn.click(fn=ui_login_to_microsoft, inputs=None, outputs=[login_status])
        refresh_btn.click(fn=ui_check_login_status, inputs=None, outputs=[login_status])

# Mount Gradio
if mount_gradio_app is not None:
    mount_gradio_app(app, ui, path="/ui")
else:
    app = gr.mount_gradio_app(app, ui, path="/ui")

# Run
if __name__ == "__main__":
    import nest_asyncio
    import uvicorn
    nest_asyncio.apply()
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)