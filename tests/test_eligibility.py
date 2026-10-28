from dataclasses import replace

import pytest

from jobhunt.eligibility import DEFAULT_PROFILE, assess
from jobhunt.models import Job


def job(location="Spain", description="", workplace="remote", language="en", description_en=None, title="PHP Developer"):
    return Job(external_id="x", title=title, company="Acme", location=location, url="u", workplace=workplace,
               language=language, description=description, description_en=description_en)


def decide(profile=DEFAULT_PROFILE, **kw):
    a = assess(job(**kw), profile)
    return a.verdict, a.reasons[0]["text"], [r["text"] for r in a.reasons]


CORPUS = [  # real sentences from the live jobs, then look-alikes
    (dict(location="Portugal", description="🌍 **100% Remote | Must be based in Portugal**"), "relocate", "Must be based in Portugal"),
    (dict(location="Ireland", description="Complete remote work flexibility (candidates must be based in Ireland)"),
     "relocate", "Must be based in Ireland"),
    (dict(location="Thessaloniki, Central Macedonia, Greece", description="EU timezone, and the right to work in the EU"),
     "eligible", "European time-zone window"),
    (dict(location="Vilnius, Vilniaus, Lithuania", description="with our WFA policy, you can work from almost anywhere in the world;"),
     "eligible", "Open worldwide"),
    (dict(location="Romania", description="Your working hours should overlap with the team between 10:00 and 19:00 Paris time (CET, UTC+1; CEST, UTC+2 in summer)"),
     "eligible", "European time-zone window"),
    (dict(location="Warsaw, Mazowieckie, Poland", description="* **Full remote position** under a B2B contract"),
     "check", "Remote in Poland — policy not stated"),
    (dict(location="Germany", language="de", description="Sehr gute Deutschkenntnisse (C1) sind erforderlich",
          description_en="Very good German skills (C1) are required"), "not_eligible", "Requires German"),
    (dict(location="Germany", language="de", description="Fließende Deutschkenntnisse in Wort und Schrift"),
     "not_eligible", "Requires German"),  # not translated yet
    (dict(location="London, England, UK", description="Remote role with great benefits"),
     "not_eligible", "Job in the UK — no work rights there"),
    (dict(location="UK", description="We hire remotely anywhere in Europe."), "eligible", "Open to anywhere in Europe"),
    (dict(location="Spain", description="Candidates must have the right to work in the UK."),
     "not_eligible", "Requires work rights in the UK"),
    (dict(location="Spain", description="You must be based in the EU."), "relocate", "Must be based in the EU"),
    (dict(location="Spain", description="Must be based in the EU or the UK."), "relocate", "Must be based in the EU"),
    (dict(location="European Union", description="Fully remote."), "relocate", "Remote within the EU"),
    (dict(location="EMEA"), "eligible", "Remote in EMEA"),
    (dict(location="Europe"), "eligible", "Remote in Europe"),
    (dict(location="Belgrade, Serbia", workplace="on-site"), "eligible", "Job in Serbia"),
    (dict(location="Spain", workplace="hybrid"), "relocate", "Hybrid in Spain"),
    (dict(location="Spain", workplace=None), "check", "Workplace unknown"),
    (dict(location="Spain", description="Remote. We hire in Poland, Serbia and Romania."), "eligible", "Open to Serbia"),
    (dict(location="Germany", description="Must be a German citizen."), "not_eligible", "Requires German citizenship"),
    (dict(location="Netherlands", description="Security clearance required."), "not_eligible", "Requires security clearance"),
    # found in the accuracy pass on the live jobs
    (dict(location="Vilnius, Vilniaus, Lithuania", workplace=None,
          description="Workation: work from anywhere in the EU for up to 30 days per year"), "check", "Workplace unknown"),
    (dict(location="Berlin, Germany", workplace="hybrid", description="Work from anywhere in the world for 30 days per year"),
     "relocate", "Hybrid in Germany"),
    (dict(location="UK", description="🌍 **Work from anywhere** - Enjoy the freedom of remote work within the country of employment"),
     "not_eligible", "Job in the UK — no work rights there"),
    (dict(location="Italy", description="Work from anywhere."), "check", "Remote in Italy — policy not stated"),
    (dict(location="Thessaloniki, Central Macedonia, Greece", workplace="hybrid",
          description="EU timezone, and the right to work in the EU"), "relocate", "Hybrid in Greece"),
    (dict(location="Warsaw, Mazowieckie, Poland", workplace="hybrid",
          description="We welcome applications from candidates based in Spain or Poland."), "relocate", "Must be based in Poland"),
    (dict(location="Spain", description="Knowledge of French is required for positions permanently located in Quebec, "
                                        "so incumbents can communicate with their colleagues. French-language training "
                                        "is offered to all incumbents who do not have a good knowledge of French."),
     "check", "Remote in Spain — policy not stated"),
    # look-alikes that must not trigger anything beyond the location
    (dict(location="Spain", description="Experience in a B2B SaaS company"), "check", "Remote in Spain — policy not stated"),
    (dict(location="France", description="**Timezone:** France (CET)"), "check", "Remote in France — policy not stated"),
    (dict(location="Spain", description="Experience with JavaScript and the German market"), "check", "Remote in Spain — policy not stated"),
    (dict(location="Spain", description="We are a Berlin-based company with an office in London."), "check", "Remote in Spain — policy not stated"),
]


@pytest.mark.parametrize("kw, verdict, reason", CORPUS)
def test_corpus(kw, verdict, reason):
    assert decide(**kw)[:2] == (verdict, reason)


def test_notes():
    assert "B2B contract — may allow working from Serbia, ask" in decide(
        location="Warsaw, Mazowieckie, Poland", description="* **Full remote position** under a B2B contract")[2]
    assert "B2B contract — may allow working from Serbia, ask" not in decide(description="Experience in a B2B SaaS company")[2]
    assert "Employer of record — may allow working from Serbia, ask" in decide(description="We employ via Deel.")[2]
    assert "You have EU work rights" in decide(location="Spain", description="Right to work in the EU required.")[2]


def test_downgrades_to_check():
    verdict, first, _ = decide(location="Germany", language="de", description="Arbeite von überall in Europa",
                               description_en="Work from anywhere in Europe")
    assert (verdict, first) == ("check", "Listing is in German — German may be required")
    verdict, first, texts = decide(location="Netherlands", description="Work from anywhere in Europe; overlap with US Eastern hours.")
    assert (verdict, first) == ("check", "Works US hours") and "Open to anywhere in Europe" in texts


def test_profile_changes_the_verdict():
    german = replace(DEFAULT_PROFILE, languages=DEFAULT_PROFILE.languages + ("de",))
    assert decide(german, location="Germany", language="de", description="Sehr gute Deutschkenntnisse (C1)",
                  description_en="Very good German skills (C1)")[0] == "check"
    serbian_only = replace(DEFAULT_PROFILE, work_rights=(), relocate_to=())
    assert decide(serbian_only, location="Portugal", description="Must be based in Portugal")[:2] == \
        ("not_eligible", "Must be based in Portugal")
    uk = replace(DEFAULT_PROFILE, work_rights=("EU", "UK"))
    assert decide(uk, location="UK", description="Remote role")[:2] == ("check", "Remote in the UK — policy not stated")
