# imports.py
from __future__ import annotations
import os
import json
import requests
import sqlalchemy as sa
from sqlalchemy.engine import URL
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
# FastAPI & Pydantic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

# LangChain & Azure OpenAI
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
from langchain.memory import ConversationBufferMemory
from langchain.tools import StructuredTool
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage
from langchain.agents import initialize_agent, AgentType, AgentExecutor
from langdetect import detect, DetectorFactory
from qdrant_client import QdrantClient
from langchain_qdrant import QdrantVectorStore

# Vector / Search
from langchain_community.vectorstores.azuresearch import AzureSearch

# Azure SDKs
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import (
    BlobServiceClient,
    generate_blob_sas,
    BlobSasPermissions,
    ContentSettings,
)
from azure.ai.formrecognizer import DocumentAnalysisClient

# Redis and Cosmos DB for Memory
import redis
from azure.cosmos import CosmosClient, PartitionKey, exceptions as cosmos_exceptions

# UI
import gradio as gr
try:
    from gradio.routes import mount_gradio_app  # gradio >= 3.32
except Exception:
    mount_gradio_app = None

# supaya bisa dipakai di file lain tanpa wildcard *
__all__ = [
    # builtins
    "os", "json", "requests", "sa", "URL", "List", "Optional", "Dict", "Any",
    "datetime", "timedelta",
    # fastapi
    "FastAPI", "HTTPException", "BaseModel", "load_dotenv",
    # langchain
    "AzureChatOpenAI", "AzureOpenAIEmbeddings",
    "ConversationBufferMemory", "StructuredTool",
    "ChatPromptTemplate", "SystemMessage",
    "initialize_agent", "AgentType", "AgentExecutor",
    # vector / search
    "AzureSearch",
    # azure sdks
    "SearchClient", "AzureKeyCredential",
    "BlobServiceClient", "generate_blob_sas",
    "BlobSasPermissions", "ContentSettings",
    "DocumentAnalysisClient",
    # memory
    "redis", "CosmosClient", "PartitionKey", "cosmos_exceptions",
    # ui
    "gr", "mount_gradio_app",
    # qdrant
    "QdrantClient","QdrantVectorStore"
]