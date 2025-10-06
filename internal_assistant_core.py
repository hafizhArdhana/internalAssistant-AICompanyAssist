from depedencies import *

# Load env & Settings
load_dotenv()

# Konfigurasi Settings
class Settings(BaseModel):
    # Azure OpenAI
    openai_key: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    openai_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    openai_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview")
    openai_deployment: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    openai_embed_deployment: str = os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large")

    # Cognitive Search
    search_endpoint: str = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    search_key: str = os.getenv("AZURE_SEARCH_KEY", "")
    search_index: str = os.getenv("AZURE_SEARCH_INDEX_NAME", "internal-docs-index")

    # Blob
    blob_conn: str = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
    blob_container: str = os.getenv("AZURE_BLOB_CONTAINER", "internal-docs")

    # Document Intelligence
    docint_endpoint: str = os.getenv("AZURE_DOCINT_ENDPOINT", "")
    docint_key: str = os.getenv("AZURE_DOCINT_KEY", "")

    # Azure Function (preprocess)
    func_preprocess_url: str = os.getenv("AZURE_FUNCTION_PREPROCESS_URL", "")
    func_preprocess_key: str = os.getenv("AZURE_FUNCTION_PREPROCESS_KEY", "")

    # SQL
    sql_server: str = os.getenv("AZURE_SQL_SERVER", "")
    sql_db: str = os.getenv("AZURE_SQL_DATABASE", "")
    sql_user: str = os.getenv("AZURE_SQL_USERNAME", "")
    sql_password: str = os.getenv("AZURE_SQL_PASSWORD", "")

        # === Load dari .env ===
    MS_CLIENT_ID : str = os.getenv("MS_CLIENT_ID","")
    MS_CLIENT_SECRET : str = os.getenv("MS_CLIENT_SECRET","")
    MS_TENANT_ID : str = os.getenv("MS_TENANT_ID","")
    MS_GRAPH_SCOPE : str = os.getenv("MS_GRAPH_SCOPE", "https://graph.microsoft.com/.default")
    MS_GROUP_ID : str = os.getenv("MS_GROUP_ID","")  # opsional, bisa kosong

    @property
    def ms_authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.MS_TENANT_ID}"
    

    # Notifications
    notify_webhook: str = os.getenv("NOTIFY_WEBHOOK_URL", "")

    debug: bool = os.getenv("APP_DEBUG", "false").lower() == "true"

    #qdrant
    qdrant_url: str = os.getenv("QDRANT_URL","")
    qdrant_api_key: str = os.getenv("QDRANT_API_KEY","")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION","internal-docs-index")

    #redis
    redis_host: str = os.getenv("REDIS_HOST", "")
    redis_port: int = int(os.getenv("REDIS_PORT", "6380"))
    redis_password: str = os.getenv("REDIS_PASSWORD", "")
    redis_ssl: bool = os.getenv("REDIS_SSL", "true").lower() == "true"
    
    # Memory - Cosmos DB (Long-term storage)
    cosmos_endpoint: str = os.getenv("COSMOS_ENDPOINT", "")
    cosmos_key: str = os.getenv("COSMOS_KEY", "")
    cosmos_database: str = os.getenv("COSMOS_DATABASE", "internal_assistant")
    cosmos_container: str = os.getenv("COSMOS_CONTAINER", "conversation_history")

settings = Settings()


# =====================
# Core Clients
# =====================
llm = AzureChatOpenAI(
    azure_endpoint=settings.openai_endpoint,
    api_key=settings.openai_key,
    api_version=settings.openai_api_version,
    deployment_name=settings.openai_deployment,
    temperature=0.2,
)

embeddings = AzureOpenAIEmbeddings(
    azure_endpoint=settings.openai_endpoint,
    api_key=settings.openai_key,
    api_version=settings.openai_api_version,
    deployment=settings.openai_embed_deployment,
    chunk_size=32
)

# # VectorStore via azure ai search
# vectorstore = AzureSearch(
#     azure_search_endpoint=settings.search_endpoint,
#     azure_search_key=settings.search_key,
#     index_name=settings.search_index,
#     embedding_function=embeddings.embed_query,
# )

# qdrant setup
qdrant_client = QdrantClient(
    url=settings.qdrant_url,
    api_key=settings.qdrant_api_key
)

vectorstoreQ = QdrantVectorStore(
    client=qdrant_client,
    collection_name=settings.qdrant_collection,
    embedding=embeddings
)

retriever = vectorstoreQ.as_retriever(
    search_type="similarity",
    k=3
)

# vectorstoreQ = None
# retriever = None

# Blob
blob_service = BlobServiceClient.from_connection_string(settings.blob_conn)
blob_container = blob_service.get_container_client(settings.blob_container)

# Document Intelligence
doc_client = DocumentAnalysisClient(
    endpoint=settings.docint_endpoint,
    credential=AzureKeyCredential(settings.docint_key),
)

#Memory System Setup
from memory_manager import initialize_memory_clients
redis_client, cosmos_container, memory_manager = initialize_memory_clients(settings)


# SQLAlchemy (optional, kept)
engine = None
if settings.sql_server and settings.sql_db and settings.sql_user:
    connection_string = URL.create(
        "mssql+pyodbc",
        username=settings.sql_user,
        password=settings.sql_password,
        host=settings.sql_server,
        database=settings.sql_db,
        query={"driver": "ODBC Driver 18 for SQL Server", "TrustServerCertificate": "yes"},
    )
    engine = sa.create_engine(connection_string, pool_pre_ping=True)

# =====================
# Import Tools dari modul lain
# =====================
from rag_modul import rag_tool
#from projectProgress_modul import project_tool, client_tool
from projectProgress_modul import (
    project_tool, project_detail_tool, project_list_tool, portfolio_analysis_tool,
)
from others import fetch_template_tool, notify_tool

# =====================
# Agent setup
# =====================
TOOLS = [
    rag_tool, project_tool, fetch_template_tool, notify_tool,
    project_detail_tool, project_list_tool, portfolio_analysis_tool,
    ]
#TOOLS = [rag_tool, project_tool, client_tool, fetch_template_tool, notify_tool]

SYSTEM_PROMPT = (
    "You are the company's Internal Assistant. You can: \n"
    "1) Jawab Q&A internal (qna_internal) – prefer when user asks policy/SOP.\n"
    "2) Cek status proyek (project_progress).\n"
    "3) Cek status client (client_status).\n"
    "4) Ambil template dokumen (fetch_template).\n"
    "5) Kirim notifikasi/pengingat (notify).\n\n"
    "Gunakan alat secara selektif. Jawaban harus ringkas dan berbasis sumber bila memungkinkan."
)

_agent_cache: Dict[str, AgentExecutor] = {}

def get_or_create_agent(user_id: str) -> AgentExecutor:
    if user_id in _agent_cache:
        return _agent_cache[user_id]
    memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
    agent = initialize_agent(
        tools=TOOLS,
        llm=llm,
        agent=AgentType.OPENAI_FUNCTIONS,
        verbose=False,
        memory=memory,
        handle_parsing_errors=True,
    )
    # inject system prompt
    agent.agent.llm_chain.prompt.messages[0] = SystemMessage(content=SYSTEM_PROMPT)
    _agent_cache[user_id] = agent
    return agent
