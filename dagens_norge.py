"""
Dagens Norge – Daily Norwegian News Digest
==========================================
Fetches headlines from free RSS feeds (NRK, VG, Aftenposten, Dagbladet, E24),
passes them to Claude Haiku for ranking and summarising,
and sends a bilingual HTML email via Resend.

No web search tool = near-zero API cost (~$0.01/day).

Setup:
    pip install requests

Environment variables (set as GitHub Actions secrets):
    ANTHROPIC_API_KEY   – your Anthropic API key
    RESEND_API_KEY      – your Resend API key
    RECIPIENT_EMAIL     – email address to send the digest to
"""

import os
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_HEADERS = {
    "x-api-key": ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}

RSS_FEEDS = [
    ("NRK",         "https://www.nrk.no/toppsaker.rss"),
    ("VG",          "https://www.vg.no/rss/feed/forsiden/"),
    ("Aftenposten", "https://www.aftenposten.no/rss"),
    ("Dagbladet",   "https://www.dagbladet.no/rss/nyheter/"),
    ("E24",         "https://e24.no/rss"),
]


# ── Step 1: Fetch RSS feeds ───────────────────────────────────────────────────

def fetch_rss_headlines():
    headlines = []
    for source, url in RSS_FEEDS:
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            # Handle both RSS and Atom namespaces
            items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            count = 0
            for item in items[:10]:  # max 10 per source
                title = (
                    item.findtext("title") or
                    item.findtext("{http://www.w3.org/2005/Atom}title") or ""
                ).strip()
                link = (
                    item.findtext("link") or
                    item.findtext("{http://www.w3.org/2005/Atom}link") or ""
                ).strip()
                description = (
                    item.findtext("description") or
                    item.findtext("{http://www.w3.org/2005/Atom}summary") or ""
                ).strip()
                if title:
                    headlines.append({
                        "source": source,
                        "title": title,
                        "link": link,
                        "description": description[:300],  # trim long descriptions
                    })
                    count += 1
            print(f"  {source}: {count} headlines")
        except Exception as e:
            print(f"  {source}: failed ({e})")
    return headlines


# ── Step 2: Rank and summarise with Haiku (no web search) ────────────────────

def summarise_with_claude(headlines):
    today = datetime.now().strftime("%-d. %B %Y")

    # Format headlines as a simple text list
    headlines_text = ""
    for i, h in enumerate(headlines, 1):
        headlines_text += f"{i}. [{h['source']}] {h['title']}"
        if h['description']:
            headlines_text += f" — {h['description']}"
        headlines_text += f" (URL: {h['link']})\n"

    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 3000,
        "system": (
            "You are a JSON serializer. You receive a list of Norwegian news headlines "
            "and output ONLY a raw JSON object — no preamble, no explanation, no markdown, "
            "no code fences. Your response starts with { and ends with }. "
            "Use only straight double quotes. Escape any double quotes inside strings with backslash."
        ),
        "messages": [{
            "role": "user",
            "content": (
                f"From this list of Norwegian news headlines from today ({today}), "
                "select the 6-8 most important stories and return ONLY this JSON:\n"
                '{"date":"' + today + '","stories":[{"rank":1,"headline":"norsk overskrift",'
                '"summaryNo":"2-3 norske setninger som forklarer hva som skjedde og hvorfor det er viktig.",'
                '"summaryEn":"2-3 English sentences explaining what happened and why it matters.",'
                '"category":"Politikk","sourceUrl":"https://...","sourceName":"NRK"}]}\n\n'
                "Categories must be one of: Politikk, Økonomi, Utenriks, Samfunn, Sport, Kultur, Helse, Teknologi\n"
                "Rank by importance, not by order in the list. Use the URL from the headline.\n\n"
                "Headlines:\n" + headlines_text
            )
        }]
    }

    response = requests.post(ANTHROPIC_URL, headers=ANTHROPIC_HEADERS, json=payload)
    response.raise_for_status()
    data = response.json()

    text = " ".join(
        block["text"] for block in data["content"] if block["type"] == "text"
    )

    start = text.index("{")
    end = text.rindex("}") + 1
    json_str = text[start:end]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        last = json_str.rfind("},")
        json_str = json_str[:last + 1] + "]}"
        return json.loads(json_str)


# ── Step 3: Build HTML email ──────────────────────────────────────────────────

CATEGORY_COLORS = {
    "Politikk": "#2563eb",
    "Økonomi": "#16a34a",
    "Utenriks": "#9333ea",
    "Samfunn": "#d97706",
    "Sport": "#dc2626",
    "Kultur": "#db2477",
    "Helse": "#0891b2",
    "Teknologi": "#4f46e5",
}

def build_html(news):
    today_nb = datetime.now().strftime("%-d. %B %Y")

    stories_html = ""
    for s in news["stories"]:
        color = CATEGORY_COLORS.get(s.get("category", ""), "#888")
        source_link = ""
        if s.get("sourceUrl"):
            source_link = (
                f'<tr><td style="padding-top:8px;">'
                f'<a href="{s["sourceUrl"]}" style="font-family:Arial,sans-serif;font-size:12px;'
                f'color:#c8a96e;text-decoration:none;border-bottom:1px solid #c8a96e;">'
                f'Les mer hos {s.get("sourceName", "kilden")} \u2192</a></td></tr>'
            )
        stories_html += f"""
        <tr><td style="padding:0 0 28px 0;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="padding-bottom:6px;">
              <span style="display:inline-block;font-family:Arial,sans-serif;font-size:10px;
                font-weight:700;color:#fff;background:{color};padding:2px 7px;border-radius:2px;
                text-transform:uppercase;letter-spacing:0.3px;">{s.get("category","")}</span>
              <span style="font-family:Arial,sans-serif;font-size:11px;color:#bbb;margin-left:6px;">
                #{s.get("rank","")}</span>
            </td></tr>
            <tr><td style="padding-bottom:10px;border-bottom:1px solid #f0ede8;">
              <span style="font-family:Georgia,serif;font-size:17px;font-weight:bold;
                color:#1a1a1a;line-height:1.35;">{s.get("headline","")}</span>
            </td></tr>
            <tr><td style="padding-top:10px;padding-bottom:4px;">
              <span style="font-family:Arial,sans-serif;font-size:11px;color:#aaa;
                text-transform:uppercase;letter-spacing:0.5px;">\U0001f1f3\U0001f1f4 Norsk</span>
            </td></tr>
            <tr><td style="padding-bottom:10px;">
              <span style="font-family:Arial,sans-serif;font-size:14px;color:#333;
                line-height:1.65;">{s.get("summaryNo","")}</span>
            </td></tr>
            <tr><td style="padding-bottom:4px;">
              <span style="font-family:Arial,sans-serif;font-size:11px;color:#aaa;
                text-transform:uppercase;letter-spacing:0.5px;">\U0001f1ec\U0001f1e7 English</span>
            </td></tr>
            <tr><td style="padding-bottom:10px;">
              <span style="font-family:Arial,sans-serif;font-size:14px;color:#555;
                line-height:1.65;font-style:italic;">{s.get("summaryEn","")}</span>
            </td></tr>
            {source_link}
          </table>
        </td></tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#faf9f7;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#faf9f7;padding:32px 16px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:4px;overflow:hidden;">
  <tr><td style="background:#1a1a1a;padding:28px 36px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td><div style="font-family:Georgia,serif;font-size:22px;color:#fff;font-weight:bold;">
        Dagens Norge</div>
        <div style="font-family:Arial,sans-serif;font-size:12px;color:#999;margin-top:4px;
          text-transform:uppercase;letter-spacing:1px;">{today_nb}</div>
      </td>
      <td align="right"><div style="font-family:Georgia,serif;font-size:32px;color:#c8a96e;">\u00a7</div></td>
    </tr></table>
  </td></tr>
  <tr><td style="background:#f5f2ed;padding:12px 36px;border-bottom:1px solid #e8e4dd;">
    <span style="font-family:Arial,sans-serif;font-size:12px;color:#888;">
      {len(news["stories"])} saker \u00b7 rangert etter viktighet, ikke publiseringstidspunkt</span>
  </td></tr>
  <tr><td style="padding:28px 36px 8px 36px;">
    <table width="100%" cellpadding="0" cellspacing="0">{stories_html}</table>
  </td></tr>
  <tr><td style="background:#f5f2ed;padding:20px 36px;border-top:1px solid #e8e4dd;">
    <span style="font-family:Arial,sans-serif;font-size:11px;color:#aaa;">
      Laget av Claude Haiku \u00b7 NRK, VG, Aftenposten, Dagbladet, E24</span>
  </td></tr>
</table></td></tr></table>
</body></html>"""


# ── Step 4: Send via Resend ───────────────────────────────────────────────────

def send_email(subject, html):
    payload = {
        "from": "Dagens Norge <onboarding@resend.dev>",
        "to": [RECIPIENT_EMAIL],
        "subject": subject,
        "html": html,
    }
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    response.raise_for_status()
    return response.json()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Step 1: Fetching RSS headlines...")
    headlines = fetch_rss_headlines()
    print(f"  Total: {len(headlines)} headlines from {len(RSS_FEEDS)} sources.")

    print("Step 2: Ranking and summarising with Claude Haiku...")
    news = summarise_with_claude(headlines)
    print(f"  Got {len(news['stories'])} stories.")

    print("Step 3: Building email...")
    today_nb = datetime.now().strftime("%-d. %B %Y")
    subject = f"Dagens Norge \u00b7 {today_nb}"
    html = build_html(news)

    print("Step 4: Sending email...")
    result = send_email(subject, html)
    print(f"  Email sent! ID: {result.get('id')}")


if __name__ == "__main__":
    main()
