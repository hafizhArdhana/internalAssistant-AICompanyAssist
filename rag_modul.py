from depedencies import *
from internal_assistant_core import llm, retriever, vectorstore, blob_container, doc_client, settings

def _local_preprocess(text: str) -> str:
    """Fallback preprocessing (this stands in for the Azure Function in the diagram)."""
    # basic cleanup; customize to your needs
    txt = text.replace("\u00a0", " ")  # non‑breaking space
    txt = " ".join(txt.split())         # collapse whitespace
    return txt.strip()

def _preprocess_via_function(text: str) -> str:
    """Call Azure Function if configured; fallback to local preprocessing."""
    url = settings.func_preprocess_url
    if not url:
        return _local_preprocess(text)

    headers = {"Content-Type": "application/json"}
    if settings.func_preprocess_key:
        headers["x-functions-key"] = settings.func_preprocess_key
    try:
        r = requests.post(url, json={"text": text}, headers=headers, timeout=30)
        if r.ok:
            data = r.json()
            # expect {"clean_text": "..."} or just a string
            return data.get("clean_text", data if isinstance(data, str) else text)
        return _local_preprocess(text)
    except Exception:
        return _local_preprocess(text)
    
def _extract_text_with_docint(binary: bytes) -> str:
    """Use Azure AI Document Intelligence (prebuilt-read)."""
    poller = doc_client.begin_analyze_document("prebuilt-read", binary)
    res = poller.result()
    lines: List[str] = []
    for page in res.pages:
        for line in page.lines:
            lines.append(line.content)
    return "\n".join(lines)

def process_and_index_docs(prefix: str = "sop/") -> Dict[str, Any]:
    """
    Admin pipeline matching the diagram:
    Blob -> (Doc Intelligence) -> (Azure Function preprocess) -> Embedding -> Azure Cognitive Search
    Only indexes textual outcomes; PDF/DOCX/Images are supported via Doc Intelligence.
    """
    indexed, skipped, errors = 0, 0, []
    blob_list = blob_container.list_blobs(name_starts_with=prefix)

    for b in blob_list:
        try:
            # Download content as bytes (works for pdf/docx/images)
            blob_client = blob_container.get_blob_client(b.name)
            content_bytes = blob_client.download_blob().readall()

            # Extract
            extracted_text = _extract_text_with_docint(content_bytes)
            if not extracted_text or not extracted_text.strip():
                skipped += 1
                continue

            # Preprocess
            cleaned_text = _preprocess_via_function(extracted_text)

            # Index
            doc_id = b.name.replace("/", "_").replace(".", "_")
            meta = {"source": b.name}
            vectorstore.add_texts([cleaned_text], metadatas=[meta], ids=[doc_id])
            indexed += 1
        except Exception as e:
            errors.append(f"{b.name}: {e}")

    return {"indexed": indexed, "skipped": skipped, "errors": errors}

def rag_answer(query: str) -> str:
    """Search internal knowledge base (Cognitive Search) and answer grounded in retrieved docs."""
    docs = retriever.get_relevant_documents(query)
    if not docs:
        return "Maaf, tidak ada informasi yang relevan di basis dokumen internal."
    context = "\n\n".join([f"[SOURCE: {d.metadata.get('source','?')}]\n{d.page_content}" for d in docs])

    sys = SystemMessage(content=(
        "You are an internal assistant. Jawab hanya menggunakan konteks yang diberikan. "
        "Jika tidak cukup, katakan bahwa informasinya tidak tersedia dan sarankan dokumen terkait."
    ))
    prompt = ChatPromptTemplate.from_messages([
        sys,
        ("human", "Question: {q}\n\nContext:\n{ctx}")
    ])
    chain = prompt | llm
    resp = chain.invoke({"q": query, "ctx": context})
    return resp.content

rag_tool = StructuredTool.from_function(
    name="qna_internal",
    description=(
        "Q&A atas dokumen internal (SOP, prosedur, kebijakan) via Azure Cognitive Search."
    ),
    func=rag_answer,
)