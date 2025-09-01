from depedencies import *
from internal_assistant_core import settings
import msal
import requests

# === Auth ke Microsoft Graph ===
def _get_access_token() -> str:
    app = msal.ConfidentialClientApplication(
        settings.MS_CLIENT_ID,
        authority=settings.ms_authority,
        client_credential=settings.MS_CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=[settings.MS_GRAPH_SCOPE])
    if "access_token" not in result:
        raise Exception(f"Graph API Auth gagal: {result.get('error_description')}")
    return result["access_token"]

# === Ambil daftar plan dari sebuah group ===
def get_plans(group_id: str = None):
    if not group_id:
        group_id = settings.MS_GROUP_ID
    token = _get_access_token()
    url = f"https://graph.microsoft.com/v1.0/groups/{group_id}/planner/plans"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json().get("value", [])

# === Ambil semua task dari sebuah plan ===
def get_plan_tasks(plan_id: str):
    token = _get_access_token()
    url = f"https://graph.microsoft.com/v1.0/planner/plans/{plan_id}/tasks"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json().get("value", [])

# === Hitung progress proyek berdasarkan rata-rata percentComplete ===
def get_project_progress(project_name: str) -> str:
    """
    Cari plan berdasarkan project_name (cocok ke title Plan),
    lalu ambil semua task dan hitung progress.
    """
    plans = get_plans()
    selected_plan = None
    for p in plans:
        if project_name.lower() in p.get("title", "").lower():
            selected_plan = p
            break
    if not selected_plan:
        return f"Tidak ada plan dengan nama mirip '{project_name}'."

    plan_id = selected_plan["id"]
    tasks = get_plan_tasks(plan_id)

    if not tasks:
        return f"Plan '{project_name}' tidak punya task."

    # Hitung rata-rata percentComplete
    completed = [t.get("percentComplete", 0) for t in tasks]
    avg_progress = sum(completed) / len(completed)

    # Format output
    details = []
    for t in tasks:
        details.append(
            f"- {t.get('title')} ({t.get('percentComplete',0)}%)"
        )
    return (
        f"📌 Project: {selected_plan.get('title')}\n"
        f"Progress rata-rata: {avg_progress:.1f}%\n"
        f"Total tasks: {len(tasks)}\n"
        f"Daftar task:\n" + "\n".join(details)
    )

# === LangChain Tool ===
project_tool = StructuredTool.from_function(
    name="project_progress",
    description="Ambil status/progress proyek dari Microsoft Planner berdasarkan nama project (Graph API).",
    func=get_project_progress,
)
