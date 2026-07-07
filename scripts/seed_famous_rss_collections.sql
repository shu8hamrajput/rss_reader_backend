-- seed_famous_rss_collections.sql
-- ---------------------------------------------------------------------------
-- Seeds public, discoverable collections built from the RSS feeds people
-- actually subscribe to most (Hacker News, TechCrunch, BBC, NYT, ESPN, ...),
-- grouped into collections that target distinct reader cohorts (tech,
-- developers, world news, business, science, gaming, design, productivity,
-- sports, entertainment).
--
-- Idempotent: re-running skips collections/items that already exist
-- (relies on uq_collection_owner_slug and uq_collection_item_url).
--
-- Usage:
--   psql "$DATABASE_URL" -f scripts/seed_famous_rss_collections.sql
--   psql "$DATABASE_URL" -v owner_email='you@example.com' -f scripts/seed_famous_rss_collections.sql
--
-- If owner_email is omitted (or matches no user), the collections are owned
-- by the lowest-id user in the database. At least one user must exist.
-- ---------------------------------------------------------------------------

\if :{?owner_email}
\else
\set owner_email ''
\endif

BEGIN;

DROP TABLE IF EXISTS _seed_owner;
CREATE TEMP TABLE _seed_owner AS
SELECT id FROM users
ORDER BY CASE WHEN :'owner_email' <> '' AND email = :'owner_email' THEN 0 ELSE 1 END, id
LIMIT 1;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM _seed_owner) THEN
        RAISE EXCEPTION 'No user found to own the seeded collections — create a user first.';
    END IF;
END $$;

-- ── Collections ──────────────────────────────────────────────────────────────

INSERT INTO collections (owner_id, name, slug, description, is_public, subscriber_count, created_at, updated_at)
SELECT o.id, c.name, c.slug, c.description, true, 0, now(), now()
FROM _seed_owner o, (VALUES
    ('Tech News Essentials',
     'tech-news-essentials',
     'The most-followed technology news feeds on the internet — gadgets, product launches, and industry moves for the general tech reader.'),
    ('Web & Software Developer',
     'web-software-developer',
     'The RSS staples of working developers: front-end craft, dev blogs, and the sites every engineer has had in their reader at some point.'),
    ('World News Wire',
     'world-news-wire',
     'Global headlines from the wire services and broadcasters most people actually subscribe to for breaking news.'),
    ('Business & Markets',
     'business-and-markets',
     'Markets, management thinking, and company news from the business publications with the largest, most loyal RSS followings.'),
    ('Science & Space Explorer',
     'science-and-space-explorer',
     'Discoveries, missions, and research highlights from the science and space feeds most subscribed to by the curious.'),
    ('Gaming Central',
     'gaming-central',
     'Reviews, industry news, and release coverage from the gaming press most followed by players.'),
    ('Design & Creativity',
     'design-and-creativity',
     'Visual inspiration, product design, and creative industry news for designers and art directors.'),
    ('Productivity & Life Hacks',
     'productivity-and-life-hacks',
     'Habits, focus, and life-optimisation writing from the most widely read productivity feeds.'),
    ('Sports Roundup',
     'sports-roundup',
     'Scores, analysis, and commentary from the sports outlets with the biggest RSS followings.'),
    ('Entertainment & Pop Culture',
     'entertainment-and-pop-culture',
     'Film, TV, and music coverage from the entertainment feeds most people keep in their reader.')
) AS c(name, slug, description)
ON CONFLICT (owner_id, slug) DO NOTHING;

-- ── Collection items ─────────────────────────────────────────────────────────

INSERT INTO collection_items (collection_id, feed_url, title, icon_url, position, created_at)
SELECT col.id, i.feed_url, i.title, NULL, i.position, now()
FROM _seed_owner o
JOIN collections col ON col.owner_id = o.id
JOIN (VALUES
    -- Tech News Essentials
    ('tech-news-essentials', 'https://news.ycombinator.com/rss',            'Hacker News',    0),
    ('tech-news-essentials', 'https://techcrunch.com/feed/',                'TechCrunch',     1),
    ('tech-news-essentials', 'https://www.theverge.com/rss/index.xml',      'The Verge',      2),
    ('tech-news-essentials', 'https://feeds.arstechnica.com/arstechnica/index', 'Ars Technica', 3),
    ('tech-news-essentials', 'https://www.wired.com/feed/rss',              'Wired',          4),
    ('tech-news-essentials', 'https://www.engadget.com/rss.xml',            'Engadget',       5),
    ('tech-news-essentials', 'https://gizmodo.com/rss',                     'Gizmodo',        6),
    ('tech-news-essentials', 'https://mashable.com/feeds/rss/all',          'Mashable',       7),

    -- Web & Software Developer
    ('web-software-developer', 'https://css-tricks.com/feed/',                       'CSS-Tricks',            0),
    ('web-software-developer', 'https://www.smashingmagazine.com/feed/',             'Smashing Magazine',     1),
    ('web-software-developer', 'https://www.freecodecamp.org/news/rss/',             'freeCodeCamp',          2),
    ('web-software-developer', 'https://dev.to/feed',                                'DEV Community',         3),
    ('web-software-developer', 'https://stackoverflow.blog/feed/',                   'Stack Overflow Blog',   4),
    ('web-software-developer', 'https://developer.mozilla.org/en-US/blog/rss.xml',   'MDN Blog',              5),
    ('web-software-developer', 'https://developers.googleblog.com/feeds/posts/default', 'Google Developers Blog', 6),
    ('web-software-developer', 'https://alistapart.com/main/feed/',                  'A List Apart',          7),

    -- World News Wire
    ('world-news-wire', 'http://feeds.bbci.co.uk/news/rss.xml',                  'BBC News',        0),
    ('world-news-wire', 'https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml', 'The New York Times', 1),
    ('world-news-wire', 'https://www.theguardian.com/world/rss',                 'The Guardian',    2),
    ('world-news-wire', 'https://feeds.npr.org/1001/rss.xml',                    'NPR News',        3),
    ('world-news-wire', 'https://www.aljazeera.com/xml/rss/all.xml',             'Al Jazeera',      4),
    ('world-news-wire', 'http://rss.cnn.com/rss/cnn_topstories.rss',             'CNN Top Stories', 5),
    ('world-news-wire', 'https://feeds.a.dj.com/rss/RSSWorldNews.xml',           'WSJ World News',  6),
    ('world-news-wire', 'https://www.reutersagency.com/feed/',                   'Reuters',         7),

    -- Business & Markets
    ('business-and-markets', 'https://feeds.bloomberg.com/markets/news.rss', 'Bloomberg Markets',  0),
    ('business-and-markets', 'https://fortune.com/feed',                     'Fortune',            1),
    ('business-and-markets', 'https://www.forbes.com/real-time/feed2/',      'Forbes',             2),
    ('business-and-markets', 'https://hbr.org/feed',                         'Harvard Business Review', 3),
    ('business-and-markets', 'https://www.fastcompany.com/latest/rss',       'Fast Company',       4),
    ('business-and-markets', 'https://www.economist.com/business/rss.xml',   'The Economist — Business', 5),
    ('business-and-markets', 'https://www.cnbc.com/id/100003114/device/rss/rss.html', 'CNBC',      6),
    ('business-and-markets', 'https://www.wsj.com/xml/rss/3_7085.xml',       'WSJ Business',       7),

    -- Science & Space Explorer
    ('science-and-space-explorer', 'https://www.nasa.gov/rss/dyn/breaking_news.rss', 'NASA Breaking News', 0),
    ('science-and-space-explorer', 'https://www.nature.com/nature.rss',              'Nature',              1),
    ('science-and-space-explorer', 'http://rss.sciam.com/ScientificAmerican-Global', 'Scientific American', 2),
    ('science-and-space-explorer', 'https://www.space.com/feeds/all',                'Space.com',           3),
    ('science-and-space-explorer', 'https://www.quantamagazine.org/feed/',           'Quanta Magazine',     4),
    ('science-and-space-explorer', 'https://www.sciencedaily.com/rss/all.xml',       'ScienceDaily',        5),
    ('science-and-space-explorer', 'https://www.newscientist.com/feed/home/',        'New Scientist',       6),
    ('science-and-space-explorer', 'https://www.popsci.com/feed/',                   'Popular Science',     7),

    -- Gaming Central
    ('gaming-central', 'https://feeds.ign.com/ign/all',                    'IGN',                0),
    ('gaming-central', 'https://kotaku.com/rss',                           'Kotaku',             1),
    ('gaming-central', 'https://www.polygon.com/rss/index.xml',            'Polygon',            2),
    ('gaming-central', 'https://www.pcgamer.com/rss/',                     'PC Gamer',           3),
    ('gaming-central', 'https://www.eurogamer.net/feed',                   'Eurogamer',          4),
    ('gaming-central', 'https://www.rockpapershotgun.com/feed',            'Rock Paper Shotgun', 5),
    ('gaming-central', 'https://www.gamespot.com/feeds/mashup/',           'GameSpot',           6),
    ('gaming-central', 'https://www.nintendolife.com/feeds/latest',        'Nintendo Life',      7),

    -- Design & Creativity
    ('design-and-creativity', 'https://www.dezeen.com/feed/',              'Dezeen',             0),
    ('design-and-creativity', 'https://www.itsnicethat.com/rss',           'It''s Nice That',    1),
    ('design-and-creativity', 'https://www.creativebloq.com/feeds/all',    'Creative Bloq',      2),
    ('design-and-creativity', 'https://www.awwwards.com/blog/feed/',       'Awwwards Blog',      3),
    ('design-and-creativity', 'https://www.behance.net/blog/feed',         'Behance Blog',       4),
    ('design-and-creativity', 'https://design-milk.com/feed/',             'Design Milk',        5),
    ('design-and-creativity', 'https://www.thisiscolossal.com/feed/',      'Colossal',           6),
    ('design-and-creativity', 'https://99designs.com/blog/feed/',          '99designs Blog',     7),

    -- Productivity & Life Hacks
    ('productivity-and-life-hacks', 'https://lifehacker.com/rss',              'Lifehacker',       0),
    ('productivity-and-life-hacks', 'https://www.themarginalian.org/feed/',    'The Marginalian',  1),
    ('productivity-and-life-hacks', 'https://jamesclear.com/feed',             'James Clear',      2),
    ('productivity-and-life-hacks', 'https://zenhabits.net/feed/',             'Zen Habits',       3),
    ('productivity-and-life-hacks', 'https://www.calnewport.com/blog/feed/',   'Cal Newport',      4),
    ('productivity-and-life-hacks', 'https://fs.blog/feed/',                   'Farnam Street',    5),
    ('productivity-and-life-hacks', 'https://longreads.com/feed/',             'Longreads',        6),
    ('productivity-and-life-hacks', 'https://blog.ted.com/feed/',              'TED Blog',         7),

    -- Sports Roundup
    ('sports-roundup', 'https://www.espn.com/espn/rss/news',                          'ESPN',            0),
    ('sports-roundup', 'http://feeds.bbci.co.uk/sport/rss.xml?edition=uk',            'BBC Sport',       1),
    ('sports-roundup', 'https://www.skysports.com/rss/12040',                         'Sky Sports',      2),
    ('sports-roundup', 'https://bleacherreport.com/articles/feed',                    'Bleacher Report', 3),
    ('sports-roundup', 'https://theathletic.com/rss/',                                'The Athletic',    4),
    ('sports-roundup', 'https://www.si.com/rss/si_topstories.rss',                    'Sports Illustrated', 5),
    ('sports-roundup', 'https://sports.yahoo.com/rss/',                               'Yahoo Sports',    6),
    ('sports-roundup', 'https://www.goal.com/feeds/en/news',                          'Goal.com',        7),

    -- Entertainment & Pop Culture
    ('entertainment-and-pop-culture', 'https://ew.com/feed/',                          'Entertainment Weekly', 0),
    ('entertainment-and-pop-culture', 'https://www.rollingstone.com/feed/',            'Rolling Stone',        1),
    ('entertainment-and-pop-culture', 'https://variety.com/feed/',                     'Variety',              2),
    ('entertainment-and-pop-culture', 'https://www.hollywoodreporter.com/feed/',       'The Hollywood Reporter', 3),
    ('entertainment-and-pop-culture', 'https://pitchfork.com/rss/news/',               'Pitchfork',            4),
    ('entertainment-and-pop-culture', 'https://www.indiewire.com/feed/',               'IndieWire',            5),
    ('entertainment-and-pop-culture', 'https://www.vulture.com/rss/index.xml',         'Vulture',              6),
    ('entertainment-and-pop-culture', 'https://consequence.net/feed/',                 'Consequence',          7)
) AS i(collection_slug, feed_url, title, position) ON i.collection_slug = col.slug
ON CONFLICT (collection_id, feed_url) DO NOTHING;

DROP TABLE _seed_owner;

COMMIT;
