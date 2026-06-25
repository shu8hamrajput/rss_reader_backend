"""
seed_upsc_collections.py
------------------------
Creates curated public collections for UPSC aspirants owned by the admin
account (first user with is_admin=True, or user_id=1 as fallback).

Run once on production:
  fly ssh console --app rss-reader-api -C "python scripts/seed_upsc_collections.py"

Or locally with the app venv active:
  python scripts/seed_upsc_collections.py
"""

import re
import sys
import os

# Allow running from repo root or scripts/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models import Collection, CollectionItem, User


# ── Catalogue ─────────────────────────────────────────────────────────────────

COLLECTIONS: list[dict] = [
    {
        "name": "UPSC — Daily Newspapers & Editorials",
        "slug": "upsc-daily-newspapers",
        "description": (
            "Must-read dailies for UPSC preparation: The Hindu, Indian Express, "
            "Livemint, and Business Standard editorials. Cover GS Paper 2 & 3 current affairs daily."
        ),
        "feeds": [
            ("https://www.thehindu.com/opinion/editorial/feeder/default.rss",         "The Hindu — Editorial",          None),
            ("https://indianexpress.com/section/opinion/editorials/feed/",             "Indian Express — Editorials",    None),
            ("https://www.thehindu.com/news/national/feeder/default.rss",              "The Hindu — National",           None),
            ("https://indianexpress.com/section/india/feed/",                          "Indian Express — India",         None),
            ("https://www.livemint.com/rss/opinion",                                   "Livemint — Opinion",             None),
            ("https://feeds.feedburner.com/bsindia/pq",                                "Business Standard — India",      None),
            ("https://www.thehindu.com/business/Economy/feeder/default.rss",           "The Hindu — Economy",            None),
            ("https://www.thehindu.com/sci-tech/feeder/default.rss",                   "The Hindu — Science & Tech",     None),
        ],
    },
    {
        "name": "UPSC — Government Sources (PIB & Ministries)",
        "slug": "upsc-government-sources",
        "description": (
            "Official government feeds: Press Information Bureau, Ministry of Finance, "
            "NITI Aayog, and Rajya Sabha proceedings. Primary source for policy & schemes."
        ),
        "feeds": [
            ("https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",                "PIB — All Ministries",           None),
            ("https://www.mea.gov.in/rss-feeds.htm?dtl/17037",                        "MEA — Press Releases",           None),
            ("https://www.india.gov.in/rss.xml",                                       "India.gov.in",                   None),
            ("https://finmin.nic.in/rss.xml",                                          "Ministry of Finance",            None),
            ("https://www.niti.gov.in/rss.xml",                                        "NITI Aayog",                     None),
            ("https://rbi.org.in/rss/RBINotifications.xml",                            "RBI — Notifications",            None),
            ("https://rbi.org.in/rss/RBIPressRelease.xml",                             "RBI — Press Releases",           None),
            ("https://sansad.in/rs/rss",                                               "Rajya Sabha",                    None),
        ],
    },
    {
        "name": "UPSC — Economy & Finance",
        "slug": "upsc-economy-finance",
        "description": (
            "Economic Survey, Union Budget, RBI policy, banking reforms, and macro-economy analysis. "
            "Essential for GS Paper 3 (Indian Economy, Agriculture, Infrastructure)."
        ),
        "feeds": [
            ("https://rbi.org.in/rss/RBIPressRelease.xml",                             "RBI — Press Releases",           None),
            ("https://rbi.org.in/rss/RBINotifications.xml",                            "RBI — Notifications",            None),
            ("https://www.livemint.com/rss/economy",                                   "Livemint — Economy",             None),
            ("https://feeds.feedburner.com/bsindia/economy",                           "Business Standard — Economy",    None),
            ("https://economictimes.indiatimes.com/rssfeedsdefault.cms",               "Economic Times",                 None),
            ("https://www.thehindu.com/business/Economy/feeder/default.rss",           "The Hindu — Economy",            None),
            ("https://www.financialexpress.com/economy/feed/",                         "Financial Express — Economy",    None),
            ("https://finmin.nic.in/rss.xml",                                          "Ministry of Finance",            None),
        ],
    },
    {
        "name": "UPSC — Environment, Ecology & Disaster Management",
        "slug": "upsc-environment-ecology",
        "description": (
            "Climate change, biodiversity, wildlife, pollution, disaster management, and sustainable development. "
            "Key for GS Paper 3 (Environment) and Essay paper."
        ),
        "feeds": [
            ("https://www.downtoearth.org.in/rss/latest",                              "Down To Earth",                  None),
            ("https://www.mongabay.com/feed/",                                         "Mongabay — Conservation",        None),
            ("https://www.carbonbrief.org/feed",                                       "Carbon Brief — Climate",         None),
            ("https://moef.gov.in/rss.xml",                                            "Ministry of Environment (MoEFCC)", None),
            ("https://www.iucn.org/rss.xml",                                           "IUCN — Conservation",            None),
            ("https://www.thehindu.com/sci-tech/energy-and-environment/feeder/default.rss", "The Hindu — Environment",   None),
            ("https://india.mongabay.com/feed/",                                       "Mongabay India",                 None),
            ("https://www.indiaclimatedialogue.net/feed/",                             "India Climate Dialogue",         None),
        ],
    },
    {
        "name": "UPSC — Science & Technology",
        "slug": "upsc-science-technology",
        "description": (
            "ISRO missions, DRDO, biotechnology, space policy, AI, cybersecurity, and defence R&D. "
            "Key for GS Paper 3 (Science & Technology) and Prelims."
        ),
        "feeds": [
            ("https://www.isro.gov.in/rss.xml",                                        "ISRO — News",                    None),
            ("https://dst.gov.in/rss.xml",                                             "DST — Dept. of Science",         None),
            ("https://drdo.gov.in/rss.xml",                                            "DRDO",                           None),
            ("https://science.thewire.in/feed/",                                       "The Wire — Science",             None),
            ("https://www.thehindu.com/sci-tech/feeder/default.rss",                   "The Hindu — Science & Tech",     None),
            ("https://www.nature.com/nindia.rss",                                      "Nature India",                   None),
            ("https://feeds.feedburner.com/IEEESpectrum",                              "IEEE Spectrum",                  None),
            ("https://www.sciencedaily.com/rss/top/technology.xml",                    "Science Daily — Technology",     None),
        ],
    },
    {
        "name": "UPSC — International Relations & Geopolitics",
        "slug": "upsc-international-relations",
        "description": (
            "Foreign policy, bilateral relations, international organisations (UN, WTO, IMF), "
            "border disputes, and strategic affairs. Essential for GS Paper 2."
        ),
        "feeds": [
            ("https://www.mea.gov.in/rss-feeds.htm?dtl/17037",                        "MEA — Press Releases",           None),
            ("https://thediplomat.com/feed/",                                          "The Diplomat",                   None),
            ("https://foreignpolicy.com/feed/",                                        "Foreign Policy",                 None),
            ("https://www.thehindu.com/news/international/feeder/default.rss",         "The Hindu — International",      None),
            ("https://indianexpress.com/section/world/feed/",                          "Indian Express — World",         None),
            ("https://www.orfonline.org/feed/",                                        "Observer Research Foundation",   None),
            ("https://www.idsa.in/rss.xml",                                            "IDSA — Strategic Affairs",       None),
            ("https://www.deccanherald.com/rss_feed/world.rss",                        "Deccan Herald — World",          None),
        ],
    },
    {
        "name": "UPSC — Polity, Governance & Social Issues",
        "slug": "upsc-polity-governance",
        "description": (
            "Constitutional amendments, Supreme Court judgments, governance reforms, welfare schemes, "
            "health, education, and social justice. Core for GS Paper 2."
        ),
        "feeds": [
            ("https://www.prsindia.org/feed/",                                         "PRS Legislative Research",       None),
            ("https://main.sci.gov.in/judgments-rss.xml",                              "Supreme Court — Judgments",      None),
            ("https://www.livelaw.in/feed/",                                           "Live Law — Legal News",          None),
            ("https://indianexpress.com/section/political-pulse/feed/",                "Indian Express — Polity",        None),
            ("https://www.thehindu.com/news/national/feeder/default.rss",              "The Hindu — National",           None),
            ("https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",                "PIB — Schemes & Policies",       None),
            ("https://www.deccanherald.com/rss_feed/national.rss",                     "Deccan Herald — National",       None),
            ("https://socialistindia.com/feed/",                                       "Social Justice — Analysis",      None),
        ],
    },
    {
        "name": "UPSC — History, Art & Culture",
        "slug": "upsc-history-art-culture",
        "description": (
            "Ancient, Medieval & Modern Indian history, freedom struggle, art forms, "
            "heritage sites (ASI), and cultural policy. Critical for GS Paper 1 and Prelims."
        ),
        "feeds": [
            ("https://www.indiaculture.gov.in/rss.xml",                                "Ministry of Culture",            None),
            ("https://asi.nic.in/rss/",                                                "Archaeological Survey of India", None),
            ("https://www.thehindu.com/entertainment/art/feeder/default.rss",          "The Hindu — Art & Culture",      None),
            ("https://scroll.in/tag/history/feed",                                     "Scroll.in — History",            None),
            ("https://thewire.in/culture/feed",                                        "The Wire — Culture",             None),
            ("https://www.livehistoryindia.com/feed",                                  "Live History India",             None),
            ("https://indianculture.gov.in/feed",                                      "Indian Culture Portal",          None),
            ("https://www.thehindu.com/news/national/heritage/feeder/default.rss",     "The Hindu — Heritage",           None),
        ],
    },
    {
        "name": "UPSC — Geography & Disaster Risk",
        "slug": "upsc-geography-disaster",
        "description": (
            "Physical geography, Indian rivers, monsoon, seismic zones, NDMA advisories, "
            "and disaster risk reduction. For GS Paper 1 (Geography) and Paper 3 (DM)."
        ),
        "feeds": [
            ("https://www.ndma.gov.in/rss.xml",                                        "NDMA — Disaster Management",     None),
            ("https://ndrf.gov.in/rss.xml",                                            "NDRF",                           None),
            ("https://www.imd.gov.in/rss.xml",                                         "IMD — Weather",                  None),
            ("https://www.thehindu.com/news/national/feeder/default.rss",              "The Hindu — National",           None),
            ("https://reliefweb.int/country/ind/rss.xml",                              "ReliefWeb — India",              None),
            ("https://www.downtoearth.org.in/rss/natural-disasters",                   "Down To Earth — Disasters",      None),
            ("https://earthobservatory.nasa.gov/feeds/natural-hazards.rss",            "NASA — Natural Hazards",         None),
            ("https://www.usgs.gov/news/earthjay/feed",                                "USGS — Earth Hazards",           None),
        ],
    },
    {
        "name": "UPSC — Ethics, Integrity & Aptitude (GS 4)",
        "slug": "upsc-ethics-gs4",
        "description": (
            "Case studies on ethical dilemmas, public administration values, governance integrity, "
            "emotional intelligence, and philosophical perspectives for GS Paper 4."
        ),
        "feeds": [
            ("https://www.cvc.gov.in/rss.xml",                                         "Central Vigilance Commission",   None),
            ("https://darpg.gov.in/rss.xml",                                           "DARPG — Good Governance",        None),
            ("https://www.thehindu.com/opinion/feeder/default.rss",                    "The Hindu — Opinion",            None),
            ("https://indianexpress.com/section/opinion/feed/",                        "Indian Express — Opinion",       None),
            ("https://thewire.in/government/feed",                                     "The Wire — Governance",          None),
            ("https://scroll.in/tag/ethics/feed",                                      "Scroll.in — Ethics",             None),
            ("https://www.countercurrents.org/feed/",                                  "Counter Currents — Dissent",     None),
            ("https://epw.in/rss.xml",                                                 "Economic & Political Weekly",    None),
        ],
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name.lower())
    s = re.sub(r"[\s-]+", "-", s).strip("-")
    return s[:60] or "collection"


def _seed(db) -> None:
    # Use the first user (typically the account owner / admin).
    # Pass --user <email> as argv[1] to target a specific account.
    if len(sys.argv) > 1:
        owner = db.query(User).filter(User.email == sys.argv[1]).first()
    else:
        owner = db.query(User).order_by(User.id).first()
    if not owner:
        print("ERROR: No admin or seed user found. Create a user first.", file=sys.stderr)
        sys.exit(1)

    print(f"Seeding as user: {owner.email} (id={owner.id})")
    created = 0
    skipped = 0

    for spec in COLLECTIONS:
        slug = spec["slug"]
        existing = (
            db.query(Collection)
            .filter(Collection.owner_id == owner.id, Collection.slug == slug)
            .first()
        )
        if existing:
            print(f"  SKIP  {spec['name']!r} — already exists (id={existing.id})")
            skipped += 1
            continue

        coll = Collection(
            owner_id=owner.id,
            name=spec["name"],
            slug=slug,
            description=spec["description"],
            is_public=True,
            subscriber_count=0,
        )
        db.add(coll)
        db.flush()  # get coll.id

        for position, (url, title, icon_url) in enumerate(spec["feeds"]):
            db.add(CollectionItem(
                collection_id=coll.id,
                feed_url=url,
                title=title,
                icon_url=icon_url,
                position=position,
            ))

        db.commit()
        print(f"  CREATE {spec['name']!r} ({len(spec['feeds'])} feeds, id={coll.id})")
        created += 1

    print(f"\nDone — {created} created, {skipped} skipped.")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        _seed(db)
    finally:
        db.close()
