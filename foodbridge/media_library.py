"""Learning media shown on the About page and the sign-in / sign-up screens.

We don't host or copy anyone else's audio/video. Videos are the organisations' own
YouTube uploads, embedded with YouTube's standard player (loaded only after the visitor
presses play). Audio items are links out to the publisher's own player.

To add or change an item, edit the lists below - no template changes needed.
"""

VIDEOS = [
    {
        "id": "eOHxZox9Evw",
        "title": "A day in the life of a food rescue driver",
        "source": "OzHarvest",
        "blurb": "Ride along with an OzHarvest driver collecting surplus food and delivering it to charities - "
                 "the same journey a FoodBridge driver makes.",
    },
    {
        "id": "OnEr3H1OaVU",
        "title": "OzHarvest 2025 impact",
        "source": "OzHarvest",
        "blurb": "A look at what a year of food rescue adds up to: tonnes of food, millions of meals and the "
                 "charities that receive them.",
    },
]

# The clip used by the "Watch how food rescue works" button on the login / sign-up pages.
INTRO_VIDEO = VIDEOS[0]

AUDIO = [
    {
        "title": "Ronni Kahn: the \"Order of the Teaspoon\" (Big Ideas, ABC Radio National)",
        "source": "ABC listen",
        "blurb": "OzHarvest's founder on ending food insecurity and food waste - a lecture recorded for the "
                 "National Library of Australia (about 55 minutes).",
        "url": "https://www.abc.net.au/listen/programs/bigideas/oz-harvest-ronni-kahn-food-insecurity-waste/106997436",
    },
    {
        "title": "Ronni Kahn on OzHarvest, food waste and ending hunger (Uncommon Ground)",
        "source": "Apple Podcasts",
        "blurb": "How an events-catering business became a national food rescue movement, told by the founder.",
        "url": "https://podcasts.apple.com/us/podcast/uncommon-ground-with-talal-yassine/id1832373691",
    },
]

# Public figures quoted on the About page, each with the page they came from.
BENCHMARKS = [
    {"num": "17,500", "unit": "tonnes", "label": "of fresh food rescued by OzHarvest in 2025, delivered as 35 million meals to 1,550+ charities",
     "source": "OzHarvest 2025 impact", "url": "https://www.youtube.com/watch?v=OnEr3H1OaVU"},
    {"num": "350M", "unit": "meals", "label": "distributed by SecondBite since it began in 2005, across every state and territory",
     "source": "SecondBite - About us", "url": "https://secondbite.org/about-secondbite/"},
    {"num": "7.6M", "unit": "tonnes", "label": "of food wasted in Australia every year, costing the economy about $36.6 billion",
     "source": "OzHarvest partner page (MedHealth)", "url": "https://www.medhealth.com.au/ozharvest/"},
]

PARTNER_LINKS = [
    {"name": "OzHarvest", "what": "Food rescue from registered businesses, delivered free to charities",
     "url": "https://www.ozharvest.org/food/give-food/"},
    {"name": "SecondBite", "what": "Surplus food from growers, manufacturers and retailers to community partners",
     "url": "https://secondbite.org/donate-food/"},
]
