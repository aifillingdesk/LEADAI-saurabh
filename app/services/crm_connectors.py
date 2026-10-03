"""
LeadAI CRM Connectors & Multi-Format Exporters (Phase 6 Product Feature).

Connectors:
- HubSpot CRM (Contacts API payload transformer & sync)
- Zoho CRM (Leads API payload transformer & sync)
- Google Sheets (Structured tabular row formatter)
- Excel (.xlsx / XML spreadsheet with formula escaping)
"""
import abc
import io
import logging
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell_value(val: Any) -> str:
    """Prevent CSV/Excel Formula Injection (CSV Injection / CWE-1236)."""
    if val is None:
        return ""
    s = str(val)
    if s.startswith(_FORMULA_PREFIXES):
        return "'" + s
    return s


def lead_name(lead: dict[str, Any]) -> str:
    """Leads from URL searches store ``commenter_name``; leads ingested through
    the public API store ``author_name``."""
    return str(lead.get("author_name") or lead.get("commenter_name") or "")


class BaseCRMConnector(abc.ABC):
    """Abstract base class for third-party CRM connectors."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def format_lead(self, lead: dict[str, Any]) -> dict[str, Any]:
        """Convert a LeadAI lead dictionary into the target CRM format."""
        raise NotImplementedError


class HubSpotConnector(BaseCRMConnector):
    name = "hubspot"

    def format_lead(self, lead: dict[str, Any]) -> dict[str, Any]:
        name_parts = (lead_name(lead) or "Lead User").strip().split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        return {
            "properties": {
                "email": lead.get("email") or "",
                "firstname": first_name,
                "lastname": last_name,
                "phone": lead.get("phone") or "",
                "lead_source": f"LeadAI ({lead.get('platform', 'social')})",
                "lead_score": str(lead.get("lead_score", 0)),
                "hs_lead_status": "OPEN",
                "notes": lead.get("comment_text") or lead.get("snippet") or "",
                "intent": lead.get("intent") or "purchase_inquiry",
            }
        }


class ZohoCRMConnector(BaseCRMConnector):
    name = "zoho"

    def format_lead(self, lead: dict[str, Any]) -> dict[str, Any]:
        name_parts = (lead_name(lead) or "Lead User").strip().split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else "Unknown"

        rating = "Hot" if lead.get("priority") == "hot" else "Warm" if lead.get("priority") == "warm" else "Cold"

        return {
            "First_Name": first_name,
            "Last_Name": last_name,
            "Email": lead.get("email") or "",
            "Phone": lead.get("phone") or "",
            "Lead_Source": f"LeadAI ({lead.get('platform', 'social')})",
            "Rating": rating,
            "Description": lead.get("comment_text") or "",
            "Lead_Score": lead.get("lead_score", 0),
        }


class GoogleSheetsFormatter:
    """Formats leads into a 2D matrix suitable for Google Sheets API append."""

    COLUMNS: ClassVar[list[str]] = [
        "Lead ID", "Platform", "Author Name", "Phone", "Email",
        "Intent", "Priority", "Score", "Comment / Text", "Created At"
    ]

    @classmethod
    def format_rows(cls, leads: list[dict[str, Any]]) -> list[list[str]]:
        rows = [cls.COLUMNS]
        for lead in leads:
            row = [
                sanitize_cell_value(str(lead.get("_id") or lead.get("id") or "")),
                sanitize_cell_value(lead.get("platform", "")),
                sanitize_cell_value(lead_name(lead)),
                sanitize_cell_value(lead.get("phone", "")),
                sanitize_cell_value(lead.get("email", "")),
                sanitize_cell_value(lead.get("intent", "")),
                sanitize_cell_value(lead.get("priority", "")),
                sanitize_cell_value(lead.get("lead_score", "")),
                sanitize_cell_value(lead.get("comment_text", "")),
                sanitize_cell_value(str(lead.get("created_at", ""))),
            ]
            rows.append(row)
        return rows


class ExcelExportService:
    """Generates an XML Spreadsheet 2003 (.xml/.xlsx compatible) table with safe cell escaping."""

    @classmethod
    def generate_spreadsheet_xml(cls, leads: list[dict[str, Any]], title: str = "LeadAI Export") -> bytes:
        headers = ["ID", "Platform", "Author", "Phone", "Email", "Intent", "Priority", "Score", "Comment", "Created At"]
        output = io.StringIO()
        output.write('<?xml version="1.0"?>\n')
        output.write('<?mso-application progid="Excel.Sheet"?>\n')
        output.write('<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"\n')
        output.write(' xmlns:o="urn:schemas-microsoft-com:office:office"\n')
        output.write(' xmlns:x="urn:schemas-microsoft-com:office:excel"\n')
        output.write(' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">\n')
        output.write(f' <Worksheet ss:Name="{title}">\n')
        output.write('  <Table>\n')

        # Header row
        output.write('   <Row>\n')
        for h in headers:
            output.write(f'    <Cell><Data ss:Type="String">{h}</Data></Cell>\n')
        output.write('   </Row>\n')

        # Data rows
        for lead in leads:
            row_data = [
                str(lead.get("_id") or lead.get("id") or ""),
                lead.get("platform", ""),
                lead_name(lead),
                lead.get("phone", ""),
                lead.get("email", ""),
                lead.get("intent", ""),
                lead.get("priority", ""),
                str(lead.get("lead_score", 0)),
                (lead.get("comment_text") or "")[:500],
                str(lead.get("created_at") or "")[:19],
            ]
            output.write('   <Row>\n')
            for cell in row_data:
                safe_val = sanitize_cell_value(cell)
                escaped = (
                    safe_val.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace('"', "&quot;")
                )
                output.write(f'    <Cell><Data ss:Type="String">{escaped}</Data></Cell>\n')
            output.write('   </Row>\n')

        output.write('  </Table>\n')
        output.write(' </Worksheet>\n')
        output.write('</Workbook>\n')
        return output.getvalue().encode("utf-8")
