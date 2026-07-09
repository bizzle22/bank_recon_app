from pathlib import Path

import pandas as pd


class ReportGenerator:

    SHEETS = {
        "matched_df": "Matched",
        "possible_df": "Review Required",
        "unmatched_df": "Exceptions",
        "bank_charges_df": "Bank Charges",
        "interest_df": "Interest",
        "investment_df": "Investments",
        "security_deposit_df": "Security Deposits",
        "vendor_transactions_df": "Vendors",
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

            for key, sheet_name in cls.SHEETS.items():
                result[key].to_excel(writer, sheet_name=sheet_name, index=False)

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
