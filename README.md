# Internal Assistant 

This project is an implementation of an **Internal Assistant** using **LangChain**, **FastAPI**, **Azure Services**, and several custom modules to manage features such as RAG (Retrieval-Augmented Generation) and project progress tracking.

---

## 📂 Project Structure

```bash
.
├── internal_assistant_core.py   # Setup client API & LangChain
├── internal_assistant_app.py    # Setup UI
├── dependencies.py              # Library imports & utilities
├── rag_modul/                   # Logic for RAG feature
├── progressProject_modul.py     # Logic for ProgressProject feature
├── to_do_modul_test.py          # Logic for Project Progress feature (testing)
├── .env                         # API keys & environment variables
├── .gitignore                   # Prevent sensitive/unnecessary files from being pushed
├── requirements.txt             # All dependencies required to run the project
