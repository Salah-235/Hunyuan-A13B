"""Builds bank-assistant-openai.html: one file to open in Chrome, using the user's own OpenAI key."""
import json
import os

here = os.path.dirname(os.path.abspath(__file__))
parent = os.path.dirname(here)
read = lambda *p: open(os.path.join(*p), encoding="utf-8").read()

page = read(here, "template.html")
page = page.replace("<!--LOGO-->", read(parent, "logo.svg"))
page = page.replace("/*CORE*/", read(parent, "core.js").replace('if (typeof module !== "undefined") module.exports = Core;', ""))
samples = json.load(open(os.path.join(here, "samples.json"), encoding="utf-8"))
page = page.replace("/*SAMPLES*/", "const SAMPLE_DOCS = " + json.dumps(samples, ensure_ascii=False) + ";")
page = page.replace("/*APP*/", read(here, "app.js"))
out = os.path.join(here, "bank-assistant-openai.html")
open(out, "w", encoding="utf-8").write(page)
print(out, len(page.encode()))
