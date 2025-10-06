"""
Memory Manager Module with Separated Storage by Feature Module
Handles conversation memory using Redis (short-term) and Cosmos DB (long-term)
"""
from depedencies import *
from typing import List, Dict, Any, Optional
import hashlib

class ConversationMemoryManager:
    """
    Manages conversation memory with dual storage and module separation:
    - Redis: Fast in-memory cache for active sessions (TTL-based)
    - Cosmos DB: Persistent long-term storage for history
    - Module Separation: RAG, Project, and To-Do have separate memory spaces
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        cosmos_container,
        session_ttl: int = 3600,  # 1 hour default
        max_history: int = 10  # Max messages to keep in context
    ):
        self.redis_client = redis_client
        self.cosmos_container = cosmos_container
        self.session_ttl = session_ttl
        self.max_history = max_history
    
    def _get_redis_key(self, user_id: str, module: str = "rag") -> str:
        """
        Generate Redis key for user session with module separation
        
        Args:
            user_id: User identifier
            module: Feature module ('rag', 'project', 'todo')
        """
        return f"chat_history:{module}:{user_id}"
    
    def _serialize_message(self, role: str, content: str, metadata: Optional[Dict] = None, module: str = "rag") -> Dict:
        """Serialize message for storage with module tag"""
        return {
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata or {},
            "module": module
        }
    
    def add_message(
        self, 
        user_id: str, 
        role: str, 
        content: str, 
        metadata: Optional[Dict] = None,
        module: str = "rag"
    ):
        """
        Add message to both Redis (cache) and Cosmos DB (persistent) with module separation
        
        Args:
            user_id: User identifier
            role: 'user' or 'assistant'
            content: Message content
            metadata: Optional metadata (sources, tool calls, etc.)
            module: Feature module ('rag', 'project', 'todo')
        """
        message = self._serialize_message(role, content, metadata, module)
        
        # Add to Redis cache with module-specific key
        redis_key = self._get_redis_key(user_id, module)
        try:
            # Get current history for this module
            history_json = self.redis_client.get(redis_key)
            if history_json:
                history = json.loads(history_json)
            else:
                history = []
            
            # Append new message
            history.append(message)
            
            # Keep only last N messages
            if len(history) > self.max_history * 2:  # *2 because user+assistant pairs
                history = history[-(self.max_history * 2):]
            
            # Save back to Redis with TTL
            self.redis_client.setex(
                redis_key,
                self.session_ttl,
                json.dumps(history)
            )
            
        except Exception as e:
            print(f"Redis error adding message to {module}: {e}")
        
        # Add to Cosmos DB for long-term storage with module tag
        try:
            doc_id = f"{module}_{user_id}_{hashlib.md5(message['timestamp'].encode()).hexdigest()[:8]}"
            cosmos_doc = {
                "id": doc_id,
                "user_id": user_id,
                "module": module,  # Tag with module
                "message": message,
                "created_at": message["timestamp"]
            }
            self.cosmos_container.create_item(body=cosmos_doc)
            
        except cosmos_exceptions.CosmosResourceExistsError:
            pass  # Document already exists
        except Exception as e:
            print(f"Cosmos DB error adding message to {module}: {e}")
    
    def get_recent_history(
        self, 
        user_id: str, 
        limit: Optional[int] = None,
        module: str = "rag"
    ) -> List[Dict]:
        """
        Get recent conversation history from Redis cache with module filter
        Falls back to Cosmos DB if Redis cache is empty
        
        Args:
            user_id: User identifier
            limit: Max messages to retrieve (default: max_history)
            module: Feature module ('rag', 'project', 'todo')
            
        Returns:
            List of message dictionaries for specified module
        """
        limit = limit or self.max_history * 2
        
        # Try Redis first (fast) with module-specific key
        redis_key = self._get_redis_key(user_id, module)
        try:
            history_json = self.redis_client.get(redis_key)
            if history_json:
                history = json.loads(history_json)
                return history[-limit:]
        except Exception as e:
            print(f"Redis error getting history for {module}: {e}")
        
        # Fallback to Cosmos DB with module filter
        try:
            query = "SELECT * FROM c WHERE c.user_id = @user_id AND c.module = @module ORDER BY c.created_at DESC"
            items = list(self.cosmos_container.query_items(
                query=query,
                parameters=[
                    {"name": "@user_id", "value": user_id},
                    {"name": "@module", "value": module}
                ],
                partition_key=user_id,
                enable_cross_partition_query=False,
                max_item_count=limit
            ))
            
            # Extract messages and reverse to chronological order
            history = [item["message"] for item in reversed(items)]
            
            # Refresh Redis cache for this module
            if history:
                self.redis_client.setex(
                    redis_key,
                    self.session_ttl,
                    json.dumps(history)
                )
            
            return history
            
        except Exception as e:
            print(f"Cosmos DB error getting history for {module}: {e}")
            return []
    
    def get_conversation_context(
        self, 
        user_id: str, 
        max_tokens: int = 1000,
        module: str = "rag"
    ) -> str:
        """
        Get formatted conversation context for specific module
        
        Args:
            user_id: User identifier
            max_tokens: Approximate max tokens for context
            module: Feature module ('rag', 'project', 'todo')
            
        Returns:
            Formatted conversation history string for module
        """
        history = self.get_recent_history(user_id, module=module)
        
        if not history:
            return ""
        
        # Format messages
        context_parts = []
        for msg in history:
            role = msg["role"].upper()
            content = msg["content"]
            context_parts.append(f"{role}: {content}")
        
        # Join and truncate if needed
        context = "\n".join(context_parts)
        
        # Simple token estimation (rough)
        estimated_tokens = len(context) // 4
        if estimated_tokens > max_tokens:
            # Truncate from beginning (keep recent messages)
            words = context.split()
            target_chars = max_tokens * 4
            context = " ".join(words[-(target_chars // 5):])
        
        return context
    
    def clear_session(self, user_id: str, module: Optional[str] = None):
        """
        Clear Redis cache for user session
        
        Args:
            user_id: User identifier
            module: Specific module to clear, or None to clear all modules
        """
        if module:
            # Clear specific module
            redis_key = self._get_redis_key(user_id, module)
            try:
                self.redis_client.delete(redis_key)
                print(f"Cleared {module} session for {user_id}")
            except Exception as e:
                print(f"Redis error clearing {module} session: {e}")
        else:
            # Clear all modules
            for mod in ["rag", "project", "todo"]:
                redis_key = self._get_redis_key(user_id, mod)
                try:
                    self.redis_client.delete(redis_key)
                except Exception as e:
                    print(f"Redis error clearing {mod} session: {e}")
            print(f"Cleared all sessions for {user_id}")
    
    def get_user_statistics(self, user_id: str, module: Optional[str] = None) -> Dict[str, Any]:
        """
        Get conversation statistics from Cosmos DB
        
        Args:
            user_id: User identifier
            module: Specific module to get stats for, or None for all modules
        """
        try:
            if module:
                # Stats for specific module
                query = "SELECT VALUE COUNT(1) FROM c WHERE c.user_id = @user_id AND c.module = @module"
                items = list(self.cosmos_container.query_items(
                    query=query,
                    parameters=[
                        {"name": "@user_id", "value": user_id},
                        {"name": "@module", "value": module}
                    ],
                    partition_key=user_id,
                    enable_cross_partition_query=False
                ))
                
                total_messages = items[0] if items else 0
                
                return {
                    "user_id": user_id,
                    "module": module,
                    "total_messages": total_messages,
                    "has_active_session": self.redis_client.exists(self._get_redis_key(user_id, module)) > 0
                }
            else:
                # Stats for all modules
                stats = {
                    "user_id": user_id,
                    "modules": {}
                }
                
                for mod in ["rag", "project", "todo"]:
                    query = "SELECT VALUE COUNT(1) FROM c WHERE c.user_id = @user_id AND c.module = @module"
                    items = list(self.cosmos_container.query_items(
                        query=query,
                        parameters=[
                            {"name": "@user_id", "value": user_id},
                            {"name": "@module", "value": mod}
                        ],
                        partition_key=user_id,
                        enable_cross_partition_query=False
                    ))
                    
                    total = items[0] if items else 0
                    
                    stats["modules"][mod] = {
                        "total_messages": total,
                        "has_active_session": self.redis_client.exists(self._get_redis_key(user_id, mod)) > 0
                    }
                
                return stats
                
        except Exception as e:
            print(f"Cosmos DB error getting statistics: {e}")
            return {"error": str(e)}


def initialize_memory_clients(settings):
    """
    Initialize Redis and Cosmos DB clients for memory management
    
    Args:
        settings: Settings object with Redis and Cosmos configuration
        
    Returns:
        Tuple of (redis_client, cosmos_container, memory_manager)
    """
    # Initialize Redis client
    try:
        redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            ssl=settings.redis_ssl,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5
        )
        # Test connection
        redis_client.ping()
        print("✅ Redis connection successful")
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        redis_client = None
    
    # Initialize Cosmos DB client
    try:
        cosmos_client = CosmosClient(
            settings.cosmos_endpoint,
            settings.cosmos_key
        )
        
        # Get or create database
        database = cosmos_client.create_database_if_not_exists(
            id=settings.cosmos_database
        )
        
        # Get or create container
        container = database.create_container_if_not_exists(
            id=settings.cosmos_container,
            partition_key=PartitionKey(path="/user_id"),
            offer_throughput=400  # Minimum RU/s
        )
        
        print("✅ Cosmos DB connection successful")
    except Exception as e:
        print(f"❌ Cosmos DB connection failed: {e}")
        container = None
    
    # Initialize Memory Manager
    if redis_client and container:
        memory_manager = ConversationMemoryManager(
            redis_client=redis_client,
            cosmos_container=container,
            session_ttl=3600,  # 1 hour
            max_history=10
        )
        print("✅ Memory Manager initialized with module separation")
    else:
        print("⚠️ Memory Manager not available - running without memory")
        memory_manager = None
    
    return redis_client, container, memory_manager