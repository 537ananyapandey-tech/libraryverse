"""Central settings. Edit these values to change library rules or demo logins."""

LOAN_DAYS = 14                 # default loan period (days)
MAX_LOAN_DAYS = 120            # longest due date the issue form will accept
MAX_BACKDATE_DAYS = 365        # how far back an issue date may be set
MAX_ACTIVE_ISSUES = 5          # books one student may hold at once
FINE_PER_DAY = 5               # late-return fine in rupees per day
PER_PAGE = 12                  # catalogue page size

# Demo logins (documented in README.md). Change before any real use.
DEMO_ADMIN_USERNAME = "admin"
DEMO_ADMIN_PASSWORD = "Admin@123"
DEFAULT_STUDENT_PASSWORD = "student123"
