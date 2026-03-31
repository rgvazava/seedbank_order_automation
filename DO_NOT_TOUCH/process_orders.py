# DO_NOT_TOUCH/process_orders.py
# Processes GRIN export and routes international orders to curator inboxes.
# Creates:
#   outputs/runs/<timestamp>/
#   outputs/curator_inboxes/
#   outputs/master_outbox/

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

from aphis_pdf import build_aphis_fields, fill_aphis_pdf


SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

OUTPUTS_DIR          = PROJECT_ROOT / "outputs"
REGISTRY_DIR         = OUTPUTS_DIR  / "registry"
PROCESSED_INPUTS_CSV = REGISTRY_DIR / "processed_inputs.csv"


def abs_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def hash_file(path: Path) -> str:
    # used to catch duplicate exports even if filename changes
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_prior_run_for_hash(file_hash: str) -> Optional[Dict[str, str]]:
    if not PROCESSED_INPUTS_CSV.exists():
        return None
    try:
        with open(PROCESSED_INPUTS_CSV, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("input_hash") or "").strip() == file_hash:
                    return {k: (v or "").strip() for k, v in row.items()}
    except Exception as e:
        print(f"WARNING: could not read registry {PROCESSED_INPUTS_CSV}: {e}")
    return None


def append_processed_input_record(*, file_hash, run_id, run_root, original_filename):
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = PROCESSED_INPUTS_CSV.exists()
    headers = ["input_hash", "first_seen", "run_id", "run_root", "original_filename"]
    row = {
        "input_hash":        file_hash,
        "first_seen":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_id":            run_id,
        "run_root":          str(run_root.resolve()),
        "original_filename": original_filename,
    }
    with open(PROCESSED_INPUTS_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            w.writeheader()
        w.writerow(row)


def load_config(config_path: str | None = None) -> Dict:
    defaults: Dict = {
        "input_csv":            "inputs/SQL_GRIN_ORDER.csv",
        "runs_root":            "outputs/runs",
        "curator_inbox_root":   "outputs/curator_inboxes",
        "domestic_names": [
            "us", "united states", "usa",
            "u.s.", "u.s.a.", "united states of america",
        ],
        "curators":              {},
        "curator_name_to_email": {},
        "curator_email_map":     {},
        "internal_recipient":    "Samantha.Baker@usda.gov",
        "templates": {
            "internal": (
                "Hi Samantha,\\n\\n"
                "Please find attached APHIS import/export requirement forms "
                "for the following order/s:\\n\\n"
                "order_GGOO - CCOO\\n\\n"
                "Regards, WRPIS Seed Bank"
            ),
            "external": (
                "Dear International Germplasm Requestor,\\n\\n"
                "Thank you for placing your request for germplasm from the Western Regional "
                "Plant Introduction Station (WRPIS) of the USDA-ARS National Plant Germplasm "
                "System (NPGS). Your request included germplasm of at least one accession "
                "corresponding to our site, but also might contain accessions from other NPGS "
                "sites. The decisions and abilities to fill this request from the WRPIS are "
                "independent of what may happen to germplasm requested from the other NPGS sites, "
                "if any. Furthermore, five separate Curatorial Programs exist at the WRPIS and a "
                "request for accessions across several Curatorial Programs requires some "
                "coordination on our end but also from the requestor. As your germplasm request "
                "is destined for international distribution we need additional documentation. We "
                "will need an Import Permit to send your requested germplasm.\\n\\n"
                "A decision to ship germplasm depends on the receipt of pertinent documentation "
                "provided by the requestor. Ultimately, the decision to ship/distribute germplasm "
                "also is contingent on the ability of the WRPIS to meet any specific requirements "
                "imposed by the country attempting to introduce germplasm (e.g., import permit, "
                "additional declarations). Please do not hesitate to contact us with questions "
                "and/or comments about your germplasm request.\\n\\n"
                "NPGS Web Order WWOO / GRIN Order GGOO - IP Request"
            ),
        },
        "aphis": {
            "template_pdf":    "DO_NOT_TOUCH/templates/APHIS_template.pdf",
            "output_filename": "order_{order_id}_APHIS.pdf",
            "dropdown11":      "W6 - GSPI",
            "date_mode":       "today",
            "static_fields":   {},
        },
    }

    if not config_path:
        return defaults

    cfg_file = abs_path(config_path)
    if not cfg_file.exists():
        return defaults

    with open(cfg_file, "r", encoding="utf-8") as f:
        user_cfg = json.load(f)

    merged = defaults.copy()
    merged.update(user_cfg)

    # nested dicts need to fully replace defaults, not get patched on top of them
    for key in ("curators", "curator_name_to_email", "curator_email_map", "templates", "aphis"):
        if key in user_cfg:
            merged[key] = user_cfg[key]

    return merged


def curator_folder_name(name_or_email: str) -> str:
    # 'brian.irish@usda.gov' -> 'Brian Irish', plain names pass through
    s = str(name_or_email).strip()
    if "@" not in s:
        return s
    user  = s.split("@")[0]
    parts = [p for p in user.replace("_", ".").split(".") if p]
    return " ".join(p.capitalize() for p in parts) if parts else user


def get_curator_email(cfg, curator_name):
    name = str(curator_name).strip()
    if not name:
        return ""

    for key in ("curator_name_to_email", "curator_email_map"):
        m = cfg.get(key) or {}
        if isinstance(m, dict) and m.get(name):
            return str(m[name]).strip()

    # curators map can also hold crop ID lists, only use it if value is an email
    curators = cfg.get("curators") or {}
    if isinstance(curators, dict):
        val = curators.get(name)
        if isinstance(val, str) and "@" in val:
            return val.strip()

    return ""


def build_internal_message(template: str, order_id: int, country: str) -> str:
    return template.replace("GGOO", str(order_id)).replace("CCOO", country)


def build_external_message(template: str, web_order_id, order_id: int) -> str:
    web_id = str(int(web_order_id)) if pd.notna(web_order_id) else "N/A"
    return template.replace("GGOO", str(order_id)).replace("WWOO", web_id)


def write_order_file(out_path: Path, *, order_id, web_order_id, country, email,
                     species, curators, curator_emails, primary_curator_name,
                     primary_curator_email, internal_recipient, internal_msg, external_msg):
    out_path.parent.mkdir(parents=True, exist_ok=True)

    SEP    = "=" * 70
    SUBSEP = "-" * 70

    int_msg = str(internal_msg).replace("\\n", "\n").strip()
    ext_msg = str(external_msg).replace("\\n", "\n").strip()
    cc_line = ", ".join(sorted(curator_emails)) if curator_emails else "(none)"
    web_id  = str(int(web_order_id)) if pd.notna(web_order_id) else "N/A"

    lines: List[str] = [
        "WRPIS ORDER SUMMARY", SEP, "",
        f"GRIN Order ID:       {order_id}",
        f"Web Order ID:        {web_id}",
        f"Destination/Country: {country}",
        f"Requestor Email:     {email}",
        "",
        "PRIMARY CURATOR (SENDER)", "",
        f"Name:   {primary_curator_name}",
        f"Email:  {primary_curator_email or 'N/A'}",

        "", SUBSEP, "",
        "ALL CURATORS ON THIS ORDER", "",
    ]

    for c in (sorted(curators) if curators else ["(Unassigned)"]):
        lines.append(f"• {c}")

    lines += ["", SUBSEP, "", "TAXA / SPECIES IN ORDER", ""]
    for s in (sorted(set(species)) if species else ["(None listed)"]):
        lines.append(f"• {s}")

    lines += [
        "", SUBSEP, "", "INTERNAL EMAIL (TO PERMITS)", "",
        f"To:      {internal_recipient}",
        f"CC:      {cc_line}", "",
        "Body:", int_msg,
        "", SUBSEP, "", "EXTERNAL EMAIL (TO REQUESTOR)", "",
        f"To:      {email}", "",
        "Body:", ext_msg,
        "", SEP, "",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")


def append_log(log_file: Path, *, order_id, web_order_id, country, email,
               curators, species, internal_msg, external_msg):
    log_file.parent.mkdir(parents=True, exist_ok=True)

    ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep    = "=" * 78
    subsep = "-" * 78
    web_id = str(int(web_order_id)) if pd.notna(web_order_id) else "N/A"

    lines = [
        sep, f"[{ts}] ORDER PROCESSED", sep,
        f"GRIN Order ID:   {order_id}",
        f"Web Order ID:    {web_id}",
        f"Country:         {country}",
        f"Requestor Email: {email}", "", "Curators:",
    ]
    for c in (sorted(curators) if curators else ["(Unassigned)"]):
        lines.append(f"  - {c}")

    lines += ["", "Taxa in Order:"]
    for s in (sorted(set(species)) if species else ["(None listed)"]):
        lines.append(f"  - {s}")

    int_clean = str(internal_msg).replace("\\n", "\n").strip()
    ext_clean = str(external_msg).replace("\\n", "\n").strip()

    lines += [
        "", "Internal Message:", subsep, int_clean,
        "", "External Message:", subsep, ext_clean, "", "",
    ]

    with open(log_file, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_master_packet(out_dir, *, order_id, country, to_email, cc_emails,
                        subject, body, attachment_path, primary_curator_name, primary_curator_email):
    # don't overwrite master packet -- status lives there and people actually edit it
    out_dir.mkdir(parents=True, exist_ok=True)
    packet_path = out_dir / "email_packet.txt"

    now        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    SEP        = "=" * 70
    SUBSEP     = "-" * 70
    cc_line    = "; ".join(sorted(e for e in cc_emails if e)) if cc_emails else "(none)"
    body_clean = str(body).replace("\\n", "\n").replace("\r\n", "\n").strip()
    attachment = str(attachment_path).strip() or "(none)"

    lines = [
        "WRPIS MASTER EMAIL PACKET", SEP, "",
        "STATUS (edit this line after sending)", "",
        "STATUS:       NOT SENT",
        f"LAST UPDATED: {now}",
        "", SUBSEP, "",
        "PRIMARY CURATOR (SENDER)", "",
        f"Name:   {primary_curator_name}",
        f"Email:  {primary_curator_email or 'N/A'}",
        "", SUBSEP, "",
        "EMAIL DETAILS", "",
        f"To:      {to_email}",
        f"CC:      {cc_line}",
        f"Subject: {subject}",
        "", SUBSEP, "",
        "EMAIL BODY", "",
        body_clean or "(no body)",
        "", SUBSEP, "",
        "ATTACHMENT", "", attachment,
        "", SUBSEP, "",
        "ORDER META", "",
        f"Order ID: {order_id}",
        f"Country:  {country}",
        "", SEP, "",
    ]

    packet_path.write_text("\n".join(lines), encoding="utf-8")
    return packet_path


def write_open_master_bat(bat_path: Path, master_packet_path: str) -> None:
    target = str(master_packet_path).replace("/", "\\")
    bat_path.write_text(
        "@echo off\nsetlocal\n"
        f'set "TARGET={target}"\n\n'
        "echo --------------------------------------------\n"
        "echo OPENING MASTER EMAIL PACKET\n"
        "echo %TARGET%\n"
        "echo --------------------------------------------\n\n"
        'if exist "%TARGET%" (\n'
        '  start "" "%TARGET%"\n'
        "  timeout /t 1 >nul\n"
        ") else (\n"
        "  echo.\n  echo ERROR: Master email packet not found.\n  echo.\n  pause\n)\n"
        "endlocal\n",
        encoding="utf-8",
    )


def write_open_folder_bat(bat_path: Path, target_folder: str) -> None:
    target = str(target_folder).replace("/", "\\")
    bat_path.write_text(
        "@echo off\nsetlocal\n"
        f'set "TARGET={target}"\n\n'
        "echo --------------------------------------------\n"
        "echo OPENING ORDER FOLDER\n"
        "echo %TARGET%\n"
        "echo --------------------------------------------\n\n"
        'if exist "%TARGET%" (\n'
        '  start "" "%TARGET%"\n'
        "  timeout /t 1 >nul\n"
        ") else (\n"
        "  echo.\n  echo ERROR: Target folder not found.\n  echo.\n  pause\n)\n"
        "endlocal\n",
        encoding="utf-8",
    )


def generate_aphis_pdf(*, cfg, order_id, country, curator_email, curator_name, taxa_text, order_dir):
    aphis_cfg = cfg.get("aphis")
    if not isinstance(aphis_cfg, dict):
        return None

    template_raw = aphis_cfg.get("template_pdf")
    if not template_raw:
        return None

    template_pdf = abs_path(str(template_raw))
    if not template_pdf.exists():
        print(f"WARNING: APHIS template not found: {template_pdf}")
        return None

    pdf_name = aphis_cfg.get("output_filename", "order_{order_id}_APHIS.pdf").format(order_id=order_id)
    out_pdf  = order_dir / pdf_name

    dropdown11 = aphis_cfg.get("dropdown11", "W6 - GSPI")
    date_mode  = str(aphis_cfg.get("date_mode", "today")).lower()

    if date_mode == "template":
        date_value = None
    elif date_mode == "blank":
        date_value = ""
    else:
        date_value = datetime.now().strftime("%B %d, %Y")

    extra = dict(aphis_cfg.get("static_fields") or {})
    extra["Order No"]    = str(order_id)
    extra["TEXT13_TEST"] = str(taxa_text).strip()  # confirmed field name in this template
    extra["Text13"]      = str(taxa_text).strip()  # fallback in case it differs

    try:
        fields = build_aphis_fields(
            country=country,
            requestor_email=curator_email,
            requestor_name=curator_name,
            dropdown11_value=dropdown11,
            date_value=date_value,
            extra_fields=extra,
        )
        fill_aphis_pdf(template_pdf, out_pdf, fields)
        return out_pdf
    except Exception as e:
        print(f"WARNING: failed to generate APHIS PDF for order {order_id}: {e}")
        return None


def process_orders(config_path: str | None = None) -> None:
    cfg = load_config(config_path)

    input_export = abs_path(cfg["input_csv"])
    run_id       = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    runs_root    = abs_path(cfg.get("runs_root", "outputs/runs"))
    run_root     = runs_root / run_id
    orders_root  = run_root  / "orders"
    inbox_root   = run_root  / "curator_inboxes"
    log_file     = run_root  / "logs" / "log_order.txt"
    curator_root = abs_path(cfg.get("curator_inbox_root", "outputs/curator_inboxes"))
    all_inbox    = curator_root / "0_ALL_INBOX"

    def _ensure_curator_folders():
        curator_root.mkdir(parents=True, exist_ok=True)
        (all_inbox / "orders").mkdir(parents=True, exist_ok=True)

        known: Set[str] = {"Unassigned"}
        for key in ("curator_name_to_email", "curator_email_map", "curators"):
            m = cfg.get(key)
            if isinstance(m, dict):
                known.update(str(n).strip() for n in m.keys() if n)

        for name in sorted(known):
            folder = curator_folder_name(name) if name != "Unassigned" else "Unassigned"
            (curator_root / folder / "orders").mkdir(parents=True, exist_ok=True)

    _ensure_curator_folders()

    if not input_export.exists():
        print(f"ERROR: input export not found: {input_export.resolve()}")
        sys.exit(1)

    file_hash = hash_file(input_export)
    prior     = find_prior_run_for_hash(file_hash)

    if prior is not None:
        print("\nWARNING: this input export looks like it was already processed.")
        print(f"  run_id:   {prior.get('run_id', '')}")
        print(f"  run_root: {prior.get('run_root', '')}")
        print(f"  file:     {prior.get('original_filename', '')}")
        print(f"  hash:     {file_hash}\n")
        if input("continue anyway? (Y/N): ").strip().lower() not in ("y", "yes"):
            print("cancelled.")
            return

    orders_root.mkdir(parents=True, exist_ok=True)
    inbox_root.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    (inbox_root   / "Unassigned").mkdir(parents=True, exist_ok=True)
    (curator_root / "Unassigned").mkdir(parents=True, exist_ok=True)

    print(f"run folder: {run_root.resolve()}")

    suffix = input_export.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        df = pd.read_excel(input_export, sheet_name=0)
        if "order_request_id" not in df.columns:
            df = pd.read_excel(input_export, sheet_name=0, header=1)
    else:
        df = pd.read_csv(input_export)
        if "order_request_id" not in df.columns:
            df = pd.read_csv(input_export, skiprows=1)

    required_cols = [
        "order_request_id", "web_order_request_id", "address",
        "country", "requestor", "organization", "email",
        "taxon", "items", "curator",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        print(f"ERROR: missing columns: {missing}")
        sys.exit(1)

    unique_orders = sorted(df["order_request_id"].dropna().astype(int).unique().tolist())
    print(f"found {len(unique_orders)} orders to evaluate.")

    summary_rows: List[Dict] = []
    curator_rows: Dict[str, List[Dict]] = {}

    for order_id in unique_orders:
        rows = df.loc[df["order_request_id"] == order_id].copy()

        country      = str(rows["country"].iloc[0]).strip()
        email        = str(rows["email"].iloc[0]).strip()
        web_order_id = rows["web_order_request_id"].iloc[0]
        species      = rows["taxon"].dropna().astype(str).unique().tolist()
        curators     = set(rows["curator"].dropna().astype(str).unique().tolist())

        if country.lower() in set(cfg["domestic_names"]):
            continue

        int_msg = build_internal_message(cfg["templates"]["internal"], order_id, country)
        ext_msg = build_external_message(cfg["templates"]["external"], web_order_id, order_id)

        order_dir  = orders_root / f"order_{order_id}"
        order_file = order_dir   / f"order_{order_id}.txt"

        # curator with most items becomes the primary sender
        rows["items"]  = pd.to_numeric(rows["items"], errors="coerce").fillna(0)
        item_totals    = rows.groupby("curator")["items"].sum()
        primary_name   = str(item_totals.idxmax()) if not item_totals.empty else "Unassigned"
        primary_email  = get_curator_email(cfg, primary_name)

        all_emails: Set[str] = {e for c in curators if (e := get_curator_email(cfg, c))}
        cc_emails = all_emails - {primary_email}

        taxa_text = "\n".join(species).strip()

        aphis_pdf = generate_aphis_pdf(
            cfg=cfg,
            order_id=order_id,
            country=country,
            curator_email=primary_email,
            curator_name=primary_name,
            taxa_text=taxa_text,
            order_dir=order_dir,
        )

        write_order_file(
            order_file,
            order_id=order_id,
            web_order_id=web_order_id,
            country=country,
            email=email,
            species=species,
            curators=curators,
            curator_emails=cc_emails,
            primary_curator_name=primary_name,
            primary_curator_email=primary_email,
            internal_recipient=cfg["internal_recipient"],
            internal_msg=int_msg,
            external_msg=ext_msg,
        )

        outbox_root   = abs_path(cfg.get("master_outbox_root", "outputs/master_outbox"))
        outbox_dir    = outbox_root / f"order_{order_id}"
        master_file   = outbox_dir  / "email_packet.txt"
        subject       = f"APHIS paperwork - Order {order_id} - {country}"
        internal_body = str(int_msg).replace("\\n", "\n").strip()
        attachment    = str(aphis_pdf) if aphis_pdf else ""

        if master_file.exists():
            # don't overwrite master packet -- status lives there and people actually edit it
            master_path = master_file
        else:
            master_path = write_master_packet(
                outbox_dir,
                order_id=order_id,
                country=country,
                to_email=cfg["internal_recipient"],
                cc_emails=cc_emails,
                subject=subject,
                body=internal_body,
                attachment_path=attachment,
                primary_curator_name=primary_name,
                primary_curator_email=primary_email,
            )

        run_outbox = run_root / "outbox" / f"order_{order_id}"
        # snapshot just points to master -- avoids duplicate STATUS files across runs
        run_outbox.mkdir(parents=True, exist_ok=True)
        (run_outbox / "MASTER_PACKET_LOCATION.txt").write_text(
            "THIS FOLDER IS A SNAPSHOT ONLY\n"
            "Do NOT update STATUS here.\n\n"
            "MASTER PACKET located here:\n"
            f"{master_path}\n",
            encoding="utf-8",
        )
        write_open_master_bat(run_outbox / "OPEN_MASTER_EMAIL.bat", str(master_path))

        def _copy_into_run_inbox(folder: str) -> None:
            dest = inbox_root / folder / "orders" / f"order_{order_id}"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(order_file, dest / order_file.name)

            pdf_copy = ""
            if aphis_pdf and aphis_pdf.exists():
                shutil.copy2(aphis_pdf, dest / aphis_pdf.name)
                pdf_copy = str(dest / aphis_pdf.name)

            curator_rows.setdefault(folder, []).append({
                "order_request_id":     str(order_id),
                "web_order_request_id": str(int(web_order_id)) if pd.notna(web_order_id) else "N/A",
                "country":              country,
                "requestor_email":      email,
                "species":              "; ".join(species),
                "order_txt_path":       str(dest / order_file.name),
                "aphis_pdf_path":       pdf_copy,
            })

        def _copy_into_persistent_inbox(folder: str) -> None:
            dest = curator_root / folder / "orders" / f"order_{order_id}"
            if dest.exists():
                # already there from a previous run, just refresh the launcher
                write_open_master_bat(dest / "OPEN_MASTER_EMAIL.bat", str(master_path))
                return
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(order_file, dest / order_file.name)
            if aphis_pdf and aphis_pdf.exists():
                shutil.copy2(aphis_pdf, dest / aphis_pdf.name)
            write_open_master_bat(dest / "OPEN_MASTER_EMAIL.bat", str(master_path))

        def _copy_into_all_inbox(primary_folder: str) -> None:
            # no file copies here -- just a launcher pointing at the real folder
            dest = all_inbox / "orders" / f"order_{order_id}"
            dest.mkdir(parents=True, exist_ok=True)
            target = curator_root / primary_folder / "orders" / f"order_{order_id}"
            write_open_folder_bat(dest / "OPEN_ORDER_FOLDER.bat", str(target))
            (dest / "SOURCE_FOLDER.txt").write_text(str(target), encoding="utf-8")

        for cname in (sorted(curators) if curators else ["Unassigned"]):
            folder = curator_folder_name(cname) if cname != "Unassigned" else "Unassigned"
            _copy_into_run_inbox(folder)
            _copy_into_persistent_inbox(folder)

        primary_folder = curator_folder_name(primary_name) if primary_name else "Unassigned"
        _copy_into_all_inbox(primary_folder)

        append_log(
            log_file,
            order_id=order_id,
            web_order_id=web_order_id,
            country=country,
            email=email,
            curators=curators,
            species=species,
            internal_msg=int_msg,
            external_msg=ext_msg,
        )

        print(f"  processed order {order_id} -> {order_file}")

        summary_rows.append({
            "order_request_id":      int(order_id),
            "web_order_request_id":  int(web_order_id) if pd.notna(web_order_id) else None,
            "country":               country,
            "requestor_email":       email,
            "primary_curator_name":  primary_name,
            "primary_curator_email": primary_email,
            "curators":              ";".join(sorted(curators)),
            "species_count":         len(species),
            "order_folder":          str(order_dir),
            "order_file":            str(order_file),
            "aphis_pdf":             str(aphis_pdf) if aphis_pdf else "",
        })

    if summary_rows:
        summary_path = run_root / "run_summary.csv"
        pd.DataFrame(summary_rows).to_csv(summary_path, index=False)
        print(f"saved run summary -> {summary_path}")
    else:
        print("no qualifying orders processed.")

    csv_headers = [
        "order_request_id", "web_order_request_id", "country",
        "requestor_email", "species", "order_txt_path", "aphis_pdf_path",
    ]
    for folder, crows in curator_rows.items():
        croot = inbox_root / folder
        croot.mkdir(parents=True, exist_ok=True)
        with open(croot / "curator_summary.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=csv_headers)
            w.writeheader()
            w.writerows(crows)

    if curator_rows:
        print(f"curator summaries written for: {', '.join(sorted(curator_rows.keys()))}")
    else:
        print("no curator summaries written.")

    try:
        append_processed_input_record(
            file_hash=file_hash,
            run_id=run_id,
            run_root=run_root,
            original_filename=input_export.name,
        )
        print(f"recorded input fingerprint -> {PROCESSED_INPUTS_CSV.resolve()}")
    except Exception as e:
        print(f"WARNING: could not update registry: {e}")

    print("done.")


if __name__ == "__main__":
    process_orders(sys.argv[1] if len(sys.argv) > 1 else None)