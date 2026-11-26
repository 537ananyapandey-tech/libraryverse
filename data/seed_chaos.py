"""Generates a messy, believable college-library history (all names/IDs are made up).

Fixed random seed, so the same data appears on every fresh database. Deliberate chaos:
a few heavy borrowers, students who never borrowed, books nobody has touched, a fully
issued book, last-copy books, a student at the 5-book limit, chronic late returners and
one loan that vanished 60+ days ago.
"""
import random
import re

FIRST = ["Aditya", "Akash", "Anjali", "Arjun", "Bhavya", "Chetan", "Deepak", "Divya", "Farhan", "Gaurav",
         "Harsh", "Isha", "Jatin", "Kavya", "Lakshya", "Manav", "Meera", "Mohit", "Nikhil", "Nisha",
         "Om", "Palak", "Pranav", "Pooja", "Rahul", "Riya", "Sakshi", "Saurabh", "Shreya", "Siddharth",
         "Simran", "Tanvi", "Tushar", "Utkarsh", "Vaibhav", "Vedika", "Yash", "Zoya", "Kunal", "Ritika"]
LAST = ["Agrawal", "Bansal", "Chauhan", "Dubey", "Goyal", "Jain", "Kushwaha", "Mishra", "Nair", "Pandey",
        "Rathore", "Saxena", "Tiwari", "Yadav", "Bhardwaj", "Dixit", "Gurjar", "Khare", "Mehta", "Pawar",
        "Rawat", "Shukla", "Tomar", "Upadhyay", "Verma", "Sikarwar", "Bhadauria", "Kulkarni", "Reddy", "Thakur"]

BRANCHES = [("CS", 5), ("IT", 3), ("AI", 3), ("DS", 2), ("EC", 3), ("EE", 2), ("ME", 3), ("CE", 2), ("AR", 1)]
YEARS = [("22", 1), ("23", 2), ("24", 4), ("25", 3)]            # 25 = first years
AFFINITY = {"CS": {"CSE", "PROG", "AIML", "IT"}, "IT": {"IT", "CSE", "PROG", "DS"}, "AI": {"AIML", "DS", "CSE", "PROG"},
            "DS": {"DS", "AIML", "MATH", "PROG"}, "EC": {"ECE", "EE", "PHY", "MATH"}, "EE": {"EE", "ECE", "PHY", "MATH"},
            "ME": {"ME", "MATH", "PHY"}, "CE": {"CE", "MATH", "PHY"}, "AR": {"ARCH", "CE", "PHY"}}
FIRST_YEAR = {"MATH", "PHY", "CHEM", "PROG"}
HOT_TITLES = {"Python Crash Course": 7, "Let Us C": 6, "Higher Engineering Mathematics": 6, "Engineering Physics": 5,
              "Engineering Chemistry": 5, "Introduction to Algorithms": 4, "Artificial Intelligence: A Modern Approach": 4,
              "Hands-On Machine Learning with Scikit-Learn, Keras, and TensorFlow": 5, "Digital Design": 3,
              "Engineering Mechanics": 3, "Operating System Concepts": 3, "Database System Concepts": 3,
              "Fundamentals of Electric Circuits": 3, "Python for Data Analysis": 4}


def _weighted(rng, pairs):
    return rng.choices([p[0] for p in pairs], [p[1] for p in pairs])[0]


def generate_students(rng, count=44, taken_ids=(), taken_names=()):
    seen_ids, seen_names, out = set(taken_ids), set(taken_names), []
    while len(out) < count:
        no = f"{_weighted(rng, BRANCHES)}{_weighted(rng, YEARS)}{rng.randint(1, 160):03d}"
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if no in seen_ids or name in seen_names:
            continue
        seen_ids.add(no); seen_names.add(name)
        out.append((no, name, rng.randint(5, 220)))              # (id, name, registered N days ago)
    return out


def _branch(no):
    m = re.match(r"[A-Za-z]+", no)
    return m.group(0).upper() if m else ""


def _year(no):
    m = re.search(r"(\d{2})\d{3}$", no)
    return m.group(1) if m else ""


def generate_loans(rng, books, students, held, out, active_pairs, target=200):
    """books: [(id, title, copies, category_short)], students: [enrollment_no].
    held / out / active_pairs describe loans that already exist and are updated in place.
    Returns [(student_no, book_id, issued_days_ago, returned_days_ago_or_None)] oldest first."""
    copies = {b[0]: b[2] for b in books}
    cat = {b[0]: b[3] for b in books}
    by_title = {b[1]: b[0] for b in books}
    ids = [b[0] for b in books]

    untouched = set(rng.sample([i for i in ids if i not in {by_title.get(t) for t in HOT_TITLES}], 16))
    bw = {}
    for b in books:
        w = min(rng.paretovariate(1.5), 8) * HOT_TITLES.get(b[1], 1)
        bw[b[0]] = 0 if b[0] in untouched else w
    never = set(rng.sample(students, 7))
    sw = {s: 0 if s in never else min(rng.paretovariate(1.3), 10) for s in students}
    chronic_late = set(rng.sample([s for s in students if s not in never], 5))
    loans = []

    def can_hold(s, b):
        return held.get(s, 0) < 5 and out.get(b, 0) < copies[b] and (s, b) not in active_pairs

    def add(s, b, ago, ret):
        loans.append((s, b, ago, ret))
        if ret is None:
            held[s] = held.get(s, 0) + 1; out[b] = out.get(b, 0) + 1; active_pairs.add((s, b))

    live = [s for s in students if s not in never]
    # fully issued book + a last-copy book
    for title, n, days in (("Design Patterns: Elements of Reusable Object-Oriented Software", 3, (2, 5, 9)),
                           ("Compilers: Principles, Techniques, and Tools", 2, (4, 11))):
        b = by_title.get(title)
        for k in range(n if b else 0):
            s = rng.choice([x for x in live if can_hold(x, b)] or [None])
            if s:
                add(s, b, days[k], None)
    # a student who maxed out at 5 books, two of them overdue
    maxed = rng.choice([s for s in live if held.get(s, 0) == 0])
    for ago in (1, 4, 8, 19, 27):
        b = rng.choices(ids, [bw[i] if can_hold(maxed, i) else 0 for i in ids])[0]
        if can_hold(maxed, b):
            add(maxed, b, ago, None)
    # one loan that simply vanished
    vanished = rng.choice(sorted(chronic_late))
    b = rng.choices(ids, [bw[i] if can_hold(vanished, i) else 0 for i in ids])[0]
    add(vanished, b, 64, None)

    overdue_budget, attempts = 4, 0
    while len(loans) < target and attempts < 6000:
        attempts += 1
        s = rng.choices(students, [sw[x] for x in students])[0]
        branch, year = _branch(s), _year(s)
        weights = []
        for i in ids:
            w = bw[i]
            if cat[i] in AFFINITY.get(branch, ()):
                w *= 4
            if year == "25" and cat[i] in FIRST_YEAR:
                w *= 6
            weights.append(w)
        if not any(weights):
            continue
        b = rng.choices(ids, weights)[0]
        if (s, b) in active_pairs:
            continue
        ago = max(1, int(rng.triangular(1, 75, 15)))
        late_type = s in chronic_late
        if ago <= 14 and rng.random() < 0.8 and can_hold(s, b):
            add(s, b, ago, None)
        elif ago > 14 and overdue_budget > 0 and rng.random() < (0.5 if late_type else 0.07) and can_hold(s, b):
            add(s, b, ago, None); overdue_budget -= 1
        else:
            gap = rng.randint(15, 40) if (late_type or rng.random() < 0.18) else rng.randint(2, 13)
            add(s, b, ago, max(ago - gap, 0))
    loans.sort(key=lambda x: -x[2])
    return loans
