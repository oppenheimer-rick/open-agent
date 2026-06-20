"""Finance Tracker - CSV-backed storage and core logic."""

import csv
from pathlib import Path
from datetime import date


CSV_FILE = "transactions.csv"
FIELDNAMES = ["date", "type", "amount", "category", "description"]


def _ensure_csv():
    """Create CSV header if file doesn't exist."""
    p = Path(CSV_FILE)
    if not p.exists() or p.stat().st_size == 0:
        with open(CSV_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()


def add_transaction(t_type: str, amount: float, category: str, description: str):
    """Append a single transaction to the CSV file."""
    _ensure_csv()
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow({
            "date": date.today().isoformat(),
            "type": t_type,
            "amount": amount,
            "category": category,
            "description": description,
        })


def get_summary():
    """Read all transactions and return a summary dict."""
    _ensure_csv()
    income = 0.0
    expenses = 0.0
    categories: dict[str, float] = {}

    with open(CSV_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            amt = float(row["amount"])
            if row["type"] == "income":
                income += amt
            else:
                expenses += amt
            cat = row["category"]
            categories[cat] = categories.get(cat, 0.0) + amt

    return {
        "income": round(income, 2),
        "expenses": round(expenses, 2),
        "net": round(income - expenses, 2),
        "categories": {k: round(v, 2) for k, v in categories.items()},
    }
