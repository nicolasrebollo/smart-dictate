"""Canonical spellings of well-known software, products, companies and acronyms.

Applied deterministically (after the LLM, and in raw mode): case-insensitive, whole word,
and never inside URLs, emails, paths or identifiers ("github.com" stays lowercase). Only
*unambiguous* names are listed - ordinary English words that are also product names
(react, slack, notion, zoom, excel, swift, rust, go, edge, teams, intel...) are left to
the LLM, which can tell from context.
"""

from __future__ import annotations

import re
from functools import lru_cache

_CANONICAL = """
GitHub GitLab Bitbucket GitHub Actions GitLab CI
PostgreSQL Postgres MySQL MongoDB SQLite MariaDB Redis Elasticsearch Kafka GraphQL DynamoDB
Supabase Firebase Snowflake Databricks BigQuery pgvector ClickHouse Cassandra
Kubernetes Docker Terraform Ansible Jenkins Grafana Datadog Cloudflare Vercel Netlify Heroku
PyTorch TensorFlow NumPy SciPy scikit-learn Jupyter JupyterLab Matplotlib Keras Hugging Face
Django FastAPI Laravel Svelte Next.js Node.js Vue.js Nuxt Express.js
JavaScript TypeScript Kotlin Haskell Java PHP Golang Fortran COBOL
ChatGPT OpenAI Anthropic DeepSeek Mistral Perplexity Midjourney LangChain LlamaIndex Ollama
iPhone iPad iMac iOS iPadOS macOS MacBook AirPods watchOS Xcode FaceTime iCloud
Android Linux Ubuntu Debian Windows 11
Microsoft Google YouTube LinkedIn Facebook Instagram WhatsApp TikTok Snapchat Pinterest Reddit
Netflix Spotify Uber Lyft Airbnb PayPal eBay Shopify Salesforce HubSpot Zendesk Atlassian Jira
Trello Asana Figma Canva Dropbox OneDrive SharePoint PowerPoint Gmail WeChat
Google Docs Google Sheets Google Slides Google Drive Google Maps Google Meet Microsoft Teams
VS Code Visual Studio Code Visual Studio IntelliJ PyCharm WebStorm Android Studio
NVIDIA AMD Qualcomm Samsung Huawei IBM Tesla SpaceX Amazon AWS GCP
PlayStation Xbox Nintendo Bluetooth Wi-Fi USB GPU CPU SSD HDMI LLM LLMs API APIs SDK SDKs
JSON YAML TOML HTML CSS SQL URL URLs HTTP HTTPS OAuth SaaS DevOps FFmpeg WordPress Twilio
"""

# Single tokens in the list above that are multi-word names are joined below; everything
# else is split on whitespace. Multi-word entries are matched with flexible spacing.
_MULTI = [
    "GitHub Actions",
    "GitLab CI",
    "Hugging Face",
    "Google Docs",
    "Google Sheets",
    "Google Slides",
    "Google Drive",
    "Google Maps",
    "Google Meet",
    "Microsoft Teams",
    "VS Code",
    "Visual Studio Code",
    "Visual Studio",
    "Android Studio",
    "Windows 11",
]

# Spoken / split forms that ASR commonly produces -> canonical.
_SPOKEN = {
    "chat gpt": "ChatGPT",
    "open ai": "OpenAI",
    "git hub": "GitHub",
    "git lab": "GitLab",
    "post gres": "Postgres",
    "postgres sql": "PostgreSQL",
    "postgre sql": "PostgreSQL",
    "my sql": "MySQL",
    "mongo db": "MongoDB",
    "sq lite": "SQLite",
    "pg vector": "pgvector",
    "java script": "JavaScript",
    "type script": "TypeScript",
    "next js": "Next.js",
    "node js": "Node.js",
    "vue js": "Vue.js",
    "nextjs": "Next.js",
    "nodejs": "Node.js",
    "py torch": "PyTorch",
    "tensor flow": "TensorFlow",
    "num py": "NumPy",
    "sci py": "SciPy",
    "fast api": "FastAPI",
    "i phone": "iPhone",
    "i pad": "iPad",
    "i mac": "iMac",
    "i o s": "iOS",
    "mac os": "macOS",
    "mac book": "MacBook",
    "air pods": "AirPods",
    "i cloud": "iCloud",
    "x code": "Xcode",
    "you tube": "YouTube",
    "power point": "PowerPoint",
    "one drive": "OneDrive",
    "share point": "SharePoint",
    "word press": "WordPress",
    "pay pal": "PayPal",
    "hub spot": "HubSpot",
    "vs code": "VS Code",
    "vscode": "VS Code",
    "wifi": "Wi-Fi",
    "wi fi": "Wi-Fi",
    "dev ops": "DevOps",
    "o auth": "OAuth",
    "lang chain": "LangChain",
    "deep seek": "DeepSeek",
    "click house": "ClickHouse",
    "big query": "BigQuery",
    "data dog": "Datadog",
    "cloud flare": "Cloudflare",
}


def _entries() -> dict[str, str]:
    table: dict[str, str] = {}
    text = _CANONICAL
    for m in _MULTI:
        table[m.lower()] = m
        text = text.replace(m, " ")
    for word in text.split():
        table[word.lower()] = word
    table.update(_SPOKEN)
    return table


TERMS: dict[str, str] = _entries()

# Characters that mean "this is part of a URL / email / path / identifier": don't touch.
_NOT_BEFORE = r"(?<![\w'’./@:\\_#-])"
_NOT_AFTER = r"(?![\w'’@\\_-])(?![./:][\w/])"


@lru_cache(maxsize=1)
def _pattern() -> re.Pattern[str]:
    keys = sorted(TERMS, key=len, reverse=True)
    alts = [r"[ \t]+".join(re.escape(part) for part in k.split()) for k in keys]
    return re.compile(_NOT_BEFORE + "(" + "|".join(alts) + ")" + _NOT_AFTER, re.IGNORECASE)


def known_terms() -> set[str]:
    """Lower-cased words that guardrails should treat as legitimate (not invented)."""
    out: set[str] = set()
    for canon in TERMS.values():
        out.update(w.lower() for w in re.findall(r"[\w.+-]+", canon))
    return out


def apply_terms(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        found = m.group(1)
        canon = TERMS[re.sub(r"\s+", " ", found.lower())]
        # Plural/possessive forms keep their tail; only fix the casing of the term itself.
        return canon

    return _pattern().sub(repl, text)
