from depedencies import *
from internal_assistant_core import blob_service, settings

def fetch_template(path: str, expiry_minutes: int = 60) -> str:
    """Generate SAS URL for a blob path like 'templates/contract.docx'."""
    try:
        account_name = blob_service.account_name
        # best effort: works when using account key via connection string
        account_key = getattr(blob_service.credential, "account_key", None)
        if not account_key:
            return (
                "Gagal membuat SAS: tidak ada account key pada credential. "
                "Pertimbangkan User Delegation SAS atau Managed Identity."
            )
        sas = generate_blob_sas(
            account_name=account_name,
            container_name=settings.blob_container,
            blob_name=path,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.utcnow() + timedelta(minutes=expiry_minutes),
        )
        return f"https://{account_name}.blob.core.windows.net/{settings.blob_container}/{path}?{sas}"
    except Exception as e:
        return f"SAS generation failed for '{path}': {e}"

fetch_template_tool = StructuredTool.from_function(
    name="fetch_template",
    description="Buat tautan unduh sementara (SAS) untuk dokumen/template di Blob Storage.",
    func=fetch_template,
)

def send_notification(channel: str, title: str, message: str) -> str:
    if not settings.notify_webhook:
        return "Notification webhook belum dikonfigurasi."
    payload = {"channel": channel, "title": title, "message": message}
    try:
        r = requests.post(settings.notify_webhook, json=payload, timeout=10)
        return "Notification sent." if r.ok else f"Failed: {r.status_code} {r.text}"
    except Exception as e:
        return f"Failed: {e}"

notify_tool = StructuredTool.from_function(
    name="notify",
    description="Kirim notifikasi/pengingat melalui webhook (Teams/Email via Logic Apps).",
    func=send_notification,
)