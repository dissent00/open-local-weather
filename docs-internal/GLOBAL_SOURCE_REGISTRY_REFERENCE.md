# Open Local Weather — Global Source Registry Reference

**Status:** research checkpoint before building the 193-WMO-Member source matrix  
**Project:** `dissent00/open-local-weather`  
**Date:** 2026-09-03

---

## 1. Objective

Open Local Weather should eventually be useful almost anywhere in the world with very little configuration.

The desired fork/install experience is approximately:

```text
1. Enter latitude/longitude or place
2. Enter Gemini or another supported LLM API key
3. Configure delivery/email
4. Let OLW discover the appropriate global, regional and local meteorological sources
5. Optionally enable richer national sources or provide credentials where required
6. Run
```

The phone application should use the same underlying source-selection logic, but hide most of the configuration behind a location-based resolver and user-selectable source controls.

The long-term goal is therefore not merely a list of weather websites. It is a **global meteorological source capability registry** that allows OLW to determine what authoritative forecast and observation sources are relevant to a coordinate, how they can be accessed, what they physically represent, and how they should enter the existing forecast/verification system.

---

## 2. The gap OLW may be able to close

Meteorological information is abundant, but it is fragmented.

Different authoritative organizations expose different combinations of:

- numerical model guidance;
- official public forecasts;
- human-edited regional forecasts;
- agricultural forecasts;
- aviation observations;
- surface observations;
- warnings;
- radar;
- satellite;
- climatology;
- historical observations.

Access mechanisms vary just as much:

- documented APIs;
- JSON/XML feeds;
- WIS2;
- CAP;
- RSS/Atom;
- downloadable files;
- open-data portals;
- HTML pages;
- PDFs;
- authenticated services;
- paid services.

Products also differ in intended audience:

- general public;
- aviation;
- agriculture;
- marine;
- emergency management;
- professional meteorologists;
- numerical-weather-prediction users.

This fragmentation is not itself evidence that OLW is unique. However, it suggests a useful problem OLW can address:

> Consolidate relevant global, regional and national forecast information for a location, preserve provenance, combine it with independently recorded observations, and continuously measure how well the available forecast sources perform.

The potentially distinctive feature is therefore **consolidation + provenance + retrospective verification**, not consolidation alone.

---

## 3. Keep coverage/richness separate from accuracy

This is a core project decision.

A source can be:

- geographically dense but meteorologically simple;
- geographically sparse but extremely detailed;
- awkward to access but full of valuable human interpretation;
- beautifully machine-readable but mediocre at a particular location.

Therefore the global registry should describe **availability, coverage and richness** without declaring forecast quality.

Forecast accuracy belongs in OLW's existing verification system and should be learned empirically over time.

Conceptually:

```text
SOURCE REGISTRY
    ↓
What exists?
Where?
What does it contain?
How do we access it?
What kind of product is it?

        separate from

VERIFICATION RECORD
    ↓
How accurate has this source actually been
for this location / variable / lead time?
```

Do not mix the two.

---

## 4. WWIS: extremely valuable, but not equivalent to the native NMHS product

The WMO World Weather Information Service (WWIS) is a major discovery for OLW.

WWIS republishes **official forecasts supplied by participating National Meteorological and Hydrological Services (NMHSs)** in a standardized format.

At the current research checkpoint it contains approximately:

- 3,446 forecast cities;
- forecasts from 140 WMO Members;
- climatological information for a larger set of Members/cities.

WWIS provides a machine-readable global city catalogue and per-city JSON records.

Useful fields include:

```text
city name
latitude / longitude
city ID
station name
timezone

WMO Member
NMHS organization
NMHS URL
WMO region

forecast issue date/time
forecast date
weather description
minimum temperature
maximum temperature

climatology where available
```

This gives OLW a powerful **zero-configuration official-local-forecast layer**.

### Important interpretation

WWIS authority and native-product equivalence are different questions.

```text
WWIS forecast
    authority: national met office
    product: standardized WWIS city forecast

native NMHS forecast
    authority: same national met office
    product: may be substantially richer
```

Therefore:

```text
same authority ≠ same product
```

WWIS should not be assumed to reproduce everything on the national meteorological service's own website or data system.

---

## 5. Kenya/KMD: first case study

Kenya is an excellent calibration case because OLW already ingests a Kenya Meteorological Department product.

WWIS provides official KMD forecasts for numerous Kenyan cities, including Kisumu and many surrounding/regional locations.

The WWIS city product is compact:

```text
forecast date
weather category/description
minimum temperature
maximum temperature
issue information
```

KMD's native ecosystem is considerably richer.

KMD publishes, among other things:

- national seven-day forecasts;
- county weekly forecasts;
- county monthly/seasonal products;
- agro-advisories;
- warnings;
- other specialized products.

The county products can contain localized narrative interpretation and impacts/advice that cannot fit into the compact WWIS city schema.

Initial classification:

```yaml
wwis_vs_native:
  same_authority: true
  same_product: false
  relationship: subset
  confidence: high
```

### Still to determine

A same-issue-date comparison should establish whether:

1. the WWIS Kisumu forecast is directly derived from the same KMD forecast represented in the national/county bulletin;
2. it is a separate KMD city forecast;
3. the products sometimes disagree;
4. WWIS preserves temperature but loses useful timing/convective narrative;
5. one product verifies better than another.

The first four questions concern **product richness/equivalence**.

The fifth concerns **accuracy** and belongs in OLW's verification record.

### Useful warning discovery

WMO's alerting-authority information revealed an official KMD CAP/RSS warning endpoint, demonstrating why authoritative global metadata can uncover machine-readable national services that are not obvious from browsing the public website.

This suggests the global registry should actively seek CAP/RSS/API endpoints before accepting HTML/PDF scraping as the only option.

---

## 6. Initial cross-country richness examples

The early spot checks show that national meteorological ecosystems fall into very different patterns.

### Kenya

Pattern:

```text
WWIS: reasonably broad city coverage
native NMHS: richer human/regional/county narrative
native access: often PDF/website-oriented
```

Value of native adapter: high.

### United Kingdom

WWIS provides a modest set of city forecasts in the compact WWIS schema.

The Met Office native ecosystem offers substantially richer site/coordinate forecasts with:

- hourly/three-hourly/daily data;
- many meteorological variables;
- deterministic products;
- probabilistic products;
- coordinate/site lookup.

Some richer services require registration/API credentials.

Pattern:

```text
WWIS: zero-config official baseline
native: vastly richer
access: structured API, but credentials may be required
```

### Germany

WWIS covers a modest set of German cities.

DWD provides extensive machine-readable open data, including MOSMIX forecasts with many parameters and long forecast horizons.

Pattern:

```text
WWIS: useful official public-city forecast
native: extremely rich structured/open ecosystem
access: excellent
```

Important semantic caveat: MOSMIX is a postprocessed forecast product, not necessarily equivalent to an independent human forecaster's opinion.

### Australia

WWIS has exceptionally dense locality coverage — hundreds of Australian locations.

BOM's native ecosystem remains substantially richer, including structured forecasts, observations, warnings, radar and satellite products.

Pattern:

```text
WWIS spatial coverage: excellent
WWIS variable/temporal richness: modest
native richness: excellent
```

This proves that WWIS city count alone is not a sufficient measure.

### Japan

WWIS exposes only a small number of major Japanese cities.

JMA's native forecast/warning ecosystem is much richer geographically and meteorologically, including regional/prefectural products, probabilities and structured machine-readable information.

Pattern:

```text
WWIS: sparse
native: much richer spatially and meteorologically
```

Native integration has high value.

### India

WWIS itself contains a large number of IMD forecast locations.

Pattern to investigate:

```text
WWIS: potentially strong zero-config spatial coverage
native IMD: likely adds richer products
```

India is useful for testing how far WWIS alone can take OLW before a native adapter is necessary.

### Norway

WWIS contains only a limited number of major cities.

For a user outside those cities, a native coordinate/grid/district source is likely much more appropriate.

Pattern:

```text
WWIS: sparse major-city layer
native coordinate-based source: potentially essential
```

### Singapore

WWIS has essentially one city/location, but the country is a city-state.

This illustrates why raw location count is misleading.

```text
1 location in Singapore
```

may provide better practical geographic coverage than:

```text
20 locations in a very large country
```

Coverage should therefore be calculated geometrically.

---

## 7. Data richness should be multidimensional

Do not create one subjective `richness_score` yet.

Record separate dimensions.

### Spatial granularity

Examples:

```text
national
broad region
administrative region
county/prefecture
city
station/site
coordinate
grid
```

### Temporal granularity

Examples:

```text
seasonal
monthly
weekly
daily
6-hourly
3-hourly
hourly
sub-hourly
```

### Meteorological variable richness

Examples:

```text
condition only
min/max temperature
precipitation
precipitation probability
wind
gust
humidity
cloud
visibility
pressure
snow
thunder
etc.
```

### Probability richness

Examples:

```text
none
categorical likelihood
PoP
percentiles
ensemble/probabilistic distributions
```

### Narrative richness

Examples:

```text
none
short condition
edited forecast narrative
timing narrative
forecaster reasoning
impact statement
agricultural advice
```

### Machine accessibility

Examples:

```text
PDF
HTML
RSS/Atom
CAP
downloadable structured file
XML
JSON
documented API
```

### Historical accessibility

Examples:

```text
none
web archive
download archive
historical files
queryable historical API
```

### Authentication/setup burden

Examples:

```text
none
free registration
API key
OAuth/token
paid subscription
institutional access
```

These fields describe usability without making an accuracy claim.

---

## 8. Product class must be preserved

An authoritative NMHS can publish several fundamentally different kinds of forecast.

Examples:

```text
human-edited public forecast
human narrative bulletin
automated site forecast
statistically postprocessed forecast
raw national NWP
ensemble/probabilistic forecast
warning
observation
climatology
```

These should not be collapsed merely because they come from the same agency.

This becomes especially important for forecast verification and source independence.

---

## 9. Authoritative does not mean independent

OLW will eventually ingest forecasts that share upstream information.

Example:

```text
ECMWF model
        ↓
national postprocessing
        ↓
NMHS automated forecast
        ↓
possibly human-edited forecast
        ↓
WWIS publication
```

Those outputs may look like multiple agreeing forecasts while sharing substantial upstream guidance.

Therefore the registry should preserve, where known:

```yaml
generation:
  type: human_edited | automated | postprocessed | raw_nwp | unknown
  upstream_models:
    - ...
```

This does not need to be fully solved during the first 193-Member audit.

The important rule is:

> Do not throw away provenance/product-type information that may later be needed to prevent false consensus.

---

## 10. WWIS coverage should be measured geometrically

Raw city counts are useful but insufficient.

For every WWIS location we have coordinates.

Therefore eventual country-level metrics can be derived from geography rather than assigned manually.

Potential metrics:

```text
number of WWIS forecast locations

median distance from populated area
to nearest WWIS location

90th-percentile distance

population-weighted distance

maximum populated-area distance

possibly elevation/terrain mismatch
where relevant
```

This distinguishes cases such as:

```text
Singapore:
1 WWIS location
excellent practical coverage

Japan:
7 WWIS locations
likely weak nationwide local coverage

Australia:
338 WWIS locations
very strong but needs geometric testing
```

For actual OLW source resolution, the most important value is ultimately:

```text
distance / representativeness
between user's coordinate
and candidate forecast location
```

---

## 11. Full METAR discovery should be automatic

The METAR station problem is much easier than the national-forecast problem.

NOAA/Aviation Weather provides worldwide machine-readable station information and worldwide METAR data.

Bulk station metadata can be refreshed automatically rather than maintained by hand.

Conceptual process:

```text
download worldwide station catalogue
        ↓
normalize/index coordinates and identifiers
        ↓
user location
        ↓
find nearby candidates
        ↓
check recent report activity
        ↓
rank by distance/elevation/freshness/representativeness
```

Users should not normally need to configure:

```yaml
icao: HKKI
```

Instead:

```yaml
metar:
  enabled: auto
```

The selected station should remain visible and overridable.

Station metadata existence must remain separate from actual reporting activity.

---

## 12. WIGOS/WIS2 surface discovery should also become automatic

The previously researched pattern remains:

```text
lat/lon
   ↓
OSCAR/Surface
   ↓
candidate WIGOS stations
   ↓
identity reconciliation
   ↓
WIS2 / METAR / national provider
   ↓
freshness / variable / elevation / representativeness
   ↓
usable observations
```

Multiple identifiers for one physical station must be deduplicated.

Example:

```text
Kisumu Airport

WIGOS 0-20000-0-63708
WMO 63708
ICAO HKKI
```

These represent one observing asset, not three independent witnesses.

---

## 13. Satellite selection can be automatic by geography

The observation research already established the likely global pattern:

```text
target coordinate
    ↓
satellite coverage/capability registry
    ↓
best operational GEO provider
    ↓
appropriate products
```

Examples:

```text
Africa/Europe:
EUMETSAT Meteosat/MTG

Americas:
NOAA GOES

East Asia/western Pacific:
JMA Himawari

other regions:
appropriate operational GEO/LEO providers
```

RealEarth can remain useful for aggregation, discovery, prototyping and fallback.

For Kisumu, EUMETSAT MTG FCI and Lightning Imager are high-priority observed-reality sources.

---

## 14. Radar should be geographically optional

Radar availability differs radically by country.

The resolver should not pretend otherwise.

Pattern:

```text
coordinate
    ↓
radar metadata discovery
    ↓
candidate radar/network
    ↓
actual data-provider lookup
    ↓
range / beam / terrain / freshness / access test
    ↓
usable radar or NONE
```

Kisumu is a useful negative example: catalogue metadata has conflicted with Kenya Met operational status, and no dependable local radar source has yet been identified.

A legitimate result is:

```text
radar: unavailable
```

For proof-of-concept implementations, Europe/OPERA and US/NEXRAD are better test environments.

---

## 15. Warnings can often use standardized feeds

The registry should seek warning sources in approximately this order:

```text
native NMHS CAP/API
        ↓
regional aggregator
        ↓
WMO/global CAP aggregation
        ↓
RSS
        ↓
HTML/PDF scrape
```

Examples already identified include:

- KMD CAP/RSS;
- MeteoAlarm in Europe;
- WMO severe-weather/CAP aggregation.

Warnings are a separate product class from ordinary forecasts.

---

## 16. Three registries, not one

The implementation concept is clearer if source metadata is separated into three logical registries.

### Country registry

Answers:

```text
Which NMHS is responsible?
What native products exist?
Which regional systems apply?
What integration tier applies?
```

Example:

```yaml
KE:
  nmhs: Kenya Meteorological Department
  wwis: true
  native_adapters:
    - kmd_7_day
    - kmd_county
  warnings:
    - kmd_cap
```

### Asset registry

Answers:

```text
What physical observing assets/products are relevant near this coordinate?
```

Examples:

- WIGOS station;
- METAR airport;
- radar;
- satellite coverage;
- radiosonde site;
- buoy.

Much of this should be generated/refreshed automatically.

### Provider registry

Answers:

```text
How do we retrieve a particular kind of data?
```

Examples:

```text
WWIS
Aviation Weather
WIS2
EUMETSAT
RealEarth
OPERA
NEXRAD
KMD
DWD
Met Office DataHub
```

This prevents hard-coded assumptions such as:

```text
Kenya = HKKI
```

Instead:

```text
Kenya permits relevant provider classes
+
coordinate resolver discovers current assets
```

---

## 17. Automatic coordinate-based source selection

The intended user experience is:

```text
Enter:
Kisumu, Kenya
```

or simply coordinates.

The resolver should be able to produce something like:

```text
Automatically configured

GLOBAL FORECASTS
✓ GFS
✓ ECMWF
✓ ICON
✓ UKMET

OFFICIAL FORECAST
✓ WWIS — nearest applicable official KMD city forecast
✓ KMD richer regional/county product

OBSERVATIONS
✓ HKKI METAR
✓ WIGOS/WIS2 surface observations

SATELLITE
✓ EUMETSAT MTG

RADAR
— no reliable local source

WARNINGS
✓ KMD CAP
```

Every choice should remain visible and overridable.

---

## 18. Proposed source-selection logic

A simplified official-forecast resolver:

```text
location
    ↓
country
    ↓
native structured/open NMHS product available?
    │
    ├── YES → enable where appropriate
    │
    └── NO
    │
    ▼
WWIS city available and representative?
    │
    ├── YES → enable
    │
    └── NO
    │
    ▼
native scrape adapter available?
    │
    ├── YES → enable
    │
    └── NO → no national forecast source
```

In practice both WWIS and a richer native source may be enabled simultaneously because they can represent different official products.

---

## 19. Four OLW integration tiers

This is a useful user-facing and implementation-facing classification.

### Tier 0 — Global automatic

No country-specific setup.

Potential baseline:

```text
GFS
ECMWF
ICON
UKMET
WWIS nearest official forecast
METAR automatic discovery
WIGOS/WIS2 automatic discovery
GEO satellite automatic selection
global/regional warning feeds where available
```

### Tier 1 — Native open integration

Country recognized automatically and a richer structured national source is enabled without credentials.

Example class:

```text
Germany → DWD open data
```

### Tier 2 — Native integration requiring credentials

Country has a richer official service but the user must provide an API key/account.

Example class:

```text
UK → Met Office DataHub
```

UI can explain that this is optional and what additional richness it provides.

### Tier 3 — Native extraction/scrape

Official product is valuable but requires PDF/HTML extraction.

Example:

```text
Kenya → KMD bulletin
```

These adapters are likely more brittle and need maintenance.

---

## 20. The matrix should identify where native adapters buy the most

Do not research 193 Members equally deeply before deriving the global facts.

First populate automatically where possible:

```text
WMO Member
ISO codes
WMO region
NMHS identity/site
WWIS participation
WWIS location count
WWIS coordinates
METAR catalogue coverage
WIGOS availability
satellite region
warning metadata
radar metadata
```

Then manually research native NMHS products.

A useful derived question is:

> Which countries have the largest gap between what WWIS exposes and what the native NMHS makes available?

Those are high-value adapter targets.

Possible categories:

```text
A
WWIS dense + native adds little
→ low native-adapter priority

B
WWIS dense + native dramatically richer
→ medium/high priority

C
WWIS sparse + excellent native structured data
→ very high priority

D
WWIS sparse/absent + native PDF/HTML only
→ high value but higher maintenance

E
very limited accessible national data
→ global sources dominate
```

---

## 21. Proposed 193-Member matrix fields

The next research artifact should contain at least:

### Identity

```text
country/member
ISO2
ISO3
WMO region
territory/state status
NMHS organization
official NMHS website
```

### WWIS

```text
WWIS forecast participation
forecast-location count
location coordinates
forecast horizon
issue frequency if discoverable
variables
machine format
nearest-location/geometric metrics later
```

### Native NMHS public forecasts

For each relevant product:

```text
product name
product class
intended audience
spatial representation
temporal resolution
forecast horizon
variables
probabilities
narrative richness
human-edited/automated/postprocessed/unknown
format
API/feed URL
authentication
licensing
historical access
adapter status
```

### Warnings

```text
native CAP
regional CAP
WMO/SWIC coverage
RSS/Atom
fallback scrape
```

### Observations

```text
METAR availability
WIGOS/OSCAR
WIS2
national surface network
upper-air
marine/buoy
other useful direct observations
```

### Remote sensing

```text
primary GEO satellite provider
satellite access route
radar metadata
radar actual-data provider
radar usability/access
```

### OLW integration

```text
integration tier
zero-config sources
optional credentialed sources
native adapter value
implementation priority
research confidence
notes
```

### WWIS/native relationship

```text
same authority
same product?
relationship:
    equivalent
    near-equivalent
    subset
    different product
    unknown

comparison status
```

Do not put forecast accuracy in this matrix except as a link/reference to later verification results.

---

## 22. Accuracy remains an OLW output

Once an official forecast source can be archived, it can potentially be scored like other forecast sources.

Example:

```text
WWIS Kisumu forecast
KMD county forecast
GFS
ECMWF
ICON
UKMET
       ↓
same observed-reality record
       ↓
verification
```

Potential future results:

```text
temperature MAE
rain-day accuracy
precipitation timing
wind error
cloud error
etc.
```

This creates a useful empirical question:

> How good is the responsible national meteorological service at this location and forecast vector compared with the numerical models?

The registry should enable that experiment but not prejudge its answer.

---

## 23. Why the global-source registry matters to OLW

The project is increasingly capable of answering a richer question than a conventional weather frontend:

> What do the relevant forecasting systems say for this location, what does the responsible national meteorological service say, what did independently recorded observations show actually happened, and which sources have historically been most reliable for each forecast variable and lead time?

For Kisumu the eventual evidence set might be:

```text
FORECAST GUIDANCE
GFS
ECMWF
ICON
UKMET

OFFICIAL PUBLIC FORECAST
WWIS / KMD

RICH LOCAL HUMAN PRODUCT
KMD county/national bulletin

SURFACE REALITY
METAR
WIGOS/WIS2

CLOUD/CONVECTION REALITY
MTG FCI
MTG Lightning Imager

HISTORICAL/FALLBACK REALITY
ERA5
```

OLW can normalize the heterogeneity while retaining its provenance.

The heterogeneity is not merely an inconvenience. It is potentially valuable information.

---

# 24. Next step — build the global matrix

The schema is now sufficiently mature.

Do not spend another research cycle redesigning it before gathering data.

Proceed with a **193-WMO-Member master matrix**, preferably grouped by WMO Region.

### Pass 1 — globally derivable facts

Populate:

```text
Member
ISO codes
WMO region
NMHS
official website
WWIS participation
WWIS forecast-location count
WWIS coordinates
known global observation layers
satellite region/provider
global warning metadata
```

### Pass 2 — native NMHS research

For every Member, identify:

```text
native public forecast products
structured/API access
authentication
CAP/warnings
open observations
radar
historical access
product class
data richness
```

### Pass 3 — classify

Assign:

```text
OLW integration tier
WWIS/native relationship
native-adapter value
research confidence
```

### Pass 4 — geometric analysis

Using country/population geometry where practical, derive WWIS spatial-coverage measures rather than relying only on raw city count.

### Pass 5 — richness spot checks

Compare same-date WWIS and native products for deliberately different services.

Start with:

```text
Kenya
United Kingdom
Germany
Australia
Japan
```

Kenya should be the first detailed product-equivalence case because OLW already has a native KMD adapter and observed-reality infrastructure.

### Pass 6 — accuracy later

Archive and score the newly integrated official forecasts through OLW.

Do not confuse this with the matrix-building exercise.

---

## 25. Working principle

The registry should ultimately allow an OLW user to supply a coordinate and have the system answer:

```text
What authoritative forecast sources apply here?
What global models apply here?
What local observations are available?
What satellite observes here?
Is useful radar available?
Where do official warnings come from?
Which sources require credentials?
Which richer native products are worth enabling?
```

Then OLW's verification system answers a different question over time:

```text
Which of those sources have actually been right?
```

Keeping those two questions separate is one of the most important design decisions made during this research.
