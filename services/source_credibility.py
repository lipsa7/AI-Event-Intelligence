"""Source credibility (v6.30.3).

Settled rule (user, 2026-10-10): sources must be credible - "a random fb comment doesn't count".
Practice in the products reviewed: Klue and Crayon score and curate sources, AlphaSense
limits itself to vetted content, Feedly lets the user pick the sources it trusts, and
NewsGuard-style ratings sort outlets by kind.

Tiers, by domain:
- official: government, regulator, parliament, court, international body;
- research: university, journal, established think tank or research institute;
- news: established news organisation, wire or specialist trade press;
- professional: established law firm, consultancy, analyst or rating firm;
- primary: an organisation's own site (its own statements, about itself);
- press_release: paid press-release wires (the issuer's claim, never an expert view);
- blocked: social media, forums, video, user-generated pages - never used;
- unrated: anything else - usable as a reported fact, labelled, never as an expert view.

The user can extend the lists, Feedly-style: data/credible_sources.txt (lines "domain tier"
or just "domain" for news) and data/blocked_sources.txt (one domain per line).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

VERSION = "source-credibility-v1"

CREDIBLE_TIERS = frozenset({"official", "research", "news", "professional"})
TIER_LABELS = {
    "official": "official source", "research": "research institution", "news": "established news",
    "professional": "established firm", "primary": "the organisation's own site",
    "press_release": "press release", "blocked": "not credible", "unrated": "unrated source",
}

BLOCKED = frozenset("""
facebook.com fb.com fb.watch instagram.com threads.net x.com twitter.com t.co tiktok.com youtube.com youtu.be
reddit.com redd.it quora.com pinterest.com linkedin.com tumblr.com discord.com discord.gg telegram.org t.me
4chan.org 9gag.com vimeo.com dailymotion.com twitch.tv snapchat.com whatsapp.com wechat.com weibo.com
vk.com bilibili.com spotify.com podcasts.apple.com soundcloud.com rumble.com truthsocial.com gab.com
parler.com mastodon.social bsky.app scribd.com slideshare.net answers.com stackexchange.com
""".split())

PRESS_RELEASE = frozenset("""
prnewswire.com businesswire.com globenewswire.com einpresswire.com accesswire.com newswire.com
prweb.com openpr.com issuewire.com
""".split())

NEWS = frozenset("""
reuters.com apnews.com afp.com bloomberg.com ft.com wsj.com nytimes.com washingtonpost.com economist.com
theguardian.com bbc.com bbc.co.uk cnn.com cnbc.com nbcnews.com cbsnews.com abcnews.go.com npr.org pbs.org
axios.com politico.com politico.eu semafor.com theatlantic.com newyorker.com time.com fortune.com forbes.com
businessinsider.com latimes.com usatoday.com thehill.com vox.com propublica.org foreignpolicy.com
foreignaffairs.com theinformation.com theverge.com techcrunch.com wired.com arstechnica.com engadget.com
zdnet.com cnet.com venturebeat.com technologyreview.com spectrum.ieee.org theregister.com protocol.com
404media.co platformer.news fastcompany.com hbr.org sloanreview.mit.edu lawfaremedia.org lawfareblog.com
therecord.media darkreading.com securityweek.com bleepingcomputer.com krebsonsecurity.com cyberscoop.com
fedscoop.com nextgov.com defensescoop.com defenseone.com breakingdefense.com govtech.com statnews.com
abc.net.au smh.com.au theage.com.au afr.com theaustralian.com.au news.com.au itnews.com.au crn.com.au
innovationaus.com sbs.com.au insurancebusinessmag.com
reuters.co.uk independent.co.uk telegraph.co.uk thetimes.co.uk sky.com news.sky.com channel4.com
lemonde.fr lefigaro.fr spiegel.de faz.net handelsblatt.com dw.com elpais.com corriere.it nikkei.com
asia.nikkei.com japantimes.co.jp scmp.com straitstimes.com channelnewsasia.com aljazeera.com
thehindu.com hindustantimes.com indianexpress.com livemint.com economictimes.indiatimes.com
timesofindia.indiatimes.com business-standard.com moneycontrol.com thehindubusinessline.com ndtv.com
theprint.in scroll.in thewire.in medianama.com inc42.com yourstory.com
globeandmail.com cbc.ca theglobeandmail.com nzherald.co.nz rnz.co.nz
euractiv.com euobserver.com devex.com restofworld.org theconversation.com techpolicy.press
law360.com reuters.legal natlawreview.com bloomberglaw.com news.bloomberglaw.com iapp.org computerweekly.com
infosecurity-magazine.com csoonline.com computerworld.com siliconangle.com datacenterdynamics.com
theregister.co.uk semianalysis.com stratechery.com
""".split())

RESEARCH = frozenset("""
arxiv.org nature.com science.org ssrn.com pnas.org cell.com thelancet.com nejm.org acm.org ieee.org
brookings.edu csis.org rand.org cfr.org carnegieendowment.org chathamhouse.org piie.com bruegel.org
atlanticcouncil.org cnas.org cset.georgetown.edu hai.stanford.edu aiindex.stanford.edu epoch.ai
epochai.org ainowinstitute.org adalovelaceinstitute.org oii.ox.ac.uk governance.ai iiss.org lowyinstitute.org
aspi.org.au orfonline.org gatewayhouse.in carnegieindia.org itif.org newamerica.org cdt.org eff.org
epic.org belfercenter.org hoover.org aei.org cato.org heritage.org urban.org pewresearch.org
oecd.ai weforum.org mckinsey.com artificialintelligenceact.eu futureoflife.org alignmentforum.org
safe.ai metr.org apolloresearch.ai aisi.gov.uk cltc.berkeley.edu brennancenter.org lawfaremedia.org
techpolicy.stanford.edu cyber.harvard.edu berkman.harvard.edu
""".split())

PROFESSIONAL = frozenset("""
gartner.com forrester.com idc.com bcg.com bain.com deloitte.com pwc.com ey.com kpmg.com accenture.com
moodys.com spglobal.com fitchratings.com morningstar.com
lw.com skadden.com cliffordchance.com linklaters.com freshfields.com allenovery.com aoshearman.com
hoganlovells.com bakermckenzie.com dlapiper.com nortonrosefulbright.com kwm.com allens.com.au
minterellison.com corrs.com.au gtlaw.com.au ashurst.com herbertsmithfreehills.com wilmerhale.com
covington.com sidley.com mayerbrown.com whitecase.com jonesday.com cooley.com orrick.com fenwick.com
gibsondunn.com davispolk.com sullcrom.com kirkland.com cyrilshroff.com azbpartners.com trilegal.com
dlapiperdataprotection.com lexology.com jdsupra.com mondaq.com insideprivacy.com hunton.com
huntonprivacyblog.com bird-bird.com taylorwessing.com twobirds.com fieldfisher.com osborneclarke.com
""".split())

OFFICIAL = frozenset("""
europa.eu un.org who.int worldbank.org imf.org oecd.org wto.org itu.int bis.org fsb.org nato.int
iso.org nist.gov rbi.org.in sebi.gov.in pib.gov.in meity.gov.in parliament.uk legislation.gov.uk
aph.gov.au legislation.gov.au oaic.gov.au esafety.gov.au asd.gov.au cyber.gov.au homeaffairs.gov.au
congress.gov federalregister.gov whitehouse.gov sec.gov ftc.gov justice.gov supremecourt.gov
uscourts.gov gc.ca canada.ca govt.nz bundesregierung.de bundestag.de elysee.fr cnil.fr ico.org.uk
gov.sg mas.gov.sg
""".split())

PRIMARY = frozenset("""
openai.com anthropic.com blog.google deepmind.google ai.google about.fb.com ai.meta.com microsoft.com
blogs.microsoft.com nvidia.com nvidianews.nvidia.com apple.com amazon.com aboutamazon.com aws.amazon.com
ibm.com oracle.com salesforce.com mistral.ai cohere.com x.ai huggingface.co
""".split())

OFFICIAL_SUFFIXES = (".gov", ".mil", ".gouv.fr", ".bund.de", ".europa.eu", ".int", ".parliament.uk", ".gc.ca",
                     ".govt.nz", ".go.jp", ".go.kr", ".gov.au", ".gov.uk", ".gov.in", ".nic.in", ".gov.sg",
                     ".gov.cn", ".gob.mx", ".gob.es", ".gov.br", ".gov.za", ".gov.ie")
RESEARCH_SUFFIXES = (".edu", ".ac.uk", ".ac.in", ".edu.au", ".ac.jp", ".ac.nz", ".edu.sg", ".ac.za", ".edu.cn",
                     ".ac.il", ".ac.kr", ".uni-heidelberg.de", ".ox.ac.uk", ".cam.ac.uk")


def host_of(url_or_host: Any) -> str:
    text = str(url_or_host or "").strip().casefold()
    if not text:
        return ""
    host = urlparse(text).netloc if "://" in text else text.split("/")[0]
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, domains: frozenset[str] | set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _user_lists(root: Path | None = None) -> tuple[dict[str, str], set[str]]:
    root = root or Path(os.getenv("EVENT_INTELLIGENCE_DATA_DIR") or Path(__file__).resolve().parents[1] / "data")
    credible: dict[str, str] = {}
    blocked: set[str] = set()
    try:
        for line in (root / "credible_sources.txt").read_text(encoding="utf-8").splitlines():
            parts = line.split("#")[0].split()
            if parts:
                tier = parts[1].casefold() if len(parts) > 1 else "news"
                credible[host_of(parts[0])] = tier if tier in TIER_LABELS else "news"
    except OSError:
        pass
    try:
        for line in (root / "blocked_sources.txt").read_text(encoding="utf-8").splitlines():
            domain = host_of(line.split("#")[0].strip())
            if domain:
                blocked.add(domain)
    except OSError:
        pass
    return credible, blocked


def tier(url_or_host: Any, *, root: Path | None = None) -> str:
    host = host_of(url_or_host)
    if not host:
        return "unrated"
    user_credible, user_blocked = _user_lists(root)
    if _matches(host, user_blocked) or _matches(host, BLOCKED):
        return "blocked"
    for domain, level in sorted(user_credible.items(), key=lambda kv: -len(kv[0])):
        if host == domain or host.endswith("." + domain):
            return level
    if _matches(host, PRESS_RELEASE):
        return "press_release"
    if _matches(host, OFFICIAL) or host.endswith(OFFICIAL_SUFFIXES) or host in {s.lstrip(".") for s in OFFICIAL_SUFFIXES}:
        return "official"
    if _matches(host, RESEARCH) or host.endswith(RESEARCH_SUFFIXES):
        return "research"
    if _matches(host, PROFESSIONAL):
        return "professional"
    if _matches(host, NEWS):
        return "news"
    if _matches(host, PRIMARY):
        return "primary"
    return "unrated"


def is_credible(url_or_host: Any) -> bool:
    """Credible enough to count as an expert or organisation view."""
    return tier(url_or_host) in CREDIBLE_TIERS


def is_blocked(url_or_host: Any) -> bool:
    return tier(url_or_host) == "blocked"


def label(url_or_host: Any) -> str:
    return TIER_LABELS[tier(url_or_host)]


MEDIA_PATHS = ("/video/", "/videos/", "/podcast/", "/podcasts/", "/watch/", "/live/")


def is_media_page(url: Any) -> bool:
    """A video or podcast page: little or no article text to quote (e.g. techcrunch.com/video/...)."""
    path = urlparse(str(url or "")).path.casefold() if "://" in str(url or "") else ""
    return any(part in path for part in MEDIA_PATHS)


def page_tier(url: Any, site_title: Any = "") -> str:
    """Tier for a fetched page; a Google grounding redirect is rated by the site name it carries."""
    if "grounding-api-redirect" in str(url or "") and "." in str(site_title or ""):
        return tier(site_title)
    return tier(url)


def record_tier(record: Mapping[str, Any]) -> str:
    """The tier stored when the page was read (it knows the real site behind a redirect)."""
    stored = record.get("source_credibility") if isinstance(record.get("source_credibility"), Mapping) else {}
    level = str(stored.get("tier") or "")
    return level if level in TIER_LABELS else tier(record_url(record))


def record_url(record: Mapping[str, Any]) -> str:
    payload = record.get("structured_payload") if isinstance(record.get("structured_payload"), Mapping) else {}
    return str(record.get("source_url") or payload.get("source_url") or record.get("url") or "")
