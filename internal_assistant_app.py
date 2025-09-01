from depedencies import *

# Core (settings, agent, blob, system prompt)
from internal_assistant_core import (
    get_or_create_agent, settings,
    SYSTEM_PROMPT, blob_container
)

# Modul RAG (answering & indexing)
from rag_modul import (
    rag_answer, process_and_index_docs
)

# Modul Project Progress (MS Planner)
from projectProgress_modul import (
    get_project_progress, project_tool
)

# Modul To Do - Updated to use LLM
from to_do_modul_test import (
    build_auth_url,
    exchange_code_for_token,
    get_todo_lists,
    get_todo_tasks,
    get_current_token,
    is_user_logged_in,
    get_login_status,
    process_todo_query_advanced,  # Use the advanced LLM version
    create_todo_task,
    complete_todo_task,
    update_todo_task
)

from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import webbrowser
import threading

# FastAPI App & Schemas
app = FastAPI(title="Internal Assistant – LangChain + Azure + UI")

# Enable CORS biar bisa akses dari browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simpan token sementara (untuk demo/testing)
user_tokens: Dict[str, dict] = {}

class ChatRequest(BaseModel):
    user_id: str
    message: str

class ChatResponse(BaseModel):
    answer: str
    tool_calls: Optional[List[Dict[str, Any]]] = None

@app.get("/health")
def health():
    return {"ok": True, "service": "Internal Assistant – LangChain + Azure + UI"}

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        agent = get_or_create_agent(req.user_id)
        agent.agent.llm_chain.prompt.messages[0] = SystemMessage(content=SYSTEM_PROMPT)
        result = agent.invoke({"input": req.message})
        answer = result.get("output", "")
        steps = result.get("intermediate_steps", [])
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

class IndexRequest(BaseModel):
    prefix: str = "sop/"

@app.post("/admin/index")
def admin_index(req: IndexRequest):
    return process_and_index_docs(prefix=req.prefix)

@app.get("/login")
def login():
    """Redirect user ke Microsoft login page (delegated)."""
    auth_url = build_auth_url()
    return RedirectResponse(auth_url)

@app.get("/auth/callback")
def auth_callback(code: str, state: Optional[str] = None):
    """Callback setelah user login, tukarkan code dengan token."""
    try:
        token = exchange_code_for_token(code)
        if not token:
            raise HTTPException(status_code=400, detail="Gagal tukar code jadi token")
        # Simpan ke memory (demo)
        user_tokens["current_user"] = token
        return {
            "status": "success", 
            "message": "Login berhasil! Anda bisa menutup tab ini dan kembali ke aplikasi.",
            "token": "tersimpan di server memory"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error saat login: {str(e)}")

# ====================
# Gradio UI Functions
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
    if not prefix:
        prefix = "sop/"
    if not prefix.endswith("/"):
        prefix += "/"

    uploaded = []
    errors = []

    for f in files or []:
        try:
            local_path = getattr(f, "name", None) or str(f)
            fname = os.path.basename(local_path)
            blob_name = f"{prefix}{fname}"
            with open(local_path, "rb") as fp:
                data = fp.read()
            content_type = _detect_mime(local_path)
            blob_client = blob_container.get_blob_client(blob_name)
            blob_client.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )
            uploaded.append(blob_name)
        except Exception as e:
            errors.append(f"{getattr(f,'name',str(f))}: {e}")

    index_report = process_and_index_docs(prefix=prefix)
    return json.dumps(
        {"uploaded": uploaded, "upload_errors": errors, "index_report": index_report},
        indent=2,
        ensure_ascii=False
    )

def ui_rag_chat(message: str, history: List[Dict[str, str]]):
    try:
        answer = rag_answer(message)
        return answer
    except Exception as e:
        return f"Terjadi error saat RAG: {e}"

def ui_project_progress(project_name: str):
    try:
        return get_project_progress(project_name)
    except Exception as e:
        return f"Terjadi error saat ambil progress project: {e}"

# =======================================
# ====   TO DO UI Functions (LLM-Enhanced)  =====
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
    """Main function untuk chat dengan To-Do menggunakan LLM (Enhanced Version)"""
    try:
        # Check login status first
        if not is_user_logged_in():
            return "❌ **Belum login ke Microsoft To-Do.** \n\nSilakan login terlebih dahulu dengan klik tombol '🔑 Login ke Microsoft' di atas."
        
        if not message.strip():
            return """📝 **Selamat datang di Smart To-Do Assistant!** 

Saya menggunakan AI untuk memahami perintah Anda dalam bahasa natural. Anda bisa mengatakan hal seperti:

**Melihat Tasks:**
• "Tampilkan semua task saya"
• "Apa saja task yang deadline hari ini?"
• "Task mana yang sudah selesai minggu ini?"
• "Tunjukkan task yang overdue"

**Membuat Tasks:**
• "Buatkan task baru: Review laporan keuangan"
• "Tambahkan task meeting dengan client besok"
• "Buat reminder untuk call vendor deadline 5 September"

**Menyelesaikan Tasks:**
• "Tandai task 'Meeting pagi' sebagai selesai"
• "Task review document sudah selesai"
• "Complete task presentation"

**Update Tasks:**
• "Ubah deadline task meeting jadi besok"
• "Update deskripsi task review: tambahkan notes dari client"

Coba katakan sesuatu dan saya akan membantu mengelola To-Do Anda! 🤖"""
        
        # Process query dengan advanced LLM processing
        response = process_todo_query_advanced(message, user_tokens.get("current_user"))
        return response
        
    except Exception as e:
        return f"❌ **Error:** {str(e)}\n\nSilakan coba lagi atau refresh status login Anda."

def ui_todo_examples():
    """Return contoh-contoh query yang bisa digunakan (Updated for LLM)"""
    examples = [
        "Tampilkan semua task saya",
        "Task apa saja yang deadline hari ini?",
        "Buatkan task baru: Review laporan keuangan deadline besok",
        "Tandai task 'Meeting pagi' sebagai selesai",
        "Task mana saja yang belum selesai?",
        "Berapa banyak task yang overdue?",
        "Buat reminder untuk call client deadline 5 September",
        "Ubah deadline task presentation jadi minggu depan",
        "Tunjukkan task yang sudah selesai bulan ini",
        "Ada task apa saja yang urgent?"
    ]
    return "\n".join([f"• {ex}" for ex in examples])

def ui_get_smart_suggestions():
    """Generate smart suggestions berdasarkan current state (LLM-powered)"""
    try:
        if not is_user_logged_in():
            return "Silakan login terlebih dahulu untuk mendapatkan suggestions."
        
        # This could be enhanced to use LLM for generating contextual suggestions
        return """💡 **Smart Suggestions berdasarkan AI:**

**Perintah Populer:**
• "Analisis produktivitas saya minggu ini"
• "Task apa yang paling urgent?"
• "Buatkan planning task untuk project baru"
• "Reminder untuk follow up client besok"

**Tips Manajemen Task:**
• Gunakan bahasa natural - AI akan memahami maksud Anda
• Sebutkan deadline dengan jelas: "hari ini", "besok", "5 September"  
• Deskripsi task bisa lebih detail untuk tracking yang better
• AI bisa membantu prioritisasi berdasarkan deadline dan urgency"""
        
    except Exception as e:
        return f"Error generating suggestions: {str(e)}"

# ====================
# Gradio UI (mounted at /ui)  
# ====================

with gr.Blocks(title="Internal Assistant – RAG ", theme=gr.themes.Soft()) as ui:
    gr.Markdown("# Internal Assistant – Knowledge + Project Progress + Smart To-Do")

    with gr.Tab("Upload & Index"):
        gr.Markdown("Upload dokumen kamu ke Azure Blob, lalu index ke Cognitive Search.")
        prefix = gr.Textbox(value="sop/", label="Folder/Prefix di Blob (akan dibuat jika belum ada)")
        files = gr.File(label="Upload Files", file_count="multiple")
        run_btn = gr.Button("Upload & Index")
        output = gr.Code(label="Hasil Upload + Index (JSON)")

        run_btn.click(
            fn=ui_upload_and_index,
            inputs=[files, prefix],
            outputs=[output],
        )

    with gr.Tab("Chat (RAG)"):
        gr.Markdown("Tanya dokumen yang sudah di-index.")
        chat = gr.ChatInterface(
            fn=ui_rag_chat,
            title="RAG Chat",
            textbox=gr.Textbox(placeholder="Tanyakan SOP/kebijakan…"),
        )

    with gr.Tab("Progress Project"):
        gr.Markdown("Cek progress project dari Microsoft Planner.")
        project_name = gr.Textbox(label="Nama Project")
        run_btn2 = gr.Button("Cek Progress")
        output2 = gr.Textbox(label="Hasil Progress", lines=15)

        run_btn2.click(
            fn=ui_project_progress,
            inputs=[project_name],
            outputs=[output2],
        )
    
    with gr.Tab("🤖 Smart To-Do (AI-Powered)"):
        gr.Markdown("# 🤖 Smart Microsoft To-Do Assistant")
        gr.Markdown("**Powered by Azure OpenAI** - Chat dengan AI untuk mengelola To-Do Anda menggunakan bahasa natural!")

        # Login Status Section
        with gr.Row():
            with gr.Column(scale=2):
                login_status = gr.Textbox(
                    label="🔐 Status Login", 
                    value="Checking...", 
                    interactive=False,
                    lines=1
                )
            with gr.Column(scale=1):
                login_btn = gr.Button("🔑 Login ke Microsoft", variant="primary", size="sm")
                refresh_btn = gr.Button("🔄 Refresh Status", size="sm")

        # AI Info Banner
        gr.Markdown("""
        ### 🧠 **AI-Enhanced Features:**
        - **Natural Language Understanding** - Gunakan bahasa sehari-hari
        - **Smart Task Matching** - AI akan mencari task yang Anda maksud
        - **Intelligent Insights** - Analisis produktivitas dan suggestions
        - **Context-Aware Responses** - Memahami konteks dan intent Anda
        """)

        # Main Chat Interface  
        gr.Markdown("### 💬 Chat dengan Smart To-Do Assistant")
        
        # Enhanced chat interface untuk To-Do dengan LLM
        todo_chat = gr.ChatInterface(
            fn=ui_todo_chat,
            title="🤖 AI To-Do Assistant",
            textbox=gr.Textbox(
                placeholder="Contoh: 'Analisis produktivitas saya' atau 'Buatkan task review laporan deadline besok'",
                lines=2
            )
        )

        # Enhanced Examples and Quick Actions
        with gr.Accordion("💡 Contoh & Quick Actions", open=False):
            
            # Smart suggestions powered by LLM context
            with gr.Row():
                with gr.Column():
                    gr.Markdown("#### 📝 **Contoh Natural Language:**")
                    examples_text = gr.Textbox(
                        label="Contoh Query AI-Powered",
                        value=ui_todo_examples(),
                        interactive=False,
                        lines=12
                    )
                
                with gr.Column():
                    gr.Markdown("#### 🎯 **Smart Suggestions:**")
                    suggestions_text = gr.Textbox(
                        label="AI Suggestions",
                        value=ui_get_smart_suggestions(),
                        interactive=False,
                        lines=12
                    )
            
            # Enhanced Quick Actions dengan LLM context
            gr.Markdown("### ⚡ **Quick Actions (AI-Enhanced)**")
            with gr.Row():
                quick_btn1 = gr.Button("📋 Analisis semua task", size="sm", variant="secondary")
                quick_btn2 = gr.Button("⏰ Task urgent hari ini", size="sm", variant="secondary")
                quick_btn3 = gr.Button("📊 Laporan produktivitas", size="sm", variant="secondary")
                quick_btn4 = gr.Button("🎯 Prioritasasi task", size="sm", variant="secondary")
            
            with gr.Row():
                quick_btn5 = gr.Button("🔍 Task yang overdue", size="sm", variant="secondary")
                quick_btn6 = gr.Button("✨ Saran optimalisasi", size="sm", variant="secondary")
                quick_btn7 = gr.Button("📈 Weekly summary", size="sm", variant="secondary")
                quick_btn8 = gr.Button("🚀 Planning task baru", size="sm", variant="secondary")

        # Event handlers untuk enhanced quick actions
        quick_btn1.click(
            fn=lambda: "Analisis semua task saya dan berikan insight tentang produktivitas",
            inputs=None,
            outputs=todo_chat.textbox
        )
        
        quick_btn2.click(
            fn=lambda: "Tampilkan task yang urgent dan deadline hari ini dengan prioritas",
            inputs=None,
            outputs=todo_chat.textbox
        )
        
        quick_btn3.click(
            fn=lambda: "Buatkan laporan produktivitas saya berdasarkan task yang sudah selesai",
            inputs=None,
            outputs=todo_chat.textbox
        )
        
        quick_btn4.click(
            fn=lambda: "Prioritaskan task saya berdasarkan deadline dan urgency",
            inputs=None,
            outputs=todo_chat.textbox
        )
        
        quick_btn5.click(
            fn=lambda: "Tunjukkan semua task yang overdue dan beri saran penanganan",
            inputs=None,
            outputs=todo_chat.textbox
        )
        
        quick_btn6.click(
            fn=lambda: "Berikan saran untuk mengoptimalkan manajemen task saya",
            inputs=None,
            outputs=todo_chat.textbox
        )
        
        quick_btn7.click(
            fn=lambda: "Buatkan summary task yang diselesaikan minggu ini",
            inputs=None,
            outputs=todo_chat.textbox
        )
        
        quick_btn8.click(
            fn=lambda: "Bantu saya planning task baru untuk project yang akan datang",
            inputs=None,
            outputs=todo_chat.textbox
        )

        # Login event handlers
        def handle_tab_select():
            """Dipanggil ketika tab To Do dibuka"""
            return ui_check_login_status()

        # Set initial values saat tab dibuka
        ui.load(
            fn=handle_tab_select,
            inputs=None,
            outputs=[login_status]
        )

        login_btn.click(
            fn=ui_login_to_microsoft,
            inputs=None,
            outputs=[login_status]
        )

        refresh_btn.click(
            fn=ui_check_login_status,
            inputs=None,
            outputs=[login_status]
        )

        # Additional AI-powered features
        with gr.Accordion("🔧 **Advanced AI Features**", open=False):
            gr.Markdown("""
            ### 🚀 **Fitur AI Lanjutan:**
            
            **Natural Language Processing:**
            - AI memahami berbagai cara Anda mengekspresikan perintah
            - Dapat mengenali konteks dan intent dari kalimat kompleks
            - Support untuk bahasa Indonesia dan English mixed
            
            **Smart Task Management:**
            - Fuzzy matching untuk mencari task berdasarkan sebagian nama
            - Intelligent date parsing (hari ini, besok, 5 September, dll)
            - Context-aware suggestions berdasarkan patterns Anda
            
            **Insights & Analytics:**
            - AI-generated insights tentang produktivitas
            - Pattern recognition dalam habit task management
            - Predictive suggestions untuk planning task
            """)

# Mount Gradio di /ui
if mount_gradio_app is not None:
    mount_gradio_app(app, ui, path="/ui")
else:
    app = gr.mount_gradio_app(app, ui, path="/ui")  # type: ignore

# Dev run (python internal_assistant_app.py)
if __name__ == "__main__":
    import nest_asyncio
    import uvicorn
    nest_asyncio.apply()
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)