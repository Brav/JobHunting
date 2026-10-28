from fastapi.testclient import TestClient

from jobhunt.preferences import load_preferences

PREFS = {"skills": "PHP", "follow_up_days": "7", "auto_close_days": "30", "goal_applications_per_week": "5",
         "goal_reviews_per_week": "50", "ntfy_url": "https://ntfy.sh/attacker"}


def test_other_sites_cannot_change_or_read_anything(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        r = c.post("/settings/preferences", data=PREFS, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        with app.state.session_factory() as s:
            assert load_preferences(s).ntfy_url == ""
        assert c.get("/data/export.zip", headers={"Host": "evil.example"}).status_code == 400  # DNS rebinding
        assert c.get("/", headers={"Host": "127.0.0.1:8765"}).status_code == 200
        assert c.get("/", headers={"Host": "[::1]:8765"}).status_code == 200
        # The app's own pages, and the bookmarklet posting a job page into the confirm form, still work.
        ok = c.post("/settings/preferences", data={**PREFS, "ntfy_url": ""}, headers={"Origin": "http://testserver"},
                    follow_redirects=False)
        assert ok.status_code == 303
        assert c.post("/capture", data={"url": "", "title": "Dev"}, headers={"Origin": "https://jobs.example"}).status_code == 200
        assert c.post("/capture/save", data={"token": app.state.form_token, "title": "Dev", "company": "Acme",
                                             "url": "https://jobs.example/1"},
                      headers={"Origin": "https://jobs.example"}).status_code == 403
