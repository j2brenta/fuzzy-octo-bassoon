"""What `refresher serve` answers: its pages and their files, the policy each is served under, the API paths and
the size of a body it takes."""
import re

from refresher import graphview
from refresher.web import STATIC

MAX_DECISION = 16 * 1024
MAX_REQUEST = 29 * 1024 * 1024
DECIDE = re.compile(r"^/api/proposals/(p-[0-9a-f]{16})$")
SUBMISSION = re.compile(r"^/api/submissions/([a-f0-9]{32})$")
RETRY = re.compile(r"^/api/submissions/([a-f0-9]{32})/retry$")
UPLOAD = re.compile(r"^/api/uploads/([a-f0-9]{64})$")
SOURCE = re.compile(r"^/api/source/([\w.:-]{1,200})/(context|state)$")
BRIEF_SOURCE = re.compile(r"^/api/brief/source/([\w.:-]{1,200})$")
THREAD = re.compile(r"^/api/threads/([\w-]{1,80})(?:/(stamp|snapshots)(?:/(\d{8}T\d{6}Z))?)?$")
HTML, CSS, JS = "text/html; charset=utf-8", "text/css; charset=utf-8", "text/javascript; charset=utf-8"
FILES = {
    "/": (STATIC / "topics.html", HTML),
    "/library/": (STATIC / "library.html", HTML),
    "/timeline/": (STATIC / "timeline.html", HTML),
    "/static/timeline.js": (STATIC / "timeline.js", JS),
    "/example/": (STATIC / "example.html", HTML),
    "/static/library.js": (STATIC / "library.js", JS),
    "/static/journey.js": (STATIC / "journey.js", JS),
    "/static/journey.css": (STATIC / "journey.css", CSS),
    "/static/brain.js": (STATIC / "brain.js", JS),
    "/static/brain.css": (STATIC / "brain.css", CSS),
    "/topic/": (STATIC / "dossier.html", HTML),
    "/brief/": (STATIC / "context.html", HTML),
    "/static/context.css": (STATIC / "context.css", CSS),
    "/static/brief.css": (STATIC / "brief.css", CSS),
    "/static/reading.css": (STATIC / "reading.css", CSS),
    "/static/navigator.css": (STATIC / "navigator.css", CSS),
    "/static/topics.js": (STATIC / "topics.js", JS),
    "/static/cytoscape.min.js": (graphview.ASSETS / "cytoscape.min.js", JS),
    "/capture/": (STATIC / "index.html", HTML),
    "/capture/app.css": (STATIC / "app.css", CSS),
    "/capture/app.js": (STATIC / "app.js", JS),
    "/capture/brain.png": (STATIC / "brain.png", "image/png"),
    "/atlas/people.js": (graphview.ASSETS / "map" / "people.js", JS),
    "/atlas/style.css": (graphview.ASSETS / "map" / "style.css", CSS),
    "/atlas/cytoscape.min.js": (graphview.ASSETS / "cytoscape.min.js", JS),
}
for _part in ("core", "ideas", "versions", "area", "source", "side", "reading", "who", "main"):  # the brief, in load order
    FILES[f"/static/brief/{_part}.js"] = (STATIC / "brief" / f"{_part}.js", JS)
for _part in ("core", "graph", "map", "results", "reading", "text", "panel", "main"):  # the topic page, in load order
    FILES[f"/static/topic/{_part}.js"] = (STATIC / "topic" / f"{_part}.js", JS)
for _name in graphview.ATLAS_SCRIPTS:
    FILES["/atlas/" + _name] = (graphview.ASSETS / "map" / _name, JS)
for _name in ("people.js", *graphview.ATLAS_SCRIPTS, "style.css", "cytoscape.min.js"):  # Review is the Atlas page in its review layout
    FILES["/review/" + _name] = FILES["/atlas/" + _name]
for _page in ("/static/", "/capture/", "/atlas/", "/review/"):  # the shared shell sits beside every page, as in wiki/graph/
    FILES[_page + "shell.css"] = (STATIC / "shell.css", CSS)
    FILES[_page + "nav.js"] = (STATIC / "nav.js", JS)
BASE_CSP = "default-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; " \
           "base-uri 'none'; form-action 'self'"
CSP = {"page": BASE_CSP + "; style-src 'self'; frame-ancestors 'none'",
       # Cytoscape and the map set inline styles; a dossier embeds the map from the same origin
       "atlas": BASE_CSP + "; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'",
       "topic": BASE_CSP + "; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"}  # its graphs are Cytoscape
