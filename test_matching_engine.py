import pandas as pd
import re
from rapidfuzz import fuzz


class MatchingEngine:
    TDS_RATE = 108 / 118  # 10% TDS deducted on the pre-GST base amount of an 18% GST invoice
    AMOUNT_TOLERANCE = 1.0

    # Receipt types that must NEVER auto-match even if unique (ambiguous / needs human sign-off)
    NON_AUTO_RECEIPT_TYPES = {"ad_hoc", "gateway", "other_receipt"}

    # Restrict which Invoice Type a given receipt type is allowed to settle against.
    # Prevents e.g. an "Advance Received" transaction from consuming the very
    # "Advance Adjusted Invoice" that the later full settlement payment needs.
    RECEIPT_TYPE_INVOICE_TYPES = {
        "net_of_tds": {"TDS Deducted Invoice"},
        "emi": {"EMI Purchase"},
        "debit_note_recovery": {"Debit Note"},
        "interest_recovery": {"Interest on Overdue"},
        "penalty_recovery": {"Penalty Invoice"},
    }

    # Non-customer buckets checked BEFORE invoice matching. Order matters:
    # more specific phrases must be checked before generic ones
    # (e.g. "CHEQUE RETURN" before a generic "RETURN"/"DEPOSIT" catch-all).
    NON_INVOICE_RULES = [
        ("interest_income", ["FD INTEREST CREDIT"]),
        ("bank_charges", ["BANK SERVICE CHARGES", "GST ON BANK CHARGES"]),
        ("investments", ["FD PLACEMENT", "FD MATURITY RECEIPT", "SWEEP TO SAVINGS", "SWEEP RETURN"]),
        ("security_deposits", ["SECURITY DEPOSIT"]),
        ("advances_received", ["ADVANCE RECEIVED"]),
        ("cheque_bounce", ["CHEQUE RETURN"]),
        ("bounce_penalty", ["BOUNCE PENALTY"]),
        ("payment_reversals", ["EXCESS PAYMENT REVERSED", "REFUND ISSUED"]),
        ("unidentified", ["UNIDENTIFIED NEFT RECEIPT"]),
        ("vendor_transactions", [
            "SALARY DISBURSEMENT", "OFFICE RENT", "GST PAYMENT", "PF CONTRIBUTION",
            "ESI CONTRIBUTION", "ADVANCE TAX PAYMENT", "TDS PAYMENT TO GOVT",
            "COURIER CHARGES", "INSURANCE PREMIUM", "INTERNET CHARGES",
            "SECURITY AGENCY CHARGES", "TELEPHONE BILL", "IT SUPPORT",
            "GENERATOR SERVICE", "SOFTWARE SUBSCRIPTION", "PEST CONTROL",
            "ELECTRICITY BILL", "FUEL REIMBURSEMENT", "CATERING CONTRACT",
            "VEHICLE HIRE", "HOSTING CHARGES", "AUDIT FEES", "OFFICE SUPPLIES",
            "MARKETING FEES", "PRINTING & STATIONERY", "SECURITY FEES",
            "EVENT MGMT", "HOUSEKEEPING", "HVAC MAINTENANCE", "LEGAL FEES",
            "MUSIC LICENSE",
        ]),
    ]

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
        missing_bank_columns = sorted(self.BANK_REQUIRED_COLUMNS - set(self.bank_df.columns))
        missing_invoice_columns = sorted(self.INVOICE_REQUIRED_COLUMNS - set(self.invoice_df.columns))

        errors = []
        if missing_bank_columns:
            errors.append("Bank statement is missing columns: " + ", ".join(missing_bank_columns))
        if missing_invoice_columns:
            errors.append("Invoice register is missing columns: " + ", ".join(missing_invoice_columns))
        if errors:
            raise ValueError(" ".join(errors))

    def run(self):
        self.validate()

        buckets = {name: [] for name, _ in self.NON_INVOICE_RULES}
        matched_rows = []
        possible_rows = []
        unmatched = []

        invoices = self._prepare_invoices()
        matched_invoice_numbers = set()

        for _, row in self.bank_df.iterrows():
            description = str(row.get("Transaction Description", "")).upper()

            bucket = self._classify_non_invoice(description)

            if bucket is not None:
                buckets[bucket].append(row)
                continue

            match = self._find_invoice_match(row, invoices, matched_invoice_numbers)

            if match and match["status"] == "Auto Matched":
                matched_invoice_numbers.add(match["invoice_no"])
                matched_rows.append(self._with_match_details(row, match, match["status"]))
            elif match and match["status"] == "Review Required":
                possible_rows.append(self._with_match_details(row, match, match["status"]))
            else:
                unmatched.append(row)

        matched_df = pd.DataFrame(matched_rows, columns=self._result_columns())
        possible_df = pd.DataFrame(possible_rows, columns=self._result_columns())
        unmatched_df = pd.DataFrame(unmatched, columns=self.bank_df.columns)

        bucket_dfs = {
            name: pd.DataFrame(rows, columns=self.bank_df.columns)
            for name, rows in buckets.items()
        }

        total_rows = len(self.bank_df)
        matched_count = len(matched_df)
        customer_receipt_count = len(matched_df) + len(possible_df) + len(unmatched_df)
        match_rate = (
            round(matched_count / customer_receipt_count * 100, 2)
            if customer_receipt_count > 0
            else 0
        )

        summary = {
            "total_transactions": total_rows,
            "matched": len(matched_df),
            "possible_matches": len(possible_df),
            "unmatched": len(unmatched_df),
            "match_rate": match_rate,
            "matched_amount": self._round_amount(self._sum_amount(matched_df, "Credit (INR)")),
            "possible_amount": self._round_amount(self._sum_amount(possible_df, "Credit (INR)")),
            "unmatched_amount": self._round_amount(self._sum_amount(unmatched_df, "Credit (INR)")),
        }

        debit_side_buckets = {
            "bank_charges", "vendor_transactions", "cheque_bounce",
            "bounce_penalty", "payment_reversals",
        }
        for name, df in bucket_dfs.items():
            summary[name] = len(df)
            if name == "investments":
                # mixes FD placements/sweeps (debit) with maturities/receipts (credit)
                summary[f"{name}_debit_amount"] = self._round_amount(self._sum_amount(df, "Debit (INR)"))
                summary[f"{name}_credit_amount"] = self._round_amount(self._sum_amount(df, "Credit (INR)"))
            else:
                column = "Debit (INR)" if name in debit_side_buckets else "Credit (INR)"
                summary[f"{name}_amount"] = self._round_amount(self._sum_amount(df, column))

        result = {
            "matched_df": matched_df,
            "possible_df": possible_df,
            "unmatched_df": unmatched_df,
            "summary": summary,
        }
        result.update({f"{name}_df": df for name, df in bucket_dfs.items()})
        return result

    def _classify_non_invoice(self, description_upper):
        for bucket_name, keywords in self.NON_INVOICE_RULES:
            if any(keyword in description_upper for keyword in keywords):
                return bucket_name
        return None

    def _prepare_invoices(self):
        invoices = self.invoice_df.copy()
        invoices["_invoice_no_norm"] = invoices["Invoice No."].astype(str).str.strip()
        invoices["_amount"] = pd.to_numeric(invoices["Total Amt (INR)"], errors="coerce")

        if "Customer Name" in invoices.columns:
            invoices["_customer_norm"] = invoices["Customer Name"].astype(str).map(self._normalize_text)
        else:
            invoices["_customer_norm"] = ""

        invoices["_invoice_date"] = pd.to_datetime(invoices.get("Invoice Date"), errors="coerce")

        date_column = self._first_existing_column(invoices, ["Due Date", "Invoice Date"])
        invoices["_match_date"] = (
            pd.to_datetime(invoices[date_column], errors="coerce") if date_column else pd.NaT
        )

        # EMI installment count, parsed from "Payment Terms" e.g. "EMI - 12 Months"
        if "Payment Terms" in invoices.columns:
            invoices["_emi_installments"] = (
                invoices["Payment Terms"].astype(str)
                .str.extract(r"EMI\s*-\s*(\d+)\s*Months", flags=re.IGNORECASE)[0]
                .astype(float)
            )
        else:
            invoices["_emi_installments"] = pd.NA

        if "Invoice Type" in invoices.columns:
            invoices["_invoice_type_norm"] = invoices["Invoice Type"].astype(str).str.strip()
        else:
            invoices["_invoice_type_norm"] = ""

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

        allowed_types = self.RECEIPT_TYPE_INVOICE_TYPES.get(receipt_type)
        if allowed_types:
            customer_candidates = customer_candidates[
                customer_candidates["_invoice_type_norm"].isin(allowed_types)
            ]

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
        reason = self._match_reason(receipt_type, best_invoice, description_norm, bank_date)

        is_unique_across_customer = len(all_amount_candidates) == 1
        can_auto_match = (
            is_unique_across_customer
            and receipt_type not in self.NON_AUTO_RECEIPT_TYPES
        )
        status = "Auto Matched" if can_auto_match else "Review Required"
        score = 95 if can_auto_match else 70

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
            "Matched Invoice", "Match Score", "Match Status", "Match Reason",
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
        if "EMI RECEIPT" in description_upper:
            return "emi"
        if "POS/GATEWAY" in description_upper or "GATEWAY" in description_upper:
            return "gateway"
        if "AD-HOC" in description_upper or "AD HOC" in description_upper:
            return "ad_hoc"
        if "CHEQUE DEPOSIT" in description_upper:
            return "cheque_deposit"
        if "DEBIT NOTE RECOVERY" in description_upper:
            return "debit_note_recovery"
        if "OVERDUE INTEREST RECOVERED" in description_upper:
            return "interest_recovery"
        if "PENALTY RECOVERY" in description_upper:
            return "penalty_recovery"
        if "CUSTOMER PAYMENT" in description_upper:
            return "customer_payment"

        return "other_receipt"

    def _filter_by_customer(self, candidates, description_norm):
        if not description_norm:
            return candidates.iloc[0:0]

        customer_hits = []
        for _, invoice in candidates.iterrows():
            customer_name = str(invoice.get("_customer_norm", ""))
            if not customer_name:
                continue

            score = fuzz.token_set_ratio(customer_name, description_norm)
            if score >= 85:
                invoice = invoice.copy()
                invoice["_customer_match_score"] = score
                customer_hits.append(invoice)

        if customer_hits:
            customer_hits_df = pd.DataFrame(customer_hits)
            return customer_hits_df.sort_values("_customer_match_score", ascending=False)

        return candidates.iloc[0:0]

    def _expected_amounts(self, receipt_type, candidates):
        if receipt_type == "net_of_tds":
            return candidates["_amount"] * self.TDS_RATE
        if receipt_type == "emi":
            installments = candidates["_emi_installments"].replace(0, pd.NA)
            return (candidates["_amount"] / installments).fillna(candidates["_amount"])
        return candidates["_amount"]

    def _tie_break_column(self, candidates, bank_date):
        if pd.isna(bank_date):
            return "_invoice_no_norm"

        candidates["_tie_break_days"] = pd.to_numeric(
            candidates["_invoice_date"].apply(lambda value: self._days_apart(bank_date, value)),
            errors="coerce"
        ).fillna(9999)
        return "_tie_break_days"

    def _match_reason(self, receipt_type, invoice, description_norm, bank_date):
        reasons = []

        customer = invoice.get("_customer_norm", "")
        if customer and customer in description_norm:
            reasons.append("customer name in narration")

        reason_labels = {
            "net_of_tds": "net of TDS formula match",
            "emi": "EMI installment amount match",
            "customer_payment": "full invoice amount match",
            "gateway": "gateway receipt amount match",
            "ad_hoc": "ad-hoc receipt amount match",
            "cheque_deposit": "cheque deposit amount match",
            "debit_note_recovery": "debit note recovery amount match",
            "interest_recovery": "overdue interest recovery amount match",
            "penalty_recovery": "penalty recovery amount match",
        }
        reasons.append(reason_labels.get(receipt_type, "amount match"))

        days_apart = self._days_apart(bank_date, invoice.get("_invoice_date"))
        if days_apart is not None:
            reasons.append(f"invoice date distance {days_apart} days")

        customer_score = invoice.get("_customer_match_score")
        if pd.notna(customer_score):
            reasons.append(f"customer similarity {round(customer_score)}%")

        return ", ".join(reasons)

    @staticmethod
    def _sum_amount(df, column):
        if df.empty or column not in df.columns:
            return 0
        return pd.to_numeric(df[column], errors="coerce").fillna(0).sum()

    @staticmethod
    def _round_amount(amount):
        return round(float(amount), 2)
    



    