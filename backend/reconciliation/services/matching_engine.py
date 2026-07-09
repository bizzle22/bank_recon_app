import pandas as pd
import re
from rapidfuzz import fuzz

class MatchingEngine:
    TDS_RATE = 0.9153
    ADVANCE_RATE = 0.30
    AMOUNT_TOLERANCE = 1.0
    NON_AUTO_RECEIPT_TYPES = {"ad_hoc", "gateway", "other_receipt"}

    BANK_REQUIRED_COLUMNS = {
        "Transaction Description",
        "Credit (INR)",
        "Debit (INR)",
    }

    INVOICE_REQUIRED_COLUMNS = {
        "Invoice No.",
        "Total Amt (INR)",
    }

    def __init__(self, bank_df, invoice_df):

        self.bank_df = bank_df.copy()

        self.invoice_df = invoice_df.copy()

    def validate(self):
        missing_bank_columns = sorted(
            self.BANK_REQUIRED_COLUMNS - set(self.bank_df.columns)
        )
        missing_invoice_columns = sorted(
            self.INVOICE_REQUIRED_COLUMNS - set(self.invoice_df.columns)
        )

        errors = []

        if missing_bank_columns:
            errors.append(
                "Bank statement is missing columns: "
                + ", ".join(missing_bank_columns)
            )

        if missing_invoice_columns:
            errors.append(
                "Invoice register is missing columns: "
                + ", ".join(missing_invoice_columns)
            )

        if errors:
            raise ValueError(" ".join(errors))

    def run(self):

        self.validate()

        matched_rows = []

        possible_rows = []

        bank_charges = []

        interest_income = []

        investments = []

        security_deposits = []

        vendor_transactions = []

        unmatched = []

        invoices = self._prepare_invoices()
        matched_invoice_numbers = set()

        for _, row in self.bank_df.iterrows():

            description = str(
                row.get(
                    "Transaction Description",
                    ""
                )
            ).upper()

            if "INTEREST" in description:

                interest_income.append(row)

            elif "CHARGE" in description:

                bank_charges.append(row)

            elif "FD" in description:

                investments.append(row)

            elif "SECURITY" in description or "DEPOSIT" in description:

                security_deposits.append(row)

            elif "VENDOR" in description:

                vendor_transactions.append(row)

            else:
                match = self._find_invoice_match(
                    row,
                    invoices,
                    matched_invoice_numbers
                )

                if match and match["status"] == "Auto Matched":
                    matched_invoice_numbers.add(match["invoice_no"])
                    matched_rows.append(
                        self._with_match_details(row, match, match["status"])
                    )

                elif match and match["status"] == "Review Required":
                    possible_rows.append(
                        self._with_match_details(row, match, match["status"])
                    )

                else:
                    unmatched.append(row)

        matched_df = pd.DataFrame(matched_rows, columns=self._result_columns())

        possible_df = pd.DataFrame(possible_rows, columns=self._result_columns())

        bank_charges_df = pd.DataFrame(bank_charges, columns=self.bank_df.columns)

        interest_df = pd.DataFrame(interest_income, columns=self.bank_df.columns)

        investment_df = pd.DataFrame(investments, columns=self.bank_df.columns)

        unmatched_df = pd.DataFrame(unmatched, columns=self.bank_df.columns)

        security_deposit_df = pd.DataFrame(security_deposits, columns=self.bank_df.columns)

        vendor_transactions_df = pd.DataFrame(vendor_transactions, columns=self.bank_df.columns)
       
        total_rows = len(self.bank_df)

        matched_count = len(matched_df)

        customer_receipt_count = (
            len(matched_df) + len(possible_df) + len(unmatched_df)
        )

        if customer_receipt_count > 0:
            match_rate = round(matched_count / customer_receipt_count * 100, 2
            )
        else:
            match_rate = 0
    

        matched_amount = self._sum_amount(matched_df, "Credit (INR)")

        interest_amount = self._sum_amount(interest_df, "Credit (INR)")

        bank_charge_amount = self._sum_amount(bank_charges_df, "Debit (INR)")

        investment_amount = self._sum_amount(investment_df, "Debit (INR)")

        unmatched_amount = self._sum_amount(unmatched_df, "Credit (INR)")

        possible_amount = self._sum_amount(possible_df, "Credit (INR)")

        security_deposit_amount = self._sum_amount(security_deposit_df, "Credit (INR)")
        
        vendor_amount = self._sum_amount(vendor_transactions_df, "Debit (INR)")

        return {

            "matched_df": matched_df,

            "possible_df": possible_df,

            "bank_charges_df": bank_charges_df,

            "interest_df": interest_df,

            "investment_df": investment_df,

            "unmatched_df": unmatched_df,

            "security_deposit_df": security_deposit_df,
            
            "vendor_transactions_df": vendor_transactions_df,

            "summary": {

                "total_transactions":total_rows,

                "matched":len(matched_df),

                "bank_charges":len(bank_charges_df),

                "interest":len(interest_df),

                "investments":len(investment_df),

                "unmatched":len(unmatched_df),

                "possible_matches": len(possible_df),

                "security_deposits": len(security_deposit_df),

                "vendor_transactions":len(vendor_transactions_df),



                "match_rate": match_rate,

                "matched_amount": self._round_amount(matched_amount),

                "interest_amount": self._round_amount(interest_amount),

                "bank_charge_amount": self._round_amount(bank_charge_amount),

                "investment_amount": self._round_amount(investment_amount),

                "unmatched_amount": self._round_amount(unmatched_amount),

                "possible_amount": self._round_amount(possible_amount),

                "security_deposit_amount": self._round_amount(
                    security_deposit_amount
                ),

                "vendor_amount": self._round_amount(vendor_amount),
            }
        }

    def _prepare_invoices(self):
        invoices = self.invoice_df.copy()
        invoices["_invoice_no_norm"] = invoices["Invoice No."].astype(str).str.strip()
        invoices["_amount"] = pd.to_numeric(
            invoices["Total Amt (INR)"],
            errors="coerce"
        )
        if "Customer Name" in invoices.columns:
            invoices["_customer_norm"] = (
                invoices["Customer Name"].astype(str).map(self._normalize_text)
            )
        else:
            invoices["_customer_norm"] = ""
        invoices["_invoice_date"] = pd.to_datetime(
            invoices.get("Invoice Date"),
            errors="coerce"
        )

        date_column = self._first_existing_column(
            invoices,
            ["Due Date", "Invoice Date"]
        )
        invoices["_match_date"] = (
            pd.to_datetime(invoices[date_column], errors="coerce")
            if date_column
            else pd.NaT
        )
        return invoices

    def _find_invoice_match(self, bank_row, invoices, matched_invoice_numbers):
        credit_amount = self._number(bank_row.get("Credit (INR)", 0))

        if credit_amount <= 0:
            return None

        description = str(bank_row.get("Transaction Description", ""))
        description_norm = self._normalize_text(description)
        bank_date = self._bank_date(bank_row)
        receipt_type = self._classify_receipt(description)
        customer_candidates = self._filter_by_customer(invoices.copy(), description_norm)

        if customer_candidates.empty:
            return None

        expected_amounts = self._expected_amounts(receipt_type, customer_candidates)
        customer_candidates = customer_candidates.assign(
            _expected_amount=expected_amounts,
            _amount_gap=(expected_amounts - credit_amount).abs(),
        )
        all_amount_candidates = customer_candidates[
            customer_candidates["_amount_gap"] <= self.AMOUNT_TOLERANCE
        ].copy()

        if all_amount_candidates.empty:
            return None

        available_candidates = all_amount_candidates[
            ~all_amount_candidates["_invoice_no_norm"].isin(matched_invoice_numbers)
        ].copy()

        if available_candidates.empty:
            return None

        tie_break_column = self._tie_break_column(available_candidates, bank_date)
        available_candidates = available_candidates.sort_values(
            by=["_amount_gap", tie_break_column, "_invoice_no_norm"],
            ascending=[True, True, True]
        )
        best_invoice = available_candidates.iloc[0]
        reason = self._match_reason(
            receipt_type,
            best_invoice,
            description_norm,
            bank_date
        )
        is_unique_across_customer = len(all_amount_candidates) == 1
        can_auto_match = (
            is_unique_across_customer
            and receipt_type not in self.NON_AUTO_RECEIPT_TYPES
        )
        status = "Auto Matched" if can_auto_match else "Review Required"
        score = (
            95
            if can_auto_match
            else 70
        )

        return {
            "invoice_no": best_invoice["_invoice_no_norm"],
            "score": score,
            "reason": reason,
            "status": status,
        }

    def _with_match_details(self, row, match, status):
        result = row.copy()
        result["Matched Invoice"] = match["invoice_no"]
        result["Match Score"] = match["score"]
        result["Match Status"] = status
        result["Match Reason"] = match["reason"]
        return result

    def _result_columns(self):
        return list(self.bank_df.columns) + [
            "Matched Invoice",
            "Match Score",
            "Match Status",
            "Match Reason",
        ]

    @staticmethod
    def _normalize_text(value):
        return re.sub(r"[^A-Z0-9]+", " ", str(value).upper()).strip()

    @staticmethod
    def _number(value):
        number = pd.to_numeric(value, errors="coerce")
        if pd.isna(number):
            return 0
        return float(number)

    @staticmethod
    def _first_existing_column(df, columns):
        for column in columns:
            if column in df.columns:
                return column
        return None

    @staticmethod
    def _days_apart(left, right):
        if pd.isna(left) or pd.isna(right):
            return None
        return abs((left - right).days)

    def _bank_date(self, row):
        for column in ["Txn Date", "Transaction Date", "Value Date", "Date"]:
            if column in row.index:
                return pd.to_datetime(row.get(column), errors="coerce")
        return pd.NaT

    def _classify_receipt(self, description):
        description_upper = str(description).upper()

        if "NET OF TDS" in description_upper:
            return "net_of_tds"
        if "ADVANCE RECEIVED" in description_upper:
            return "advance"
        if "CUSTOMER PAYMENT" in description_upper:
            return "customer_payment"
        if "POS/GATEWAY" in description_upper:
            return "gateway"
        if "AD-HOC" in description_upper or "AD HOC" in description_upper:
            return "ad_hoc"

        return "other_receipt"

    def _filter_by_customer(self, candidates, description_norm):

        if not description_norm:
            return candidates.iloc[0:0]

        customer_hits = []

        for _, invoice in candidates.iterrows():

            customer_name = str(
                invoice.get("_customer_norm", "")
            )

            if not customer_name:
                continue

            score = fuzz.token_set_ratio(
                customer_name,
                description_norm
            )

            # Exact/near exact match
            if score >= 85:

                invoice = invoice.copy()

                invoice["_customer_match_score"] = score

                customer_hits.append(invoice)

        if customer_hits:

            customer_hits_df = pd.DataFrame(customer_hits)

            return customer_hits_df.sort_values(
                "_customer_match_score",
                ascending=False
            )

        return candidates.iloc[0:0]

    def _expected_amounts(self, receipt_type, candidates):
        if receipt_type == "net_of_tds":
            return candidates["_amount"] * self.TDS_RATE
        if receipt_type == "advance":
            return candidates["_amount"] * self.ADVANCE_RATE
        return candidates["_amount"]

    def _tie_break_column(self, candidates, bank_date):
        if pd.isna(bank_date):
            return "_invoice_no_norm"

        candidates["_tie_break_days"] = pd.to_numeric(
            candidates["_invoice_date"].apply(
                lambda value: self._days_apart(bank_date, value)
            ),
            errors="coerce"
        ).fillna(9999)
        return "_tie_break_days"

    def _match_reason(self, receipt_type, invoice, description_norm, bank_date):
        reasons = []

        customer = invoice.get("_customer_norm", "")
        if customer and customer in description_norm:
            reasons.append("customer name in narration")

        if receipt_type == "net_of_tds":
            reasons.append("net of TDS formula match")
        elif receipt_type == "advance":
            reasons.append("advance formula match")
        elif receipt_type == "customer_payment":
            reasons.append("full invoice amount match")
        elif receipt_type == "gateway":
            reasons.append("gateway receipt amount match")
        elif receipt_type == "ad_hoc":
            reasons.append("ad-hoc receipt amount match")
        else:
            reasons.append("amount match")

        days_apart = self._days_apart(bank_date, invoice.get("_invoice_date"))
        if days_apart is not None:
            reasons.append(f"invoice date distance {days_apart} days")

        customer_score = invoice.get("_customer_match_score")

        if pd.notna(customer_score):

            reasons.append(
                f"customer similarity {round(customer_score)}%"
            )

        return ", ".join(reasons)

    @staticmethod
    def _sum_amount(df, column):
        if df.empty or column not in df.columns:
            return 0

        return pd.to_numeric(
            df[column],
            errors="coerce"
        ).fillna(0).sum()

    @staticmethod
    def _round_amount(amount):
        return round(float(amount), 2)
