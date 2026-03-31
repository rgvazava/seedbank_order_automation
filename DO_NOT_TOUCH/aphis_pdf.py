# DO_NOT_TOUCH/aphis_pdf.py
# Run directly to print field names from the template PDF.
# Useful whenever the template gets swapped out.

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject


def _ensure_need_appearances(writer: PdfWriter) -> None:
    # without this most viewers won't show filled field values
    # /AcroForm can be an IndirectObject so we have to dereference it first
    root     = writer._root_object
    acroform = root.get("/AcroForm")
    if acroform is None:
        return
    try:
        acroform = acroform.get_object()
    except Exception:
        pass
    try:
        acroform.update({NameObject("/NeedAppearances"): BooleanObject(True)})
    except Exception:
        acroform[NameObject("/NeedAppearances")] = BooleanObject(True)


def list_form_fields(pdf_path: Path) -> Dict[str, Any]:
    reader = PdfReader(str(pdf_path))
    fields = reader.get_fields() or {}
    return {k: (v.get("/V") if hasattr(v, "get") else None) for k, v in fields.items()}


def fill_aphis_pdf(template_pdf: Path, output_pdf: Path, field_values: Dict[str, Any]) -> None:
    # clone_document_from_reader carries /AcroForm over with it
    # using add_page() alone drops it and update_page_form_field_values() blows up
    template_pdf = Path(template_pdf)
    output_pdf   = Path(output_pdf)

    reader = PdfReader(str(template_pdf))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)

    for page in writer.pages:
        writer.update_page_form_field_values(page, field_values)

    _ensure_need_appearances(writer)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with open(output_pdf, "wb") as f:
        writer.write(f)


def build_aphis_fields(
    *,
    country:          str,
    requestor_email:  str,
    requestor_name:   Optional[str]            = None,
    dropdown11_value: str                      = "W6 - GSPI",
    date_value:       Optional[str]            = None,
    extra_fields:     Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds the field dict for fill_aphis_pdf().
    All PDF field name mappings live here so process_orders doesn't have to care.
    date_value=None defaults to today, pass "" to force blank.
    """
    if date_value is None:
        date_value = datetime.now().strftime("%B %d, %Y")

    fields: Dict[str, Any] = {
        "Country":    country,
        "Email":      requestor_email,
        "Dropdown11": dropdown11_value,
        "Date":       date_value,
    }

    if requestor_name:
        fields["Requestor"] = requestor_name

    if extra_fields:
        fields.update(extra_fields)

    return fields


if __name__ == "__main__":
    TEMPLATE = Path("DO_NOT_TOUCH/templates/APHIS_template.pdf")
    if not TEMPLATE.exists():
        print(f"template not found at: {TEMPLATE.resolve()}")
    else:
        print("found fields:")
        for name, val in sorted(list_form_fields(TEMPLATE).items()):
            print(f"  {name!r}: {val!r}")