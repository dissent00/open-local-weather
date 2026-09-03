# Global Radar, Satellite & Regional Observation Discovery
## Technical handoff for implementation

**Purpose:** Extend the global weather-observation architecture beyond point weather stations to radar, satellite, upper-air, marine, and regional/WMO networks.

**Intended consumer:** A coding LLM/controller (Claude Code, Codex, etc.) implementing a worldwide weather application that already uses global NWP/forecast-model data and wants observations for verification, bias tracking, and learned local accuracy.

**Status:** Architecture plus a live Kisumu, Kenya discovery test performed 2026-09-03.

---

# 1. Product goal

The application should work for arbitrary locations worldwide.

Given a latitude/longitude and one or more forecast variables, the observation subsystem should automatically determine which real observations are useful for verification.

Observation types include:

```text
POINT
  surface station
  METAR
  rain gauge
  buoy
  ship

PROFILE
  radiosonde / TEMP
  pilot balloon / wind profiler
  aircraft profile / AMDAR

GRID / AREA
  weather radar
  radar composite
  satellite imagery
  satellite-derived products
```

The calling forecast system should not know whether the underlying observation arrived via WIS2 BUFR, METAR JSON, radar ODIM HDF5, CfRadial, EUMETSAT NetCDF, RealEarth raster tiles, etc.

Target abstraction:

```text
location + variable + valid time
            |
            v
    Observation Resolver
            |
    +-------+-------+
    |       |       |
   Point  Profile  Grid
    |       |       |
    +-------+-------+
            |
            v
 normalized observations
            |
            v
 forecast verification
            |
            v
 accumulated accuracy / bias learning
```

---


# 1A. Primary purpose: observations verify the model forecast; they are not the forecast

The central product remains a **model-derived forecast** built from numerical weather prediction sources such as:

```text
GFS
ECMWF / "Euro"
UKMET
ICON
```

The observation system described in this document exists primarily to provide **recorded ground truth after forecast issuance**.

The intended learning loop is:

```text
GFS / ECMWF / UKMET / ICON forecasts
                |
                v
       forecast values saved
                |
                | valid time passes
                v
      recorded observations arrive
 stations / radar / satellite / profiles / marine
                |
                v
       derive comparable truth
                |
                v
 score each model by forecast vector
                |
                v
 historical model-skill estimates
                |
                v
 improve future model selection / weighting
```

The key question is therefore not simply:

> Which model is best?

It is closer to:

> For this location or region, this forecast variable, this lead time, this season/time of day, and eventually this weather regime, which model or combination of models has historically been most accurate?

Different forecast vectors may have different winners.

For example, a future blended forecast could legitimately conclude:

```text
temperature:        ECMWF strongest historical skill
wind:               UKMET strongest historical skill
cloud cover:        ICON strongest historical skill
precipitation time: ECMWF + UKMET combination strongest
pressure:           GFS and ECMWF effectively tied
```

Model skill should therefore be accumulated **per variable/vector**, rather than assigning a single permanent rank to an entire forecast model.

Useful conditioning dimensions may eventually include:

```text
location / grid region
forecast variable
forecast lead time
season
time of day
terrain/elevation class
coastal vs inland
weather regime
```

Do not require all of these dimensions in the first implementation. The important requirement is to preserve enough historical forecast and observation data that skill estimates can become more conditional and sophisticated later.

---

# 1B. Three distinct roles for observation data

Every observational data source should be understood in terms of three possible roles.

## Role 1 — Verification / recorded truth

**This is the core purpose of the observation system.**

After a model forecast's valid time passes, recorded observations are used to determine what actually happened and score the forecast.

Examples:

```text
surface station:
  observed air temperature, pressure, wind, rainfall

radar:
  observed precipitation location/timing/intensity aloft

geostationary satellite:
  observed cloud state, cloud evolution, convection, cloud-top properties

radiosonde:
  observed atmospheric temperature/humidity/wind profile

buoy:
  observed marine surface conditions
```

These verification records continually improve the system's estimate of which model performs best for each forecast vector.

## Role 2 — Current-state / nowcast input

Very recent observations could eventually be used to adjust the first few forecast hours or initialize a separate nowcast layer.

Examples:

```text
latest station temperature
current radar echo
latest satellite cloud field
current lightning
```

This is potentially valuable, but it is **not the initial purpose of the observation architecture** and should not be conflated with model-skill verification.

The first implementation should be able to produce excellent forecasts using historical model skill without requiring observations to modify the forecast in real time.

A future nowcast/current-state correction layer can be evaluated separately once enough data exist to determine whether it improves forecast skill.

## Role 3 — Live user display

Some observation sources are intrinsically useful and interesting to expose directly to the user.

Examples:

```text
latest nearby station observations
live/recent radar animation
latest satellite imagery
current lightning display
```

This is a valuable application feature, but it is conceptually separate from verification.

The same underlying provider can support both:

```text
satellite feed
    |
    +--> archived/normalized observation -> forecast verification
    |
    +--> latest image -> user display
```

Likewise:

```text
radar feed
    |
    +--> historical grid -> precipitation verification
    |
    +--> latest mosaic -> live radar UI
```

The availability of a visually compelling live product should not determine whether it is a scientifically appropriate verification source, and vice versa.

---

# 1C. How the major observation sources fit these roles

| Observation source | Verification / truth | Possible nowcast role | Live user display |
|---|---|---|---|
| Surface station / SYNOP | Excellent for directly measured surface variables | Excellent | Useful |
| METAR | Excellent for many surface/aviation variables | Excellent | Useful |
| Rain gauge | Excellent for surface precipitation amount | Excellent when timely | Usually limited |
| Weather radar | Excellent for precipitation location/timing and derived intensity | Excellent | Extremely useful |
| GEO satellite | Excellent for clouds/convection and appropriate derived products | Excellent | Extremely useful |
| LEO satellite | Valuable but intermittent; often unique sensors/retrievals | Situational | Sometimes useful |
| Radiosonde / TEMP | Excellent atmospheric-profile truth | Limited by launch cadence | Usually specialist |
| AMDAR / aircraft | Valuable atmospheric profile/route observations | Potentially useful | Usually not |
| Buoy / ship | Excellent marine truth where available | Useful | Sometimes useful |
| OSCAR / WRD catalogues | Metadata only; not truth observations themselves | None | Usually none |
| RBON / GBON membership | Network/quality/context metadata | None directly | None |

This table describes typical roles, not an absolute ranking. Suitability still depends on variable, location, timing, quality control, and physical representativeness.

---

# 1D. Raw observations versus derived truth products

A critical verification principle is:

> Compare physically equivalent quantities.

Not every raw observation is directly comparable to a model forecast field.

For satellite cloud verification, this would be invalid:

```text
model total cloud cover = 70%
vs
satellite IR brightness temperature = -52 C
```

Instead:

```text
satellite radiances / channels
            |
            v
     cloud detection / mask
            |
            v
  derived cloud fraction/state
            |
            v
compare with model cloud quantity
```

Similarly, radar reflectivity is not identical to modeled accumulated precipitation:

```text
radar reflectivity
       |
       +--> precipitation occurrence
       +--> derived rain rate
       +--> derived accumulation
       |
       v
appropriate model comparison
```

And these quantities must remain semantically distinct:

```text
2 m air temperature
land-surface skin temperature
IR brightness temperature
cloud-top temperature
```

Likewise:

```text
gauge precipitation accumulation
radar reflectivity
radar-derived precipitation rate
satellite-retrieved precipitation rate
```

The system should therefore preserve two conceptual levels where applicable:

```text
RAW / NATIVE OBSERVATION
          |
          v
DERIVED VERIFICATION PRODUCT
          |
          v
MODEL COMPARISON
```

Preserve the native quantity and provenance even if a derived truth product is what ultimately enters a forecast score.

---

# 1E. Multi-source truth is useful because sources observe different parts of an event

The purpose of collecting radar, satellite, and surface data is not necessarily to collapse them into one synthetic "truth" number.

For precipitation/convection, for example:

```text
SATELLITE
  cloud development / convection begins
          |
          v
RADAR
  precipitation echo develops or approaches
          |
          v
SURFACE GAUGE
  precipitation actually reaches the ground
```

A model forecast can therefore fail in different ways.

Example observed sequence:

```text
13:15 satellite convection initiates
13:25 cloud tops rapidly cool
13:42 radar echo appears
14:06 surface gauge records rainfall
```

Model forecast:

```text
convection initiation: 14:30
surface rain onset:    15:00
```

Potential verification metrics include:

```text
cloud/convection initiation timing error
radar-echo timing error
surface-rain onset timing error
storm displacement error
rainfall amount error
```

This richer verification history can eventually reveal *why* a particular model performs better or worse, rather than producing only a generic accuracy score.

---

# 1F. Historical model skill is the bridge back into forecast generation

Observations should influence future forecasts primarily through accumulated model-performance statistics.

Conceptually:

```text
FORECAST ARCHIVE
GFS / ECMWF / UKMET / ICON
          |
          | valid time passes
          v
OBSERVATION / TRUTH ARCHIVE
          |
          v
VERIFICATION HISTORY
          |
          v
MODEL SKILL
(variable × location × lead time × context)
          |
          v
FORECAST SELECTION / WEIGHTING
          |
          v
FINAL USER FORECAST
```

This preserves a clean distinction:

```text
MODELS
  predict the future

OBSERVATIONS
  record what happened

VERIFICATION
  learns which predictions were best

MODEL SKILL
  informs future forecast construction
```

This feedback loop is the central reason to invest in the broader observation ecosystem described in the remainder of this document.


# 2. Generalize the previous station architecture

The earlier surface-station design should become a generic **Observing Asset + Provider Source** architecture.

A physical observing asset is not the same thing as a source/API representation.

Examples:

```text
physical airport weather station
  WIGOS ID
  WMO ID
  ICAO ID
  WIS2 feed
  METAR feed
  national met-office feed

physical weather radar
  WRD/WIGOS metadata identity
  national radar feed
  regional composite feed

satellite platform/instrument
  OSCAR/Space identity
  EUMETSAT/NOAA/JMA/etc delivery service
  possibly RealEarth aggregation
```

Core logical model:

```text
ObservingAsset
    |
    +-- AssetIdentity(...)
    +-- NetworkMembership(...)
    +-- ObservationCapability(...)
    +-- ProviderSource(...)
```

The system should preserve all identities and provider paths even when they resolve to one physical asset.

---

# 3. Observation geometry taxonomy

Use at least three observation kinds:

```python
class ObservationKind(Enum):
    POINT = "point"
    PROFILE = "profile"
    GRID = "grid"
```

## POINT

Examples:

- weather station;
- airport/METAR;
- gauge;
- buoy;
- ship.

Compare against an interpolated model grid point.

## PROFILE

Examples:

- radiosonde TEMP;
- wind profiler;
- AMDAR ascent/descent profile.

Compare against the model atmospheric column at corresponding pressure/height levels.

## GRID

Examples:

- radar reflectivity;
- radar precipitation mosaic;
- satellite IR brightness temperature;
- cloud mask;
- satellite rainfall retrieval.

Compare against model fields spatially, not merely one interpolated point.

This taxonomy should be explicit in database and API models.

---

# 4. RBON/GBON: network metadata, not another provider API

WMO RBON is a subset of WIGOS surface-based observing stations selected for international/global exchange at the regional level.

Do **not** implement RBON as a standalone provider.

Instead treat network membership as metadata:

```json
{
  "asset_id": "station_xyz",
  "networks": [
    "WIGOS",
    "RBON_RA_I",
    "GBON"
  ]
}
```

Possible uses of network membership:

- confidence/prioritization;
- expected reporting requirements;
- expected persistence;
- distinguishing globally exchanged stations from purely local networks.

RBON replaces older RBSN/RBCN arrangements and includes surface and upper-air observing requirements.

The previous station provider layer should therefore expand to regional/upper-air observations through the same WIGOS/WIS2 infrastructure rather than inventing a parallel RBON ingestion path.

---

# 5. Regional and upper-air observations

WIS2 is capable of carrying more than SYNOP/surface data.

Operational WIS2 nodes already expose collections such as:

```text
SYNOP
TEMP
TEMP SHIP
PILOT
PILOT SHIP
moored buoy
drifting buoy
```

A NOAA WIS2 node currently exposes separate API collections for TEMP observations and marine/buoy observations.

The application should eventually support:

```text
WIS2Provider
  capabilities:
    POINT:
      SYNOP
      buoy
      ship

    PROFILE:
      TEMP
      PILOT
```

## Why profiles matter for this app

The application is not merely displaying current conditions; it is verifying NWP forecasts.

A radiosonde can verify:

```text
surface
925 hPa
850 hPa
700 hPa
500 hPa
300 hPa
...
```

for temperature, humidity, and wind.

This enables diagnostics such as:

> Model X has a persistent warm bias near 850 hPa in this region before afternoon convection.

That is potentially far more informative than surface temperature error alone.

## Africa-specific note

WMO reported in 2026 that upper-air coverage remains sparse in parts of Africa, and AMDAR is being expanded to compensate. Kenya Airways aircraft were actively contributing AMDAR observations in 2026.

Therefore provider/capability discovery should include aircraft observations later, but this is not required for the first prototype.

---

# 6. Weather radar: metadata discovery and actual data are separate

## 6.1 WMO Weather Radar Database (WRD)

Treat WRD as the radar analogue of OSCAR/Surface:

```text
WRD answers:
  where radar systems exist
  nominal metadata
  band
  polarization
  status
  formats/products/exchange metadata

WRD does NOT inherently answer:
  give me the latest reflectivity pixels for this coordinate
```

WRD metadata can seed a canonical radar catalogue.

Suggested radar asset:

```json
{
  "asset_id": "radar_xyz",
  "kind": "weather_radar",
  "name": "Example Radar",

  "latitude": -1.2,
  "longitude": 36.8,
  "elevation_m": 1700,

  "identifiers": {
    "wigos": "...",
    "wrd": "..."
  },

  "band": "C",
  "polarization": "dual",
  "nominal_range_km": 250,

  "declared_status": "operational",

  "provider_sources": [
    {
      "provider": "national_met_service",
      "id": "..."
    }
  ]
}
```

WRD shows that global radar formats are heterogeneous, including ODIM HDF5, CfRadial, NetCDF, MDV, proprietary formats, and unknown/unreported formats.

Therefore radar ingestion must be adapter-based.

---

# 7. Radar suitability is not just distance

For surface stations, distance is a major factor.

For radar, ask:

> Does this radar provide physically useful coverage of this coordinate and altitude?

Potential scoring components:

```python
radar_score = (
    distance_penalty
    + nominal_range_penalty
    + beam_height_penalty
    + terrain_blockage_penalty
    + freshness_penalty
    + data_availability_penalty
    + product_quality_penalty
)
```

Important concepts:

- radar beam rises with distance;
- mountains can block beams;
- a radar within nominal range may still have poor low-level coverage;
- composites may be preferable to a single radar;
- declared operational status is not enough;
- live scan availability must be checked.

Expected output should expose score components.

---

# 8. Radar providers should be regional/national adapters

There is no single universally reliable worldwide radar-pixel API.

Suggested provider pattern:

```python
class RadarProvider(ObservationProvider):

    async def discover(self, lat, lon, radius_km):
        ...

    async def latest_products(self, asset_id):
        ...

    async def fetch(self, asset_id, product, valid_time):
        ...
```

Potential adapters:

```text
WRDMetadataProvider       # metadata/discovery only

Europe:
  EUMETNET / OPERA Open Radar Data

United States:
  NEXRAD

National examples:
  DWD
  MeteoSwiss
  BOM
  JMA
  etc.
```

## Europe as a strong first production radar implementation

EUMETNET's Open Radar Data service is architecturally attractive because it exposes:

- single-site radar volumes;
- OPERA composites;
- national products;
- radar metadata;
- OGC EDR APIs;
- OGC Records discovery;
- MQTT near-real-time notifications;
- archive access.

OPERA commonly exchanges radar data in ODIM HDF5.

This is a good test of the generic radar provider interface.

---

# 9. Satellite discovery: OSCAR/Space

Satellite selection is fundamentally different from station/radar discovery.

Do not ask:

> Which satellite is nearest?

Ask:

> Which active platform/instrument/product can observe this location and variable at the required spatial/temporal resolution and latency?

OSCAR/Space provides global metadata for:

- satellite platforms;
- orbits;
- geostationary longitude;
- instruments;
- capabilities;
- operational status;
- expected lifetime;
- near-real-time availability;
- access links/information.

Conceptually:

```text
OSCAR/Space
    |
    v
platform
    |
    +-- instrument
            |
            +-- channel/product capability
```

Suggested entities:

```text
SatellitePlatform
SatelliteInstrument
SatelliteProduct
ProviderSource
```

---

# 10. Satellite suitability scoring

Potential score:

```python
satellite_score = (
    coverage_penalty
    + view_angle_penalty
    + spatial_resolution_penalty
    + temporal_resolution_penalty
    + latency_penalty
    + product_relevance_penalty
    + provider_reliability_penalty
)
```

## GEO vs LEO

Geostationary satellites:

- continuous regional coverage;
- excellent temporal resolution;
- useful for cloud evolution, convection, water vapor, cloud-top temperature, fog, fire;
- typically minutes between observations.

Polar/LEO satellites:

- intermittent overpasses;
- often higher-resolution or different sensors;
- microwave rainfall, atmospheric sounding, surface retrievals, etc.

Do not treat GEO and LEO as mutually exclusive.

---

# 11. Production satellite providers

Prefer authoritative satellite operator services for production where possible.

Examples:

```text
EUMETSAT
  Meteosat / MTG
  Metop

NOAA
  GOES
  JPSS

JMA
  Himawari

other regional operators as required
```

Provider registry must include access/licensing metadata.

---

# 12. EUMETSAT is especially relevant for Africa

As of 2026, Meteosat-12 (MTG-I1) is operational as the primary Meteosat imaging platform at 0 degrees.

It carries:

```text
FCI - Flexible Combined Imager
LI  - Lightning Imager
```

OSCAR/Space lists FCI objectives including:

- cloud cover;
- cloud optical depth;
- cloud-top height;
- cloud-top temperature;
- cloud type;
- integrated water vapor;
- horizontal wind.

EUMETSAT's Data Store provides programmatic access using:

- Browse REST API;
- Download REST API;
- OpenSearch API;
- EUMDAC Python client.

Current key FCI collections include:

```text
EO:EUM:DAT:0662
FCI Level 1c Normal Resolution Image Data - MTG - 0 degree

EO:EUM:DAT:0665
FCI Level 1c High Resolution Image Data - MTG - 0 degree
```

Data catalogue browse/search is possible without registration. Downloading generally requires an account/token.

This should be considered a production-grade Africa/Europe satellite provider.

---

# 13. RealEarth: excellent prototype/aggregation provider, not sole backbone

University of Wisconsin SSEC RealEarth exposes an unusually convenient HTTP API.

Useful endpoints include:

```text
/api/products
/api/times
/api/time
/api/latest
/api/extents
/api/image
/api/shapes
/api/data
/api/probe
```

Capabilities include:

- product catalogue discovery;
- available/latest times;
- arbitrary image bounds;
- tiles;
- GeoTIFF;
- WMS/WMTS-style access;
- raw pixel/data probing at lat/lon for supported products.

This makes RealEarth excellent for a prototype of:

```text
location
  -> candidate satellite product
  -> latest valid time
  -> extract value/image around coordinate
```

But RealEarth explicitly states that it is a repository of user-generated products and does not guarantee ownership or availability. Raw/full-size data may require additional arrangements.

Therefore recommended role:

```text
RealEarth:
  prototype
  discovery
  visualization
  easy point probing
  fallback/secondary source

not:
  sole authoritative production archive
```

---

# 14. Live Kisumu test — target

Test coordinate:

```text
Kisumu, Kenya
lat: -0.0917
lon: 34.768
```

Test date/time:

```text
2026-09-03
approximately 08:45 EAT
05:45 UTC
```

Goals:

1. identify nearby weather radars and determine whether useful ground-radar coverage exists;
2. identify current satellite coverage/products;
3. test whether an aggregation API exposes live product metadata;
4. investigate regional/upper-air observation availability;
5. document contradictions/failure modes for implementation.

---

# 15. Kisumu radar findings

## 15.1 Global radar catalogue result

The WMO Weather Radar Database currently reports:

```text
Kenya: 2 active radar systems
```

Independent radar listings identify two Kenyan systems:

```text
DWSR-8501 - JKIA
DWSR-8501 - Malindi
```

No radar near Kisumu appeared.

Approximate straight-line distances from central Kisumu:

```text
JKIA/Nairobi radar: ~276 km
Malindi radar:      ~688 km
```

Even if JKIA were active, ~276 km is not attractive low-level radar coverage for Kisumu. Beam height and terrain would materially reduce usefulness.

Result:

```text
Kisumu has no credible nearby ground weather radar in the discovered national network.
```

For forecast verification, satellite should be expected to fill much of the precipitation/cloud-observation gap.

---

# 16. Radar metadata conflict discovered

This was the most important radar test result.

The global WRD statistics currently count Kenya as having **two active radars**.

However, Kenya Meteorological Department's own station pages state:

## JKIA radar

```text
Name: RADAR DWSR-8501 - JKIA
Latitude: -1.3213889
Longitude: 36.9397222
Elevation: 1658 m
Date established: 2016-04-29
Date closed: 2021-05-17
Declared status: Closed
Program/network WRO: Closed
```

## Malindi radar

```text
Name: RADAR DWSR-8501 - MALINDI
Latitude: -3.2694444
Longitude: 40.0502778
Elevation: 37 m
Date established: 2016-04-29
Date closed: 2021-06-28
Declared status: Closed
Program/network WRO: Closed
```

Third-party radar catalogues still list both systems.

This means:

```text
catalogue says radar exists
        !=
national metadata says operational
        !=
live scans are actually available
```

A production resolver must retain multiple status claims and independently verify live data.

Suggested radar status representation:

```json
{
  "asset_id": "radar_x",
  "status_claims": [
    {
      "source": "WRD",
      "status": "active",
      "retrieved_at": "..."
    },
    {
      "source": "KenyaMet",
      "status": "closed",
      "effective_date": "2021-05-17",
      "retrieved_at": "..."
    }
  ],

  "live_data": {
    "last_scan": null,
    "reachable": false
  },

  "effective_status": "unavailable",
  "status_confidence": "high"
}
```

Prefer authoritative national metadata over stale aggregate metadata when the conflict is clear, but never infer active/unavailable solely from metadata: test the feed.

---

# 17. Kisumu satellite findings

Satellite coverage is dramatically better than radar coverage.

## 17.1 Meteosat coverage

Kisumu is well placed for the EUMETSAT Meteosat geostationary system over Africa.

Current OSCAR/Space metadata:

```text
Meteosat-12 / MTG-I1
status: operational
instrument: FCI
nominal geostationary service near 0 degrees
```

Meteosat-12 is the modern production platform to target through EUMETSAT.

Meteosat-11 also remains operational, with SEVIRI near-real-time data and a geostationary longitude of 9.5 E.

---

# 18. RealEarth live API test

The RealEarth product catalogue currently exposes Meteosat-11 full-disk SEVIRI products.

Relevant product IDs include:

```text
Met11-SEVIRI-FD-BAND01   VIS 0.6 µm
Met11-SEVIRI-FD-BAND02   VIS 0.8 µm
Met11-SEVIRI-FD-BAND03   NIR 1.6 µm
Met11-SEVIRI-FD-BAND04   IR 3.9 µm
Met11-SEVIRI-FD-BAND05   WV 6.2 µm
Met11-SEVIRI-FD-BAND06   WV 7.3 µm
Met11-SEVIRI-FD-BAND07   IR 8.7 µm
Met11-SEVIRI-FD-BAND08   IR 9.7 µm
Met11-SEVIRI-FD-BAND09   IR 10.8 µm
Met11-SEVIRI-FD-BAND10   IR 12.0 µm
Met11-SEVIRI-FD-BAND11   IR 13.4 µm
Met11-SEVIRI-HRV-BAND12  high-resolution visible
```

For forecast verification, the 10.8 µm IR channel is particularly relevant because it can represent surface/cloud-top brightness temperature and is useful for cloud/cirrus detection.

---

# 19. RealEarth freshness test

The API metadata for:

```text
Met11-SEVIRI-FD-BAND09
```

showed current available frames through:

```text
2026-09-03 05:00:00 UTC
2026-09-03 08:00:00 EAT
```

At test time (~05:45 UTC), this was about 45 minutes old.

RealEarth exposed a direct tile/API URL for that exact frame.

The metadata reports units:

```text
C
```

and product description identifying it as IR 10.8 µm.

The test environment could inspect RealEarth metadata and exact current timestamps but could not directly execute the arbitrary `/api/data?lat=...&lon=...` pixel query due to browsing-tool URL restrictions. The documented RealEarth API supports that operation and the implementation agent should test it directly.

This is a tooling limitation of this handoff test, **not evidence that the RealEarth endpoint is unavailable**.

---

# 20. RealEarth polar-orbiting precipitation test

RealEarth also exposed a current:

```text
MIRS Rain Rate
product ID: MIRS-RainRate
```

This is derived from the ATMS microwave sounder aboard JPSS polar-orbiting satellites.

At test time RealEarth showed a latest product timestamp:

```text
2026-09-03 04:48:51 UTC
2026-09-03 07:48:51 EAT
```

Important distinction:

```text
Meteosat GEO:
  regular full-disk observations
  high temporal continuity

JPSS MiRS:
  swath/overpass observation
  intermittent spatial coverage
```

The existence of a global MiRS frame timestamp does **not** prove the swath covered Kisumu at that instant. The production code must query product extents or the point value and treat "outside swath" as no observation.

This is a good example of why satellite coverage requires geometry-aware discovery.

---

# 21. Satellite result for Kisumu

For Kisumu, the prototype source ranking should currently look conceptually like:

```text
1. Meteosat-12 FCI via EUMETSAT
   role: production primary
   coverage: excellent
   temporal continuity: excellent

2. Meteosat-11 SEVIRI via RealEarth
   role: easy prototype / secondary
   coverage: excellent
   latest verified metadata: 05:00 UTC in test

3. JPSS MiRS rain-rate via RealEarth/NOAA sources
   role: intermittent complementary precipitation observation
   coverage: swath-dependent
   latest global product timestamp in test: 04:48:51 UTC
```

Satellite coverage is therefore suitable as the default area-observation layer where ground radar is absent.

---

# 22. Regional/upper-air test findings

The initial public search did not reveal a simple Kenya-specific WIS2 TEMP endpoint during this test.

However, current operational WIS2 implementations demonstrate that:

```text
TEMP
PILOT
buoy
SYNOP
```

are normal WIS2 data classes and can be exposed through queryable APIs.

WMO reports that African upper-air coverage has significant gaps, while AMDAR coverage is being expanded. Kenya Airways aircraft were actively contributing AMDAR data in 2026.

Therefore the resolver should not assume every location has a nearby radiosonde.

Expected behavior:

```text
query profile sources
  |
  +-- radiosonde/TEMP available? use it
  |
  +-- AMDAR profile near location/time? use if licensed/accessible
  |
  +-- satellite sounder product? use as separate profile/grid source
  |
  +-- none? report no direct upper-air observation
```

The architecture should support sparse profile observations without pretending they are continuously available.

---

# 23. Revised global observation resolver

Recommended public interface:

```python
result = await observation_resolver.discover(
    latitude=-0.0917,
    longitude=34.768,
    valid_time=now,
    variables=[
        "air_temperature",
        "precipitation",
        "cloud_cover",
        "wind",
    ],
)
```

Example response:

```json
{
  "point": [...],

  "profile": [...],

  "radar": {
    "available": false,
    "candidates": [
      {
        "name": "JKIA radar",
        "distance_km": 276,
        "status": "metadata_conflict",
        "usable": false
      }
    ]
  },

  "satellite": [
    {
      "platform": "Meteosat-12",
      "instrument": "FCI",
      "provider": "eumetsat",
      "role": "primary"
    },
    {
      "platform": "Meteosat-11",
      "instrument": "SEVIRI",
      "provider": "realearth",
      "product": "Met11-SEVIRI-FD-BAND09",
      "latest_time": "2026-09-03T05:00:00Z"
    }
  ]
}
```

---

# 24. Variable-aware source selection

There should not be one universal "best observation."

Examples:

## Air temperature

```text
1. representative nearby surface station
2. METAR
3. satellite land-surface temperature only if semantics are appropriate
```

Do not substitute satellite skin temperature for 2 m air temperature without explicit variable transformation/semantics.

## Precipitation

```text
1. nearby calibrated gauge
2. quality-controlled radar/composite
3. satellite precipitation retrieval
```

Keep all available sources for analysis; do not necessarily collapse them into one value.

## Cloud cover

```text
1. geostationary satellite cloud product
2. METAR
3. surface SYNOP
```

## Convection timing

```text
1. geostationary IR/cloud-top cooling
2. lightning imagery/product
3. radar echo onset
4. gauge precipitation onset
```

This can diagnose different stages of model timing error.

---

# 25. Forecast-verification opportunity: multi-stage event verification

Radar + satellite + surface observations allow richer model diagnosis.

For example:

```text
13:15 satellite convection initiates
13:25 cloud-top cooling accelerates
13:42 radar echo begins
14:06 gauge records rainfall
```

versus forecast:

```text
model convection initiation: 14:30
model rain onset: 15:00
```

This can produce distinct error metrics:

```text
cloud initiation timing error
radar echo timing error
surface rainfall timing error
rainfall amount error
storm displacement error
```

This is much more valuable than a binary "forecast rain/no rain" score.

---

# 26. Canonical normalized observation model

Replace a station-specific central schema with a generic observing asset reference.

Example point observation:

```json
{
  "observation_id": "...",

  "asset_id": "station_xyz",
  "kind": "point",

  "variable": "air_temperature",
  "valid_time": "...",

  "geometry": {
    "type": "Point",
    "coordinates": [34.768, -0.0917]
  },

  "value": 24.1,
  "unit": "degC",

  "provider": "wis2",
  "source_reference": "..."
}
```

Grid observation:

```json
{
  "observation_id": "...",

  "asset_id": "meteosat12_fci",
  "kind": "grid",

  "variable": "brightness_temperature_10_5um",
  "valid_time": "...",

  "geometry": {
    "type": "Grid"
  },

  "provider": "eumetsat",
  "source_reference": "..."
}
```

A radar raster should use the same grid observation family.

---

# 27. Keep raw measured variables semantically correct

Do not over-normalize different physical quantities.

Examples that must remain distinct:

```text
2 m air temperature
land-surface skin temperature
IR brightness temperature
cloud-top temperature
```

Likewise:

```text
gauge rainfall accumulation
radar reflectivity
radar-estimated rain rate
satellite-retrieved rain rate
```

The verification layer may derive common metrics, but ingestion must preserve what was actually measured/retrieved.

---

# 28. Provider registry extension

Example:

```yaml
providers:

  wis2:
    scope: global
    kinds: [point, profile]

  aviation_weather:
    scope: global
    kinds: [point]

  wrd:
    scope: global
    role: metadata_only
    asset_types: [weather_radar]

  eumetsat:
    scope:
      coverage: europe_africa_indian_ocean
    kinds: [grid, profile]
    asset_types: [satellite]

  realearth:
    scope: global
    kinds: [grid]
    roles:
      - prototype
      - aggregation
      - visualization

  opera:
    scope:
      region: europe
    kinds: [grid]
    asset_types:
      - weather_radar
      - radar_composite
```

Manifest fields should include:

```text
authentication
licensing
commercial use
redistribution
rate limits
latency expectation
archive depth
data formats
supported variables
geographic coverage
provider priority
```

---

# 29. Separate metadata status from observed provider health

This is mandatory after the Kenya radar test.

For every asset/source maintain:

```text
metadata_status
live_source_status
last_successful_observation
last_attempt
expected_cadence
freshness
recent_success_rate
```

Example:

```json
{
  "provider_source_id": "...",

  "metadata_status": "operational",

  "health": {
    "reachable": true,
    "last_success": "2026-09-03T05:00:00Z",
    "expected_interval_seconds": 600,
    "freshness_seconds": 240,
    "success_rate_24h": 0.98
  }
}
```

The effective resolver status should be derived from both.

---

# 30. Recommended radar coverage object

```json
{
  "asset_id": "radar_xyz",
  "target": {
    "lat": -0.0917,
    "lon": 34.768
  },

  "distance_km": 120,

  "coverage": {
    "within_nominal_range": true,
    "estimated_beam_height_m_agl": 2100,
    "terrain_blockage_fraction": 0.12,
    "usable_low_level": false
  },

  "latest_scan": "...",
  "freshness_seconds": 300,

  "score": 0.42
}
```

Beam-height/terrain calculations can be deferred from milestone 1 but the schema should anticipate them.

---

# 31. Recommended satellite coverage object

```json
{
  "platform": "Meteosat-12",
  "instrument": "FCI",
  "product": "...",

  "target": {
    "lat": -0.0917,
    "lon": 34.768
  },

  "coverage": true,
  "view_angle_deg": "...",

  "native_resolution_km": "...",
  "temporal_resolution_minutes": "...",
  "latest_valid_time": "...",
  "latency_seconds": "...",

  "score": 0.96
}
```

For LEO products, include:

```text
swath_contains_target
overpass_time
next/previous overpass if useful
```

---

# 32. Recommended implementation phases

## Phase A — retain prior surface-station work

Implement:

```text
OSCAR/Surface
WIS2 SYNOP
AviationWeather/METAR
canonical station identity resolution
```

## Phase B — generic observation model

Introduce:

```text
ObservingAsset
ObservationKind
ProviderSource
NormalizedObservation
Capability
ProviderHealth
```

Refactor station implementation to use the generic core.

## Phase C — satellite prototype using RealEarth

Implement:

```text
RealEarthProvider
```

Capabilities:

1. search/list products;
2. fetch product metadata;
3. identify latest time;
4. query geographic extent;
5. query raw point value using `/api/data`;
6. optionally obtain bounded image/GeoTIFF.

First integration test:

```text
Kisumu
Met11-SEVIRI-FD-BAND09
latest time
lat/lon pixel value
```

Also test:

```text
MIRS-RainRate
```

and correctly return "no swath at target" if applicable.

## Phase D — EUMETSAT production satellite provider

Implement:

```text
EumetsatProvider
```

Start with Meteosat-12 FCI.

Use EUMETSAT Browse/Search API for discovery and EUMDAC/download APIs for data acquisition.

Do not initially ingest all FCI channels globally unless required.

Select a minimal useful set of verification products/channels.

## Phase E — radar metadata

Implement:

```text
WRDMetadataProvider
```

Store WRD radar metadata but do not trust status blindly.

Add authoritative national metadata when available.

## Phase F — first live radar provider

Choose a region with strong open infrastructure:

```text
EUMETNET OPERA
or
US NEXRAD
```

Prove:

```text
coordinate
 -> candidate radar/composite
 -> coverage check
 -> latest scan
 -> extract reflectivity/rain-rate at/around coordinate
```

## Phase G — upper-air/profile observations

Implement WIS2 TEMP ingestion.

Normalize to profile observations.

Later add:

```text
PILOT
AMDAR
satellite sounder profiles
```

---

# 33. Prototype tests after Kisumu

Do not tune only for Kenya.

Use locations designed to exercise different infrastructure:

```text
Kisumu, Kenya
  poor/no radar; strong GEO satellite

London or central Europe
  dense radar + surface + satellite

US Midwest
  NEXRAD + METAR + GOES

Australian interior
  sparse point/radar; Himawari

Japanese metro
  dense radar + Himawari

small ocean island
  limited radar; strong satellite; marine obs

mountain location
  radar blockage/elevation problems

coastal location
  radar + buoy + satellite
```

Assertions should test source selection and failure reporting, not specific weather values.

---

# 34. Failure states must be first-class

Examples:

```text
NO_ASSET
  no observing asset known

OUT_OF_COVERAGE
  asset exists but target is outside coverage

SOURCE_UNAVAILABLE
  provider endpoint failed

STALE
  data is older than variable-specific threshold

METADATA_CONFLICT
  providers disagree on operational state

NO_SWATH
  LEO satellite product exists but does not cover target

NOT_REPRESENTATIVE
  observation exists but unsuitable for verification

LICENSE_RESTRICTED
  discoverable but not usable by this application
```

Do not silently substitute or hide these states.

---

# 35. Key lessons from the Kisumu test

1. **Radar availability is highly regional.**
   A global app must expect no useful ground radar at many locations.

2. **Satellite is the true global area-observation backbone.**
   In Kisumu, geostationary satellite data are far more accessible and useful than radar.

3. **Metadata catalogues can disagree.**
   WRD's current Kenya radar count conflicts with Kenya Met's own closed-status records.

4. **Live-feed verification is essential.**
   "Operational" must never mean "usable" without checking recent data.

5. **GEO and LEO satellite products serve different roles.**
   A current MiRS timestamp does not imply the target lies within that swath.

6. **RealEarth is excellent for prototyping.**
   It exposes live product IDs, times, imagery, and point-probe capability with little plumbing.

7. **Production satellite access should prefer operators.**
   For Kisumu/Africa, EUMETSAT Meteosat-12/FCI should likely become the production primary.

8. **Regional observations belong in the same generic framework.**
   RBON is metadata/network designation; TEMP, PILOT, buoy, etc. should flow through provider adapters.

9. **Variable semantics matter.**
   Do not equate radar rain rate, gauge rainfall, satellite rain rate, and model precipitation without preserving provenance and measurement type.

10. **The resolver should be variable-aware.**
    "Best source" depends on whether the app is verifying temperature, clouds, wind, precipitation, convection timing, etc.

---

# 36. Immediate assignment for a coding controller

Extend the previous station-observation prototype into a generic observation framework without overbuilding.

## Required first deliverable

Implement a vertical slice that can answer:

> For arbitrary latitude/longitude and requested variable, what observational assets are available now, what provider supplies them, are they fresh, and how suitable are they for forecast verification?

For Kisumu, the first version should be able to return approximately:

```text
POINT
  surface-station candidates from previous module

PROFILE
  none/unknown unless a current provider is discovered

RADAR
  no trustworthy useful local radar coverage
  record candidate/status conflicts rather than pretending otherwise

SATELLITE
  Meteosat product available
  RealEarth metadata/latest timestamp available
  point probe attempted and normalized
```

## Concrete coding tasks

1. Introduce generic `ObservingAsset`, `ProviderSource`, `ObservationKind`, and `NormalizedObservation`.
2. Refactor station objects to use them without breaking station behavior.
3. Implement `RealEarthProvider`.
4. Test `Met11-SEVIRI-FD-BAND09` at Kisumu.
5. Test `MIRS-RainRate` and handle no-swath correctly.
6. Implement provider health/freshness fields.
7. Implement radar metadata model.
8. Research whether WRD exposes a stable machine-readable radar metadata endpoint; if not, create a provider abstraction without scraping fragile UI pages in production.
9. Add Kenya radar metadata as a test fixture showing conflicting status claims.
10. Produce transparent discovery output with rejection/failure reasons.

Do not yet implement every satellite operator or national radar network.

---

# 37. Public references used

WMO RBON:
https://community.wmo.int/site/knowledge-hub/programmes-and-initiatives/wmo-integrated-global-observing-system-wigos/rbon-regional-basic-observing-network

WMO Weather Radar Database:
https://wrd.mgm.gov.tr/Home/Wrd

RealEarth HTTP API:
https://www.ssec.wisc.edu/realearth/api/
https://realearth.ssec.wisc.edu/doc/api.php

RealEarth product catalogue:
https://realearth.ssec.wisc.edu/products/

WMO OSCAR/Space:
https://space.oscar.wmo.int/

EUMETSAT Data Store guide:
https://user.eumetsat.int/resources/user-guides/data-store-detailed-guide

EUMETSAT MTG data access:
https://user.eumetsat.int/resources/user-guides/mtg-data-access-guide

Kenya Meteorological Department radar station records:
https://meteo.go.ke/our-stations/143/
https://meteo.go.ke/our-stations/144/

NOAA WIS2 collections example:
https://wis2node.globaldata.nws.noaa.gov/collections?f=html

These should be rechecked during implementation because operational status, APIs, platform roles, and data licensing can change.

---


# 37A. Product-context summary

The large number of possible observation feeds should not obscure the product's core objective.

The app is fundamentally attempting to improve forecasts made from GFS, ECMWF, UKMET, ICON, and potentially other NWP models by building its own empirical record of model skill.

Radar, satellite, surface stations, upper-air profiles, marine observations, and regional networks are valuable chiefly because they provide increasingly rich evidence of **what actually occurred**.

A satellite snapshot is therefore useful even if it never changes a live forecast: if it provides an objective record of cloud state at the forecast valid time, it can help determine whether one model's cloud forecast was better than another's.

Likewise, radar is valuable even in a location where the user never opens a radar map, because archived radar can establish precipitation timing/location truth.

Conversely, the same feeds can often provide an excellent user-facing feature:

```text
forecast
+
latest station conditions
+
live/recent radar where available
+
latest satellite view
```

That UI capability should be treated as a beneficial second use of data infrastructure that primarily exists to build a better long-term verification record.


# 38. Bottom line

The correct abstraction is no longer:

> find nearby weather stations.

It is:

> **Discover observational assets capable of verifying a requested weather variable at a requested location and time; reconcile their identities and metadata; determine whether actual data are live, fresh, accessible, licensed, and representative; normalize point, profile, and grid observations while preserving physical semantics and provenance; and feed them into a forecast-verification system that can learn source/model accuracy over time.**

For a worldwide application, satellite should be considered the default global area-observation backbone, radar a high-value but regionally sparse enhancement, and point/profile networks a complementary source of direct physical ground truth.
