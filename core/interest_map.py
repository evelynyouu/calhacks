"""Curated map of niche interests -> publicly traded stocks.

When someone types a specific interest that doesn't match a broad theme, we look here first
before falling back to live symbol search. Each entry maps one or more keywords to a short
list of tickers — real companies that represent that interest.

Keep lists short (2-4 tickers): this is a personalization slice, not a full sector ETF.
"""

# {normalized_keyword: [(symbol, name), ...]}
# Keywords are lowercase, matched by substring or exact word.
NICHE_MAP: dict[str, list[tuple[str, str]]] = {
    # Sports teams (publicly traded)
    "sports team":     [("MANU", "Manchester United"), ("MSGS", "MSG Sports (Knicks/Rangers)"), ("BATRK", "Atlanta Braves"), ("TKO", "TKO Group (UFC/WWE)")],
    "sports teams":    [("MANU", "Manchester United"), ("MSGS", "MSG Sports (Knicks/Rangers)"), ("BATRK", "Atlanta Braves"), ("TKO", "TKO Group (UFC/WWE)")],
    "soccer":          [("MANU", "Manchester United"), ("TKO", "TKO Group Holdings")],
    "football":        [("MSGS", "MSG Sports"), ("BATRK", "Atlanta Braves"), ("TKO", "TKO Group Holdings")],
    "basketball":      [("MSGS", "MSG Sports (Knicks/Rangers)"), ("DKNG", "DraftKings")],
    "baseball":        [("BATRK", "Atlanta Braves"), ("MSGS", "MSG Sports")],
    "ufc":             [("TKO", "TKO Group Holdings (UFC/WWE)")],
    "mma":             [("TKO", "TKO Group Holdings (UFC/WWE)")],
    "wrestling":       [("TKO", "TKO Group Holdings (UFC/WWE)")],
    "wwe":             [("TKO", "TKO Group Holdings (UFC/WWE)")],
    "formula one":     [("FWONK", "Liberty Formula One"), ("RACE", "Ferrari")],
    "formula 1":       [("FWONK", "Liberty Formula One"), ("RACE", "Ferrari")],
    "f1":              [("FWONK", "Liberty Formula One"), ("RACE", "Ferrari")],
    "nascar":          [("FWONA", "Liberty Media / NASCAR")],
    "golf":            [("ACGL", "Acushnet Holdings (Titleist)"), ("ELY", "Callaway Golf")],
    "esports":         [("MSFT", "Microsoft (Activision/Xbox)"), ("EA", "Electronic Arts"), ("RBLX", "Roblox")],

    # Coffee & food
    "coffee":          [("SBUX", "Starbucks"), ("QSR", "Restaurant Brands (Tim Hortons)")],
    "starbucks":       [("SBUX", "Starbucks")],
    "fast food":       [("MCD", "McDonald's"), ("QSR", "Restaurant Brands"), ("YUM", "Yum! Brands")],
    "pizza":           [("DPZ", "Domino's Pizza"), ("YUM", "Yum! Brands (Pizza Hut)")],
    "chipotle":        [("CMG", "Chipotle Mexican Grill")],
    "restaurants":     [("MCD", "McDonald's"), ("CMG", "Chipotle"), ("DRI", "Darden Restaurants")],
    "alcohol":         [("BUD", "Anheuser-Busch InBev"), ("STZ", "Constellation Brands"), ("DEO", "Diageo")],
    "beer":            [("BUD", "Anheuser-Busch InBev"), ("SAM", "Boston Beer Company")],
    "wine":            [("STZ", "Constellation Brands"), ("WVVI", "Willamette Valley Vineyards")],
    "chocolate":       [("HSY", "Hershey"), ("MDLZ", "Mondelez")],

    # Fashion & outdoor
    "sneakers":        [("NKE", "Nike"), ("SKX", "Skechers"), ("ONON", "On Holdings")],
    "nike":            [("NKE", "Nike")],
    "adidas":          [("ADDYY", "Adidas")],
    "outdoor":         [("VFC", "VF Corp (The North Face/Timberland)"), ("COLM", "Columbia Sportswear"), ("MODG", "Callaway/TopGolf")],
    "arcteryx":        [("AMER", "Amer Sports (Arc'teryx)")],
    "hiking":          [("VFC", "VF Corp (The North Face)"), ("COLM", "Columbia Sportswear")],
    "luxury":          [("LVMUY", "LVMH"), ("TPR", "Tapestry (Coach/Kate Spade)"), ("CPRI", "Capri (Michael Kors/Versace)")],
    "streetwear":      [("NKE", "Nike"), ("VFC", "VF Corp (Supreme/Vans)")],

    # Entertainment & media
    "streaming":       [("NFLX", "Netflix"), ("DIS", "Disney"), ("PARA", "Paramount")],
    "netflix":         [("NFLX", "Netflix")],
    "disney":          [("DIS", "Walt Disney")],
    "music":           [("SPOT", "Spotify"), ("LYV", "Live Nation Entertainment"), ("UMG", "Universal Music Group")],
    "concerts":        [("LYV", "Live Nation Entertainment"), ("MSGS", "MSG Entertainment")],
    "movies":          [("DIS", "Walt Disney"), ("PARA", "Paramount"), ("NFLX", "Netflix")],
    "youtube":         [("GOOGL", "Alphabet (YouTube)")],
    "podcasts":        [("SPOT", "Spotify"), ("SIRI", "SiriusXM")],

    # Tech & games
    "video games":     [("MSFT", "Microsoft (Xbox/Activision)"), ("EA", "Electronic Arts"), ("TTWO", "Take-Two Interactive")],
    "gaming":          [("MSFT", "Microsoft"), ("EA", "Electronic Arts"), ("RBLX", "Roblox")],
    "roblox":          [("RBLX", "Roblox")],
    "minecraft":       [("MSFT", "Microsoft")],
    "fortnite":        [("TTWO", "Take-Two Interactive")],
    "iphone":          [("AAPL", "Apple")],
    "apple":           [("AAPL", "Apple")],
    "tesla":           [("TSLA", "Tesla")],
    "electric cars":   [("TSLA", "Tesla"), ("RIVN", "Rivian"), ("F", "Ford")],
    "ev":              [("TSLA", "Tesla"), ("RIVN", "Rivian"), ("GM", "General Motors")],
    "crypto":          [("COIN", "Coinbase"), ("MSTR", "MicroStrategy"), ("MARA", "Marathon Digital")],
    "bitcoin":         [("COIN", "Coinbase"), ("MSTR", "MicroStrategy")],
    "ai":              [("NVDA", "Nvidia"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet")],
    "social media":    [("META", "Meta"), ("SNAP", "Snap"), ("PINS", "Pinterest")],
    "tiktok":          [("META", "Meta"), ("SNAP", "Snap")],
    "instagram":       [("META", "Meta (Instagram)")],

    # Health & wellness
    "gym":             [("PTON", "Peloton"), ("NKE", "Nike"), ("LULU", "Lululemon")],
    "yoga":            [("LULU", "Lululemon"), ("PTON", "Peloton")],
    "lululemon":       [("LULU", "Lululemon Athletica")],
    "supplements":     [("CELH", "Celsius Holdings"), ("MNST", "Monster Beverage")],
    "energy drinks":   [("MNST", "Monster Beverage"), ("CELH", "Celsius Holdings")],
    "meditation":      [("CALM", "Calm"), ("PTON", "Peloton")],

    # Travel & lifestyle
    "travel":          [("BKNG", "Booking Holdings"), ("ABNB", "Airbnb"), ("AAL", "American Airlines")],
    "airbnb":          [("ABNB", "Airbnb")],
    "hotels":          [("MAR", "Marriott"), ("HLT", "Hilton"), ("H", "Hyatt")],
    "airlines":        [("DAL", "Delta Air Lines"), ("UAL", "United Airlines"), ("AAL", "American Airlines")],
    "cruises":         [("CCL", "Carnival"), ("RCL", "Royal Caribbean"), ("NCLH", "Norwegian Cruise")],
    "theme parks":     [("DIS", "Walt Disney"), ("SIX", "Six Flags")],
    "camping":         [("YETI", "YETI Holdings"), ("COLM", "Columbia Sportswear")],

    # Finance & investing
    "sports betting":  [("DKNG", "DraftKings"), ("PENN", "PENN Entertainment"), ("MGM", "MGM Resorts")],
    "gambling":        [("MGM", "MGM Resorts"), ("DKNG", "DraftKings"), ("LVS", "Las Vegas Sands")],

    # Space & defense
    "space":           [("RKLB", "Rocket Lab"), ("SPCE", "Virgin Galactic"), ("BA", "Boeing")],
    "spacex":          [("RKLB", "Rocket Lab")],  # SpaceX is private; closest public play

    # Cars
    "cars":            [("TSLA", "Tesla"), ("F", "Ford"), ("GM", "General Motors")],
    "ferrari":         [("RACE", "Ferrari")],
    "porsche":         [("P911", "Porsche AG")],

    # Pets
    "pets":            [("CHWY", "Chewy"), ("FRPT", "Freshpet"), ("IDXX", "IDEXX Laboratories")],
    "pet care":        [("CHWY", "Chewy"), ("FRPT", "Freshpet"), ("IDXX", "IDEXX Laboratories")],
    "dogs":            [("CHWY", "Chewy"), ("FRPT", "Freshpet")],
    "cats":            [("CHWY", "Chewy"), ("FRPT", "Freshpet")],

    # Photography & art
    "photography":     [("GPRO", "GoPro"), ("SONY", "Sony"), ("COHR", "Coherent Corp")],
    "cameras":         [("GPRO", "GoPro"), ("SONY", "Sony")],
    "art":             [("SOTHBY", "Sotheby's"), ("LVMUY", "LVMH")],

    # Books & education
    "books":           [("AMZN", "Amazon (Kindle)"), ("CHGG", "Chegg")],
    "education":       [("CHGG", "Chegg"), ("DUOL", "Duolingo"), ("2U", "2U")],
    "language learning": [("DUOL", "Duolingo")],

    # Home & design
    "home":            [("HD", "Home Depot"), ("LOW", "Lowe's"), ("W", "Wayfair")],
    "furniture":       [("W", "Wayfair"), ("RH", "RH (Restoration Hardware)")],
    "real estate":     [("AMT", "American Tower"), ("PLD", "Prologis"), ("O", "Realty Income")],

    # Beauty & personal care
    "beauty":          [("ELF", "e.l.f. Beauty"), ("ULTA", "Ulta Beauty"), ("EL", "Estee Lauder")],
    "skincare":        [("ELF", "e.l.f. Beauty"), ("EL", "Estee Lauder")],
    "cosmetics":       [("ELF", "e.l.f. Beauty"), ("ULTA", "Ulta Beauty")],

    # Fitness tech
    "peloton":         [("PTON", "Peloton")],
    "fitness tracker": [("AAPL", "Apple (Apple Watch)"), ("GRMN", "Garmin")],
    "running":         [("NKE", "Nike"), ("ONON", "On Holdings"), ("ASICS", "Asics")],

    # Food delivery & grocery
    "food delivery":   [("DASH", "DoorDash"), ("UBER", "Uber Eats")],
    "grocery delivery": [("DASH", "DoorDash"), ("AMZN", "Amazon (Whole Foods)")],
    "meal kits":       [("APRN", "Blue Apron")],

    # Finance & payments
    "investing":       [("SCHW", "Charles Schwab"), ("HOOD", "Robinhood"), ("IBKR", "Interactive Brokers")],
    "credit cards":    [("V", "Visa"), ("MA", "Mastercard"), ("AXP", "American Express")],
    "payments":        [("V", "Visa"), ("MA", "Mastercard"), ("PYPL", "PayPal")],
    "banking":         [("JPM", "JPMorgan Chase"), ("BAC", "Bank of America"), ("WFC", "Wells Fargo")],

    # Cybersecurity
    "cybersecurity":   [("CRWD", "CrowdStrike"), ("PANW", "Palo Alto Networks"), ("ZS", "Zscaler")],
    "privacy":         [("CRWD", "CrowdStrike"), ("NET", "Cloudflare")],

    # Cloud & SaaS
    "cloud":           [("AMZN", "Amazon AWS"), ("MSFT", "Microsoft Azure"), ("GOOGL", "Google Cloud")],
    "saas":            [("CRM", "Salesforce"), ("NOW", "ServiceNow"), ("WDAY", "Workday")],

    # Clean energy
    "solar":           [("ENPH", "Enphase Energy"), ("FSLR", "First Solar"), ("SEDG", "SolarEdge")],
    "clean energy":    [("ENPH", "Enphase Energy"), ("NEE", "NextEra Energy"), ("FSLR", "First Solar")],
    "wind energy":     [("NEE", "NextEra Energy"), ("GE", "GE Vernova")],

    # Defense & aerospace
    "defense":         [("LMT", "Lockheed Martin"), ("RTX", "RTX Corp"), ("NOC", "Northrop Grumman")],
    "rockets":         [("RKLB", "Rocket Lab"), ("BA", "Boeing")],
    "satellites":      [("RKLB", "Rocket Lab"), ("IRDM", "Iridium Communications")],

    # Healthcare
    "biotech":         [("MRNA", "Moderna"), ("REGN", "Regeneron"), ("VRTX", "Vertex Pharmaceuticals")],
    "weight loss":     [("NVO", "Novo Nordisk (Ozempic)"), ("LLY", "Eli Lilly (Zepbound)")],
    "ozempic":         [("NVO", "Novo Nordisk")],
    "mental health":   [("ACAD", "Acadia Pharmaceuticals"), ("PRAX", "Praxis Precision Medicine")],
}


def lookup(phrase: str) -> list[tuple[str, str]]:
    """Return [(symbol, name), ...] for a niche interest phrase, or [] if none found."""
    p = phrase.lower().strip()
    # Exact match first
    if p in NICHE_MAP:
        return NICHE_MAP[p]
    # Substring match: phrase contains a key or key contains the phrase
    for key, tickers in NICHE_MAP.items():
        if key in p or p in key:
            return tickers
    return []
