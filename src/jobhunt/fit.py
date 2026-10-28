"""How well a job's stack matches the user's skills, and the best-first ranking.

The vocabulary lists technologies with their spellings; a job's terms are those found as whole words. The user's
skills default to the vocabulary terms in their CV (read only) and are edited in Settings.
"""

import re

# (name, pattern, case sensitive?) — case-sensitive where the lower-case word is ordinary English ("go", "swift").
_VOCABULARY = [
    ("PHP", r"PHP", False), ("Python", r"Python", False), ("JavaScript", r"JavaScript|JS(?=\b)", False),
    ("TypeScript", r"TypeScript|TS(?=\b)", True), ("Java", r"Java(?!\s?Script)", False), ("Kotlin", r"Kotlin", False),
    ("Go", r"Golang|(?<=[,/(])\s?Go(?=\s?[,/)])|\bGo(?=\s+(?:developer|engineer|backend|services|microservices)\b)", True),
    ("Rust", r"Rust", True), ("C#", r"C#", False), ("C++", r"C\+\+", False), ("Ruby", r"Ruby", True),
    ("Scala", r"Scala", True), ("Swift", r"Swift", True), ("Elixir", r"Elixir", True), ("Perl", r"Perl", True),
    ("Laravel", r"Laravel", False), ("Symfony", r"Symfony", False), ("Laminas", r"Laminas|Zend(?:\s+Framework)?", False),
    ("Yii", r"Yii2?", False), ("CodeIgniter", r"CodeIgniter", False), ("CakePHP", r"CakePHP", False),
    ("Drupal", r"Drupal", False), ("WordPress", r"WordPress", False), ("Magento", r"Magento", False),
    ("Shopware", r"Shopware", False), ("Livewire", r"Livewire", False), ("Inertia", r"Inertia(?:\.js)?", True),
    ("Django", r"Django", False), ("Flask", r"Flask", True), ("FastAPI", r"FastAPI", False),
    ("Rails", r"Ruby\s+on\s+Rails|Rails", True), ("Spring", r"Spring(?:\s+Boot)?", True), (".NET", r"(?:ASP)?\.NET|dotnet", False),
    ("Node.js", r"Node\.?js", False), ("Express", r"Express\.?js", False), ("NestJS", r"Nest\.?js", False),
    ("Next.js", r"Next\.?js", False), ("Nuxt", r"Nuxt(?:\.?js)?", False), ("React Native", r"React\s+Native", False),
    ("React", r"React(?:\.?js)?(?!\s+Native)", False), ("Vue", r"Vue(?:\.?js)?", False), ("Angular", r"Angular(?:JS)?", False),
    ("Svelte", r"Svelte(?:Kit)?", False), ("jQuery", r"jQuery", False), ("Alpine.js", r"Alpine\.?js", False),
    ("Tailwind", r"Tailwind(?:\s?CSS)?", False), ("Bootstrap", r"Bootstrap", True), ("htmx", r"htmx", False),
    ("HTML", r"HTML5?", True), ("CSS", r"CSS3?|SCSS|Sass", True), ("Flutter", r"Flutter", False),
    ("iOS", r"iOS", True), ("Android", r"Android", True),
    ("MySQL", r"MySQL", False), ("PostgreSQL", r"PostgreSQL|Postgres", False), ("MariaDB", r"MariaDB", False),
    ("SQLite", r"SQLite", False), ("SQL Server", r"SQL\s+Server|MSSQL", False), ("Oracle", r"Oracle(?:\s+DB)?", True),
    ("MongoDB", r"MongoDB|Mongo", False), ("Redis", r"Redis", False), ("Elasticsearch", r"Elastic\s?search|OpenSearch", False),
    ("DynamoDB", r"DynamoDB", False), ("Cassandra", r"Cassandra", True), ("ClickHouse", r"ClickHouse", False),
    ("Neo4j", r"Neo4j", False), ("SQL", r"SQL(?!\s+Server)", True), ("NoSQL", r"NoSQL", False),
    ("RabbitMQ", r"RabbitMQ", False), ("Kafka", r"Kafka", False), ("SQS", r"SQS", True),
    ("AWS", r"AWS|Amazon\s+Web\s+Services", True), ("Azure", r"Azure", True), ("GCP", r"GCP|Google\s+Cloud", True),
    ("Docker", r"Docker", False), ("Kubernetes", r"Kubernetes|K8s", False), ("Terraform", r"Terraform", False),
    ("Ansible", r"Ansible", False), ("Helm", r"Helm", True), ("Jenkins", r"Jenkins", True),
    ("GitHub Actions", r"GitHub\s+Actions", False), ("GitLab CI", r"GitLab(?:\s+CI)?", False), ("CI/CD", r"CI\s?/\s?CD", False),
    ("Linux", r"Linux", False), ("Nginx", r"Nginx", False), ("Apache", r"Apache", True),
    ("REST", r"REST(?:ful)?(?:\s+APIs?)?", True), ("GraphQL", r"GraphQL", False), ("gRPC", r"gRPC", False),
    ("WebSockets", r"WebSockets?", False), ("OpenAPI", r"OpenAPI|Swagger", False),
    ("PHPUnit", r"PHPUnit", False), ("Pest", r"Pest(?:PHP)?", True), ("Jest", r"Jest", True), ("Cypress", r"Cypress", True),
    ("Playwright", r"Playwright", False), ("Selenium", r"Selenium", False), ("TDD", r"TDD|test-driven", False),
    ("Microservices", r"micro-?services", False), ("DDD", r"DDD|domain-driven\s+design", False), ("CQRS", r"CQRS", False),
    ("Event sourcing", r"event\s+sourcing", False), ("Git", r"Git(?!Hub|Lab)", True), ("Composer", r"Composer", True),
    ("Webpack", r"Webpack", False), ("Vite", r"Vite", True), ("Agile", r"Agile", True), ("Scrum", r"Scrum", False),
    ("LLM", r"LLMs?|large\s+language\s+models?", False), ("OpenAI", r"OpenAI", False), ("LangChain", r"LangChain", False),
    ("RAG", r"RAG", True), ("Machine learning", r"machine\s+learning|\bML\b", False), ("Stripe", r"Stripe", True),
    ("Shopify", r"Shopify", False), ("Salesforce", r"Salesforce", False), ("SAP", r"SAP", True),
]
VOCABULARY = [(name, re.compile(r"(?<![\w.#+])(?:" + pattern + r")(?![\w#+])", 0 if case else re.IGNORECASE))
              for name, pattern, case in _VOCABULARY]
_ORDER = {name: i for i, (name, _) in enumerate(VOCABULARY)}


def terms_in(text: str | None) -> set[str]:
    return {name for name, pattern in VOCABULARY if text and pattern.search(text)}


def default_skills(cv_text: str) -> tuple[str, ...]:
    """The vocabulary terms in the CV, in vocabulary order."""
    return tuple(sorted(terms_in(cv_text), key=_ORDER.get))


def fit(job_text: str | None, skills) -> tuple[list[str], list[str], int | None]:
    """(terms the user has, terms they don't, percent matched) for the job's stack; score None with no terms."""
    have = {s.casefold() for s in skills}
    found = sorted(terms_in(job_text))
    matched = [t for t in found if t.casefold() in have]
    missing = [t for t in found if t.casefold() not in have]
    return matched, missing, round(100 * len(matched) / len(found)) if found else None


_ELIGIBILITY_RANK = {"eligible": 3, "relocate": 2, "check": 1, "not_eligible": 0}


def rank(job) -> int:
    """Best first: eligibility, then how excited the user is, then fit."""
    verdict = job.eligibility_override or job.eligibility or "check"
    return _ELIGIBILITY_RANK.get(verdict, 1) * 1000 + (job.excitement or 0) * 101 + (job.fit_score or 0)
