"""A demo database with made-up jobs, for the README's screenshots. Nothing in it is real.

Run: uv run python scripts/demo_data.py <path/to/new.db>
"""

import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from jobhunt import checks
from jobhunt.actions import set_job_status
from jobhunt.applications import add_step
from jobhunt.db import init_db, make_engine, make_session_factory
from jobhunt.dedup import match_key
from jobhunt.eligibility import Profile, save_profile
from jobhunt.models import Job, Listing, Match, Run, RunListing
from jobhunt.preferences import load_preferences, save_preferences

PROFILE = Profile(work_rights=("EU", "UK"), home_country="DE", time_zone="Europe/Berlin", relocate_to=("EU",),
                  languages=("en", "de"))
SKILLS = ("Python", "Django", "FastAPI", "PostgreSQL", "Docker", "AWS", "React", "TypeScript")

# (title, company, location, workplace, what the posting says, salary, days since posted)
JOBS = [
    ("Senior Python Developer", "Northwind Analytics", "Berlin, Germany", "hybrid",
     "Build data APIs with Python, FastAPI and PostgreSQL. Two days a week in our Berlin office.",
     "€70,000 – €85,000 per year", 1),
    ("Backend Engineer (Django)", "Brightmoor Data", "Germany", "remote",
     "Fully remote within the EU. Django, PostgreSQL, Docker and AWS.", "€65,000 – €78,000 per year", 2),
    ("Full-Stack Developer", "Cobalt Finch", "Munich, Germany", "on-site",
     "React and TypeScript on the front end, Python on the back end. Office in Munich.", None, 3),
    ("Python Engineer, Payments", "Mosswood Bank", "Lisbon, Portugal", "on-site",
     "Join our Lisbon team. Python, PostgreSQL, Kafka. Relocation support offered.", "€55,000 – €65,000 per year", 2),
    ("Data Engineer", "Duneway Logistics", "Amsterdam, Netherlands", "hybrid",
     "Airflow, Python and Snowflake. Hybrid, three days in Amsterdam.", "€60,000 – €72,000 per year", 4),
    ("Software Engineer, Platform", "Emberline Energy", "United States", "remote",
     "Remote, but you must be based in the US. Go, Kubernetes, AWS.", "$140,000 – $165,000 per year", 1),
    ("Senior Backend Developer", "Fjordbit", "Oslo, Norway", "remote",
     "Remote across Europe. Python, Django, Docker.", None, 5),
    ("Machine Learning Engineer", "Saltmarsh AI", "London, England, United Kingdom", "hybrid",
     "PyTorch and Python. Hybrid in our London office.", "£75,000 – £90,000 per year", 3),
    ("Python Developer", "Glasswing Health", "Vienna, Austria", "on-site",
     "Python and Django for a patient-records platform. German is a plus.", None, 6),
    ("Backend Engineer", "Harbor & Vale", "Remote", None,
     "Work on our booking engine with Python and PostgreSQL.", None, 2),
    ("DevOps Engineer", "Ironleaf Games", "Warsaw, Poland", "hybrid",
     "Kubernetes, Terraform and AWS. Hybrid in Warsaw.", "€50,000 – €60,000 per year", 7),
    ("Senior Software Engineer", "Kitefield", "Remote", "remote",
     "Remote (US only). TypeScript, Node.js, React.", "$150,000 – $170,000 per year", 1),
    ("Python Backend Developer", "Lanternfish Labs", "Hamburg, Germany", "remote",
     "Remote within Germany. FastAPI, PostgreSQL, Docker.", "€62,000 – €74,000 per year", 0),
    ("Django Developer", "Quillstone Media", "Barcelona, Spain", "on-site",
     "Django and React for our newsroom tools. Office in Barcelona.", None, 8),
    ("Data Platform Engineer", "Riverlight Insurance", "Zurich, Switzerland", "hybrid",
     "Python, Spark and AWS. Hybrid in Zurich.", "CHF 110,000 – 130,000 per year", 4),
    ("Backend Developer", "Tallow & Rye", "Dublin, Ireland", "remote",
     "Remote within Ireland. Python and Django.", None, 9),
    ("Security Engineer", "Umbra Security", "Toronto, Canada", "on-site",
     "On-site in Toronto. Python and cloud security.", None, 3),
    ("Python Developer (German speaking)", "Vellum Docs", "Cologne, Germany", "hybrid",
     "Python and FastAPI. Fluent German required.", "€58,000 – €68,000 per year", 5),
    ("Software Engineer", "Willowbyte", "Stockholm, Sweden", "hybrid",
     "Python and React. Hybrid in Stockholm.", None, 10),
    ("Climate Data Engineer", "Yarrow Climate", "Copenhagen, Denmark", "remote",
     "Remote within the EU. Python, PostgreSQL, dbt.", "€64,000 – €76,000 per year", 2),
    ("Backend Engineer, Ticketing", "Zephyr Rail", "Paris, France", "on-site",
     "Python and Django. Office in Paris; French is a plus.", None, 6),
    ("Platform Engineer", "Oakridge Mobility", "Prague, Czechia", "hybrid",
     "Kubernetes and Python. Hybrid in Prague.", None, 4),
    ("Senior Django Engineer", "Parsec Freight", "Remote", "remote",
     "Remote within Europe. Django, PostgreSQL, Celery.", "€72,000 – €88,000 per year", 3),
    ("API Developer", "Juniper Quay", "Brussels, Belgium", None,
     "REST APIs in Python for public-sector clients.", None, 8),
    ("Full-Stack Engineer", "Tidewater Systems", "Leipzig, Germany", "on-site",
     "TypeScript, React and Python. Office in Leipzig.", "€52,000 – €62,000 per year", 11),
    ("Python Engineer", "Fernhill Software", "Remote", "remote",
     "Remote, anywhere in the EU. Python, FastAPI, AWS.", "€60,000 – €70,000 per year", 12),
]
# Applications, by index into JOBS: the stages reached after "applied", and how many days ago it was applied for.
APPLIED = {13: ([], 3), 4: (["hr_interview"], 12), 6: (["hr_interview", "technical_interview"], 18),
           10: (["rejected"], 20), 19: (["hr_interview", "technical_interview", "final_interview", "offer"], 30)}
SAVED = (0, 1, 22)
HIDDEN = (11, 16)


def build_demo(db_path: Path, today: date) -> None:
    engine = make_engine(db_path)
    init_db(engine)
    sf = make_session_factory(engine)
    now = datetime.combine(today, time(8, 0))
    with sf() as s:
        save_profile(s, PROFILE, now)
        save_preferences(s, replace(load_preferences(s), skills=SKILLS), now)
        listings = [Listing(name="Python backend", keywords="python developer", location="Berlin, Germany",
                            radius_km=50, created_at=now - timedelta(days=30)),
                    Listing(name="Data engineering", keywords="data engineer", location="Germany",
                            created_at=now - timedelta(days=30))]
        s.add_all(listings)
        run = Run(trigger="scheduled", started_at=now, finished_at=now + timedelta(minutes=3), status="ok")
        s.add(run)
        s.commit()
        jobs = []
        for n, (title, company, location, workplace, text, pay, days) in enumerate(JOBS, start=1):
            seen = now - timedelta(days=0 if days <= 3 else days - 3, hours=n % 5)  # a full report for today
            job = Job(external_id=f"demo-{n}", match_key=match_key(title, company), first_seen_at=seen,
                      title=title, company=company, location=location, url=f"https://example.com/jobs/{n}",
                      posted_at=today - timedelta(days=days), workplace=workplace, salary_source_text=pay,
                      description=text, description_fetched_at=seen, language="en")
            s.add(job)
            jobs.append(job)
        s.commit()
        for i, job in enumerate(jobs):
            listing = listings[i % 2]
            s.add(Match(run_id=run.id, listing_id=listing.id, job_id=job.id, is_new=job.first_seen_at.date() == today))
        for listing in listings:
            mine = [j for i, j in enumerate(jobs) if listings[i % 2] is listing]
            s.add(RunListing(run_id=run.id, listing_id=listing.id, found=len(mine),
                             new=sum(j.first_seen_at.date() == today for j in mine)))
        s.commit()
        checks.refresh_all(s)
        for i in SAVED:
            set_job_status(s, jobs[i].id, "interested", now)
        for i in HIDDEN:
            set_job_status(s, jobs[i].id, "hidden", now)
        for i, (stages, days_ago) in APPLIED.items():
            applied_on = today - timedelta(days=days_ago)
            set_job_status(s, jobs[i].id, "applied", datetime.combine(applied_on, time(9, 0)))
            for k, stage in enumerate(stages, start=1):
                on = min(applied_on + timedelta(days=4 * k), today)
                add_step(s, jobs[i].id, stage, on, "", today, datetime.combine(on, time(9, 0)))
    engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/demo_data.py <path/to/new.db>")
    target = Path(sys.argv[1])
    if target.exists():
        sys.exit(f"{target} exists; give a new file name")
    build_demo(target, date.today())
    print(f"Demo database written to {target}")
