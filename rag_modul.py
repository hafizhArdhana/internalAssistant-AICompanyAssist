from depedencies import *
from depedencies import detect, DetectorFactory
from internal_assistant_core import llm, retriever, vectorstoreQ, blob_container, doc_client, settings
import base64
import re
import tiktoken
from langchain.text_splitter import RecursiveCharacterTextSplitter
from typing import Dict, List, Any, Optional
import hashlib
import time
import sys
from io import BytesIO
import contextlib
import uuid
from difflib import SequenceMatcher

tokenizer = tiktoken.get_encoding("cl100k_base")
def tiktoken_len(text):
    return len(tokenizer.encode(text))

def _make_safe_doc_id(blob_name: str) -> str:
    return base64.urlsafe_b64encode(blob_name.encode()).decode()

# === Advanced text cleaning dengan preserve struktur ===
def _clean_text(text: str) -> str:
    if not text:
        return ""
    
    # Preserve struktur dokumen yang penting
    txt = text.replace("\u00a0", " ")            # Non-breaking space
    txt = re.sub(r"[•●▪∙◦]", "- ", txt)          # Bullet points dengan spasi
    txt = re.sub(r'[ \t]+', ' ', txt)            # Multiple spaces jadi single space
    txt = re.sub(r'\n{4,}', '\n\n\n', txt)       # Max 3 newlines berturut-turut
    
    # Preserve numbering dan struktur hierarki
    txt = re.sub(r'(\d+)\.(\s*)', r'\1. ', txt)  # Normalize numbering
    txt = re.sub(r'(\d+\.\d+)\.(\s*)', r'\1. ', txt)  # Sub-numbering
    
    return txt.strip()

# ============Table Handler============
# === Table Continuation Detection Functions ===

def _extract_table_headers(table: Any) -> List[str]:
    """Extract header row from table."""
    if not hasattr(table, 'cells') or not table.cells:
        return []
    
    header_cells = sorted(
        [c for c in table.cells if c.row_index == 0],
        key=lambda c: c.column_index
    )
    
    return [_clean_text(c.content) for c in header_cells]


def _calculate_header_similarity(headers1: List[str], headers2: List[str]) -> float:
    """Calculate similarity between two header lists."""
    if len(headers1) != len(headers2):
        return 0.0
    
    if not headers1:
        return 0.0
    
    matches = sum(1 for h1, h2 in zip(headers1, headers2) if h1.lower() == h2.lower())
    return matches / len(headers1)


def _analyze_column_types(table: Any) -> List[str]:
    """Analyze content type of each column."""
    if not hasattr(table, 'cells') or not table.cells:
        return []
    
    col_count = getattr(table, 'column_count', 0)
    if not col_count:
        return []
    
    column_types = []
    
    for col_idx in range(col_count):
        col_cells = [c for c in table.cells if c.column_index == col_idx and c.row_index > 0]
        
        if not col_cells:
            column_types.append('empty')
            continue
        
        has_numbers = sum(1 for c in col_cells if re.search(r'\d', c.content))
        has_text = sum(1 for c in col_cells if re.search(r'[a-zA-Z]', c.content))
        
        total = len(col_cells)
        
        if has_numbers > total * 0.7:
            column_types.append('number')
        elif has_text > total * 0.7:
            column_types.append('text')
        else:
            column_types.append('mixed')
    
    return column_types


def _is_table_continuation(table1: Any, table2: Any, distance: int = 1) -> bool:
    """Detect if table2 is a continuation of table1."""
    
    if distance > 2:
        return False
    
    # Check column count
    if not (hasattr(table1, 'column_count') and hasattr(table2, 'column_count')):
        return False
    
    if table1.column_count != table2.column_count:
        return False
    
    # Check page proximity
    page1 = getattr(table1, 'bounding_regions', [None])[0]
    page2 = getattr(table2, 'bounding_regions', [None])[0]
    
    if page1 and page2:
        page1_num = getattr(page1, 'page_number', None)
        page2_num = getattr(page2, 'page_number', None)
        
        if page1_num and page2_num:
            if page2_num > page1_num + 1:
                return False
    
    # Compare headers
    headers1 = _extract_table_headers(table1)
    headers2 = _extract_table_headers(table2)
    
    if headers1 and headers2:
        if headers1 == headers2:
            print(f"  ✓ Identical headers detected")
            return True
        
        similarity = _calculate_header_similarity(headers1, headers2)
        if similarity > 0.8:
            print(f"  ✓ Similar headers: {similarity:.2f}")
            return True
    
    # Check if first row is data
    if hasattr(table2, 'cells') and table2.cells:
        first_row_cells = [c for c in table2.cells if c.row_index == 0]
        
        if first_row_cells:
            avg_length = sum(len(c.content) for c in first_row_cells) / len(first_row_cells)
            
            if avg_length > 30:
                print(f"  ✓ First row appears to be data")
                return True
            
            has_data_pattern = any(
                bool(re.search(r'\d+[.,]\d+|\d{4}|IDR|Rp|\%', c.content))
                for c in first_row_cells
            )
            
            if has_data_pattern:
                print(f"  ✓ First row contains data patterns")
                return True
    
    # Check column types
    col_types1 = _analyze_column_types(table1)
    col_types2 = _analyze_column_types(table2)
    
    if col_types1 and col_types2 and len(col_types1) == len(col_types2):
        type_match = sum(1 for t1, t2 in zip(col_types1, col_types2) if t1 == t2)
        type_ratio = type_match / len(col_types1)
        
        if type_ratio > 0.7:
            print(f"  ✓ Column types match: {type_ratio:.2f}")
            return True
    
    return False


def _merge_table_list(tables: List[Any]) -> Any:
    """Merge multiple table objects into one."""
    if len(tables) == 1:
        return tables[0]
    
    print(f"  Merging {len(tables)} tables into one")
    
    all_rows = {}
    current_row_offset = 0
    
    for table_idx, table in enumerate(tables):
        if not hasattr(table, 'cells'):
            continue
        
        # Skip header for continuation tables
        start_row = 1 if table_idx > 0 else 0
        
        for cell in table.cells:
            if cell.row_index < start_row:
                continue
            
            adjusted_row = cell.row_index - start_row + current_row_offset
            
            if adjusted_row not in all_rows:
                all_rows[adjusted_row] = {}
            
            all_rows[adjusted_row][cell.column_index] = _clean_text(cell.content)
        
        max_row = max((c.row_index for c in table.cells), default=0)
        current_row_offset += (max_row - start_row + 1)
    
    # Create pseudo-table object
    class MergedTable:
        def __init__(self, rows_dict, column_count, original_table):
            self.rows_dict = rows_dict
            self.column_count = column_count
            self.row_count = len(rows_dict)
            self.bounding_regions = getattr(original_table, 'bounding_regions', [])
            
        @property
        def cells(self):
            cells = []
            for row_idx, row_data in self.rows_dict.items():
                for col_idx, content in row_data.items():
                    cell = type('Cell', (), {
                        'row_index': row_idx,
                        'column_index': col_idx,
                        'content': content
                    })()
                    cells.append(cell)
            return cells
    
    return MergedTable(all_rows, tables[0].column_count, tables[0])


def _merge_multi_page_tables(tables: List[Any]) -> List[Any]:
    """Merge tables that are continuations across pages."""
    if not tables or len(tables) < 2:
        return list(tables)
    
    merged = []
    i = 0
    
    while i < len(tables):
        current_table = tables[i]
        continuation_tables = [current_table]
        j = i + 1
        
        while j < len(tables):
            is_continuation = _is_table_continuation(
                continuation_tables[-1], 
                tables[j],
                j - i
            )
            
            if is_continuation:
                print(f"✓ Table {j} detected as continuation of table {i}")
                continuation_tables.append(tables[j])
                j += 1
            else:
                break
        
        if len(continuation_tables) > 1:
            merged_table = _merge_table_list(continuation_tables)
            merged.append(merged_table)
            print(f"✓ Merged {len(continuation_tables)} tables")
        else:
            merged.append(current_table)
        
        i = j
    
    return merged


# === Ekstraksi teks yang comprehensive dan general ===

def _extract_text_with_docint(binary: bytes) -> Dict[str, List[Dict[str, Any]]]:
    """Extract structured text dengan metadata posisi dan context - GENERAL untuk semua dokumen."""
    try:
        # ✅ Force baca semua halaman
        poller = doc_client.begin_analyze_document(
            "prebuilt-layout",
            document=BytesIO(binary)   # lebih aman untuk file besar
        )
        res = poller.result()
    except Exception as e:
        print(f"Error analyzing document: {e}")
        return {"sections": [], "raw_tables": [], "document_structure": []}

    # ✅ Debug jumlah halaman yang berhasil dibaca
    if hasattr(res, "pages"):
        print(f"✅ Document Intelligence extracted {len(res.pages)} pages")

    processed = {
        "sections": [],  # Semua bagian dengan metadata
        "raw_tables": [],
        "document_structure": []  # Struktur hierarki dokumen
    }

    current_section = None
    section_counter = 0

    # Process paragraphs dengan context dan posisi - GENERAL approach
    if hasattr(res, "paragraphs"):
        for idx, para in enumerate(res.paragraphs):
            role = getattr(para, "role", None)
            text = _clean_text(para.content)
            if not text: #or len(text) < 10:  # Skip very short content
                continue

            content_type = _classify_content_type(text, role)
            
            section_data = {
                "content": text,
                "type": content_type,
                "role": role,
                "position": idx,
                "tokens": tiktoken_len(text)
            }

            # Jika heading, mulai section baru
            if content_type in ["title", "heading", "section_header", "chapter", "subsection"]:
                if current_section:
                    processed["sections"].append(current_section)
                
                current_section = {
                    "header": text,
                    "type": content_type,
                    "content_parts": [section_data],
                    "section_id": section_counter,
                    "total_tokens": tiktoken_len(text)
                }
                section_counter += 1
            else:
                if current_section:
                    current_section["content_parts"].append(section_data)
                    current_section["total_tokens"] += tiktoken_len(text)
                else:
                    current_section = {
                        "header": "Document Content",
                        "type": "content",
                        "content_parts": [section_data],
                        "section_id": section_counter,
                        "total_tokens": tiktoken_len(text)
                    }
                    section_counter += 1

            processed["document_structure"].append(section_data)

        if current_section:
            processed["sections"].append(current_section)

    # Process tables dengan context yang lebih baik
    if hasattr(res, "tables"):
        print(f"📊 Found {len(res.tables)} raw tables, checking for continuations...")
        merged_tables = _merge_multi_page_tables(res.tables)
        print(f"📊 After merging: {len(merged_tables)} tables")

        for table_idx, table in enumerate(merged_tables):
            if not hasattr(table, 'cells') or not table.cells:
                print(f"⚠️  Table {table_idx}: No cells found, skipping")
                continue
            rows = {}
            headers = []
            
            for cell in table.cells:
                content = _clean_text(cell.content)
                if cell.row_index not in rows:
                    rows[cell.row_index] = {}
                rows[cell.row_index][cell.column_index] = content
                
                if cell.row_index == 0:
                    headers.append(content)

            table_rows = []
            for r in sorted(rows.keys()):
                row_data = [rows[r].get(c, "") for c in sorted(rows[r].keys())]
                table_rows.append(" | ".join(row_data))
            
            table_text = "\n".join(table_rows)
            actual_rows = len(rows)

            print(f"✓ Table {table_idx}: Extracted {actual_rows} rows, {len(headers)} columns")
            
            processed["raw_tables"].append({
                "content": table_text,
                "headers": headers,
                "table_id": table_idx,
                "tokens": tiktoken_len(table_text),
                "row_count": len(rows)
            })

    return processed


def _classify_content_type(text: str, role: Optional[str] = None) -> str:
    """FIXED: Klasifikasi jenis konten dengan deteksi core values yang lebih baik."""
    text_upper = text.upper()
    text_lower = text.lower()
    
    # Deteksi berdasarkan role
    if role and "title" in role.lower():
        return "title"
    if role and "heading" in role.lower():
        return "heading"
    
    # FIX: Enhanced core values detection
    if any(keyword in text_upper for keyword in ["CORE VALUES", "NILAI INTI"]):
        return "core_values_header"
    
    # FIX: Detect individual core value items
    core_value_items = ["HUMBLE", "CUSTOMER FOCUSED", "EMPLOYEE SATISFACTION", 
                       "SPEED", "PASSION", "INTEGRITY", "DISCIPLINE"]
    if any(cv in text_upper for cv in core_value_items):
        # Check if it's a header or detailed content
        if len(text.split()) < 10:  # Short text, likely header
            return "core_value_item"
        else:  # Longer text with core value content
            return "core_value_content"
    
    # Pattern umum untuk berbagai bahasa dan jenis dokumen
    # Table of Contents patterns
    if any(keyword in text_upper for keyword in 
           ["DAFTAR ISI", "TABLE OF CONTENTS", "CONTENTS", "INDEX", "INDEKS"]):
        return "table_of_contents"
    
    # Chapter/Section patterns
    if re.match(r'^(BAB|CHAPTER|SECTION|BAGIAN)\s*\d+', text_upper):
        return "chapter"
    
    if re.match(r'^\d+\.', text.strip()):  # Dimulai dengan nomor
        return "section_header"
    
    if re.match(r'^\d+\.\d+', text.strip()):  # Sub section
        return "subsection_header"
    
    # Appendix patterns
    if any(keyword in text_upper for keyword in 
           ["APPENDIX", "LAMPIRAN", "ANNEX", "ATTACHMENT"]):
        return "appendix"
    
    # General important sections
    if any(keyword in text_upper for keyword in 
           ["PURPOSE", "TUJUAN", "VISION", "VISI", "MISSION", "MISI", 
            "OBJECTIVE", "SASARAN", "GOAL", "TARGET", "INTRODUCTION", 
            "PENDAHULUAN", "OVERVIEW", "RINGKASAN", "SUMMARY",
            "CONCLUSION", "KESIMPULAN", "RECOMMENDATION", "REKOMENDASI"]):
        return "purpose_statement"
    
    # Procedure/Process patterns
    if any(keyword in text_upper for keyword in 
           ["PROCEDURE", "PROSEDUR", "PROCESS", "PROSES", "WORKFLOW",
            "LANGKAH", "TAHAP", "STEPS", "CARA"]):
        return "detailed_content"
    
    # Policy/Rule patterns
    if any(keyword in text_upper for keyword in 
           ["POLICY", "KEBIJAKAN", "RULE", "ATURAN", "REGULATION",
            "REGULASI", "GUIDELINE", "PANDUAN"]):
        return "detailed_content"
    
    # Long detailed content
    if len(text.split()) > 100:
        return "detailed_content"
    
    # Table content detection
    if any(char in text for char in ["|", ":", "─", "┌", "└"]) or \
       (text.count("|") > 2 and "\n" in text):
        return "table_content"
    
    # List content
    if text.count("- ") > 2 or text.count("• ") > 2:
        return "content"
    
    return "content"

# === Cost-optimized intelligent chunking strategy ===
def _create_intelligent_chunks(doc_data: Dict[str, List[Dict]]) -> List[Dict[str, Any]]:
    """FIXED: Create chunks dengan special handling untuk core values."""
    chunks = []
    
    # FIX: Detect and group core values content
    core_values_sections = []
    other_sections = []
    
    for section in doc_data.get("sections", []):
        section_type = section.get("type", "")
        section_header = section.get("header", "").lower()
        
        # Check if this section contains core values
        is_core_values = False
        if "core" in section_type or "core" in section_header:
            is_core_values = True
        else:
            # Check content parts for core values keywords
            for part in section.get("content_parts", []):
                content = part.get("content", "").lower()
                if any(cv in content for cv in ["humble", "customer focused", "employee satisfaction",
                                              "speed", "passion", "integrity", "discipline", "core values"]):
                    is_core_values = True
                    break
        
        if is_core_values:
            core_values_sections.append(section)
        else:
            other_sections.append(section)
    
    # FIX: Create comprehensive core values chunk if found
    if core_values_sections:
        core_values_chunk = _create_comprehensive_core_values_chunk(core_values_sections, doc_data)
        if core_values_chunk:
            chunks.append(core_values_chunk)
    
    # Process other sections normally
    for section in other_sections:
        section_chunks = _process_section_intelligently(section)
        chunks.extend(section_chunks)
    
    # Process tables sebagai chunks terpisah dengan optimization
    for table in doc_data.get("raw_tables", []):
        if table["tokens"] > 5000:
            table_chunks = _split_large_table(table)
            chunks.extend(table_chunks)
        else:
            chunks.append({
                "content": f"=== TABLE ===\n{table['content']}",
                "type": "table",
                "metadata": {
                    "table_id": table["table_id"], 
                    "headers": table["headers"],
                    "row_count": table.get("row_count", 0),
                },
                "tokens": table["tokens"]
            })
    
    # Deduplicate untuk avoid redundant storage
    chunks = _deduplicate_chunks(chunks)
    
    return chunks


def _process_section_intelligently(section: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Process section dengan cost optimization - larger chunks untuk reduce storage cost."""
    chunks = []
    section_header = section["header"]
    content_parts = section["content_parts"]
    
    # Target chunk size yang lebih besar untuk cost efficiency (3000-4000 tokens)
    target_chunk_size = 3500
    
    # Jika section kecil atau medium, jadikan satu chunk
    if section["total_tokens"] <= target_chunk_size:
        full_content = f"=== {section_header} ===\n"
        full_content += "\n\n".join([part["content"] for part in content_parts])
        
        chunks.append({
            "content": full_content,
            "type": section["type"],
            "metadata": {
                "section_header": section_header,
                "section_id": section["section_id"],
                "is_complete_section": True
            },
            "tokens": section["total_tokens"]
        })
    else:
        # Section besar, bagi dengan larger chunks untuk cost efficiency
        current_chunk_parts = []
        current_tokens = tiktoken_len(f"=== {section_header} ===\n")
        
        for part in content_parts:
            # Target yang lebih besar untuk reduce number of chunks
            if current_tokens + part["tokens"] > target_chunk_size:
                if current_chunk_parts:
                    # Create chunk
                    chunk_content = f"=== {section_header} ===\n"
                    chunk_content += "\n\n".join([p["content"] for p in current_chunk_parts])
                    
                    chunks.append({
                        "content": chunk_content,
                        "type": section["type"],
                        "metadata": {
                            "section_header": section_header,
                            "section_id": section["section_id"],
                            "is_partial_section": True,
                            "chunk_part": len(chunks) + 1
                        },
                        "tokens": current_tokens
                    })
                
                # Start new chunk
                current_chunk_parts = [part]
                current_tokens = tiktoken_len(f"=== {section_header} ===\n") + part["tokens"]
            else:
                current_chunk_parts.append(part)
                current_tokens += part["tokens"]
        
        # Add final chunk if exists
        if current_chunk_parts:
            chunk_content = f"=== {section_header} ===\n"
            chunk_content += "\n\n".join([p["content"] for p in current_chunk_parts])
            
            chunks.append({
                "content": chunk_content,
                "type": section["type"],
                "metadata": {
                    "section_header": section_header,
                    "section_id": section["section_id"],
                    "is_partial_section": True,
                    "chunk_part": len(chunks) + 1
                },
                "tokens": current_tokens
            })
    
    return chunks

def _create_comprehensive_core_values_chunk(core_values_sections: List[Dict], doc_data: Dict) -> Dict[str, Any]:
    """NEW: Create a comprehensive chunk containing all core values information."""
    if not core_values_sections:
        return None
    
    all_content = []
    total_tokens = 0
    
    # Collect all core values content
    for section in core_values_sections:
        section_header = section.get("header", "")
        if section_header and "core" in section_header.lower():
            all_content.append(f"=== {section_header} ===")
        
        for part in section.get("content_parts", []):
            content = part.get("content", "")
            if content:
                all_content.append(content)
                total_tokens += part.get("tokens", 0)
    
    # Also check for any scattered core values content in other sections
    for section in doc_data.get("sections", []):
        if section not in core_values_sections:
            for part in section.get("content_parts", []):
                content = part.get("content", "")
                content_lower = content.lower()
                
                # If this content mentions multiple core values, include it
                cv_mentions = sum(1 for cv in ["humble", "customer focused", "employee satisfaction",
                                             "speed", "passion", "integrity", "discipline"] 
                                if cv in content_lower)
                
                if cv_mentions >= 2:  # Contains multiple core values
                    all_content.append(content)
                    total_tokens += part.get("tokens", tiktoken_len(content))
    
    if not all_content:
        return None
    
    comprehensive_content = "\n\n".join(all_content)
    
    return {
        "content": comprehensive_content,
        "type": "core_values_comprehensive",
        "metadata": {
            "is_core_values": True,
            "is_comprehensive": True,
            "content_type": "core_values_comprehensive"
        },
        "tokens": total_tokens
    }

def _split_large_table(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Split table besar dengan preserve headers dan target size yang lebih besar."""
    chunks = []
    lines = table["content"].split("\n")
    headers = lines[0] if lines else ""
    
    current_chunk_lines = [headers]  # Always include headers
    current_tokens = tiktoken_len(headers)
    
    # Target size yang lebih besar untuk tables
    target_size = 5000
    
    for line in lines[1:]:  # Skip header line
        line_tokens = tiktoken_len(line)
        if current_tokens + line_tokens > target_size:
            # Create chunk
            chunk_content = f"=== TABLE (Part {len(chunks) + 1}) ===\n"
            chunk_content += "\n".join(current_chunk_lines)
            
            chunks.append({
                "content": chunk_content,
                "type": "table",
                "metadata": {
                    "table_id": table["table_id"],
                    "headers": table["headers"],
                    "is_partial_table": True,
                    "part": len(chunks) + 1,
                    "row_count": table.get("row_count", 0),
                    "total_parts": None
                },
                "tokens": current_tokens
            })
            
            # Start new chunk with headers
            current_chunk_lines = [headers, line]
            current_tokens = tiktoken_len(headers) + line_tokens
        else:
            current_chunk_lines.append(line)
            current_tokens += line_tokens
    
    # Add final chunk
    if len(current_chunk_lines) > 1:  # More than just headers
        chunk_content = f"=== TABLE (Part {len(chunks) + 1}) ===\n"
        chunk_content += "\n".join(current_chunk_lines)
        
        chunks.append({
            "content": chunk_content,
            "type": "table",
            "metadata": {
                "table_id": table["table_id"],
                "headers": table["headers"],
                "is_partial_table": True,
                "part": len(chunks) + 1,
                "row_count": table.get("row_count", 0),
                "total_parts": None
            },
            "tokens": current_tokens
        })
    for chunk in chunks:
        chunk["metadata"]["total_parts"] = len(chunks)
    
    return chunks

def _deduplicate_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicate chunks untuk cost optimization."""
    unique_chunks = []
    seen_hashes = set()
    
    for chunk in chunks:
        # Create content hash untuk deduplication
        content_hash = hashlib.md5(chunk["content"].encode()).hexdigest()
        
        if content_hash not in seen_hashes:
            seen_hashes.add(content_hash)
            unique_chunks.append(chunk)
    
    return unique_chunks

# === Enhanced indexing pipeline - tetap nama function yang sama ===
def process_and_index_docs(prefix: str = "") -> Dict[str, Any]:
    """Process dan index dokumen dengan cost optimization - support semua prefix termasuk kosong."""
    indexed, skipped, errors = 0, 0, []
    total_chunks = 0
    
    # Jika prefix kosong, process semua blobs
    if prefix:
        blob_list = blob_container.list_blobs(name_starts_with=prefix)
    else:
        blob_list = blob_container.list_blobs()

    print(f"Starting to process documents with prefix: '{prefix}'")
    
    for b in blob_list:
        try:
            print(f"Processing: {b.name}")
            blob_client = blob_container.get_blob_client(b.name)
            content_bytes = blob_client.download_blob().readall()

            # Extract dengan struktur yang comprehensive dan general
            doc_data = _extract_text_with_docint(content_bytes)
            
            if not doc_data.get("sections") and not doc_data.get("raw_tables"):
                skipped += 1
                print(f"Skipped {b.name}: No content extracted")
                continue

            # Create cost-optimized chunks
            chunks = _create_intelligent_chunks(doc_data)
            
            if not chunks:
                skipped += 1
                print(f"Skipped {b.name}: No chunks created")
                continue

            # Index each chunk dengan cost-efficient metadata
            for i, chunk_data in enumerate(chunks):
                unique_string_id = f"{_make_safe_doc_id(b.name)}_{i}"
                chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_string_id))
                
                # Optimized metadata - only essential fields
                base_metadata = {
                    "source": b.name,
                    "chunk_index": i,
                    "content_type": chunk_data["type"],
                    "token_count": chunk_data["tokens"],
                    "total_chunks": len(chunks)
                }
                
                # Add specific metadata dari chunk
                base_metadata.update(chunk_data.get("metadata", {}))
                
                try:
                    print(f"Attempting to index chunk {chunk_id} for {b.name}...")
                    vectorstoreQ.add_texts(
                        [chunk_data["content"]], 
                        metadatas=[base_metadata], 
                        ids=[chunk_id]
                    )
                    print(f"Successfully indexed chunk {chunk_id}.")
                except Exception as e:
                    print(f"!!!!!!!!!!!!! FATAL ERROR indexing chunk {chunk_id} !!!!!!!!!!!!!")
                    import traceback
                    traceback.print_exc()  # Ini akan print error lengkapnya
                    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                    continue
            
            total_chunks += len(chunks)
            print(f"Indexed {b.name}: {len(chunks)} chunks")
            indexed += 1
            
            # Add small delay untuk avoid rate limiting
            time.sleep(0.1)

        except Exception as e:
            error_msg = f"{b.name}: {str(e)}"
            errors.append(error_msg)
            print(f"Error processing {b.name}: {e}")

    return {
        "indexed": indexed, 
        "skipped": skipped, 
        "errors": errors,
        "total_chunks": total_chunks,
        "avg_chunks_per_doc": total_chunks / max(indexed, 1)
    }

# === NEW: Function to get unique document count ===
def _get_unique_documents_info(docs: List[Any]) -> Dict[str, Any]:
    """Get information about unique documents from retrieved chunks."""
    unique_sources = set()
    source_chunks = {}
    
    for doc in docs:
        source = doc.metadata.get('source', 'unknown')
        unique_sources.add(source)
        
        if source not in source_chunks:
            source_chunks[source] = []
        source_chunks[source].append(doc)
    
    # Debug info
    print(f"[DEBUG] Raw sources found: {list(unique_sources)}")
    
    return {
        "unique_document_count": len(unique_sources),
        "unique_sources": sorted(list(unique_sources)),  # Sort for consistency
        "source_chunks": source_chunks,
        "total_chunks": len(docs)
    }

# === ENHANCED: Smart Document Query Detection ===
def _is_document_listing_query(query: str) -> bool:
    """
    Enhanced detection for document listing queries using multiple approaches:
    1. Semantic similarity with fuzzy matching
    2. Expanded synonym groups
    3. Pattern-based detection with regex
    4. Context-aware word combinations
    """
    query_lower = query.lower().strip()
    
    # Approach 1: Expanded synonym groups dengan sinonim yang lebih luas
    intent_groups = {
        'quantity_words': {
            'id': ['berapa', 'ada berapa', 'jumlah', 'total', 'banyak', 'sejumlah', 
                   'sekitar', 'kurang lebih', 'kira-kira', 'berapa banyak', 'berapa jumlah'],
            'en': ['how many', 'number of', 'count', 'total', 'amount', 
                   'quantity', 'approximately', 'how much']
        },
        'listing_words': {
            'id': ['daftar', 'list', 'apa saja', 'apa aja', 'yang mana', 'mana saja',
                   'sebutkan', 'tunjukkan', 'tampilkan', 'lihat', 'cek', 'show me',
                   'kasih tau', 'kasih tahu', 'informasikan'],
            'en': ['list', 'what', 'which', 'show', 'display', 'check', 'see',
                   'tell me', 'give me', 'provide']
        },
        'document_words': {
            'id': ['dokumen', 'file', 'berkas', 'arsip', 'data', 'laporan', 
                   'catatan', 'rekaman', 'informasi', 'referensi'],
            'en': ['document', 'file', 'record', 'archive', 'data', 'report', 
                   'information', 'pdf', 'doc', 'reference']
        },
        'availability_words': {
            'id': ['tersedia', 'ada', 'punya', 'miliki', 'simpan', 'tersimpan',
                   'exist', 'ready', 'available', 'yang ada', 'yang tersedia'],
            'en': ['available', 'exist', 'have', 'stored', 'saved', 'present', 
                   'accessible', 'ready']
        }
    }
    
    # Approach 2: Common phrase patterns dengan fuzzy matching
    target_phrases = [
        # Indonesian variations
        "berapa dokumen yang ada", "dokumen apa saja tersedia", "daftar semua dokumen", 
        "jumlah file yang tersimpan", "ada dokumen apa", "tunjukkan dokumen yang ada",
        "berapa banyak berkas", "sebutkan dokumen internal", "dokumen apa saja",
        "ada berapa dokumen", "berapa jumlah dokumen", "dokumen yang tersedia",
        "list dokumen", "cek dokumen apa saja", "kasih tau dokumen yang ada",
        "berapa file tersimpan", "dokumen internal apa saja", "arsip apa yang ada",
        "data apa saja tersedia", "laporan apa yang ada", "berkas apa yang tersimpan",
        
        # English variations
        "how many documents available", "what documents do you have", "list all documents",
        "show me the documents", "count of files stored", "available document list",
        "what files are available", "document inventory", "tell me about documents",
        "give me list of documents", "what documents exist", "show available files",
        "how many files do you have", "what records are stored"
    ]
    
    # Check fuzzy similarity dengan target phrases (threshold disesuaikan)
    for phrase in target_phrases:
        similarity = SequenceMatcher(None, query_lower, phrase).ratio()
        if similarity > 0.80:  # Threshold dikurangi untuk lebih fleksibel
            print(f"[DEBUG] Fuzzy match found: '{query_lower}' vs '{phrase}' (similarity: {similarity:.2f})")
            return True
    
    # Approach 3: Semantic pattern detection
    def has_semantic_match(word_groups: List[set]) -> bool:
        """Check if query contains words from each required group"""
        matches = []
        for group in word_groups:
            group_match = any(word in query_lower for word in group)
            matches.append(group_match)
            if group_match:
                matched_words = [word for word in group if word in query_lower]
                print(f"[DEBUG] Found words from group: {matched_words}")
        return all(matches)
    
    # Combine all synonym variants
    all_quantity = set(intent_groups['quantity_words']['id'] + 
                      intent_groups['quantity_words']['en'])
    all_listing = set(intent_groups['listing_words']['id'] + 
                     intent_groups['listing_words']['en'])
    all_document = set(intent_groups['document_words']['id'] + 
                      intent_groups['document_words']['en'])
    all_availability = set(intent_groups['availability_words']['id'] + 
                          intent_groups['availability_words']['en'])
    
    # Pattern 1: Quantity + Document words
    if has_semantic_match([all_quantity, all_document]):
        print(f"[DEBUG] Semantic match: Quantity + Document")
        return True
    
    # Pattern 2: Listing + Document words  
    if has_semantic_match([all_listing, all_document]):
        print(f"[DEBUG] Semantic match: Listing + Document")
        return True
        
    # Pattern 3: Document + Availability words
    if has_semantic_match([all_document, all_availability]):
        print(f"[DEBUG] Semantic match: Document + Availability")
        return True
    
    # Approach 4: Regular expression patterns untuk struktur kalimat umum
    patterns = [
        # Indonesian patterns - lebih fleksibel
        r'\b(berapa|ada berapa|jumlah)\s+\w*\s*(dokumen|file|berkas|arsip|data)',
        r'\b(dokumen|file|berkas|arsip)\s+\w*\s*(apa saja|yang ada|tersedia|available)',
        r'\b(daftar|list|tunjukkan|sebutkan|kasih tau)\s+\w*\s*(dokumen|file|berkas)',
        r'\b(cek|lihat|show)\s+\w*\s*(dokumen|file|berkas)',
        r'\b(ada)\s+\w*\s*(dokumen|file|berkas)\s+\w*\s*(apa|what)',
        
        # English patterns - lebih fleksibel
        r'\b(how many|number of|count of)\s+\w*\s*(document|file|record)',
        r'\b(what|which)\s+\w*\s*(document|file|record)',
        r'\b(show|list|display|give me)\s+\w*\s*(document|file|record)',
        r'\b(available|existing)\s+\w*\s*(document|file|record)',
        r'\b(document|file|record)\s+\w*\s*(available|exist|stored)'
    ]
    
    for pattern in patterns:
        if re.search(pattern, query_lower, re.IGNORECASE):
            print(f"[DEBUG] Regex pattern match: {pattern}")
            return True
    
    # Approach 5: Check untuk kombinasi kata yang umum tapi tidak tertangkap pattern di atas
    # Khusus untuk variasi kata yang lebih natural/colloquial
    colloquial_patterns = [
        # Indonesian colloquial
        ("dokumen", "apa"), ("file", "apa"), ("berkas", "mana"), 
        ("ada", "dokumen"), ("punya", "dokumen"), ("simpan", "file"),
        ("internal", "dokumen"), ("company", "dokumen"),
        
        # English colloquial  
        ("have", "document"), ("got", "file"), ("stored", "document"),
        ("internal", "document"), ("company", "file")
    ]
    
    for word1, word2 in colloquial_patterns:
        if word1 in query_lower and word2 in query_lower:
            print(f"[DEBUG] Colloquial pattern match: {word1} + {word2}")
            return True
    
    return False

# === Cost-optimized RAG answering dengan document counting fix ===
DetectorFactory.seed = 0
def rag_answer(query: str, user_id: str = "default_user", max_docs: int = 10) -> str:
    """
    Cost-optimized RAG dengan smart retrieval, proper document counting, dan conversation memory.
    
    Args:
        query: User question
        user_id: User identifier for memory management
        max_docs: Maximum documents to retrieve
        
    Returns:
        Answer string with context from both documents and conversation history
    """
    # Import memory manager
    from internal_assistant_core import memory_manager
    
    # === MEMORY: Get conversation context ===
    conversation_context = ""
    if memory_manager:
        try:
            conversation_context = memory_manager.get_conversation_context(user_id, max_tokens=1000)
            if conversation_context:
                print(f"[MEMORY] Retrieved conversation history for user: {user_id}")
        except Exception as e:
            print(f"[MEMORY] Error retrieving history: {e}")
    
    # Check if this is a document listing/counting query FIRST
    is_doc_listing = _is_document_listing_query(query)
    
    # If it's a document listing query, retrieve more docs to ensure we get all unique sources
    if is_doc_listing:
        max_docs = min(max_docs * 2, 20)
        print(f"[DEBUG] Document listing query detected, increasing max_docs to {max_docs}")
    
    # Single-stage optimized retrieval (EXISTING LOGIC - NO CHANGES)
    retrieved_docs = _multi_stage_retrieval(query, max_docs)
    
    if not retrieved_docs:
        answer = "Maaf, tidak ada informasi yang relevan di basis dokumen internal."
        
        # === MEMORY: Save to history even if no docs found ===
        if memory_manager:
            try:
                memory_manager.add_message(user_id, "user", query)
                memory_manager.add_message(user_id, "assistant", answer)
            except Exception as e:
                print(f"[MEMORY] Error saving to history: {e}")
        
        return answer

    # Get unique document information (EXISTING LOGIC)
    doc_info = _get_unique_documents_info(retrieved_docs)
    
    # Build context efficiently (EXISTING LOGIC)
    context = _build_comprehensive_context(retrieved_docs, query, doc_info, is_doc_listing)
    
    # Detect language efficiently (EXISTING LOGIC)
    try:
        lang = detect(query[:100])
    except:
        lang = "id"

    # Build system prompt with document info (EXISTING LOGIC)
    sys_prompt = _build_advanced_system_prompt(lang, query, retrieved_docs, doc_info, is_doc_listing)
    
    # === MEMORY: Add conversation context to system prompt ===
    if conversation_context:
        memory_section = "\n\n=== CONVERSATION HISTORY (For Context) ===\n"
        memory_section += conversation_context
        memory_section += "\n=== END CONVERSATION HISTORY ===\n\n"
        memory_section += "Note: Use this conversation history to understand context and maintain continuity, but prioritize information from the retrieved documents for factual answers."
        
        sys_prompt = sys_prompt + memory_section
    
    # Add debug info for document listing queries (EXISTING LOGIC)
    if is_doc_listing:
        print(f"[DEBUG] Document listing query detected")
        print(f"[DEBUG] Unique documents: {doc_info['unique_document_count']}")
        print(f"[DEBUG] Sources: {doc_info['unique_sources']}")
        print(f"[DEBUG] Total chunks: {doc_info['total_chunks']}")
    
    # Create LLM chain and invoke (EXISTING LOGIC)
    sys = SystemMessage(content=sys_prompt)
    prompt = ChatPromptTemplate.from_messages([
        sys,
        ("human", "Question: {q}\n\nContext:\n{ctx}")
    ])

    chain = prompt | llm
    resp = chain.invoke({"q": query, "ctx": context})
    answer = resp.content
    
    # === MEMORY: Save interaction to history ===
    if memory_manager:
        try:
            # Save user query
            memory_manager.add_message(
                user_id, 
                "user", 
                query
            )
            
            # Save assistant response with metadata
            memory_manager.add_message(
                user_id,
                "assistant",
                answer,
                metadata={
                    "sources": doc_info['unique_sources'],
                    "num_documents": doc_info['unique_document_count'],
                    "num_chunks": doc_info['total_chunks']
                }
            )
            
            print(f"[MEMORY] Saved interaction to history for user: {user_id}")
            
        except Exception as e:
            print(f"[MEMORY] Error saving to history: {e}")
    
    return answer

def _multi_stage_retrieval(query: str, max_docs: int) -> List[Any]:
    """Cost-optimized single retrieval call untuk minimize costs."""
    try:
        # Single retrieval call dengan slightly higher k untuk better coverage
        num_docs_to_fetch = min(max_docs + 2, 15)  # Slight buffer, but capped
        docs = retriever.get_relevant_documents(
            query, 
            k=num_docs_to_fetch  # Slight buffer, but capped
        )
        
        # Simple reranking without additional calls
        return _rerank_documents(docs, query, max_docs)
        
    except Exception as e:
        print(f"Error in retrieval: {e}")
        return []

def _rerank_documents(docs: List[Any], query: str, max_docs: int) -> List[Any]:
    """FIXED: Simple reranking dengan core values awareness."""
    scored_docs = []
    query_lower = query.lower()
    query_words = set(query_lower.split())
    
    # FIX: Detect core values query
    is_core_values_query = any(cv in query_lower for cv in ["core value", "nilai inti", "humble", 
                                                           "customer focused", "employee satisfaction",
                                                           "speed", "passion", "integrity", "discipline"])
    
    for doc in docs:
        score = 0
        content = doc.page_content.lower()
        metadata = doc.metadata
        
        # Simple relevance scoring
        for word in query_words:
            if word in content:
                score += content.count(word) * 10
        
        # FIX: Core values specific scoring
        if is_core_values_query:
            content_type = metadata.get("content_type", "")
            
            # High priority for comprehensive core values content
            if "core_values" in content_type or metadata.get("is_core_values"):
                score += 500
            
            if metadata.get("is_comprehensive"):
                score += 300
            
            # Count core values mentioned in content
            core_values_count = sum(1 for cv in ["humble", "customer focused", "employee satisfaction",
                                                "speed", "passion", "integrity", "discipline"] 
                                  if cv in content)
            score += core_values_count * 100
            
            # Boost for core values keywords in content
            if "core values" in content or "nilai inti" in content:
                score += 200
        
        # Boost for complete sections
        if metadata.get("is_complete_section", False):
            score += 50
        
        # Content type bonuses
        content_type = metadata.get("content_type", "")
        if "table_of_contents" in content_type and any(toc_word in query_lower for toc_word in ["daftar", "isi", "contents"]):
            score += 100
        
        if "table" in content_type and any(table_word in query_lower for table_word in ["tabel", "table", "data"]):
            score += 30
        
        # Length bonus untuk comprehensive content
        if len(doc.page_content) > 500:
            score += 20
        
        scored_docs.append((doc, score))
    
    # Sort and return top docs
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    return [doc for doc, score in scored_docs[:max_docs]]

def _build_comprehensive_context(docs: List[Any], query: str, doc_info: Dict[str, Any], is_doc_listing: bool) -> str:
    """Build context yang efficient dengan document counting information."""
    context_parts = []
    
    # If this is a document listing query, add comprehensive document summary
    if is_doc_listing:
        doc_summary = f"=== INFORMASI DOKUMEN LENGKAP ===\n"
        doc_summary += f"Jumlah dokumen unik yang tersedia: {doc_info['unique_document_count']}\n\n"
        doc_summary += f"Daftar semua dokumen:\n"
        
        for i, source in enumerate(doc_info['unique_sources'], 1):
            # Extract filename without path
            filename = source.split('/')[-1] if '/' in source else source
            filename = filename.replace('.pdf', '')  # Remove extension for cleaner display
            
            doc_summary += f"{i}. {filename}\n"
        
        doc_summary += f"\n(Total chunks dalam sistem: {doc_info['total_chunks']})\n"
        doc_summary += f"=== AKHIR INFORMASI DOKUMEN ===\n\n"
        context_parts.append(doc_summary)
    
    # Add document contents for context
    for i, doc in enumerate(docs):
        metadata = doc.metadata
        source = metadata.get('source', 'unknown')
        content_type = metadata.get('content_type', 'content')
        section_header = metadata.get('section_header', '')
        
        # Add metadata info untuk context
        meta_info = f"[SUMBER: {source} | TIPE: {content_type}"
        if section_header:
            meta_info += f" | BAGIAN: {section_header}"
        meta_info += "]"
        
        context_parts.append(f"{meta_info}\n{doc.page_content}")
    
    return "\n\n".join(context_parts)

def _build_advanced_system_prompt(lang: str, query: str, docs: List[Any], doc_info: Dict[str, Any], is_doc_listing: bool) -> str:
    """FIXED: Build system prompt dengan core values awareness."""
    
    # FIX: Detect core values query and content
    is_core_values_query = any(cv in query.lower() for cv in ["core value", "nilai inti", "7 core", 
                                                             "humble", "customer focused", "employee satisfaction"])
    
    has_core_values_content = any(
        "core_values" in doc.metadata.get("content_type", "") or 
        doc.metadata.get("is_core_values", False) or
        any(cv in doc.page_content.lower() for cv in ["humble", "customer focused", "employee satisfaction"])
        for doc in docs
    )
    
    # Analyze available content types
    content_types = set()
    has_complete_sections = False
    has_tables = False
    
    for doc in docs:
        content_types.add(doc.metadata.get('content_type', 'content'))
        if doc.metadata.get('is_complete_section'):
            has_complete_sections = True
        if 'table' in doc.metadata.get('content_type', ''):
            has_tables = True
    
    if lang == "id":
        base_prompt = (
            "Anda adalah asisten ahli dokumen internal yang memberikan jawaban AKURAT, JELAS, dan MUDAH DIPAHAMI. "
            "Tugas Anda adalah menjawab pertanyaan berdasarkan konteks yang diberikan dengan ringkas tapi tetap lengkap. "
        )
        
        instructions = [
            "1. KHUSUS UNTUK PERTANYAAN TENTANG JUMLAH ATAU DAFTAR DOKUMEN:",
            f"   - Ada TEPAT {doc_info['unique_document_count']} dokumen unik yang tersedia",
            "   - JANGAN sebutkan kata 'chunks' atau 'bagian' kepada user",
            "   - Berikan nama dokumen dengan format yang bersih (tanpa path/extension)",
            "   - Sertakan penjelasan singkat tentang isi setiap dokumen",
            "",
        ]
        
        # FIX: Add specific instructions for core values
        if is_core_values_query and has_core_values_content:
            instructions.extend([
                "2. KHUSUS UNTUK PERTANYAAN CORE VALUES:",
                "   - Berikan SEMUA 7 core values yang ada dalam konteks, yaitu:",
                "   - 1. HUMBLE",
                "   - 2. CUSTOMER FOCUSED", 
                "   - 3. EMPLOYEE SATISFACTION",
                "   - 4. SPEED",
                "   - 5. PASSION",
                "   - 6. INTEGRITY",
                "   - 7. DISCIPLINE",
                "   - JANGAN tambahkan atau kurangi dari list ini",
                "   - Sertakan nama core value DAN penjelasan lengkapnya",
                "   - Gunakan informasi LENGKAP dari konteks, jangan ringkas",
                "   - Format dengan jelas dan mudah dibaca",
                "   - WAJIB menggunakan semua detail yang tersedia di konteks",
                "",
            ])
        
        instructions.extend([
            "3. Untuk pertanyaan UMUM (seperti sapaan), jawab dengan:",
            "   'Halo! Senang bisa membantu Anda. 😊\\n"
            "   Ingat, One Team One Solution!\\n"
            "   Saya adalah asisten internal perusahaan yang siap mendukung kebutuhan Anda terkait dokumen dan informasi internal.\\n"
            "   Bagaimana saya bisa membantu Anda lebih lanjut?'",
            "",
            "4. Berikan jawaban yang KOMPREHENSIF berdasarkan SEMUA informasi relevan dalam konteks",
            "5. Jika ada struktur hierarki (daftar, bab, sub-bab), tampilkan dengan format yang jelas",
            "6. Gunakan SEMUA detail yang tersedia - jangan ringkas atau potong informasi",
            "7. Jika ada tabel, tampilkan dengan format yang mudah dibaca",
            "8. JANGAN PERNAH menyuruh user membaca dokumen asli atau mereferensikan ke sumber lain",
            "9. Jika informasi tersebar di beberapa bagian, gabungkan menjadi jawaban yang koheren",
            "10. Berikan jawaban dalam bahasa Indonesia yang natural dan profesional",
            "11. Jika pertanyaan terkait kebijakan, prosedur, atau aturan, fokus pada bagian tersebut",
            "12. Jika pertanyaan spesifik, fokus hanya pada informasi yang relevan tanpa bertele-tele"
        ])
        
        if "daftar isi" in query.lower() or "contents" in query.lower():
            instructions.append("13. Untuk daftar isi: tampilkan SEMUA item dengan hierarki yang lengkap dan jelas")
        
        if has_tables:
            instructions.append("13. Format tabel dengan rapi menggunakan struktur yang mudah dibaca")
            instructions.append(
                "14. Untuk tabel: Sebutkan jumlah rows jika metadata row_count tersedia. "
                "Jika tabel di-split menjadi beberapa bagian (is_partial_table=True), "
                "beri tahu user bahwa ini bagian dari tabel yang lebih besar."
            )

        full_prompt = base_prompt + "\n\nINSTRUKSI:\n" + "\n".join(instructions)
        
    else:
        base_prompt = (
            "You are an expert internal document assistant that provides ACCURATE, CLEAR, and EASY-TO-UNDERSTAND answers. "
            "Your task is to answer questions based on the given context in a concise but complete way. "
        )
        
        instructions = [
            "1. SPECIFICALLY FOR DOCUMENT COUNT/LISTING QUESTIONS:",
            f"   - There are EXACTLY {doc_info['unique_document_count']} unique documents available",
            "   - DO NOT mention 'chunks' or 'parts' to the user",
            "   - Provide document names in clean format (without path/extension)",
            "   - Include brief explanation of each document's contents",
            "",
        ]
        
        # FIX: Add specific instructions for core values
        if is_core_values_query and has_core_values_content:
            instructions.extend([
                "2. SPECIFICALLY FOR CORE VALUES QUESTIONS:",
                "   - Provide ALL 7 core values found in context",
                "   - Include each core value name AND complete explanation",
                "   - Use COMPLETE information from context, don't summarize",
                "   - Format clearly and readably",
                "   - MUST use all available details from context",
                "",
            ])
        
        instructions.extend([
            "3. For GENERAL questions (like greetings), respond with:",
            "   'Hello! Glad to assist you. 😊\\n"
            "   Remember, One Team One Solution!\\n"
            "   I am your internal company assistant, here to support your needs regarding documents and internal information.\\n"
            "   How can I help you further?'",
            "",
            "4. Provide COMPREHENSIVE answers based on ALL relevant information in the context",
            "5. If there are hierarchical structures (lists, chapters, sub-chapters), display them clearly",
            "6. Use ALL available details - don't summarize or cut information",
            "7. If there are tables, display them in readable format",
            "8. NEVER direct users to read original documents or reference other sources",
            "9. If information is spread across sections, combine into coherent answer",
            "10. Provide answers in natural and professional language",
            "11. If the question relates to policies, procedures, or rules, focus on those sections",
            "12. If the question is specific, focus ONLY on relevant information without unnecessary explanations"
        ])
        
        if "table of contents" in query.lower() or "contents" in query.lower():
            instructions.append("13. For table of contents: display ALL items with complete and clear hierarchy")
        
        if has_tables:
            instructions.append("13. Format tables neatly using readable structure")
            instructions.append(
                "14. For tables: State the number of rows if the metadata row_count is available. "
                "If the table is split into multiple parts (is_partial_table=True), inform the user that this is part of a larger table."
            )
        
        full_prompt = base_prompt + "\n\nINSTRUCTIONS:\n" + "\n".join(instructions)

    return full_prompt

# Enhanced tool definition - tetap nama yang sama
rag_tool = StructuredTool.from_function(
    name="qna_internal",
    description=(
        "Cost-optimized comprehensive Q&A system for ALL internal documents "
        "via Azure AI Search with conversation memory. Handles any document type (SOPs, procedures, policies, "
        "handbooks, reports, etc.) with efficient retrieval to minimize Azure costs "
        "while maintaining high accuracy and completeness. Includes conversation history "
        "for context-aware responses. Now includes enhanced document "
        "counting based on unique sources with smart query detection using fuzzy matching, "
        "semantic similarity, and expanded synonym recognition."
    ),
    func=lambda query, user_id="default_user": rag_answer(query, user_id),
)

# Debug functions untuk troubleshoot document detection issue

def debug_document_availability():
    """Debug function untuk check apakah dokumen sudah diindex."""
    print("🔍 DEBUGGING DOCUMENT AVAILABILITY")
    print("=" * 50)
    
    # Check direct retrieval dengan berbagai query
    test_queries = [
        "berapa dokumen yang ada",
        "document count",
        "sop",
        "employee handbook", 
        "kesiagaan",
        "*"  # wildcard untuk semua
    ]
    
    for query in test_queries:
        print(f"\n📝 Testing query: '{query}'")
        try:
            # Direct retrieval test
            docs = retriever.get_relevant_documents(query, k=20)
            
            if docs:
                print(f"✅ Found {len(docs)} chunks")
                
                # Get unique sources
                unique_sources = set()
                for doc in docs:
                    source = doc.metadata.get('source', 'unknown')
                    unique_sources.add(source)
                
                print(f"📁 Unique documents: {len(unique_sources)}")
                for i, source in enumerate(sorted(unique_sources), 1):
                    print(f"   {i}. {source}")
                    
                # Show first few chunks
                print(f"\n📄 Sample chunks:")
                for i, doc in enumerate(docs[:3]):
                    content_preview = doc.page_content[:100].replace('\n', ' ')
                    print(f"   Chunk {i+1}: {content_preview}...")
                    print(f"   Source: {doc.metadata.get('source', 'unknown')}")
                    print()
                    
            else:
                print("❌ No documents found!")
                
        except Exception as e:
            print(f"❌ Error: {e}")
            
    print("\n" + "=" * 50)

def debug_indexing_status(prefix: str = "sop/"):
    """Debug apakah dokumen dengan prefix tertentu sudah diindex."""
    print(f"🔍 DEBUGGING INDEXING STATUS FOR PREFIX: '{prefix}'")
    print("=" * 60)
    
    try:
        # List blobs dengan prefix
        if prefix:
            blob_list = list(blob_container.list_blobs(name_starts_with=prefix))
        else:
            blob_list = list(blob_container.list_blobs())
            
        print(f"📂 Found {len(blob_list)} blobs in storage:")
        for i, blob in enumerate(blob_list, 1):
            print(f"   {i}. {blob.name}")
            
        # Test retrieval untuk setiap dokumen
        print(f"\n🔍 Testing retrieval for each document:")
        for blob in blob_list:
            # Extract clean name untuk query
            clean_name = blob.name.replace(prefix, "").replace(".pdf", "").replace("-", " ").replace("_", " ")
            
            print(f"\n📝 Testing: {blob.name}")
            print(f"   Clean name: {clean_name}")
            
            # Try different variations
            test_variations = [
                clean_name,
                blob.name.split('/')[-1].replace('.pdf', ''),  # filename only
                blob.name.split('/')[-1].replace('.pdf', '').replace('-', ' '),
                "employee" if "employee" in clean_name.lower() else "kesiagaan"
            ]
            
            found_any = False
            for variation in test_variations:
                if not variation.strip():
                    continue
                    
                try:
                    docs = retriever.get_relevant_documents(variation, k=10)
                    relevant_docs = [doc for doc in docs if blob.name in doc.metadata.get('source', '')]
                    
                    if relevant_docs:
                        print(f"   ✅ Found {len(relevant_docs)} chunks with query: '{variation}'")
                        found_any = True
                        break
                except:
                    continue
                    
            if not found_any:
                print(f"   ❌ No chunks found for {blob.name} - LIKELY NOT INDEXED!")
                
    except Exception as e:
        print(f"❌ Error accessing blob storage: {e}")
        
    print("\n" + "=" * 60)

def debug_enhanced_query_detection():
    """Test apakah enhanced detection berfungsi."""
    print("🔍 DEBUGGING ENHANCED QUERY DETECTION")
    print("=" * 50)
    
    test_queries = [
        "berapa dokumen yang ada",
        "dokumen apa saja",  
        "ada berapa file",
        "daftar dokumen sop",
        "how many documents",
        "what files are available"
    ]
    
    for query in test_queries:
        is_detected = _is_document_listing_query(query)
        print(f"Query: '{query}' -> Detected: {is_detected}")
        
    print("\n" + "=" * 50)

def debug_full_rag_pipeline(query: str = "berapa dokumen yang ada"):
    """Debug full RAG pipeline step by step."""
    print(f"🔍 DEBUGGING FULL RAG PIPELINE")
    print(f"Query: '{query}'")
    print("=" * 60)
    
    # Step 1: Query detection
    is_doc_listing = _is_document_listing_query(query)
    print(f"1️⃣ Document listing query detected: {is_doc_listing}")
    
    # Step 2: Retrieval
    max_docs = 20 if is_doc_listing else 10
    print(f"2️⃣ Retrieving documents (max_docs: {max_docs})...")
    
    try:
        retrieved_docs = retriever.get_relevant_documents(query, k=max_docs)
        print(f"   ✅ Retrieved {len(retrieved_docs)} chunks")
        
        if retrieved_docs:
            # Step 3: Unique document analysis
            doc_info = _get_unique_documents_info(retrieved_docs)
            print(f"3️⃣ Document analysis:")
            print(f"   - Unique documents: {doc_info['unique_document_count']}")
            print(f"   - Total chunks: {doc_info['total_chunks']}")
            print(f"   - Sources: {doc_info['unique_sources']}")
            
            # Step 4: Context building
            context = _build_comprehensive_context(retrieved_docs, query, doc_info, is_doc_listing)
            context_preview = context[:500].replace('\n', ' ')
            print(f"4️⃣ Context preview: {context_preview}...")
            
        else:
            print("❌ No documents retrieved!")
            
    except Exception as e:
        print(f"❌ Error in retrieval: {e}")
        import traceback
        traceback.print_exc()
        
    print("\n" + "=" * 60)

def force_reindex_documents(prefix: str = "sop/"):
    """Force reindex dokumen untuk memastikan mereka ada di search index."""
    print(f"🔄 FORCE REINDEXING DOCUMENTS WITH PREFIX: '{prefix}'")
    print("=" * 60)
    
    try:
        result = process_and_index_docs(prefix)
        print(f"📊 Indexing results:")
        print(f"   - Indexed: {result['indexed']}")
        print(f"   - Skipped: {result['skipped']}")  
        print(f"   - Errors: {len(result['errors'])}")
        print(f"   - Total chunks: {result['total_chunks']}")
        
        if result['errors']:
            print(f"\n❌ Errors encountered:")
            for error in result['errors']:
                print(f"   - {error}")
                
        print(f"\n✅ Reindexing completed!")
        
    except Exception as e:
        print(f"❌ Error during reindexing: {e}")
        import traceback
        traceback.print_exc()
        
    print("\n" + "=" * 60)

# === MAIN TROUBLESHOOTING FUNCTION ===
def troubleshoot_document_detection():
    """Main function untuk troubleshoot document detection issue."""
    print("🚨 TROUBLESHOOTING DOCUMENT DETECTION ISSUE")
    print("=" * 70)
    
    # Step 1: Check basic query detection
    debug_enhanced_query_detection()
    
    # Step 2: Check document availability in search index  
    debug_document_availability()
    
    # Step 3: Check indexing status
    debug_indexing_status("sop/")
    
    # Step 4: Test full pipeline
    debug_full_rag_pipeline("berapa dokumen yang ada")
    
    print("\n🔧 RECOMMENDED ACTIONS:")
    print("1. If no chunks found -> Run: force_reindex_documents('sop/')")
    print("2. If chunks found but wrong count -> Check retrieval parameters")
    print("3. If query not detected -> Check query detection logic")
    print("4. If context building fails -> Check metadata structure")
    
    return "Troubleshooting completed - check output above"

# === USAGE EXAMPLES ===
if __name__ == "__main__":
    # Uncomment the functions you want to run:
    
    # Full troubleshooting
    troubleshoot_document_detection()
    
    # Or run individual debug functions:
    # debug_enhanced_query_detection()
    # debug_document_availability()  
    # debug_indexing_status("sop/")
    # debug_full_rag_pipeline("berapa dokumen yang ada")
    # force_reindex_documents("sop/")