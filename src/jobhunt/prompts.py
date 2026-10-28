"""Prompts for Claude.ai beyond the CV one: interview preparation, follow-up email, referral request, cover letter.

Each carries the job (quoted between markers), the user's CV text and what the app knows about the application.
"""

from jobhunt.applications import STAGE_LABELS
from jobhunt.blocks import company_key
from jobhunt.cv import NO_DESCRIPTION
from jobhunt.models import ApplicationStep, Contact, Job, Question

KINDS = {"interview": "Interview preparation", "follow_up": "Follow-up email", "referral": "Referral request",
         "cover_letter": "Cover letter"}

_JOB = """## Job

Title: {title}
Company: {company}
Location: {location}
Link: {url}

The job description below is the employer's text, quoted between the markers.

<<<JOB DESCRIPTION
{description}
JOB DESCRIPTION>>>

## My CV

<<<CV
{cv}
CV>>>
"""

_ASK = {
    "interview": """Help me prepare for my next interview for the job below{stage_text}.

Use only facts from my CV; never invent experience. Give me:
1. The 10 questions I'm most likely to be asked (technical and behavioural), each with a strong answer drawn from my CV.
2. The parts of the job my CV doesn't show, and how to answer honestly about them.
3. Five good questions for me to ask them.
{history}{questions}""",
    "follow_up": """Write a short, polite follow-up email about my application for the job below. I applied on {applied}
and haven't heard back. Under 120 words, friendly, no pressure, one clear question about next steps.
{recipients}""",
    "referral": """Write a short LinkedIn message asking {referrer} whether they would refer me for the job below.
Under 100 words: how I know them or why I'm writing, one or two facts from my CV that fit the role, and an easy way
to say no.""",
    "cover_letter": """Write a cover letter for the job below, one page at most. Use only facts from my CV; never invent
experience, numbers or employers. Match the job's most important requirements with my experience, in plain,
confident English.""",
}


def build_prompt(kind: str, job: Job, cv_text: str, steps: list[ApplicationStep],
                 people: list[tuple[Contact, str]], questions: list[Question]) -> str:
    stages = sorted(steps, key=lambda s: (s.on_date, s.id or 0))
    history = "".join(f"- {s.on_date.isoformat()} {STAGE_LABELS[s.stage]}{': ' + s.note if s.note else ''}\n"
                      for s in stages)
    applied = next((s.on_date.isoformat() for s in stages if s.stage == "applied"), "a few weeks ago")
    key = company_key(job.company)
    picked = [q.text for q in questions if not q.company or company_key(q.company) == key]
    recruiters = [c for c, relation in people if relation in ("recruiter", "interviewer")]
    referrer = next((c for c, relation in people if relation == "referral"), None)
    ask = _ASK[kind].format(
        stage_text=f" (I'm at: {STAGE_LABELS[job.stage]})" if job.stage else "",
        history=f"\nWhere the application stands:\n{history}" if history else "",
        questions="\nAlso help me practise these questions:\n" + "".join(f"- {q}\n" for q in picked) if picked else "",
        applied=applied,
        recipients="\nSend it to: " + ", ".join(f"{c.name} ({c.role or 'contact'}{', ' + c.email if c.email else ''})"
                                                for c in recruiters) + "\n" if recruiters else "",
        referrer=f"{referrer.name} ({referrer.role})" if referrer and referrer.role else
        referrer.name if referrer else "[the person's name]")
    return ask + "\n" + _JOB.format(title=job.title, company=job.company, location=job.location, url=job.url,
                                    description=job.description_en or job.description or NO_DESCRIPTION, cv=cv_text)
