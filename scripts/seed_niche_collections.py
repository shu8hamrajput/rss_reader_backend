"""
seed_niche_collections.py
--------------------------
Creates 45 curated public collections spanning diverse professional and
interest cohorts — each discoverable via /collections/discover.

Run on production:
  fly ssh console --app rss-reader-api -C \
    "python /app/scripts/seed_niche_collections.py [email]"

Idempotent: skips collections whose slug already exists for the owner.
"""

import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models import Collection, CollectionItem, User

# ── Catalogue ─────────────────────────────────────────────────────────────────

COLLECTIONS: list[dict] = [

    # ── Startup & Business ────────────────────────────────────────────────────

    {
        "name": "Indie Hacker & Solo Founder",
        "slug": "indie-hacker-solo-founder",
        "description": "Bootstrapped SaaS, micro-startups, building-in-public, and lessons from founders who went from zero to revenue without VC money.",
        "feeds": [
            ("https://www.indiehackers.com/feed.xml",              "Indie Hackers",              None),
            ("https://levels.io/rss/",                             "Pieter Levels",              None),
            ("https://www.saastr.com/feed/",                       "SaaStr",                     None),
            ("https://feeds.transistor.fm/rogue-startups",         "Rogue Startups Podcast",     None),
            ("https://bootstrapped.fm/feed/",                      "Bootstrapped.fm",            None),
            ("https://www.startupsfortherestofus.com/feed",        "Startups For the Rest of Us",None),
            ("https://hnrss.org/frontpage",                        "Hacker News",                None),
            ("https://www.microconf.com/feed/",                    "MicroConf",                  None),
        ],
    },
    {
        "name": "VC & Angel Investor",
        "slug": "vc-angel-investor",
        "description": "Deal flow, fund management, LP relations, emerging sector theses, and long-form writing from active investors.",
        "feeds": [
            ("https://a16z.com/feed/",                             "a16z",                       None),
            ("https://bothsidesofthetable.com/feed",               "Both Sides of the Table",    None),
            ("https://vcstarterkit.substack.com/feed",             "VC Starter Kit",             None),
            ("https://feeds.feedburner.com/Techcrunch",            "TechCrunch",                 None),
            ("https://www.cbinsights.com/research/feed/",          "CB Insights",                None),
            ("https://www.theinformation.com/feed",                "The Information",            None),
            ("https://fortune.com/term-sheet/feed/",               "Fortune Term Sheet",         None),
            ("https://pitchbook.com/news/rss.xml",                 "PitchBook News",             None),
        ],
    },
    {
        "name": "Product Manager",
        "slug": "product-manager",
        "description": "Product strategy, roadmapping, metrics, discovery frameworks, and case studies from PMs at top companies.",
        "feeds": [
            ("https://www.lennysnewsletter.com/feed",              "Lenny's Newsletter",         None),
            ("https://www.mindtheproduct.com/feed/",               "Mind the Product",           None),
            ("https://stratechery.com/feed/",                      "Stratechery",                None),
            ("https://www.producthunt.com/feed",                   "Product Hunt",               None),
            ("https://blackboxofpm.com/feed",                      "Black Box of PM",            None),
            ("https://www.intercom.com/blog/feed/",                "Intercom Blog",              None),
            ("https://amplitude.com/blog/feed",                    "Amplitude Blog",             None),
            ("https://www.reforge.com/blog/rss.xml",               "Reforge",                    None),
        ],
    },
    {
        "name": "Growth & Performance Marketing",
        "slug": "growth-performance-marketing",
        "description": "Paid acquisition, SEO, CRO, email marketing, attribution, and growth loops from practitioners.",
        "feeds": [
            ("https://cxl.com/blog/feed/",                         "CXL",                        None),
            ("https://sparktoro.com/blog/feed/",                   "SparkToro",                  None),
            ("https://searchengineland.com/feed",                  "Search Engine Land",         None),
            ("https://www.marketing-brew.com/feed.rss",            "Marketing Brew",             None),
            ("https://backlinko.com/feed",                         "Backlinko",                  None),
            ("https://blog.hubspot.com/marketing/rss.xml",         "HubSpot Marketing",          None),
            ("https://neilpatel.com/blog/feed/",                   "Neil Patel",                 None),
            ("https://moz.com/blog/feed",                          "Moz Blog",                   None),
        ],
    },
    {
        "name": "E-commerce & DTC Brand Builder",
        "slug": "ecommerce-dtc",
        "description": "Direct-to-consumer strategy, Shopify ecosystem, fulfillment, retention, and the business of selling online.",
        "feeds": [
            ("https://2pml.com/feed/",                             "2PM",                        None),
            ("https://www.modernretail.co/feed/",                  "Modern Retail",              None),
            ("https://retailbrew.com/feed.rss",                    "Retail Brew",                None),
            ("https://www.practicalecommerce.com/feed",            "Practical Ecommerce",        None),
            ("https://www.shopify.com/blog.atom",                  "Shopify Blog",               None),
            ("https://feeds.feedburner.com/ecommercefuel",         "eCommerceFuel",              None),
            ("https://www.klaviyo.com/blog/feed",                  "Klaviyo Blog",               None),
            ("https://www.bigcommerce.com/blog/feed/",             "BigCommerce Blog",           None),
        ],
    },
    {
        "name": "CFO & Finance Director",
        "slug": "cfo-finance-director",
        "description": "Corporate finance, capital allocation, treasury, financial planning & analysis, and the evolving CFO role.",
        "feeds": [
            ("https://www.cfo.com/feed/",                          "CFO Magazine",               None),
            ("https://cfodive.com/feeds/news/",                    "CFO Dive",                   None),
            ("https://www.ft.com/rss/home",                        "Financial Times",            None),
            ("https://www2.deloitte.com/us/en/insights/rss.html",  "Deloitte Insights",          None),
            ("https://www.mckinsey.com/feeds/rss/all",             "McKinsey Insights",          None),
            ("https://hbr.org/section/finance/rss",                "HBR Finance",                None),
            ("https://www.wsj.com/xml/rss/3_7014.xml",             "WSJ CFO Journal",            None),
            ("https://kpmg.com/rss/insight.xml",                   "KPMG Insights",              None),
        ],
    },

    # ── Legal ─────────────────────────────────────────────────────────────────

    {
        "name": "Advocate & Legal Professional (India)",
        "slug": "advocate-legal-india",
        "description": "Supreme Court and High Court judgments, legislative updates, Bar Council news, and legal commentary from India's top legal journals.",
        "feeds": [
            ("https://www.livelaw.in/feed/",                       "Live Law",                   None),
            ("https://www.barandbench.com/feed/",                  "Bar and Bench",              None),
            ("https://www.scobserver.in/feed/",                    "SC Observer",                None),
            ("https://lawstreetindia.com/feed/",                   "Law Street India",           None),
            ("https://www.prsindia.org/feed/",                     "PRS Legislative Research",   None),
            ("https://main.sci.gov.in/judgments-rss.xml",          "Supreme Court Judgments",    None),
            ("https://www.ssrn.com/rss/SSRN_AbstractsByDate.xml",  "SSRN — Law Papers",          None),
            ("https://www.manupatra.com/rss.aspx",                 "Manupatra",                  None),
        ],
    },
    {
        "name": "IP & Patent Attorney",
        "slug": "ip-patent-attorney",
        "description": "Patent prosecution, trademark disputes, copyright developments, WIPO filings, and global IP policy.",
        "feeds": [
            ("https://www.ipwatchdog.com/feed/",                   "IPWatchdog",                 None),
            ("https://ipkitten.blogspot.com/feeds/posts/default",  "The IPKat",                  None),
            ("https://patentlyo.com/patent/feed",                  "PatentlyO",                  None),
            ("https://www.wipo.int/pressroom/en/rss.xml",          "WIPO Press",                 None),
            ("https://www.managingip.com/rss.xml",                 "Managing IP",                None),
            ("https://www.lexology.com/rss/ip",                    "Lexology IP",                None),
            ("https://www.uspto.gov/rss/content/uspto-press-releases.rss", "USPTO News",         None),
            ("https://epo.org/en/news-events/rss.xml",             "European Patent Office",     None),
        ],
    },
    {
        "name": "Privacy & Data Protection Counsel",
        "slug": "privacy-data-protection",
        "description": "GDPR enforcement, India DPDP Act, CCPA, cross-border data flows, cookie compliance, and regulatory updates.",
        "feeds": [
            ("https://iapp.org/news/rss/",                         "IAPP — Privacy News",        None),
            ("https://www.privacyinternational.org/rss.xml",       "Privacy International",      None),
            ("https://fpf.org/blog/feed/",                         "Future of Privacy Forum",    None),
            ("https://www.fieldfisher.com/en/insights/feed",       "Fieldfisher Privacy",        None),
            ("https://gdprhub.eu/feed",                            "GDPRhub",                    None),
            ("https://www.dataguidance.com/news/feed",             "DataGuidance",               None),
            ("https://techpolicy.press/feed/",                     "Tech Policy Press",          None),
            ("https://edpb.europa.eu/rss.xml",                     "EDPB News",                  None),
        ],
    },

    # ── Tech & Engineering ────────────────────────────────────────────────────

    {
        "name": "DevOps & Platform Engineering",
        "slug": "devops-platform-engineering",
        "description": "Kubernetes, CI/CD, observability, SRE practices, cloud architecture, and developer productivity tooling.",
        "feeds": [
            ("https://kubernetes.io/feed.xml",                     "Kubernetes Blog",            None),
            ("https://www.hashicorp.com/blog/feed.xml",            "HashiCorp Blog",             None),
            ("https://thenewstack.io/feed/",                       "The New Stack",              None),
            ("https://sreweekly.com/feed/",                        "SRE Weekly",                 None),
            ("https://aws.amazon.com/blogs/devops/feed/",          "AWS DevOps Blog",            None),
            ("https://cloud.google.com/blog/rss/",                 "Google Cloud Blog",          None),
            ("https://www.cncf.io/blog/feed/",                     "CNCF Blog",                  None),
            ("https://github.blog/feed/",                          "GitHub Blog",                None),
        ],
    },
    {
        "name": "AI / ML Researcher & Practitioner",
        "slug": "ai-ml-researcher",
        "description": "Papers, model releases, benchmarks, research from top labs, and practical ML engineering from the field.",
        "feeds": [
            ("https://openai.com/blog/rss/",                       "OpenAI Blog",                None),
            ("https://deepmind.com/blog/feed/basic/",              "DeepMind Blog",              None),
            ("https://ai.googleblog.com/feeds/posts/default",      "Google AI Blog",             None),
            ("https://bair.berkeley.edu/blog/feed.xml",            "BAIR Blog",                  None),
            ("https://towardsdatascience.com/feed",                "Towards Data Science",       None),
            ("https://thegradient.pub/rss/",                       "The Gradient",               None),
            ("https://huggingface.co/blog/feed.xml",               "Hugging Face Blog",          None),
            ("https://www.interconnects.ai/feed",                  "Interconnects",              None),
        ],
    },
    {
        "name": "Cybersecurity Researcher",
        "slug": "cybersecurity-researcher",
        "description": "Threat intelligence, CVEs, malware analysis, red team techniques, ransomware trends, and security research publications.",
        "feeds": [
            ("https://krebsonsecurity.com/feed/",                  "Krebs on Security",          None),
            ("https://www.schneier.com/blog/atom.xml",             "Schneier on Security",       None),
            ("https://isc.sans.edu/rssfeed_full.xml",              "SANS ISC",                   None),
            ("https://www.darkreading.com/rss.xml",                "Dark Reading",               None),
            ("https://feeds.feedburner.com/TheHackersNews",        "The Hacker News",            None),
            ("https://googleprojectzero.blogspot.com/feeds/posts/default", "Project Zero",       None),
            ("https://www.bleepingcomputer.com/feed/",             "BleepingComputer",           None),
            ("https://unit42.paloaltonetworks.com/feed/",          "Palo Alto Unit 42",          None),
        ],
    },
    {
        "name": "Data Engineer",
        "slug": "data-engineer",
        "description": "Pipelines, lakehouse architecture, dbt, Spark, streaming, data contracts, and the modern data stack.",
        "feeds": [
            ("https://www.getdbt.com/blog/rss.xml",                "dbt Blog",                   None),
            ("https://www.databricks.com/blog/feed",               "Databricks Blog",            None),
            ("https://airbyte.com/blog/rss.xml",                   "Airbyte Blog",               None),
            ("https://dataengineeringweekly.com/p/feed",           "Data Engineering Weekly",    None),
            ("https://locallyoptimistic.com/feed.xml",             "Locally Optimistic",         None),
            ("https://www.confluent.io/blog/feed/",                "Confluent — Kafka",          None),
            ("https://mattturck.com/feed/",                        "Matt Turck — MAD Landscape", None),
            ("https://benn.substack.com/feed",                     "Benn Stancil",               None),
        ],
    },
    {
        "name": "Blockchain & Web3 Developer",
        "slug": "blockchain-web3-developer",
        "description": "Smart contracts, DeFi protocols, NFT infrastructure, L2 scaling, auditing, and on-chain data analysis.",
        "feeds": [
            ("https://blog.ethereum.org/en/feed.xml",              "Ethereum Foundation Blog",   None),
            ("https://www.coindesk.com/arc/outboundfeeds/rss/",    "CoinDesk",                   None),
            ("https://decrypt.co/feed",                            "Decrypt",                    None),
            ("https://thedefiant.io/feed",                         "The Defiant",                None),
            ("https://banklesshq.com/feed/",                       "Bankless",                   None),
            ("https://a16zcrypto.com/feed/",                       "a16z Crypto",                None),
            ("https://rekt.news/rss/",                             "Rekt News",                  None),
            ("https://dune.com/blog/rss.xml",                      "Dune Analytics Blog",        None),
        ],
    },
    {
        "name": "Open Source Developer & Maintainer",
        "slug": "open-source-developer",
        "description": "OSS governance, contributor experience, sustainability, licensing, and notable project releases.",
        "feeds": [
            ("https://changelog.com/feed",                         "The Changelog",              None),
            ("https://github.blog/feed/",                          "GitHub Blog",                None),
            ("https://opensource.org/blog/feed",                   "Open Source Initiative",     None),
            ("https://lwn.net/headlines/rss",                      "LWN.net",                    None),
            ("https://planet.gnome.org/rss20.xml",                 "Planet GNOME",               None),
            ("https://www.linux.com/feed/",                        "Linux.com",                  None),
            ("https://sfconservancy.org/blog/feed/",               "Software Freedom Conservancy",None),
            ("https://tidelift.com/blog/feed",                     "Tidelift Blog",              None),
        ],
    },
    {
        "name": "Quantum Computing Researcher",
        "slug": "quantum-computing-researcher",
        "description": "Qubit architectures, error correction, algorithm breakthroughs, hardware roadmaps, and policy implications of quantum.",
        "feeds": [
            ("https://www.ibm.com/blogs/research/feed/",           "IBM Research",               None),
            ("https://quantumcomputingreport.com/feed/",           "Quantum Computing Report",   None),
            ("https://www.quantum.amsterdam/feed/",                "Quantum Amsterdam",          None),
            ("https://feeds.feedburner.com/IEEESpectrum",          "IEEE Spectrum",              None),
            ("https://www.quantamagazine.org/feed/",               "Quanta Magazine",            None),
            ("https://ionq.com/posts/rss.xml",                     "IonQ Blog",                  None),
            ("https://www.nature.com/npjqi.rss",                   "npj Quantum Information",   None),
            ("https://arxiv.org/rss/quant-ph",                     "arXiv Quantum Physics",      None),
        ],
    },

    # ── Healthcare & Life Sciences ────────────────────────────────────────────

    {
        "name": "Doctor & Clinician",
        "slug": "doctor-clinician",
        "description": "Clinical trials, treatment guidelines, medical news, and journal highlights from NEJM, BMJ, The Lancet, and JAMA.",
        "feeds": [
            ("https://www.nejm.org/action/showFeed?jc=nejm&type=etoc&feed=rss", "NEJM",           None),
            ("https://www.bmj.com/rss/current.xml",                "BMJ",                        None),
            ("https://www.thelancet.com/rssfeed/lancet_current.xml","The Lancet",                None),
            ("https://jamanetwork.com/rss/site_3/67.xml",          "JAMA",                       None),
            ("https://www.medpagetoday.com/rss/headlines.xml",     "MedPage Today",              None),
            ("https://www.statnews.com/feed/",                     "STAT News",                  None),
            ("https://www.medscape.com/cx/rssfeeds/2678.xml",      "Medscape",                   None),
            ("https://www.health.harvard.edu/blog/feed",           "Harvard Health Blog",        None),
        ],
    },
    {
        "name": "Biotech & Pharma Researcher",
        "slug": "biotech-pharma-researcher",
        "description": "Clinical pipelines, FDA approvals, gene therapy, CRISPR, bioinformatics, and big pharma M&A.",
        "feeds": [
            ("https://www.fiercepharma.com/rss/xml",               "FiercePharma",               None),
            ("https://biopharma.com/feed/",                        "BioPharma",                  None),
            ("https://endpts.com/feed/",                           "Endpoints News",             None),
            ("https://www.nature.com/nbt.rss",                     "Nature Biotechnology",       None),
            ("https://www.genengnews.com/feed/",                   "Genetic Engineering News",   None),
            ("https://www.biospace.com/rss/",                      "BioSpace",                   None),
            ("https://www.sciencedaily.com/rss/health_medicine/biotechnology.xml", "ScienceDaily Biotech", None),
            ("https://www.cell.com/cell/rss/current",              "Cell Journal",               None),
        ],
    },
    {
        "name": "Mental Health Practitioner",
        "slug": "mental-health-practitioner",
        "description": "Psychotherapy research, psychiatric medications, trauma-informed care, public mental health policy, and practitioner wellbeing.",
        "feeds": [
            ("https://www.apa.org/news/press/releases/rss.aspx",   "APA News",                   None),
            ("https://www.psychiatrictimes.com/rss/all",           "Psychiatric Times",          None),
            ("https://psychcentral.com/feed/",                     "PsychCentral",               None),
            ("https://madinamerica.com/feed/",                     "Mad in America",             None),
            ("https://www.psychologytoday.com/us/blog/feed",       "Psychology Today",           None),
            ("https://www.thenationalcouncil.org/feed/",           "National Council — MH",      None),
            ("https://www.bmj.com/rss/current.xml",                "BMJ — Psychiatry subset",    None),
            ("https://mentalhealthweekly.co.uk/feed/",             "Mental Health Weekly",       None),
        ],
    },

    # ── Finance & Investing ───────────────────────────────────────────────────

    {
        "name": "Value Investor & Stock Picker",
        "slug": "value-investor-stock-picker",
        "description": "Fundamental analysis, annual reports, earnings commentary, portfolio letters, and deep-value research from practitioners.",
        "feeds": [
            ("https://www.gurufocus.com/news/rss_news.php",        "GuruFocus",                  None),
            ("https://seekingalpha.com/feed.xml",                  "Seeking Alpha",              None),
            ("https://acquirersmultiple.com/feed/",                "The Acquirer's Multiple",    None),
            ("https://www.valuewalk.com/feed/",                    "ValueWalk",                  None),
            ("https://novelinvestor.com/feed/",                    "Novel Investor",             None),
            ("https://ofdollarsanddata.com/feed/",                 "Of Dollars and Data",        None),
            ("https://www.collaborativefund.com/blog/feed/",       "Collaborative Fund Blog",    None),
            ("https://fs.blog/feed/",                              "Farnam Street",              None),
        ],
    },
    {
        "name": "Personal Finance & FIRE",
        "slug": "personal-finance-fire",
        "description": "Financial independence, early retirement, frugality, index investing, tax optimisation, and the psychology of money.",
        "feeds": [
            ("https://www.mrmoneymustache.com/feed/",              "Mr Money Mustache",          None),
            ("https://www.madfientist.com/feed/",                  "Mad Fientist",               None),
            ("https://awealthofcommonsense.com/feed/",             "A Wealth of Common Sense",   None),
            ("https://ofdollarsanddata.com/feed/",                 "Of Dollars and Data",        None),
            ("https://jlcollinsnh.com/feed/",                      "JL Collins — Simple Path",   None),
            ("https://thecalculatedimpact.com/feed/",              "The Calculated Impact",      None),
            ("https://www.frugalwoods.com/feed/",                  "Frugalwoods",                None),
            ("https://earlyretirementnow.com/feed/",               "Early Retirement Now",       None),
        ],
    },
    {
        "name": "Real Estate Investor",
        "slug": "real-estate-investor",
        "description": "Rental property, REITs, commercial real estate, cap rates, short-term rentals, and market analysis.",
        "feeds": [
            ("https://www.biggerpockets.com/blog/feed",            "BiggerPockets",              None),
            ("https://www.cbre.com/newsroom/rss",                  "CBRE Research",              None),
            ("https://www.urban.org/rss.xml",                      "Urban Institute",            None),
            ("https://www.nahb.org/news-and-economics/housing-economics/rss", "NAHB",            None),
            ("https://therealdeal.com/feed/",                      "The Real Deal",              None),
            ("https://www.globest.com/feed/",                      "GlobeSt — CRE",              None),
            ("https://listwithclever.com/real-estate-blog/feed/",  "Clever Real Estate Blog",    None),
            ("https://www.housingwire.com/feed/",                  "HousingWire",                None),
        ],
    },

    # ── Academia & Research ───────────────────────────────────────────────────

    {
        "name": "PhD Researcher & Academic",
        "slug": "phd-researcher-academic",
        "description": "Open access publishing, reproducibility, academic job market, grant writing, and life inside the research university.",
        "feeds": [
            ("https://www.nature.com/nature.rss",                  "Nature",                     None),
            ("https://www.science.org/rss/news_current.xml",       "Science Magazine",           None),
            ("https://arxiv.org/rss/cs",                           "arXiv — CS",                 None),
            ("https://scholarlykitchen.sspnet.org/feed/",          "The Scholarly Kitchen",      None),
            ("https://www.timeshighereducation.com/feed",          "Times Higher Education",     None),
            ("https://chroniclevitae.com/news/rss",                "Chronicle of Higher Ed",     None),
            ("https://retractionwatch.com/feed/",                  "Retraction Watch",           None),
            ("https://www.plos.org/feed/",                         "PLOS Blog",                  None),
        ],
    },
    {
        "name": "Social Science & Policy Researcher",
        "slug": "social-science-policy-researcher",
        "description": "Economics, sociology, political science, behavioural research, think-tank output, and evidence-based policy.",
        "feeds": [
            ("https://www.brookings.edu/feed/",                    "Brookings Institution",      None),
            ("https://www.nber.org/rss/new_working_papers.rss",    "NBER Working Papers",        None),
            ("https://voxeu.org/rss.xml",                          "VoxEU",                      None),
            ("https://www.rand.org/content/rand/blog.xml",         "RAND Blog",                  None),
            ("https://epw.in/rss.xml",                             "Economic & Political Weekly",None),
            ("https://iza.org/rss_pressreleases.xml",              "IZA — Labour Economics",     None),
            ("https://www.worldbank.org/en/news/rss",              "World Bank Blog",            None),
            ("https://ssrn.com/rss/SSRN_AbstractsByDate.xml",      "SSRN Papers",                None),
        ],
    },

    # ── Policy & Government ───────────────────────────────────────────────────

    {
        "name": "Climate Policy & Clean Energy Advocate",
        "slug": "climate-policy-clean-energy",
        "description": "IPCC updates, COP negotiations, carbon markets, clean energy finance, and national climate legislation.",
        "feeds": [
            ("https://www.carbonbrief.org/feed",                   "Carbon Brief",               None),
            ("https://insideclimatenews.org/feed/",                "Inside Climate News",        None),
            ("https://www.climatechangenews.com/feed/",            "Climate Home News",          None),
            ("https://www.iea.org/rss/newsreleases.rss",           "IEA News",                   None),
            ("https://rmi.org/feed/",                              "Rocky Mountain Institute",   None),
            ("https://www.eenews.net/rss/eenews.xml",              "E&E News",                   None),
            ("https://energymonitor.ai/feed/",                     "Energy Monitor",             None),
            ("https://unfccc.int/rss-feed",                        "UNFCCC",                     None),
        ],
    },
    {
        "name": "Urban Planner & Smart Cities",
        "slug": "urban-planner-smart-cities",
        "description": "Transit-oriented development, zoning reform, housing density, public space, mobility, and digital city infrastructure.",
        "feeds": [
            ("https://www.planetizen.com/rss.xml",                 "Planetizen",                 None),
            ("https://www.citylab.com/feed/",                      "Bloomberg CityLab",          None),
            ("https://www.strongtowns.org/journal?format=rss",     "Strong Towns",               None),
            ("https://itdp.org/feed/",                             "ITDP",                       None),
            ("https://www.uli.org/rss/",                           "Urban Land Institute",       None),
            ("https://archpaper.com/feed/",                        "The Architect's Newspaper",  None),
            ("https://www.smartcitiesdive.com/feeds/news/",        "Smart Cities Dive",          None),
            ("https://www.citiscope.org/rss.xml",                  "Citiscope",                  None),
        ],
    },
    {
        "name": "Defence & Strategic Affairs Analyst",
        "slug": "defence-strategic-affairs",
        "description": "Military technology, geopolitical conflict, arms control, naval power, space security, and defence procurement.",
        "feeds": [
            ("https://warontherocks.com/feed/",                    "War on the Rocks",           None),
            ("https://breakingdefense.com/feed/",                  "Breaking Defense",           None),
            ("https://www.defensenews.com/rss/",                   "Defense News",               None),
            ("https://www.iiss.org/rss",                           "IISS",                       None),
            ("https://www.bellingcat.com/feed/",                   "Bellingcat",                 None),
            ("https://www.idsa.in/rss.xml",                        "IDSA",                       None),
            ("https://thediplomat.com/feed/",                      "The Diplomat",               None),
            ("https://foreignpolicy.com/feed/",                    "Foreign Policy",             None),
        ],
    },

    # ── Creative & Media ──────────────────────────────────────────────────────

    {
        "name": "Journalist & Investigative Reporter",
        "slug": "journalist-reporter",
        "description": "Press freedom, investigative methods, newsroom economics, fact-checking, audience development, and digital journalism tools.",
        "feeds": [
            ("https://www.niemanlab.org/feed/",                    "Nieman Lab",                 None),
            ("https://www.cjr.org/feed",                           "Columbia Journalism Review", None),
            ("https://www.poynter.org/feed/",                      "Poynter",                    None),
            ("https://pressgazette.co.uk/feed/",                   "Press Gazette",              None),
            ("https://www.icij.org/feed/",                         "ICIJ",                       None),
            ("https://themarkup.org/feed.xml",                     "The Markup",                 None),
            ("https://www.propublica.org/feeds/propublica/main",   "ProPublica",                 None),
            ("https://www.reutersinstitute.politics.ox.ac.uk/rss.xml","Reuters Institute",       None),
        ],
    },
    {
        "name": "UX Designer & Design Researcher",
        "slug": "ux-designer-design-researcher",
        "description": "User research methods, interaction design, design systems, accessibility, prototyping, and design leadership.",
        "feeds": [
            ("https://uxdesign.cc/feed",                           "UX Collective",              None),
            ("https://www.nngroup.com/feed/rss/",                  "Nielsen Norman Group",       None),
            ("https://www.smashingmagazine.com/feed/",             "Smashing Magazine",          None),
            ("https://alistapart.com/main/feed/",                  "A List Apart",               None),
            ("https://sidebar.io/feed.xml",                        "Sidebar.io",                 None),
            ("https://www.designsystems.com/feed/",                "Design Systems",             None),
            ("https://prototypr.io/feed/",                         "Prototypr",                  None),
            ("https://bradfrost.com/feed/",                        "Brad Frost",                 None),
        ],
    },
    {
        "name": "Filmmaker & Cinematographer",
        "slug": "filmmaker-cinematographer",
        "description": "Cinematography techniques, production budgeting, distribution, film festival strategy, and gear reviews.",
        "feeds": [
            ("https://nofilmschool.com/feed",                      "No Film School",             None),
            ("https://www.indiewire.com/feed/",                    "IndieWire",                  None),
            ("https://filmmakermagazine.com/feed/",                "Filmmaker Magazine",         None),
            ("https://mubi.com/notebook/feed",                     "MUBI Notebook",              None),
            ("https://www.screendaily.com/rss",                    "Screen Daily",               None),
            ("https://deadline.com/feed/",                         "Deadline Hollywood",         None),
            ("https://www.hollywoodreporter.com/feed/",            "The Hollywood Reporter",     None),
            ("https://variety.com/feed/",                          "Variety",                    None),
        ],
    },
    {
        "name": "Fiction Writer & Author",
        "slug": "fiction-writer-author",
        "description": "Craft essays, literary criticism, publishing industry news, agent queries, and the business of being a working writer.",
        "feeds": [
            ("https://www.theparisreview.org/feed/",               "The Paris Review",           None),
            ("https://lithub.com/feed/",                           "Literary Hub",               None),
            ("https://electricliterature.com/feed/",               "Electric Literature",        None),
            ("https://www.pw.org/content/rss.xml",                 "Poets & Writers",            None),
            ("https://pubperspectives.com/feed/",                  "Publishers Weekly",          None),
            ("https://www.writersdigest.com/feed",                 "Writer's Digest",            None),
            ("https://jeannecavallos.com/feed/",                   "Jane Friedman Blog",         None),
            ("https://kriswrites.com/feed/",                       "Kris Writes",                None),
        ],
    },
    {
        "name": "Music Producer & Audio Engineer",
        "slug": "music-producer-audio-engineer",
        "description": "Mixing, mastering, synthesis, plugin reviews, studio acoustics, and the music business from a creator's perspective.",
        "feeds": [
            ("https://www.soundonsound.com/rss/news",              "Sound On Sound",             None),
            ("https://www.attackmagazine.com/feed/",               "Attack Magazine",            None),
            ("https://www.musicradar.com/news/feeds/all/rss.xml",  "MusicRadar",                 None),
            ("https://cdm.link/feed/",                             "Create Digital Music",       None),
            ("https://reaper.fm/blog/?feed=rss2",                  "REAPER Blog",                None),
            ("https://www.gearslutz.com/board/rss.php",            "Gearspace",                  None),
            ("https://www.musictech.net/feed/",                    "MusicTech",                  None),
            ("https://www.edmprod.com/feed/",                      "EDMProd",                    None),
        ],
    },
    {
        "name": "Podcast Creator & Strategist",
        "slug": "podcast-creator",
        "description": "Podcast growth, monetisation, dynamic ad insertion, listener analytics, and the business of audio.",
        "feeds": [
            ("https://soundsprofitable.com/feed",                  "Sounds Profitable",          None),
            ("https://www.podcastbusinessjournal.com/feed/",       "Podcast Business Journal",   None),
            ("https://feeds.buzzsprout.com/1537867.rss",           "Buzzsprout Podcast",         None),
            ("https://podcasting.substack.com/feed",               "Podcasting 2.0",             None),
            ("https://www.podnews.net/rss",                        "Podnews",                    None),
            ("https://jacobjanz.com/feed",                         "Jacob Janz — Pod Strategy",  None),
            ("https://www.podcastinsights.com/feed/",              "Podcast Insights",           None),
            ("https://castos.com/blog/feed/",                      "Castos Blog",                None),
        ],
    },

    # ── Lifestyle, Wellness & Niche Hobbies ──────────────────────────────────

    {
        "name": "Athlete & Sports Performance Coach",
        "slug": "athlete-sports-performance",
        "description": "Strength & conditioning science, periodisation, recovery, nutrition for athletes, and sports psychology.",
        "feeds": [
            ("https://www.nsca.com/news/rss/",                     "NSCA",                       None),
            ("https://breakingmuscle.com/feed/",                   "Breaking Muscle",            None),
            ("https://www.precisionnutrition.com/rss",             "Precision Nutrition",        None),
            ("https://examine.com/feeds/updates/",                 "Examine.com",                None),
            ("https://www.strengthandconditioningresearch.com/feed/","S&C Research",             None),
            ("https://www.trainingpeaks.com/blog/feed/",           "TrainingPeaks",              None),
            ("https://journals.lww.com/rss/journalofstrengthandconditioning", "JSCR",            None),
            ("https://www.suppversity.blogspot.com/feeds/posts/default", "SuppVersity",          None),
        ],
    },
    {
        "name": "Sustainable Living & Zero Waste",
        "slug": "sustainable-living-zero-waste",
        "description": "Plastic-free living, ethical fashion, regenerative agriculture, conscious consumerism, and community resilience.",
        "feeds": [
            ("https://www.treehugger.com/feeds/latest/",           "Treehugger",                 None),
            ("https://www.goodonyou.eco/feed/",                    "Good On You",                None),
            ("https://zerowastehome.com/feed/",                    "Zero Waste Home",            None),
            ("https://sustainably-vegan.com/feed/",                "Sustainably Vegan",          None),
            ("https://www.ecowatch.com/rss",                       "EcoWatch",                   None),
            ("https://www.greenmatters.com/rss/new-green",         "Green Matters",              None),
            ("https://lowimpact.org/feed",                         "Low Impact",                 None),
            ("https://www.lesswaste.org.nz/feed/",                 "Less Waste",                 None),
        ],
    },
    {
        "name": "Space & Astronomy Enthusiast",
        "slug": "space-astronomy-enthusiast",
        "description": "Rocket launches, exoplanet discoveries, space policy, commercial spaceflight, and astrophysics papers for the scientifically curious.",
        "feeds": [
            ("https://www.nasa.gov/rss/dyn/breaking_news.rss",     "NASA Breaking News",         None),
            ("https://www.esa.int/rssfeed/Our_Activities/Space_Science", "ESA Space Science",    None),
            ("https://www.spaceflightnow.com/feed/",               "Spaceflight Now",            None),
            ("https://www.space.com/feeds/all",                    "Space.com",                  None),
            ("https://skyandtelescope.org/astronomy-news/feed/",   "Sky & Telescope",            None),
            ("https://www.universetoday.com/feed/",                "Universe Today",             None),
            ("https://arxiv.org/rss/astro-ph",                     "arXiv — Astrophysics",       None),
            ("https://www.planetary.org/rss/articles",             "The Planetary Society",      None),
        ],
    },
    {
        "name": "Game Developer",
        "slug": "game-developer",
        "description": "Unity, Unreal, game design theory, indie release strategy, monetisation, and the business of the games industry.",
        "feeds": [
            ("https://www.gamedeveloper.com/rss.xml",              "Game Developer",             None),
            ("https://www.gamesindustry.biz/rss",                  "GamesIndustry.biz",          None),
            ("https://blog.unity.com/feed",                        "Unity Blog",                 None),
            ("https://www.unrealengine.com/blog/rss",              "Unreal Engine Blog",         None),
            ("https://www.polygon.com/rss/index.xml",              "Polygon",                    None),
            ("https://kotaku.com/rss",                             "Kotaku",                     None),
            ("https://howtomarketagame.com/feed/",                 "How To Market A Game",       None),
            ("https://www.deconstructoroffun.com/blog?format=rss", "Deconstructor of Fun",       None),
        ],
    },
    {
        "name": "Digital Nomad & Remote Worker",
        "slug": "digital-nomad-remote-worker",
        "description": "Visa strategies, co-living spaces, tax for nomads, productivity while travelling, and community-building abroad.",
        "feeds": [
            ("https://nomadicmatt.com/feed/",                      "Nomadic Matt",               None),
            ("https://www.thepointsguy.com/feed/",                 "The Points Guy",             None),
            ("https://blog.remoteok.com/rss",                      "Remote OK Blog",             None),
            ("https://www.tropicalmba.com/feed/",                  "Tropical MBA",               None),
            ("https://www.locationindependent.co.uk/feed/",        "Location Independent",       None),
            ("https://teleport.org/blog/feed/",                    "Teleport Blog",              None),
            ("https://weworkremotely.com/blog/feed",               "We Work Remotely Blog",      None),
            ("https://www.nomadlist.com/blog/rss",                 "Nomad List Blog",            None),
        ],
    },
    {
        "name": "HR & People Operations Leader",
        "slug": "hr-people-ops",
        "description": "Talent acquisition, performance management, DEI, workforce analytics, compensation, and building great company culture.",
        "feeds": [
            ("https://www.shrm.org/rss/pages/rss.aspx",            "SHRM",                       None),
            ("https://hrbrew.com/feed.rss",                        "HR Brew",                    None),
            ("https://www.peoplemanagement.co.uk/rss",             "People Management",          None),
            ("https://lattice.com/blog/rss.xml",                   "Lattice Blog",               None),
            ("https://blog.greenhouse.io/feed",                    "Greenhouse Blog",            None),
            ("https://www.workable.com/blog/feed",                 "Workable Blog",              None),
            ("https://hbr.org/section/talent-management/rss",      "HBR Talent Management",      None),
            ("https://wheniwork.com/blog/feed/",                   "When I Work Blog",           None),
        ],
    },
    {
        "name": "Chef & Food Industry Professional",
        "slug": "chef-food-industry",
        "description": "Culinary technique, food science, restaurant business, supply chain from farm to table, and global food culture.",
        "feeds": [
            ("https://www.foodandwine.com/feed",                   "Food & Wine",                None),
            ("https://www.eater.com/rss/index.xml",                "Eater",                      None),
            ("https://www.seriouseats.com/feeds/all/rss.xml",      "Serious Eats",               None),
            ("https://www.tastecooking.com/feed/",                 "Taste Cooking",              None),
            ("https://www.foodnavigator.com/rss/",                 "Food Navigator",             None),
            ("https://modernistcuisine.com/feed/",                 "Modernist Cuisine",          None),
            ("https://chefsfeed.com/blog/rss.xml",                 "ChefsFeed",                  None),
            ("https://www.restaurantbusinessonline.com/feed",      "Restaurant Business",        None),
        ],
    },
    {
        "name": "Architect & Built Environment Professional",
        "slug": "architect-built-environment",
        "description": "Contemporary architecture, parametric design, sustainable building, materials innovation, and construction technology.",
        "feeds": [
            ("https://www.dezeen.com/feed/",                       "Dezeen",                     None),
            ("https://www.archdaily.com/feed",                     "ArchDaily",                  None),
            ("https://www.architecturalrecord.com/rss",            "Architectural Record",       None),
            ("https://www.domusweb.it/en/rss.html",                "Domus",                      None),
            ("https://architizer.com/blog/feed/",                  "Architizer",                 None),
            ("https://www.wallpaper.com/rss",                      "Wallpaper*",                 None),
            ("https://archpaper.com/feed/",                        "The Architect's Newspaper",  None),
            ("https://www.architectsjournal.co.uk/feed",           "Architects' Journal",        None),
        ],
    },
    {
        "name": "EdTech Founder & Educator",
        "slug": "edtech-educator",
        "description": "Learning science, adaptive systems, online course design, ed-policy, K-12 innovation, and the future of credentialling.",
        "feeds": [
            ("https://www.edsurge.com/news/feed",                  "EdSurge",                    None),
            ("https://edpolicyworks.org/feed/",                    "EdPolicyWorks",              None),
            ("https://www.edweek.org/rss/articles.rss",            "Education Week",             None),
            ("https://www.chronicle.com/feeds/articles",           "The Chronicle",              None),
            ("https://www.elearningindustry.com/feed",             "eLearning Industry",         None),
            ("https://blog.duolingo.com/feed",                     "Duolingo Blog",              None),
            ("https://khanacademy.org/blog/rss",                   "Khan Academy Blog",          None),
            ("https://teachthought.com/feed/",                     "TeachThought",               None),
        ],
    },
    {
        "name": "Supply Chain & Logistics Manager",
        "slug": "supply-chain-logistics",
        "description": "Procurement, inventory optimisation, last-mile delivery, nearshoring, port congestion, and trade compliance.",
        "feeds": [
            ("https://www.supplychaindive.com/feeds/news/",        "Supply Chain Dive",          None),
            ("https://www.logisticsmgmt.com/rss.xml",              "Logistics Management",       None),
            ("https://www.dcvelocity.com/rss.xml",                 "DC Velocity",                None),
            ("https://www.supplychainbrain.com/rss/news.xml",      "Supply Chain Brain",         None),
            ("https://www.freightwaves.com/news/rss",              "FreightWaves",               None),
            ("https://www.supplychain247.com/rss",                 "Supply Chain 247",           None),
            ("https://www.flexport.com/blog/rss.xml",              "Flexport Blog",              None),
            ("https://www.inboundlogistics.com/rss/",              "Inbound Logistics",          None),
        ],
    },
    {
        "name": "Renewable Energy Engineer",
        "slug": "renewable-energy-engineer",
        "description": "Solar PV, wind, grid-scale battery storage, hydrogen, power electronics, and energy market developments.",
        "feeds": [
            ("https://pv-magazine.com/feed/",                      "PV Magazine",                None),
            ("https://www.windpowerengineering.com/feed/",         "Wind Power Engineering",     None),
            ("https://www.energy-storage.news/feed/",              "Energy Storage News",        None),
            ("https://electrek.co/feed/",                          "Electrek",                   None),
            ("https://cleantechnica.com/feed/",                    "CleanTechnica",              None),
            ("https://www.greentechmedia.com/rss",                 "GreenTech Media",            None),
            ("https://www.energymonitor.ai/feed/",                 "Energy Monitor",             None),
            ("https://www.irena.org/rss.xml",                      "IRENA",                      None),
        ],
    },
    {
        "name": "Fashion & Luxury Industry Professional",
        "slug": "fashion-luxury-industry",
        "description": "Trend forecasting, supply chain transparency, brand strategy, sustainability in fashion, and luxury market dynamics.",
        "feeds": [
            ("https://www.businessoffashion.com/feed/",            "Business of Fashion",        None),
            ("https://www.voguebusiness.com/rss",                  "Vogue Business",             None),
            ("https://wwd.com/feed/",                              "WWD",                        None),
            ("https://www.highsnobiety.com/feed/",                 "Highsnobiety",               None),
            ("https://www.fashionunited.com/rss",                  "FashionUnited",              None),
            ("https://www.drapersonline.com/rss",                  "Drapers",                    None),
            ("https://fashionista.com/feed",                       "Fashionista",                None),
            ("https://www.thefashionlaw.com/feed/",                "The Fashion Law",            None),
        ],
    },
    {
        "name": "Sports Analytics & Data Professional",
        "slug": "sports-analytics-data",
        "description": "Expected goals, tracking data, player evaluation models, sports betting markets, and the business of analytics in professional sport.",
        "feeds": [
            ("https://www.statsperform.com/resource/feed/",        "Stats Perform",              None),
            ("https://fivethirtyeight.com/sports/feed/",           "FiveThirtyEight Sports",     None),
            ("https://www.americansocceranalysis.com/feed/",       "American Soccer Analysis",   None),
            ("https://theathletic.com/rss/",                       "The Athletic",               None),
            ("https://www.sloansportsconference.com/feed",         "MIT Sloan Sports",           None),
            ("https://trainingground.guru/articles/rss",           "Training Ground Guru",       None),
            ("https://www.optasports.com/news/rss/",               "Opta Sports",                None),
            ("https://www.espn.com/espn/rss/news",                 "ESPN",                       None),
        ],
    },
    {
        "name": "Non-profit & NGO Programme Manager",
        "slug": "nonprofit-ngo-programme-manager",
        "description": "Impact measurement, grant fundraising, donor relations, humanitarian aid, development sector, and civil society advocacy.",
        "feeds": [
            ("https://www.devex.com/news/rss.xml",                 "Devex",                      None),
            ("https://ssir.org/feed/",                             "Stanford Social Innovation Review", None),
            ("https://www.alliancemagazine.org/feed/",             "Alliance Magazine",          None),
            ("https://www.reliefweb.int/rss.xml",                  "ReliefWeb",                  None),
            ("https://www.irinnews.org/rss",                       "IRIN News",                  None),
            ("https://nonprofitquarterly.org/feed/",               "Nonprofit Quarterly",        None),
            ("https://www.bond.org.uk/feed/",                      "Bond — UK NGO Network",      None),
            ("https://www.theguardian.com/global-development/rss", "Guardian Global Development",None),
        ],
    },
    {
        "name": "Veterinarian & Animal Scientist",
        "slug": "veterinarian-animal-scientist",
        "description": "Clinical case reports, zoonotic disease, companion animal welfare, livestock health, and veterinary pharmacology.",
        "feeds": [
            ("https://www.dvm360.com/rss/all",                     "dvm360",                     None),
            ("https://www.avma.org/javma/feed",                    "AVMA — JAVMA",               None),
            ("https://bvajournals.onlinelibrary.wiley.com/rss/journal/17517176", "Veterinary Record", None),
            ("https://vetfolio.com/learn/rss",                     "VetFolio",                   None),
            ("https://www.laboklin.co.uk/rss",                     "Laboklin Insights",          None),
            ("https://www.merckvetmanual.com/rss",                 "Merck Vet Manual",           None),
            ("https://www.vettimes.co.uk/feed/",                   "Vet Times",                  None),
            ("https://www.frontiersin.org/journals/veterinary-science/rss", "Frontiers Vet Sci", None),
        ],
    },
    {
        "name": "Agri-tech & Precision Farming",
        "slug": "agritech-precision-farming",
        "description": "Drones, soil sensors, crop modelling, AgTech VC, vertical farming, and the digital transformation of agriculture.",
        "feeds": [
            ("https://agfundernews.com/feed/",                     "AgFunder News",              None),
            ("https://precisionag.com/feed/",                      "Precision Ag",               None),
            ("https://www.agweb.com/rss",                          "AgWeb",                      None),
            ("https://modernfarmer.com/feed/",                     "Modern Farmer",              None),
            ("https://www.farmjournal.com/rss",                    "Farm Journal",               None),
            ("https://www.tridge.com/blog/rss",                    "Tridge Blog",                None),
            ("https://www.newfoodmagazine.com/feed/",              "New Food Magazine",          None),
            ("https://theland.com.au/feed/",                       "The Land",                   None),
        ],
    },
    {
        "name": "Philosophy & Critical Thinking",
        "slug": "philosophy-critical-thinking",
        "description": "Ethics, epistemology, political philosophy, logic, applied philosophy, and long-form essays that challenge assumptions.",
        "feeds": [
            ("https://aeon.co/feed.rss",                           "Aeon",                       None),
            ("https://www.philosophynow.org/rss",                  "Philosophy Now",             None),
            ("https://www.nybooks.com/feed/rss2/",                 "The New York Review of Books",None),
            ("https://iep.utm.edu/feed/",                          "Internet Encyclopedia of Philosophy", None),
            ("https://plato.stanford.edu/feed.rss",                "Stanford Encyclopedia of Philosophy", None),
            ("https://www.3quarksdaily.com/3quarksdaily/rss.xml",  "3 Quarks Daily",             None),
            ("https://philosophybro.com/feed",                     "Philosophy Bro",             None),
            ("https://daily.jstor.org/feed/",                      "JSTOR Daily",                None),
        ],
    },
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _seed(db, email: str | None = None) -> None:
    if email:
        owner = db.query(User).filter(User.email == email).first()
    else:
        owner = db.query(User).order_by(User.id).first()

    if not owner:
        print("ERROR: No user found.", file=sys.stderr)
        sys.exit(1)

    print(f"Seeding {len(COLLECTIONS)} collections as: {owner.email} (id={owner.id})\n")
    created = skipped = 0

    for spec in COLLECTIONS:
        existing = (
            db.query(Collection)
            .filter(Collection.owner_id == owner.id, Collection.slug == spec["slug"])
            .first()
        )
        if existing:
            print(f"  SKIP  {spec['name']!r}")
            skipped += 1
            continue

        coll = Collection(
            owner_id=owner.id,
            name=spec["name"],
            slug=spec["slug"],
            description=spec["description"],
            is_public=True,
            subscriber_count=0,
        )
        db.add(coll)
        db.flush()

        for position, (url, title, icon_url) in enumerate(spec["feeds"]):
            db.add(CollectionItem(
                collection_id=coll.id,
                feed_url=url,
                title=title,
                icon_url=icon_url,
                position=position,
            ))

        db.commit()
        print(f"  CREATE {spec['name']!r}  ({len(spec['feeds'])} feeds)")
        created += 1

    print(f"\n{'─'*50}")
    print(f"Done — {created} created, {skipped} skipped.")


if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else None
    db = SessionLocal()
    try:
        _seed(db, email)
    finally:
        db.close()
