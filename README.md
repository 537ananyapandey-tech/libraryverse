# LIBRARYVERSE

*Your next chapter starts here.* An engineering library management system built with Python, Flask and SQLite.
It runs fully offline after installation.

## Easiest way

Double-click **run.bat**. It installs Flask, starts the server and opens your browser.

## Run it manually (Windows PowerShell)

```powershell
cd path\to\libraryverse
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000** in your browser. Stop the server with `Ctrl+C`.

If PowerShell blocks the activation script, run `Set-ExecutionPolicy -Scope Process Bypass` once and try again.
You can also skip the virtual environment: `pip install -r requirements.txt` then `python app.py`.

The first run creates `instance\library.db` and loads 142 textbooks across 13 branches, 52 students and about 200 loans (a deliberately messy, made-up history).
Your data stays in that file between restarts. The sample students and loans are made up and are the same on every fresh database. To start fresh, stop the server and delete `instance\library.db`.

## Demo logins

| Role    | ID / username | Password     |
|---------|---------------|--------------|
| Admin   | `admin`       | `Admin@123`  |
| Student | `CS24001` (Aarav Sharma, has an overdue book) | `student123` |
| Student | `AI24003` (Rohan Patel, has an overdue book)  | `student123` |
| Student | `EC24004`, `ME24005`, `IT24006`, `DS24007`, `CS24002`, `CE24008` | `student123` |

Students who are registered automatically at the issue desk also get the default password `student123`.
Change the demo values in `config.py` before using this for anything real.

## Folder structure

```
libraryverse/
  app.py              Starts the app (python app.py). Creates the Flask app, security headers, error pages.
  config.py           Loan period (14 days), max books per student, demo passwords.
  database.py         SQLite schema, transactions, first-run seeding.
  import_csv.py       Imports books from a CSV file without touching existing data.
  services/
    library.py        All rules: search, issue, return, stock, categories, statistics.
    security.py       CSRF tokens and login guards.
  routes/
    public.py         Landing, dashboard, catalogue, book details, issue and return pages.
    api.py            JSON endpoints used by search, issue and return.
    student.py        Student sign in and "My shelf".
    admin.py          Admin panel: books, loans, students, categories, reports.
  data/seed_books.py  The 142 seeded textbooks, demo students and demo loans.
  templates/          HTML (Jinja). admin/ holds the admin pages.
  static/css, js      Styles and scripts. No external libraries, so offline works.
  tests/test_app.py   28 automated tests.
  instance/           library.db and secret.key are created here at first run.
```

## How the important parts work

* **Stock safety.** Issuing and returning run inside `BEGIN IMMEDIATE` database transactions. Stock changes and the
  loan record are saved together or not at all. The database also forbids a second active loan of the same book
  for the same student.
* **Overdue** means the due date has passed and the book has not been returned. It is highlighted in orange on the
  return page, dashboard, student page and admin pages.
* **Deleting a book.** A book that was never issued is deleted. A book with loan history is hidden from the catalogue
  but its records are kept. A book with copies still on loan cannot be deleted.
* **Passwords** are hashed (Werkzeug). Forms and API calls carry a CSRF token. Admin pages need a login.
* **Adding books from a CSV:** `python import_csv.py your_books.csv`.

## Show it to evaluators (about 5 minutes)

1. Open the home page and point out the shelf, which is built from real catalogue records.
2. Click **Open dashboard**. Every figure comes from the database. Note the overdue banner.
3. Click **Explore books**, choose a branch tile or use the Branch filter, then type `python` or `machine learning`.
4. Toggle grid and list view. Open **Python Crash Course** and note "7 of 8 copies available".
5. Click **Issue this book**. Enter ID `NEW001` and a name. Watch the due-date slip fill in, then submit.
   The student is registered automatically.
6. Open the book page again: stock dropped by one. Open the dashboard: Issued went up and activity shows the loan.
7. Open **Return**, search `NEW001`, press Return and confirm. Stock goes back up. Try the **Overdue** filter.
8. Open **Admin**, sign in, choose **Books** then **Add a book**. Show validation by saving an empty form first.
9. Open **Reports** for branch-wise stock, most issued books and overdue transactions.
10. Stop the server, run `python app.py` again, and show that the data is still there.

## Things worth knowing

* **Catalogue accuracy.** Titles, authors and publishers are real. Edition and ISBN are filled only where they are
  known and left as "Not specified" otherwise. ISBNs pass a checksum test, but please verify them against your
  physical copies before presenting them as authoritative.
* **Fonts.** Space Grotesk and DM Sans load from Google Fonts when online. Offline, the site falls back to
  Segoe UI, which looks fine but slightly different.
* **Icons.** Icons are inline SVGs drawn in the Lucide style (no CDN), so nothing needs the internet.
* **Issue and return pages are open** (like a library desk computer) so the demo needs no login. Admin pages are protected.
* **Run on your network** (for example a second laptop): `$env:LIBRARYVERSE_HOST="0.0.0.0"; python app.py`.
  This server is for demos, not for the public internet.

## Tests

```powershell
python -m unittest discover -s tests -v
```
