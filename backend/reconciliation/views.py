import os
from uuid import uuid4

import pandas as pd
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.http import FileResponse, Http404
from django.shortcuts import render

from .services.excel_reader import ExcelReader

from .services.matching_engine import MatchingEngine
from .services.report_generator import ReportGenerator


PREVIEW_COLUMNS = [
    "Date",
    "Transaction Date",
    "Value Date",
    "Transaction Description",
    "Linked Invoice",
    "Debit (INR)",
    "Credit (INR)",
    "Balance (INR)",
    "Matched Invoice",
    "Match Score",
    "Match Status",
    "Match Reason",
]


def _save_upload(uploaded_file):
    upload_dir = os.path.join(settings.MEDIA_ROOT, "uploads")
    storage = FileSystemStorage(location=upload_dir)
    filename = storage.save(
        f"{uuid4().hex}_{uploaded_file.name}",
        uploaded_file
    )
    return storage.path(filename)


def _table_payload(df, limit=8):
    columns = [column for column in PREVIEW_COLUMNS if column in df.columns]

    if not columns:
        columns = list(df.columns[:8])

    preview_df = df[columns].head(limit)

    return {
        "columns": columns,
        "rows": [
            {
                column: _display_value(value)
                for column, value in row.items()
            }
            for row in preview_df.to_dict("records")
        ],
        "total": len(df),
    }


def _display_value(value):
    if pd.isna(value):
        return ""

    if isinstance(value, pd.Timestamp):
        return value.strftime("%d-%b-%Y")

    return value


def _build_context(result):
    return {
        **result["summary"],
        "tables": {
            "matched": _table_payload(result["matched_df"]),
            "possible": _table_payload(result["possible_df"]),
            "unmatched": _table_payload(result["unmatched_df"]),
            "bank_charges": _table_payload(result["bank_charges_df"]),
            "interest": _table_payload(result["interest_df"]),
            "investments": _table_payload(result["investment_df"]),
            "security_deposits": _table_payload(result["security_deposit_df"]),
            "vendor_transactions": _table_payload(result["vendor_transactions_df"]),
        },
    }


def home(request):

    if request.method == "POST":

        try:
            bank_file = request.FILES["bank_file"]
            invoice_file = request.FILES["invoice_file"]

            bank_path = _save_upload(bank_file)
            invoice_path = _save_upload(invoice_file)

            bank_df = ExcelReader.read(bank_path)
            invoice_df = ExcelReader.read(invoice_path)

            result = MatchingEngine(
                bank_df,
                invoice_df
            ).run()

            report_name = f"reconciliation_report_{uuid4().hex}.xlsx"
            report_path = os.path.join(
                settings.MEDIA_ROOT,
                "reports",
                report_name
            )
            ReportGenerator.generate(result, report_path)
            request.session["latest_report_path"] = report_path

            context = _build_context(result)
            context["report_ready"] = True

        except KeyError:
            return render(
                request,
                "reconciliation/home.html",
                {
                    "error": "Please upload both the bank statement and invoice register."
                }
            )

        except Exception as exc:
            return render(
                request,
                "reconciliation/home.html",
                {
                    "error": str(exc)
                }
            )


        return render(
            request,
            "reconciliation/dashboard.html",
            context
        )

    return render(
        request,
        "reconciliation/home.html"
    )


def download_latest_report(request):
    report_path = request.session.get("latest_report_path")

    if not report_path or not os.path.exists(report_path):
        raise Http404("No reconciliation report is available for download.")

    return FileResponse(
        open(report_path, "rb"),
        as_attachment=True,
        filename="reconciliation_report.xlsx"
    )
