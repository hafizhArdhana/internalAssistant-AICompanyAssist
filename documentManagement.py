# documentManagement.py - Modul Manajemen Dokumen (Fixed Incremental Indexing)

from azure.storage.blob import BlobServiceClient, ContentSettings
import os
from typing import List, Dict, Any, Optional, Set
import json
import uuid

# --- Impor Klien Qdrant & Model ---
from internal_assistant_core import blob_container, settings, qdrant_client
from qdrant_client.http.models import (
    Filter, 
    FieldCondition, 
    MatchValue,
    PointsSelector, 
    PointIdsList,
    ScrollRequest,
    MatchText
)
from qdrant_client.http import models as qdrant_models

def _detect_mime(path: str) -> str:
    """Detect MIME type from file extension (TETAP SAMA)"""
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

# ==============================================
# FUNGSI UPLOAD & INDEXING - FIXED!
# ==============================================

def upload_file_to_blob(file_path: str, blob_name: str, blob_container) -> Dict[str, Any]:
    """Upload single file to Azure Blob Storage (TETAP SAMA)"""
    try:
        with open(file_path, "rb") as fp:
            data = fp.read()
        
        content_type = _detect_mime(file_path)
        blob_client = blob_container.get_blob_client(blob_name)
        
        blob_client.upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        
        return {
            "success": True,
            "blob_name": blob_name,
            "size": len(data),
            "content_type": content_type,
            "message": f"Successfully uploaded {blob_name}"
        }
    except Exception as e:
        return {
            "success": False,
            "blob_name": blob_name,
            "error": str(e),
            "message": f"Failed to upload {blob_name}: {str(e)}"
        }

def batch_upload_files(files: List, prefix: str, blob_container) -> Dict[str, Any]:
    """Upload multiple files to blob storage (TETAP SAMA)"""
    if not prefix.endswith("/"):
        prefix += "/"
    
    results = {
        "successful_uploads": 0,
        "failed_uploads": 0,
        "total_files" : len(files) if files else 0,
        "uploaded_files": [],
        "failed_files": [],
        "details": [],
    }
    
    if not files:
        results["message"] = "No files provided for upload"
        return results
    
    for f in files:
        try:
            local_path = getattr(f, "name", None) or str(f)
            fname = os.path.basename(local_path)
            blob_name = f"{prefix}{fname}"
            
            upload_result = upload_file_to_blob(local_path, blob_name, blob_container)
            results["details"].append(upload_result)
            
            if upload_result["success"]:
                results["successful_uploads"] += 1
                results["uploaded_files"].append(blob_name)
            else:
                results["failed_uploads"] += 1
                results["failed_files"].append({
                    "file": fname,
                    "error": upload_result["error"]
                })
                
        except Exception as e:
            results["failed_uploads"] += 1
            results["failed_files"].append({
                "file": str(f),
                "error": str(e)
            })
    
    results["message"] = f"Upload completed: {results['successful_uploads']} successful, {results['failed_uploads']} failed"
    return results

def get_indexed_documents_in_qdrant(settings, qdrant_client) -> Set[str]:
    """
    🔧 BARU: Mendapatkan daftar semua dokumen yang sudah diindeks di Qdrant.
    Return set of blob names yang sudah ada di index.
    """
    try:
        print("🔍 Checking existing indexed documents in Qdrant...")
        
        indexed_sources = set()
        
        # Scroll through all points to get unique sources
        results, next_offset = qdrant_client.scroll(
            collection_name=settings.qdrant_collection,
            limit=1000,
            with_payload=True,
            with_vectors=False
        )
        
        for point in results:
            # Check both direct source and metadata.source
            source_direct = point.payload.get('source')
            metadata = point.payload.get('metadata', {})
            source_metadata = metadata.get('source') if isinstance(metadata, dict) else None
            
            if source_direct:
                indexed_sources.add(source_direct)
            if source_metadata:
                indexed_sources.add(source_metadata)
        
        print(f"📊 Found {len(indexed_sources)} unique documents already indexed:")
        for source in sorted(indexed_sources):
            print(f"  - {source}")
        
        return indexed_sources
        
    except Exception as e:
        print(f"❌ Error checking indexed documents: {str(e)}")
        return set()

def process_and_index_documents_incremental(prefix: str = "sop/", blob_container=None, settings=None, specific_files: List[str] = None) -> Dict[str, Any]:
    """
    🔧 DIPERBAIKI: Memproses dan mengindeks dokumen dengan incremental indexing.
    Hanya mengindeks file baru atau file yang specified.
    
    Args:
        prefix: Prefix untuk blob storage
        blob_container: Azure blob container
        settings: Settings object
        specific_files: List blob names spesifik yang ingin diindeks (jika ada)
    """
    try:
        # Import RAG module
        from rag_modul import process_and_index_docs
        
        if specific_files:
            # Mode: Index specific files only
            print(f"🎯 Mode: Indexing specific files: {specific_files}")
            
            # Create temporary function untuk process specific files
            def process_specific_files():
                indexed, skipped, errors = 0, 0, []
                total_chunks = 0
                
                for blob_name in specific_files:
                    try:
                        print(f"Processing specific file: {blob_name}")
                        
                        # Get blob client and content
                        blob_client = blob_container.get_blob_client(blob_name)
                        
                        if not blob_client.exists():
                            print(f"❌ Blob {blob_name} does not exist, skipping...")
                            skipped += 1
                            continue
                        
                        content_bytes = blob_client.download_blob().readall()
                        
                        # Extract dengan struktur yang comprehensive
                        from rag_modul import _extract_text_with_docint, _create_intelligent_chunks, _make_safe_doc_id
                        
                        doc_data = _extract_text_with_docint(content_bytes)
                        
                        if not doc_data.get("sections") and not doc_data.get("raw_tables"):
                            skipped += 1
                            print(f"Skipped {blob_name}: No content extracted")
                            continue

                        # Create chunks
                        chunks = _create_intelligent_chunks(doc_data)
                        
                        if not chunks:
                            skipped += 1
                            print(f"Skipped {blob_name}: No chunks created")
                            continue

                        # Index each chunk
                        from internal_assistant_core import vectorstoreQ
                        
                        for i, chunk_data in enumerate(chunks):
                            unique_string_id = f"{_make_safe_doc_id(blob_name)}_{i}"
                            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_string_id))
                            
                            base_metadata = {
                                "source": blob_name,
                                "chunk_index": i,
                                "content_type": chunk_data["type"],
                                "token_count": chunk_data["tokens"],
                                "total_chunks": len(chunks)
                            }
                            
                            base_metadata.update(chunk_data.get("metadata", {}))
                            
                            try:
                                print(f"Attempting to index chunk {chunk_id} for {blob_name}...")
                                vectorstoreQ.add_texts(
                                    [chunk_data["content"]], 
                                    metadatas=[base_metadata], 
                                    ids=[chunk_id]
                                )
                                print(f"Successfully indexed chunk {chunk_id}.")
                            except Exception as e:
                                print(f"Error indexing chunk {chunk_id}: {e}")
                                continue
                        
                        total_chunks += len(chunks)
                        print(f"✅ Indexed {blob_name}: {len(chunks)} chunks")
                        indexed += 1
                        
                    except Exception as e:
                        error_msg = f"{blob_name}: {str(e)}"
                        errors.append(error_msg)
                        print(f"❌ Error processing {blob_name}: {e}")

                return {
                    "indexed": indexed, 
                    "skipped": skipped, 
                    "errors": errors,
                    "total_chunks": total_chunks,
                    "avg_chunks_per_doc": total_chunks / max(indexed, 1)
                }
            
            index_report = process_specific_files()
            
        else:
            # Mode: Incremental indexing - hanya index file baru
            print(f"🔄 Mode: Incremental indexing for prefix: '{prefix}'")
            
            # 1. Get list of indexed documents in Qdrant
            indexed_documents = get_indexed_documents_in_qdrant(settings, qdrant_client)
            
            # 2. Get list of all documents in blob storage
            if not prefix.endswith("/"):
                prefix += "/"
                
            blob_list = list(blob_container.list_blobs(name_starts_with=prefix))
            blob_names = [b.name for b in blob_list]
            
            print(f"📊 Found {len(blob_names)} documents in blob storage")
            
            # 3. Filter out already indexed documents
            new_documents = []
            for blob_name in blob_names:
                if blob_name not in indexed_documents:
                    new_documents.append(blob_name)
                    print(f"🆕 New document to index: {blob_name}")
                else:
                    print(f"⏭️  Already indexed, skipping: {blob_name}")
            
            if not new_documents:
                print("✅ No new documents to index. All documents are up to date.")
                return {
                    "success": True,
                    "prefix": prefix,
                    "index_report": {
                        "indexed": 0,
                        "skipped": len(blob_names),
                        "errors": [],
                        "total_chunks": 0,
                        "message": "No new documents to index"
                    },
                    "message": f"No new documents to index in {prefix}. All {len(blob_names)} documents are already indexed."
                }
            
            # 4. Index only new documents
            print(f"🔄 Indexing {len(new_documents)} new documents...")
            index_report = process_and_index_documents_incremental(
                prefix=prefix, 
                blob_container=blob_container, 
                settings=settings, 
                specific_files=new_documents
            )
            return index_report
        
        return {
            "success": True,
            "prefix": prefix,
            "index_report": index_report,
            "message": f"Successfully processed and indexed documents to Qdrant"
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "prefix": prefix,
            "error": str(e),
            "message": f"Failed to index documents: {str(e)}"
        }

def process_and_index_documents(prefix: str = "sop/", blob_container=None, settings=None) -> Dict[str, Any]:
    """
    🔧 DIPERBAIKI: Wrapper function yang menggunakan incremental indexing.
    """
    return process_and_index_documents_incremental(prefix, blob_container, settings)

def upload_and_index_complete_incremental(files: List, prefix: str, blob_container, settings) -> Dict[str, Any]:
    """
    🔧 DIPERBAIKI: Complete upload and index workflow dengan incremental indexing.
    Hanya mengindeks file yang baru diupload.
    """
    results = {
        "upload_results": None,
        "index_results": None,
        "overall_success": False,
        "message": ""
    }
    
    try:
        # Step 1: Upload files
        upload_results = batch_upload_files(files, prefix, blob_container)
        results["upload_results"] = upload_results
        
        # Step 2: Index ONLY newly uploaded files (incremental)
        if upload_results["successful_uploads"] > 0:
            uploaded_files = upload_results["uploaded_files"]
            print(f"🎯 Indexing only newly uploaded files: {uploaded_files}")
            
            # Index hanya file yang baru diupload
            index_results = process_and_index_documents_incremental(
                prefix=prefix, 
                blob_container=blob_container, 
                settings=settings,
                specific_files=uploaded_files  # Hanya file yang baru diupload
            )
            results["index_results"] = index_results
            
            results["overall_success"] = upload_results["successful_uploads"] > 0 and index_results.get("success", False)
            results["message"] = f"Upload: {upload_results['message']}. Incremental Index: {index_results.get('message', 'Completed')}"
        else:
            results["overall_success"] = False
            results["message"] = f"Upload failed: {upload_results['message']}. Indexing skipped."
            
    except Exception as e:
        results["overall_success"] = False
        results["message"] = f"Complete workflow failed: {str(e)}"
    
    return results

def upload_and_index_complete(files: List, prefix: str, blob_container, settings) -> Dict[str, Any]:
    """
    🔧 DIPERBAIKI: Wrapper yang menggunakan incremental workflow.
    """
    return upload_and_index_complete_incremental(files, prefix, blob_container, settings)

# ==============================================
# FUNGSI LISTING DOKUMEN (TETAP SAMA)
# ==============================================

def list_documents_in_blob(prefix: str = "sop/", blob_container=None) -> List[Dict[str, Any]]:
    """List all documents in Azure Blob Storage (TETAP SAMA)"""
    try:
        if not prefix.endswith("/"):
            prefix += "/"
        
        documents = []
        blob_list = blob_container.list_blobs(name_starts_with=prefix)
        
        for blob in blob_list:
            blob_client = blob_container.get_blob_client(blob.name)
            properties = blob_client.get_blob_properties()
            
            documents.append({
                "name": blob.name,
                "display_name": blob.name.replace(prefix, ""),
                "size": blob.size,
                "content_type": properties.content_settings.content_type if properties.content_settings else "unknown",
                "last_modified": blob.last_modified.isoformat() if blob.last_modified else None,
                "creation_time": properties.creation_time.isoformat() if properties.creation_time else None,
                "blob_url": blob_client.url
            })
        
        return sorted(documents, key=lambda x: x["last_modified"] or "", reverse=True)
        
    except Exception as e:
        print(f"Error listing documents: {str(e)}")
        return []

# ==============================================
# FUNGSI PENGHAPUSAN DOKUMEN (TETAP SAMA)
# ==============================================

def delete_document_from_blob(blob_name: str, blob_container) -> bool:
    """Delete document from Azure Blob Storage (TETAP SAMA)"""
    try:
        blob_client = blob_container.get_blob_client(blob_name)
        
        if not blob_client.exists():
            print(f"Blob {blob_name} does not exist")
            return False
        
        blob_client.delete_blob()
        print(f"Successfully deleted blob: {blob_name}")
        return True
        
    except Exception as e:
        print(f"Error deleting blob {blob_name}: {str(e)}")
        return False

def debug_all_qdrant_sources(settings, qdrant_client) -> List[Dict[str, Any]]:
    """DEBUG: Melihat semua source yang ada di Qdrant untuk debugging"""
    try:
        print(f"\n🐛 DEBUG: Retrieving ALL points from Qdrant collection...")
        
        results, next_offset = qdrant_client.scroll(
            collection_name=settings.qdrant_collection,
            limit=1000,
            with_payload=True,
            with_vectors=False
        )
        
        all_sources = []
        print(f"📊 Found {len(results)} total points in collection:")
        for i, point in enumerate(results):
            source_value = point.payload.get('source', 'NO_SOURCE')
            metadata = point.payload.get('metadata', {})
            metadata_source = metadata.get('source', 'NO_METADATA_SOURCE') if isinstance(metadata, dict) else 'INVALID_METADATA'
            
            all_sources.append({
                'point_id': point.id,
                'source': source_value,
                'metadata_source': metadata_source,
                'full_payload': point.payload
            })
            print(f"  {i+1}. Point ID: {point.id}")
            print(f"     Direct source: '{source_value}'")
            print(f"     Metadata source: '{metadata_source}'")
            
        return all_sources
        
    except Exception as e:
        print(f"❌ Error debugging Qdrant sources: {str(e)}")
        return []

def search_documents_in_qdrant(blob_name: str, settings, qdrant_client) -> List[str]:
    """Search for documents in Qdrant by blob name"""
    try:
        print(f"🔍 Searching Qdrant for documents with source: '{blob_name}'")
        
        strategies = [
            ("direct_source_exact", Filter(must=[FieldCondition(key="source", match=MatchValue(value=blob_name))])),
            ("metadata_source_exact", Filter(must=[FieldCondition(key="metadata.source", match=MatchValue(value=blob_name))])),
        ]
        
        final_point_ids = []
        
        for strategy_name, qdrant_filter in strategies:
            try:
                results, next_offset = qdrant_client.scroll(
                    collection_name=settings.qdrant_collection,
                    scroll_filter=qdrant_filter,
                    limit=1000,
                    with_payload=True,
                    with_vectors=False
                )
                
                strategy_point_ids = [point.id for point in results]
                
                if strategy_point_ids:
                    print(f"✅ Strategy '{strategy_name}' found {len(strategy_point_ids)} points")
                    final_point_ids = strategy_point_ids
                    break
                    
            except Exception as e:
                print(f"❌ Strategy '{strategy_name}' failed: {str(e)}")
                continue
        
        print(f"✅ Found {len(final_point_ids)} indexed chunks for blob: {blob_name}")
        return final_point_ids
        
    except Exception as e:
        print(f"❌ Error searching Qdrant: {str(e)}")
        return []

def delete_points_from_qdrant(point_ids: List[str], settings, qdrant_client) -> bool:
    """Delete points from Qdrant"""
    if not point_ids:
        return True

    try:
        converted_point_ids = [str(pid) for pid in point_ids]
        
        result = qdrant_client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=converted_point_ids,
            wait=True
        )
        
        print(f"✅ Successfully deleted {len(point_ids)} points from Qdrant.")
        return True
            
    except Exception as e:
        print(f"❌ Error deleting points from Qdrant: {str(e)}")
        return False

def get_qdrant_collection_info(settings, qdrant_client) -> Dict[str, Any]:
    """Get Qdrant collection info"""
    try:
        info = qdrant_client.get_collection(collection_name=settings.qdrant_collection)
        
        return {
            "collection_name": settings.qdrant_collection,
            "status": str(info.status),
            "points_count": info.points_count,
            "vectors_config": dict(info.config.params.vectors),
        }
        
    except Exception as e:
        return {"error": f"Failed to get Qdrant collection info: {str(e)}"}

def delete_document_complete(blob_name: str, blob_container, settings, qdrant_client) -> Dict[str, Any]:
    """Delete document from both Blob Storage and Qdrant"""
    result = {
        "blob_name": blob_name,
        "blob_deleted": False,
        "search_documents_deleted": 0,
        "search_deletion_errors": False,
        "success": False,
        "message": "",
        "debug_info": {}
    }
    
    try:
        print(f"\n🔄 Starting deletion process for: {blob_name}")
        
        # Step 1: Find related points in Qdrant
        point_ids = search_documents_in_qdrant(blob_name, settings, qdrant_client)
        result["debug_info"]["found_point_ids"] = point_ids
        
        # Step 2: Delete from Qdrant
        if point_ids:
            if delete_points_from_qdrant(point_ids, settings, qdrant_client):
                result["search_documents_deleted"] = len(point_ids)
            else:
                result["search_deletion_errors"] = True

        # Step 3: Delete from blob storage
        blob_deleted = delete_document_from_blob(blob_name, blob_container)
        result["blob_deleted"] = blob_deleted
        
        # Step 4: Determine success
        if blob_deleted and not result["search_deletion_errors"]:
            result["success"] = True
            result["message"] = f"✅ Document successfully deleted. Removed {result['search_documents_deleted']} indexed chunks and 1 blob file."
        else:
            result["success"] = False
            result["message"] = f"❌ Failed to completely delete document."
            
    except Exception as e:
        result["success"] = False
        result["message"] = f"❌ Error during document deletion: {str(e)}"
        result["debug_info"]["error"] = str(e)
    
    return result

def batch_delete_documents(blob_names: List[str], blob_container, settings, qdrant_client) -> Dict[str, Any]:
    """Delete multiple documents in batch"""
    results = {
        "total_requested": len(blob_names),
        "successful_deletions": 0,
        "failed_deletions": 0,
        "details": []
    }
    
    for blob_name in blob_names:
        delete_result = delete_document_complete(blob_name, blob_container, settings, qdrant_client)
        results["details"].append(delete_result)
        
        if delete_result["success"]:
            results["successful_deletions"] += 1
        else:
            results["failed_deletions"] += 1
    
    return results

# ==============================================
# FUNGSI MANAJEMEN INDEX (TETAP SAMA)
# ==============================================

def inspect_qdrant_collection_sample(settings, qdrant_client, blob_name: Optional[str] = None) -> Dict[str, Any]:
    """Inspect sample documents in Qdrant collection"""
    try:
        qdrant_filter = None
        if blob_name:
            qdrant_filter = Filter(
                must=[FieldCondition(key="source", match=MatchValue(value=blob_name))]
            )

        results, next_offset = qdrant_client.scroll(
            collection_name=settings.qdrant_collection,
            scroll_filter=qdrant_filter,
            limit=5,
            with_payload=True,
            with_vectors=False
        )
        
        count_result = qdrant_client.count(
            collection_name=settings.qdrant_collection, 
            exact=False
        )
        
        sample_docs = []
        for point in results:
            sample_docs.append({
                "id": point.id,
                "payload": point.payload
            })
        
        return {
            "total_documents_approx": count_result.count,
            "sample_documents": sample_docs,
            "collection_name": settings.qdrant_collection
        }
        
    except Exception as e:
        return {"error": f"Failed to inspect Qdrant collection: {str(e)}"}

def rebuild_qdrant_index(settings, qdrant_client, prefix: str = "sop/") -> Dict[str, Any]:
    """Rebuild entire Qdrant index - DANGEROUS OPERATION"""
    try:
        collection_name = settings.qdrant_collection
        
        # 1. Delete collection
        print(f"WARNING: Deleting collection: {collection_name}...")
        qdrant_client.delete_collection(collection_name=collection_name)
        
        # 2. Recreate collection
        print(f"Re-creating collection: {collection_name}...")
        qdrant_client.create_collection(
            collection_name=collection_name,
            vectors_config=qdrant_models.VectorParams(
                size=3072,
                distance=qdrant_models.Distance.COSINE
            )
        )
        
        # 3. Reindex all documents
        print(f"Starting re-indexing of all documents from prefix: {prefix}...")
        from rag_modul import process_and_index_docs
        index_report = process_and_index_docs(prefix=prefix)
        
        return {
            "success": True,
            "message": f"Successfully rebuilt index {collection_name}.",
            "index_report": index_report
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": f"Failed to rebuild index: {str(e)}"}