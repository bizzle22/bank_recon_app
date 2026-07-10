from pathlib import Path

import pandas as pd


class ReportGenerator:

    SHEET_NAMES = {
        "matched_df": "Matched",
        "possible_df": "Review Required",
        "unmatched_df": "Exceptions",
        "interest_income_df": "Interest Income",
        "bank_charges_df": "Bank Charges",
        "investments_df": "Investments",
        "security_deposits_df": "Security Deposits",
        "advances_received_df": "Advances Received",
        "cheque_bounce_df": "Cheque Bounces",
        "bounce_penalty_df": "Bounce Penalties",
        "payment_reversals_df": "Payment Reversals",
        "unidentified_df": "Unidentified Receipts",
        "vendor_transactions_df": "Vendor & Statutory Payments",
    }

    @classmethod
    def generate(cls, result, output_path):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
            summary_df = pd.DataFrame(
                result["summary"].items(),
                columns=["Metric", "Value"]
            )
            summary_df.to_excel(writer, sheet_name="Summary", index=False)

            for key, sheet_name in cls.SHEET_NAMES.items():
                if key not in result:
                    continue
                df = result[key]
                if df is None:
                    continue
                df.to_excel(writer, sheet_name=sheet_name, index=False)

            workbook = writer.book
            header_format = workbook.add_format({
                "bold": True,
                "bg_color": "#0f766e",
                "font_color": "#ffffff",
                "border": 1,
            })
            money_format = workbook.add_format({"num_format": "#,##0.00"})

            for worksheet in writer.sheets.values():
                worksheet.freeze_panes(1, 0)
                worksheet.set_row(0, None, header_format)
                worksheet.set_column(0, 0, 24)
                worksheet.set_column(1, 20, 18, money_format)

        return output_path
    


    