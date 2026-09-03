# Global Weather-Station Discovery & Observation Layer

## Technical handoff for implementation

**Purpose:** This document captures the weather-station portion of a
design discussion for a globally usable weather application. It is
intended to be handed directly to a coding LLM/controller (Claude Code,
Codex, etc.) as implementation context.

**Status:** Architecture/design stage. The recommended first
implementation is a bounded prototype, not a full worldwide
national-provider catalogue.

------------------------------------------------------------------------

## 1. Product goal

The application already/ultimately uses **global numerical
forecast-model data** to produce forecasts for arbitrary locations
worldwide.

This submodule adds **ground-truth surface observations** so the
application can:

1.  programmatically discover useful observing stations near any
    latitude/longitude;
2.  ingest their latest and historical observations;
3.  compare observations against forecast-model predictions;
4.  accumulate forecast-error/accuracy history;
5.  learn which stations are most representative of a forecast point and
    how model errors behave locally.

Initial observation-source scope:

-   WMO/WIGOS stations, discovered through OSCAR/Surface and
    observations distributed through WIS2 where available;
-   airport stations / METAR;
-   national meteorological-office networks where they add stations or
    observations not adequately represented through the global sources.

The desired user experience is automatic. A user should not manually
configure a station.

Conceptually:

``` text
user latitude/longitude
        |
        v
discover nearby observation sites
        |
        v
resolve duplicate identities/providers
        |
        v
test data availability + freshness
        |
        v
rank stations by usefulness
        |
        v
select best N (initially 3)
        |
        v
ingest normalized observations
        |
        v
forecast verification / accumulated accuracy model
```

------------------------------------------------------------------------

## 2. Most important architectural principle

### A station is not the same thing as a data source.

The same physical observing site may appear under several identifiers
and through several distribution systems.

For example, an airport station may simultaneously have:

-   a WIGOS Station Identifier (WSI);
-   a legacy WMO station number;
-   an ICAO airport/weather identifier;
-   possibly an IATA airport identifier;
-   a national meteorological-service identifier;
-   observations available through WIS2;
-   the same or overlapping observations available as METAR;
-   observations available through a national API.

Do **not** model these as separate physical stations.

Instead:

``` text
CanonicalStation
    |
    +-- StationIdentity(provider=WIGOS, id=...)
    +-- StationIdentity(provider=WMO, id=...)
    +-- StationIdentity(provider=ICAO, id=...)
    +-- StationIdentity(provider=NATIONAL, id=...)
    |
    +-- ObservationSource(WIS2, ...)
    +-- ObservationSource(METAR/AviationWeather, ...)
    +-- ObservationSource(NationalMetService, ...)
```

This separation is foundational. It enables deduplication, provenance,
fallback between providers, consistency checks, and future source
expansion.

------------------------------------------------------------------------

## 3. Recommended global source layers

### 3.1 WMO OSCAR/Surface: station discovery and metadata

Use OSCAR/Surface primarily as a **global observing-station catalogue**.

Useful metadata includes, where available:

-   WIGOS Station Identifier;
-   station name;
-   latitude/longitude;
-   elevation;
-   operational/declared status;
-   station/facility type;
-   observed variables;
-   programme/network affiliation;
-   other station identifiers and metadata.

The linked wis2box documentation makes an important distinction: station
metadata can be sourced/cached from OSCAR/Surface, while wis2box/WIS2
handles publication/distribution.

Treat OSCAR as an answer to:

> What recognized observing stations exist here, what are they, and what
> should they measure?

Do not assume that "registered/operational in OSCAR" means "a fresh
observation is currently accessible."

------------------------------------------------------------------------

### 3.2 WIS2: WMO observation discovery/distribution

WIS2 should be treated as an observation-distribution ecosystem, not as
a single universal endpoint such as:

``` text
GET /weather?lat=x&lon=y
```

Relevant WIS2 concepts:

-   Global Discovery Catalogue: discover datasets and access
    information;
-   Global Brokers: real-time notifications, using publish/subscribe
    (MQTT);
-   Global Caches: downloadable WIS2 Core data;
-   WIGOS identifiers carried with station observations;
-   BUFR is common for WMO observational payloads;
-   ecCodes is an appropriate decoder for BUFR;
-   `pywis-pubsub` is a WMO-oriented Python tool/library for WIS2
    subscription workflows.

A production WIS2 consumer may therefore look like:

``` text
Global Discovery Catalogue
        |
        v
discover relevant surface-observation dataset
        |
        v
subscribe to Global Broker notifications
        |
        v
notification identifies data/object
        |
        v
download from cache/origin
        |
        v
decode BUFR
        |
        v
normalize observation
        |
        v
local database
```

For the first prototype, avoid deploying a complete wis2box stack merely
to consume observations.

------------------------------------------------------------------------

### 3.3 Worldwide airport/METAR layer

Use a global METAR source as the second global baseline.

The U.S. Aviation Weather Center Data API is useful even for a worldwide
application because it provides worldwide METAR/station coverage and
machine-readable output. Its bulk/current caches are preferable to
making large numbers of individual REST calls.

This source is attractive because airport observations are:

-   widespread;
-   standardized;
-   frequent;
-   easy to parse relative to arbitrary national formats;
-   often cross-identifiable with WMO/WIGOS stations.

Typical METAR variables include:

-   air temperature;
-   dew point;
-   pressure/QNH;
-   wind direction/speed/gusts;
-   visibility;
-   clouds;
-   weather phenomena;
-   observation time.

Airport observations have representativeness limitations: airports are
not necessarily representative of dense urban cores, mountain slopes,
coastlines, etc. Preserve station type so ranking/learning can account
for this.

------------------------------------------------------------------------

### 3.4 National meteorological-service adapters

There is no universal national-met-office API.

National services may expose data via:

-   REST/JSON APIs;
-   OGC APIs;
-   downloadable CSV/files;
-   FTP/HTTP directories;
-   API keys;
-   WIS2 datasets;
-   bespoke systems;
-   partially open networks.

Therefore national services should be **plugins/adapters**, not
hard-coded branches throughout the application.

Suggested interface:

``` python
class StationProvider:
    name: str

    async def discover(
        self,
        latitude: float,
        longitude: float,
        radius_km: float,
    ) -> list["ProviderStation"]:
        ...

    async def observations(
        self,
        station_ids: list[str],
        since: datetime,
    ) -> list["ProviderObservation"]:
        ...
```

Potential future adapters:

``` text
WIS2Provider
AviationWeatherProvider
DWDProvider
MetOfficeProvider
MeteoFranceProvider
BOMProvider
EnvironmentCanadaProvider
KenyaMetProvider
...
```

The rest of the application should not care whether an observation
originated as BUFR, METAR, OGC JSON, CSV, etc.

------------------------------------------------------------------------

## 4. Provider registry/manifests

Separate provider configuration/metadata from adapter implementation.

Example:

``` yaml
provider: dwd
countries:
  - DE

station_discovery: true
observations: true

authentication:
  type: none

license:
  redistributable: true

variables:
  - air_temperature
  - relative_humidity
  - pressure
  - precipitation
  - wind_speed
  - wind_direction

adapter: providers.dwd.DWDProvider
```

Conceptual layout:

``` text
providers/
├── global/
│   ├── wis2.yaml
│   └── aviationweather.yaml
├── DE/
│   └── dwd.yaml
├── GB/
│   └── metoffice.yaml
├── AU/
│   └── bom.yaml
└── KE/
    └── kenmet.yaml
```

Runtime discovery:

``` python
global_providers = registry.global_providers()
country = country_for_coordinate(lat, lon)
national_providers = registry.providers_for_country(country)
```

This makes adding a country mostly:

``` text
adapter + provider manifest + tests
```

------------------------------------------------------------------------

## 5. Canonical station model

Suggested logical representation:

``` json
{
  "station_id": "wx_7f291a",
  "name": "Example Airport",
  "latitude": -1.3192,
  "longitude": 36.9278,
  "elevation_m": 1624,
  "station_type": "airport",
  "operational": true,

  "identifiers": {
    "wigos": "0-20000-0-63740",
    "wmo": "63740",
    "icao": "HKJK",
    "iata": "NBO"
  },

  "providers": [
    {
      "provider": "wis2",
      "provider_station_id": "0-20000-0-63740"
    },
    {
      "provider": "aviationweather",
      "provider_station_id": "HKJK"
    }
  ],

  "capabilities": [
    "air_temperature",
    "dew_point",
    "pressure",
    "wind_speed",
    "wind_direction",
    "visibility",
    "precipitation"
  ]
}
```

Do not assume all identifiers or capabilities exist.

------------------------------------------------------------------------

## 6. Identity reconciliation / deduplication

This is likely harder than basic station discovery.

Use progressively weaker evidence:

1.  **Explicit identifier cross-reference**
    -   WIGOS ↔ WMO ↔ ICAO ↔ national identifier.
2.  **Exact or nearly exact coordinates plus compatible identity/name.**
3.  **Very close coordinates plus compatible elevation/station
    type/name.**
4.  **Leave unresolved if ambiguous.**

Do not merge solely because two stations are geographically close.
Airports, research sites, agricultural networks, and national AWS
installations can legitimately coexist within a small area.

Preserve all provider identities after reconciliation.

This allows:

``` text
canonical station X
   |
   +-- WIS2 observation path
   +-- METAR observation path
   +-- national observation path
```

It also permits provider-consistency analysis.

If WIS2 and METAR represent the same physical observation, avoid
counting them as two independent verifying stations.

------------------------------------------------------------------------

## 7. Observation normalization

Normalize provider-specific data immediately after ingestion.

Suggested logical event:

``` json
{
  "station_id": "wx_7f291a",
  "source": "aviationweather",
  "source_station_id": "HKJK",

  "observed_at": "2026-09-03T05:00:00Z",
  "received_at": "2026-09-03T05:04:17Z",

  "variable": "air_temperature",
  "value": 21.4,
  "unit": "degC",

  "qc": {
    "provider_quality": null,
    "internal_quality": "good"
  },

  "raw_reference": "..."
}
```

A long-form observation table is attractive:

``` text
station_id
observed_at
variable
value
unit
source
source_station_id
qc
```

because different networks report different variables.

A materialized/wide "latest station observation" view can be generated
for application use.

Preserve raw-source provenance even if raw payloads are not retained
forever.

------------------------------------------------------------------------

## 8. Do not define "best" as merely "nearest"

The forecast-verification use case needs **representative** stations,
not simply geographically closest stations.

Potential scoring factors:

### Required/strong factors

-   geographic distance;
-   observation freshness;
-   required-variable availability;
-   station/data-source operational status;
-   historical reporting reliability.

### Important representativeness factors

-   elevation difference between forecast point and station;
-   station type;
-   terrain differences;
-   coastal vs inland position;
-   urban/rural differences;
-   potentially land-cover differences.

Conceptually:

``` python
score = (
    distance_penalty
    + elevation_penalty
    + observation_age_penalty
    + missing_variable_penalty
    + reliability_penalty
    + representativeness_penalty
)
```

Do not overfit the initial scoring formula. Record the components so
weights can later be learned from actual forecast-verification
performance.

The mature system may learn that:

> Station A is best for temperature at this grid cell, while Station B
> is more representative for wind or precipitation.

This can become a major advantage of accumulating historical
verification data.

------------------------------------------------------------------------

## 9. Adaptive station search

Do not request exactly the three nearest registered stations.

Instead, search until enough **usable** stations exist.

Example:

``` text
search within 25 km
       |
       +-- >= 3 usable? return ranked set
       |
       v
search within 50 km
       |
       +-- >= 3 usable? return ranked set
       |
       v
search within 100 km
       |
       +-- rank usable candidates
       |
       v
return best available, possibly fewer than 3
```

A station is not "usable" merely because metadata says it exists.

Track separately:

``` text
station exists
station metadata says operational
observation source is known
source is reachable
observation is fresh
required variable is present
station is representative enough
```

It is acceptable---and preferable---to return:

``` text
1/3 suitable verification stations available
```

rather than silently substitute a very distant or stale station.

------------------------------------------------------------------------

## 10. Suggested discovery workflow

``` python
async def discover_best_stations(
    lat,
    lon,
    variables,
    desired_count=3,
):
    candidates = []

    # Global sources
    candidates += await oscar.discover(lat, lon)
    candidates += await aviation_weather.discover(lat, lon)

    # Country-specific enrichment
    country = reverse_geocode_country(lat, lon)

    for provider in provider_registry.for_country(country):
        candidates += await provider.discover(lat, lon)

    # Convert provider records into canonical physical stations.
    stations = station_resolver.reconcile(candidates)

    # Test actual data availability/freshness.
    stations = await observation_availability.enrich(stations, variables)

    # Score physical stations, not duplicate provider records.
    ranked = station_ranker.rank(
        stations,
        target=(lat, lon),
        variables=variables,
    )

    return ranked[:desired_count]
```

A production implementation should avoid doing all remote discovery on
every app request. Maintain/cache station metadata locally and update it
periodically.

------------------------------------------------------------------------

## 11. Storage recommendation

### Prototype

SQLite is sufficient.

Tables might include:

``` text
stations
station_identifiers
station_sources
station_capabilities
latest_observations
observations
provider_health
```

### Larger deployment

PostgreSQL + PostGIS is a natural upgrade.

PostGIS makes spatial indexing and nearest-neighbor queries
straightforward and avoids repeatedly computing Haversine distances in
application code.

For current-weather lookup, keep a compact latest-observation
representation even if full history is also retained.

------------------------------------------------------------------------

## 12. Forecast-verification integration

The observation layer should feed a separate verification subsystem.

Conceptually:

``` text
ForecastRun
  model
  initialization_time
  valid_time
  grid/location
  predicted variables
        |
        | compare at valid_time
        v
CanonicalObservation
  station
  observed_time
  observed variables
        |
        v
ForecastVerification
  model
  station
  variable
  lead_time
  forecast value
  observed value
  error
  station suitability metadata
```

Preserve enough metadata to later analyze:

-   bias by model;
-   error by forecast lead time;
-   error by station;
-   error by variable;
-   error by season/time of day;
-   local model bias;
-   whether station representativeness affects apparent model accuracy.

Do not allow duplicate provider representations of the same physical
station/observation to artificially increase sample count.

------------------------------------------------------------------------

# 13. Kisumu, Kenya test case

A manual/live discovery exercise was performed around central Kisumu,
approximately:

``` text
latitude  ~ -0.092
longitude ~ 34.768
```

The test intentionally approximated the future resolver: prefer
official/recognized stations with recent accessible observations rather
than simply selecting the three closest station records.

The exercise exposed useful real-world edge cases.

## 13.1 Kisumu / Kisumu Airport

Approximate distance from central Kisumu: **\~4 km**.

Metadata/identity discovered:

``` text
Name:   Kisumu
WIGOS:  0-20000-0-63708
WMO:    63708
ICAO:   HKKI
Elevation: ~1171 m
```

This is an excellent demonstration of identity reconciliation:

``` text
WIGOS 0-20000-0-63708
WMO   63708
ICAO  HKKI
```

should resolve to one canonical physical station, not three.

A complete METAR surfaced during the test:

``` text
HKKI 021800Z 21005KT 9999 SCT024 25/16 Q1017
```

Decoded:

``` json
{
  "temperature_c": 25,
  "dewpoint_c": 16,
  "wind_direction_deg": 210,
  "wind_speed_kt": 5,
  "visibility_m": 10000,
  "pressure_hpa": 1017,
  "cloud_cover": "SCT",
  "cloud_base_ft": 2400,
  "observed_at": "2026-09-02T18:00:00Z"
}
```

However, at the time of the test this report was stale for a "latest
current weather" query.

**Lesson:** Excellent station metadata and identity do not guarantee a
currently fresh report. Freshness must be independently evaluated.

------------------------------------------------------------------------

## 13.2 Kericho

Approximate distance: **\~64 km**.

Identity:

``` text
WIGOS: 0-20000-0-63710
WMO:   63710
ICAO:  HKKR
Elevation: ~1992 m
```

A relatively fresh observation surfaced during the test, approximately
47 minutes old:

``` text
temperature: ~18 C
conditions: partly cloudy
visibility: ~30 km
wind: not present in the surfaced report
```

Kericho was therefore more useful from a freshness perspective than the
stale Kisumu report.

But Kericho is roughly **820 m higher** than Kisumu.

**Lesson:** A fresh station 64 km away and \~820 m higher should receive
a significant representativeness penalty for verifying Kisumu surface
temperature, despite being operational and fresh.

------------------------------------------------------------------------

## 13.3 Kisii

Approximate distance: **\~66 km**.

Identity:

``` text
WIGOS: 0-20000-0-63709
WMO:   63709
ICAO:  HKKS
Elevation: ~1767 m
```

The station appeared in official metadata, but no sufficiently current
public observation was verified during the test.

**Lesson:** "Operational station" and "currently usable data source" are
different states.

------------------------------------------------------------------------

## 13.4 Kakamega MET AWS

Approximate distance: **\~40 km**, making it geographically more
attractive than Kericho or Kisii.

Metadata found during the test:

``` text
Name: KAKAMEGA MET AWS
WIGOS: 0-404-300-372021012AS63681
Elevation: ~1585 m
```

Operational metadata was less reassuring: it appeared partly
operational/unknown/pre-operational depending on field/programme status,
and a reliable fresh public observation feed was not identified during
the test.

**Lesson:** A geographically excellent AWS should not outrank a more
distant station if its live data cannot actually be obtained reliably.

------------------------------------------------------------------------

## 13.5 What the Kisumu test proved

The resolver must distinguish:

``` text
EXISTS
    Station is present in authoritative metadata.

OPERATIONAL
    Metadata says the observing facility/network is operational.

ACCESSIBLE
    At least one usable observation source is known/reachable.

FRESH
    Recent observations are actually arriving.

CAPABLE
    Required variables are present.

REPRESENTATIVE
    The station is suitable for verifying this forecast point/variable.
```

These must not collapse into one boolean.

The test also demonstrated why WIGOS/WMO/ICAO deduplication is necessary
from the first implementation.

------------------------------------------------------------------------

# 14. Recommended prototype

Do **not** begin by implementing every national weather service.

Build a vertical slice that proves the abstraction.

Suggested repository/module shape:

``` text
station_observations/
├── models.py
├── registry.py
├── resolver.py
├── ranking.py
├── storage.py
├── providers/
│   ├── base.py
│   ├── oscar.py
│   ├── aviation_weather.py
│   └── wis2.py
├── decoders/
│   ├── metar.py
│   └── bufr.py
└── tests/
```

Possible provider responsibility split:

``` text
OSCARProvider
    station metadata/discovery

AviationWeatherProvider
    airport station metadata
    METAR observations

WIS2Provider
    WMO observation discovery/ingestion

National providers
    later enrichment
```

### Prototype milestone 1

Input:

``` json
{
  "latitude": -0.0917,
  "longitude": 34.768,
  "variables": [
    "air_temperature",
    "pressure",
    "wind_speed",
    "precipitation"
  ],
  "count": 3
}
```

Output:

``` json
{
  "target": {
    "latitude": -0.0917,
    "longitude": 34.768
  },
  "stations": [
    {
      "station_id": "...",
      "name": "...",
      "distance_km": 4.0,
      "elevation_m": 1171,
      "identifiers": {
        "wigos": "...",
        "wmo": "...",
        "icao": "..."
      },
      "sources": ["wis2", "aviationweather"],
      "latest_observation": {...},
      "freshness_seconds": 1200,
      "score": 0.91,
      "score_components": {
        "distance": ...,
        "elevation": ...,
        "freshness": ...,
        "availability": ...,
        "reliability": ...
      }
    }
  ],
  "rejected_candidates": [
    {
      "station_id": "...",
      "reason": "stale observation"
    }
  ]
}
```

Expose **score components and rejection reasons** during development. Do
not make the ranking algorithm opaque.

------------------------------------------------------------------------

## 15. Recommended prototype implementation order

1.  Define canonical station and provider-station models.
2.  Define normalized observation model.
3.  Implement SQLite persistence.
4.  Implement worldwide Aviation Weather station/METAR ingestion first.
    -   This is likely the easiest way to prove end-to-end discovery +
        observations.
5.  Implement OSCAR/WIGOS station metadata ingestion/discovery.
6.  Implement identity reconciliation:
    -   ICAO/WMO/WIGOS explicit links first;
    -   conservative geographic matching second.
7.  Implement simple station ranking:
    -   distance;
    -   elevation delta;
    -   freshness;
    -   required-variable availability.
8.  Re-run Kisumu as the first integration test.
9.  Add WIS2 observation ingestion/BUFR decoding.
10. Compare WIS2 vs METAR observations for stations represented by both.
11. Add provider reliability/history metrics.
12. Only then add selected national-provider adapters.

------------------------------------------------------------------------

## 16. Suggested first national-provider tests

After global WIS2 + METAR work, choose national services that exercise
different access patterns rather than immediately attempting maximum
country count.

Potential examples:

-   Germany / DWD;
-   UK / Met Office;
-   Australia / Bureau of Meteorology;
-   an African national service;
-   a provider with authentication or more restrictive distribution.

The purpose is to test whether the provider abstraction survives diverse
real-world systems.

------------------------------------------------------------------------

## 17. Important implementation rules for the coding agent

1.  **Do not couple forecast logic to provider-specific formats.**
2.  **Do not equate provider records with physical stations.**
3.  **Do not count duplicate WIS2/METAR representations as independent
    verifying stations.**
4.  **Do not treat OSCAR operational status as proof of fresh accessible
    observations.**
5.  **Do not rank by distance alone.**
6.  **Do not silently use stale observations.**
7.  **Do not silently substitute distant stations just to return exactly
    three.**
8.  **Preserve source provenance and timestamps.**
9.  **Store raw identifiers even after canonicalization.**
10. **Keep initial scoring explainable; learn weights later from
    accumulated verification data.**
11. **Cache global station metadata locally rather than repeatedly
    querying remote catalogues per user request.**
12. **Prefer bulk/cache observation feeds where providers recommend
    them.**
13. **Design national integrations as plugins/adapters.**
14. **Treat licensing/access metadata as provider configuration, not an
    afterthought.**

------------------------------------------------------------------------

# 18. Open questions for implementation/research

The coding/research agent should resolve these empirically rather than
assuming:

1.  What is the most reliable programmatic OSCAR/Surface interface for
    bulk/global station metadata in the intended deployment?
2.  Which WIS2 Global Discovery Catalogue/Broker/Cache endpoints should
    production consumers use, and what failover strategy is appropriate?
3.  Which WIS2 surface datasets provide the desired variables globally,
    and how consistent are BUFR templates across publishers?
4.  How should observation duplicates be identified across WIS2 and
    METAR when timestamps/rounding differ slightly?
5.  What freshness thresholds should apply by variable/network?
6.  What historical data retention is required to train useful local
    forecast-verification statistics?
7.  How should station elevation be compared to forecast-grid/orography
    elevation?
8.  Should the initial spatial database be SQLite only, SQLite + spatial
    extension, or PostgreSQL/PostGIS?
9.  Which national services materially improve coverage beyond WIS2 +
    worldwide METAR?
10. What licensing/redistribution restrictions apply to each national
    source?
11. How should provider health/reliability be measured over time?
12. How should mobile/user-facing requests be insulated from temporarily
    unavailable upstream station providers?

------------------------------------------------------------------------

# 19. Recommended immediate task for a coding controller

Build the smallest end-to-end prototype that answers:

> Given arbitrary latitude/longitude, discover nearby physical stations
> from global metadata, reconcile airport/WMO identities, obtain the
> latest available observations, reject stale/unavailable candidates,
> and return the best three stations with transparent ranking
> components.

Use **Kisumu, Kenya** as the first integration/torture test because it
already demonstrates:

-   WIGOS/WMO/ICAO identity overlap;
-   an excellent nearby airport/WMO station;
-   intermittent/stale reporting;
-   a closer AWS with questionable live accessibility;
-   more distant operational stations;
-   substantial elevation differences;
-   the need to return fewer than three if necessary.

Then test at several deliberately different locations worldwide before
adding national adapters.

Suggested test set after Kisumu:

``` text
major European city
remote rural Europe
Australian interior/coast
North American city
South American city
small island
mountain location
equatorial African location
```

The goal is not to tune for Kisumu. It is to expose assumptions that
fail globally.

------------------------------------------------------------------------

# 20. Useful public references from the design discussion

-   WMO wis2box station metadata documentation:
    https://docs.wis2box.wis.wmo.int/en/1.0.0/reference/running/station-metadata.html

-   WMO WIS2 overview:
    https://community.wmo.int/site/knowledge-hub/programmes-and-initiatives/wmo-information-system-wis/wis2-overview

-   Aviation Weather Center Data API:
    https://aviationweather.gov/data/api/

-   WMO pywis-pubsub:
    https://github.com/World-Meteorological-Organization/pywis-pubsub

These should be rechecked against current documentation during
implementation because APIs and operational WIS2 details can evolve.

------------------------------------------------------------------------

## Bottom line

The correct abstraction is not "find the nearest three WMO stations."

It is:

> **Discover nearby physical observing stations from multiple global and
> national catalogues; reconcile their identities; determine which
> observation paths are actually alive and fresh; rank the physical
> stations for representativeness to the requested forecast point and
> variable; normalize their observations; and preserve enough
> provenance/history to learn which stations and forecast models perform
> best locally over time.**

That architecture supports the intended worldwide weather application
without forcing the forecast system to know or care how each country
distributes observations.
