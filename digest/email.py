from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from digest.constants import ALL_SECTIONS


def article_count(digest: dict) -> int:
    return sum(len(digest.get(section, [])) for section in ALL_SECTIONS)


def render_html(template_path: Path, digest: dict, context: dict) -> str:
    environment = Environment(
        loader=FileSystemLoader(template_path.parent),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment.get_template(template_path.name).render(digest=digest, **context)


def render_text(digest: dict, title: str, date_label: str) -> str:
    lines = [title, date_label, ""]
    if digest.get("empty_reason"):
        return "\n".join(lines + ["Nothing new today.", digest["empty_reason"]])
    if digest.get("tldr"):
        lines.extend(["TODAY AT A GLANCE", *[f"- {item}" for item in digest["tldr"]], ""])
    for section in ALL_SECTIONS:
        items = digest.get(section, [])
        if not items:
            continue
        lines.extend([section.replace("_", " ").upper()])
        for item in items:
            lines.append(f"- {item.get('title', 'Untitled')}: {item.get('what', '')}")
            lines.append(f"  {item.get('url', '')}")
            lines.extend(f"  • {bullet}" for bullet in item.get("bullets", []))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def format_subject(digest: dict, digest_name: str, date_label: str) -> str:
    custom = digest.get("subject_line", "").strip()
    return f"{custom or digest_name} — {date_label}"


def send_email(config: dict, subject: str, html: str, plain_text: str, password: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["from"]
    message["To"] = ", ".join(config["to"])
    message.set_content(plain_text)
    message.add_alternative(html, subtype="html")

    with smtplib.SMTP(config["smtp_host"], config["smtp_port"]) as server:
        server.starttls()
        server.login(config["from"], password)
        server.send_message(message)
