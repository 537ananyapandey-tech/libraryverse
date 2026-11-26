"""Import books from a CSV file (for example your old project's books.csv).

Usage (PowerShell):   python import_csv.py path\to\books.csv

Column names are matched flexibly (case-insensitive). Recognised columns:
  title / book / book_name, author, publisher, edition, isbn, category / branch,
  subject, copies / total / quantity / stock, available / available_copies, description
Unknown categories are created. Rows with no title are skipped. Existing titles
(same title + author) are skipped, so the script is safe to run twice.
Nothing is deleted or overwritten.
"""
import csv
import os
import sys

import database
from services import library

ALIASES = {
    "title": ("title", "book", "book_name", "bookname", "name"),
    "author": ("author", "authors", "writer"),
    "publisher": ("publisher",), "edition": ("edition",), "isbn": ("isbn",),
    "category": ("category", "branch", "department"), "subject": ("subject",),
    "copies": ("copies", "total", "total_copies", "quantity", "qty", "stock"),
    "available": ("available", "available_copies", "avail"),
    "description": ("description", "summary"),
}


def pick(row, key):
    for alias in ALIASES[key]:
        for col, val in row.items():
            if col and col.strip().lower().replace(" ", "_") == alias and val is not None:
                return val.strip()
    return ""


def main(csv_path):
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "library.db")
    database.init_db(db_path)
    conn = database.connect(db_path)
    added = skipped = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            title, author = pick(row, "title"), pick(row, "author") or "Unknown"
            if not title:
                skipped += 1
                continue
            if conn.execute("SELECT 1 FROM books WHERE title = ? COLLATE NOCASE AND author = ? COLLATE NOCASE",
                            (title, author)).fetchone():
                skipped += 1
                continue
            cat_name = pick(row, "category") or "Programming and Computer Fundamentals"
            cat = conn.execute("SELECT id FROM categories WHERE name = ? OR short_name = ? COLLATE NOCASE",
                               (cat_name, cat_name)).fetchone()
            if cat is None:
                library.add_category(conn, cat_name, "", "CSV import")
                cat = conn.execute("SELECT id FROM categories WHERE name = ? COLLATE NOCASE", (cat_name,)).fetchone()
            try:
                total = max(int(pick(row, "copies") or 1), 0)
            except ValueError:
                total = 1
            try:
                avail = min(max(int(pick(row, "available") or total), 0), total)
            except ValueError:
                avail = total
            isbn = pick(row, "isbn")
            if isbn and not library.is_valid_isbn(isbn):
                print(f"  note: '{title}' has an invalid ISBN ({isbn}); imported without it")
                isbn = ""
            try:
                with database.transaction(conn):
                    conn.execute(
                        """INSERT INTO books(title, author, publisher, edition, isbn, description, category_id,
                                             subject, total_copies, available_copies) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (title, author, pick(row, "publisher"), pick(row, "edition"), library.normalize_isbn(isbn),
                         pick(row, "description"), cat["id"], pick(row, "subject"), total, avail))
                added += 1
            except Exception as exc:  # duplicate ISBN etc.; keep going
                print(f"  skipped '{title}': {exc}")
                skipped += 1
    print(f"Imported {added} book(s), skipped {skipped}.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
