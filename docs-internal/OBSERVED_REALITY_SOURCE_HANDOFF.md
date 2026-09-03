# Open Local Weather — Observed Reality Source Handoff

**Purpose:** source-discovery and ingestion research for expanding the observed-reality dataset in `dissent00/open-local-weather`.

**Primary objective:** identify recorded observational sources that Open Local Weather can ingest, normalize, preserve with provenance, and use to improve its continually accumulating account of **what actually happened**. This is not a greenfield architecture exercise. The existing project already has the forecast → actuals → deterministic verification → model-skill → next-forecast feedback loop.

---

## 1. Existing project context: add truth sources, do not redesign the loop

Open Local Weather already:

1. fetches forward guidance from GFS, ECMWF, UKMET, ICON and an Open-Meteo blend;
2. stores per-model predictions for later scoring;
3. obtains actual conditions;
4. deterministically scores model predictions by variable and lead time;
5. rebuilds rolling skill statistics from the stored record;
6. gives those computed skill results and current model disagreement to the LLM for synthesis.

The current observed-reality work has already moved beyond treating reanalysis as unquestioned truth. `DailyActual` retains reanalysis-derived values while also storing direct METAR evidence such as precipitation occurrence, thunder, precipitation onset, station temperature extrema and peak wind. Provenance is now explicit.

This is the correct pattern for every source in this document:

```text
discover source
    ↓
ingest native observation
    ↓
preserve provenance / quality / time / geometry
    ↓
derive a comparable truth quantity if necessary
    ↓
store beside existing actuals
    ↓
measure agreement and divergence
    ↓
only then decide whether it has earned scoring precedence
```

Do **not** silently replace an existing historical truth series merely because a new instrument sounds more authoritative.

The main goal of this work is therefore:

> Expand the empirical evidence available to describe observed reality, especially where ERA5/reanalysis or a single station misses local weather, while retaining enough provenance to determine later which source should govern each verification vector.

---

## 2. Models predict; observations establish what happened

The forecast remains model-centric:

```text
GFS / ECMWF / UKMET / ICON
              ↓
stored forecast vectors
              ↓
       valid time passes
              ↓
recorded observational evidence
              ↓
deterministic verification
              ↓
model skill by variable × lead time
              ↓
future forecast synthesis
```

The project should not seek one universally “best model.” Different models can earn trust for different vectors:

- temperature;
- precipitation occurrence;
- precipitation amount;
- precipitation timing;
- wind speed;
- wind direction;
- pressure;
- cloud cover;
- convection;
- eventually other atmospheric quantities.

Likewise, there need not be one universally “best actuals source.” The strongest observed-reality record may use different instruments for different quantities.

Example:

```text
temperature          representative surface station
pressure             surface station
rain occurrence      station/METAR + radar/satellite evidence
rain amount          trustworthy gauge, otherwise radar/reanalysis with caveats
cloud fraction       geostationary satellite-derived product
convection timing    GEO satellite + lightning + radar
upper-air state      radiosonde / AMDAR / sounder
fallback             reanalysis
```

---

## 3. Three roles for an observational source

A source can support one or more distinct product roles.

### A. Verification / observed truth — PRIMARY PURPOSE

This is why the source belongs in the present project.

The valid time passes, the observation records what occurred, and the system uses that evidence to score the stored model prediction.

### B. Current-state / nowcast input — OPTIONAL LATER PURPOSE

A fresh METAR, radar echo, satellite cloud field or lightning observation may eventually improve the first few forecast hours.

This is valuable but should remain conceptually separate from historical verification until demonstrated to improve the forecast.

### C. Live user display — USEFUL ADD-ON

Many of the same feeds can expose:

- latest nearby station conditions;
- live/recent satellite imagery;
- radar loops;
- lightning.

This is a useful second product from observation infrastructure. It is not the reason an observation earns authority in scoring.

---

## 4. Metadata/discovery is not observational truth

This distinction is critical.

Several WMO systems tell us **what observing assets exist**, not necessarily where to retrieve the observations.

```text
OSCAR/Surface       station metadata/discovery
RBON / GBON         network membership/designation
WMO WRD             radar metadata/discovery
OSCAR/Space         satellite/instrument metadata/discovery

WIS2                actual exchanged meteorological data
Aviation Weather    actual METAR data
EUMETSAT            actual satellite products
OPERA/NEXRAD/etc.   actual radar products
national services   potentially actual station/radar data
```

The resolver must therefore distinguish:

```text
asset exists
asset claims operational status
provider can be reached
observations are actually arriving
observations are fresh
needed variable is present
observation is representative of target location
```

A catalogue entry is never sufficient evidence that usable current data exist.

---

# PART I — SURFACE AND POINT OBSERVATIONS

## 5. OSCAR/Surface and WIGOS

### What it is

WMO OSCAR/Surface is the primary global station metadata catalogue for internationally exchanged observing stations. Stations exchanging data through WIS2 are registered with a WIGOS Station Identifier (WSI).

Useful metadata includes:

- WIGOS identifier;
- traditional/WMO identifiers;
- name;
- latitude/longitude;
- elevation;
- station/facility type;
- variables observed;
- operational status;
- affiliations/network membership;
- reporting schedules;
- automation status.

### What it contributes to Open Local Weather

**Discovery and identity, not normally the observation itself.**

For an arbitrary configured location:

```text
lat/lon
  ↓
OSCAR search
  ↓
candidate stations
  ↓
identity reconciliation
  ↓
actual data-provider lookup
```

OSCAR is especially valuable for finding non-airport stations that a METAR-only implementation would miss.

### Identity rule

A physical station can have several identifiers and appear through several feeds.

Example from Kisumu:

```text
Kisumu / Kisumu Airport
WIGOS: 0-20000-0-63708
WMO:   63708
ICAO:  HKKI
```

Those are identities for the same physical observing site, not three independent stations.

The project should conceptually retain:

```text
CanonicalStation
    ├── WIGOS identity
    ├── WMO/traditional identity
    ├── ICAO identity
    └── provider-specific identities
```

Do not count WIS2 and METAR representations of the same measurement as corroboration from two independent stations.

---

## 6. Kisumu-area station findings

The earlier source-discovery test produced several useful examples.

### Kisumu / Kisumu Airport

- WIGOS `0-20000-0-63708`
- WMO `63708`
- ICAO `HKKI`
- elevation approximately 1171 m
- only a few kilometres from central Kisumu

This is an excellent example of identifier deduplication and is already useful through METAR.

### Kericho

- WIGOS `0-20000-0-63710`
- WMO `63710`
- ICAO `HKKR`
- approximately 64 km from Kisumu
- elevation approximately 1992 m

It may provide useful regional evidence, but the roughly 800 m elevation difference means it should not automatically substitute for Kisumu surface temperature.

### Kisii

- WIGOS `0-20000-0-63709`
- WMO `63709`
- ICAO `HKKS`
- approximately 66 km from Kisumu

A sufficiently fresh public observation was not established in the earlier test. Its metadata may exist while its useful live data do not.

### Kakamega MET AWS

- WIGOS `0-404-300-372021012AS63681`
- approximately 40 km from Kisumu
- elevation around 1585 m

Potentially interesting because it is closer than Kericho/Kisii, but actual operational/public-data availability remained uncertain.

### Lesson

The station resolver needs to distinguish:

```text
EXISTS
OPERATIONAL
ACCESSIBLE
FRESH
CAPABLE
REPRESENTATIVE
```

The nearest catalogue station is not automatically the best truth source.

---

## 7. WIS2

### Role

WMO Information System 2.0 is one of the most important potential global **observation delivery mechanisms** behind the WIGOS discovery layer.

Relevant internationally exchanged classes can include:

- SYNOP/surface observations;
- TEMP/radiosonde;
- PILOT;
- moored buoy;
- drifting buoy;
- marine/ship observations;
- other WMO exchange products.

Data may be BUFR and require decoding/normalization.

### Why it matters

For a globally forkable Open Local Weather, the ideal pattern is:

```text
OSCAR discovers station
       ↓
WIGOS identity
       ↓
WIS2 or another provider supplies observation
```

This potentially gives the system access to surface stations beyond airports.

### Caveat

WIS2 availability is not synonymous with every OSCAR station having a convenient globally accessible observation endpoint. Discovery and actual retrieval need to be tested independently.

### Recommended use

High priority for research and ingestion because it is one of the best candidates for a standards-based global surface/profile observation layer.

---

## 8. METAR / Aviation Weather

This source is already proving its value in the current system.

### Strengths

- global-ish aviation coverage;
- straightforward station identifiers;
- frequent observations at active airports;
- temperature/dewpoint;
- wind;
- pressure;
- visibility;
- cloud layers;
- present weather;
- thunder/convective indicators;
- historical archives from appropriate providers.

### Existing Open Local Weather lesson

Kisumu METAR observations already demonstrated that reanalysis can miss weather that genuinely occurred locally. Direct station reports corrected dry classifications on days when precipitation or thunder was observed.

This is exactly the kind of observational evidence the broader source effort should seek.

### Weaknesses

- airports are not everywhere;
- airport siting may be unrepresentative of a city or mountain/coastal microclimate;
- precipitation amounts can be absent or unreliable;
- reporting gaps occur;
- present-weather observations and measured accumulations are different evidence types.

METAR should remain a strong source, but not the global station-discovery strategy by itself.

---

## 9. National meteorological station networks

National services may expose denser AWS/SYNOP networks than international feeds.

Potential value:

- closer station;
- local rain gauges;
- national automatic stations;
- higher-frequency data;
- climatological/local-network observations.

Problems:

- APIs vary by country;
- access/licensing varies;
- HTML-only portals are common;
- station IDs may need reconciliation with WIGOS;
- feeds may disappear or change.

Recommendation:

Treat national providers as modular enrichment behind global discovery, not as the baseline portability mechanism.

---

# PART II — PROFILE AND REGIONAL OBSERVATIONS

## 10. Radiosonde / TEMP

Radiosondes are excellent recorded truth for the vertical atmosphere:

- temperature profile;
- humidity profile;
- pressure/geopotential;
- wind profile.

Potential verification targets include:

- model temperature at pressure levels;
- moisture profile;
- wind aloft;
- instability-related derived quantities;
- boundary-layer depth where appropriate.

Limitations:

- sparse geography;
- commonly only one or two launches per day;
- balloon drift means the profile is not a perfectly vertical column above the launch point.

For the present project, radiosonde data should be viewed as valuable future profile truth, not required daily local truth.

WIS2 TEMP is a promising access route.

---

## 11. AMDAR / aircraft observations

Aircraft observations can provide:

- temperature;
- wind;
- sometimes humidity;
- repeated ascent/descent profiles around airports;
- cruise-level observations along routes.

Earlier WMO material noted active African AMDAR development and Kenya Airways participation.

This is potentially valuable for East African upper-air verification because conventional radiosonde coverage is sparse.

However, investigate:

- public accessibility;
- licensing/redistribution;
- latency;
- exact variables;
- spatial/temporal matching.

Do not assume that evidence of an operational AMDAR programme means unrestricted ingest is available.

---

## 12. Marine observations

WIS2 and other networks can expose:

- moored buoys;
- drifting buoys;
- ship observations.

These become important for coastal/island forks and for evaluating model behavior over water.

Possible variables:

- air temperature;
- pressure;
- wind;
- sea-surface temperature;
- waves where available.

For inland Kisumu these are lower priority unless a Lake Victoria observing network with usable data is found. National/regional lake buoys would be particularly interesting if discoverable.

---

## 13. RBON and GBON

### RBON

The Regional Basic Observing Network is a WIGOS network designation selected at WMO regional level.

### GBON

The Global Basic Observing Network is the global set intended to support essential internationally exchanged observations, particularly for numerical weather prediction.

### Important implementation interpretation

Neither should be treated as an observation provider.

They are useful metadata:

```text
station belongs to GBON
station belongs to RBON Region I
```

Membership may be useful as one quality/context signal when choosing among stations, but actual data still need to come through WIS2 or another feed.

---

# PART III — RADAR

## 14. WMO Weather Radar Database (WRD)

### Role

The WMO Weather Radar Database is primarily a **radar metadata catalogue**.

Potential metadata includes:

- radar location;
- status;
- radar band;
- polarization;
- nominal range;
- scanning/exchange information;
- formats/products.

It is the radar analogue of OSCAR discovery much more than a global radar-pixel API.

### Why useful

For an arbitrary location:

```text
lat/lon
  ↓
WRD candidate radars
  ↓
range / geometry / status check
  ↓
find actual regional/national data provider
```

### Do not assume

```text
WRD says radar exists
    ⇒ live scans are available
```

The Kisumu test demonstrated why.

---

## 15. Kisumu radar findings

The WMO WRD statistics indicated two active Kenyan radars:

- JKIA / Nairobi;
- Malindi.

Approximate distance from Kisumu:

```text
JKIA/Nairobi    ~276 km
Malindi         ~688 km
```

Even if operational, Nairobi is a poor low-level precipitation truth source for Kisumu because of range, beam height and terrain.

More importantly, Kenya Meteorological Department station metadata reported both radar installations as **closed**, with closure dates in 2021, while other catalogues continued to list them.

This is a valuable general lesson:

```text
aggregate metadata status
        ≠
national authoritative status
        ≠
successful live observation retrieval
```

Preserve multiple status claims rather than forcing them into one prematurely.

A useful conceptual state is:

```text
metadata_status_claims
last_live_observation
last_fetch_attempt
expected_cadence
freshness
recent_provider_success
effective_usability
```

`METADATA_CONFLICT` is a legitimate state.

For Kisumu, current design assumption should be:

> no dependable local ground-radar truth source has yet been identified.

That is acceptable; radar must be geographically optional.

---

## 16. Radar providers worth adding regionally

### EUMETNET OPERA — Europe

Strong candidate for the first regional radar implementation.

Useful characteristics:

- European radar composites;
- single-site radar products;
- near-real-time and archive capabilities;
- metadata/catalogue services;
- ODIM HDF5 is common;
- modern APIs including OGC-oriented access exist in the ecosystem.

Good test environment because Europe has dense radar coverage.

### NEXRAD — United States

Excellent candidate for US deployments.

Advantages:

- extensive public archive;
- high-quality radar network;
- well-understood formats/tooling;
- good test case for precipitation occurrence, timing and spatial displacement.

### National networks

Other countries/regions will need provider adapters.

Examples worth eventual investigation:

- Australia BOM;
- Japan JMA;
- national European services;
- South African/regional services;
- other WMO Member feeds.

Do not try to implement all radar networks before proving the observation model with one or two high-quality regions.

---

## 17. What radar can verify

Raw radar reflectivity is not equivalent to model precipitation accumulation.

Potential derived truth products:

```text
radar reflectivity
    ↓
precipitation occurrence
rain-rate estimate
accumulation estimate
storm location
echo onset
echo cessation
storm movement
```

Potential model metrics:

- rain/no-rain correctness;
- precipitation onset timing;
- precipitation cessation;
- rain-rate error;
- accumulation error;
- storm displacement;
- missed/false precipitation areas.

Keep the native radar quantity and the derived verification product distinct.

Radar suitability should consider:

- distance;
- beam height at target;
- nominal range;
- terrain blockage;
- scan freshness;
- product quality;
- provider health.

Nearest radar alone is not enough.

---

# PART IV — SATELLITE

## 18. Why satellite is particularly valuable

Satellite may become one of the highest-value additions to Open Local Weather's observed-reality record because it provides broad, frequent coverage even where surface/radar networks are sparse.

For Kisumu, this is especially important:

```text
surface observations: useful but limited
radar: poor/unavailable
GEO satellite: excellent continuous coverage
```

Satellite is therefore not merely a pretty map layer. It can provide recorded evidence for variables that reanalysis or sparse stations represent poorly.

---

## 19. OSCAR/Space

OSCAR/Space is primarily satellite/platform/instrument/capability metadata.

Use it to discover:

- active platforms;
- instruments;
- observing capabilities;
- coverage;
- data-access information.

Conceptual hierarchy:

```text
SatellitePlatform
    └── Instrument
          └── Product
```

As with OSCAR/Surface and WRD, it should help identify what could observe the target. The actual pixels/products should preferably come from an operational data provider.

---

## 20. EUMETSAT Meteosat Third Generation — primary Africa candidate

For Africa, EUMETSAT's operational Meteosat Third Generation system is a particularly strong production source.

### FCI

The Flexible Combined Imager provides frequent multispectral geostationary imagery.

Relevant characteristics from current EUMETSAT documentation:

- full-disc operational imaging;
- 16 FCI channels;
- native full-disc repeat cycle around 10 minutes;
- Africa-tailored products;
- approximately 3 km Africa products for many channels;
- 1 km VIS 0.6 continuity product;
- netCDF formats in the Africa service;
- Level 1c and selected Level 2 products.

### Data Store access

EUMETSAT provides:

- web UI;
- REST API;
- EUMDAC CLI;
- EUMDAC Python library;
- ROI/chunk selection for FCI data.

Important FCI Data Store collections previously identified:

```text
EO:EUM:DAT:0662
FCI Level 1c Normal Resolution Image Data — MTG — 0 degree

EO:EUM:DAT:0665
FCI Level 1c High Resolution Image Data — MTG — 0 degree
```

### Lightning Imager

MTG also provides Lightning Imager products, including:

- lightning events;
- groups;
- flashes;
- accumulated flashes;
- accumulated flash area;
- accumulated flash radiance.

This is highly interesting for Open Local Weather because direct lightning observations can provide independent convective truth.

### Africa Level 2 / derived products

Current EUMETSAT documentation describes Africa-tailored derived products including examples such as:

- Global Instability Indices subsets;
- Optical Cloud Analysis subsets/options;
- NWC products;
- RGB products;
- lightning products.

Before building cloud retrievals from raw channels, investigate whether an appropriate official L2 cloud product already supplies the needed physical quantity.

### Recommendation

For Kisumu/Africa, EUMETSAT MTG should be treated as a **high-priority production ingestion target**, ahead of trying to make radar globally useful.

---

## 21. Satellite quantities and semantic discipline

Do not compare unlike quantities.

Invalid:

```text
GFS total cloud cover = 70%
satellite IR brightness temperature = -52 C
```

Better:

```text
FCI radiance / official L2 product
             ↓
cloud mask / cloud classification
             ↓
cloud fraction over target area/time window
             ↓
model cloud-cover comparison
```

Keep distinct:

- 2 m air temperature;
- land-surface skin temperature;
- brightness temperature;
- cloud-top temperature.

Likewise, satellite precipitation retrieval is not a rain gauge.

Provenance should make the physical meaning obvious.

---

## 22. Potential satellite-derived truth vectors

High-value candidates include:

### Cloud

- cloud/no-cloud;
- cloud fraction;
- cloud type;
- cloud-top temperature/height;
- morning/afternoon cloudiness;
- clearing time;
- convective cloud initiation.

### Convection

- rapid cloud-top cooling;
- deep convective cloud appearance;
- lightning onset;
- lightning frequency;
- storm development timing.

### Precipitation

Some satellite products estimate precipitation/rain rate.

Use as complementary evidence, particularly where radar/gauges are absent, but preserve retrieval uncertainty.

### Surface/other products

Potentially useful products include:

- land-surface temperature;
- water-vapor fields;
- instability indices.

These should only verify corresponding modeled physical quantities, not be substituted for surface measurements merely because they are available.

---

## 23. Meteosat-11 / SEVIRI and RealEarth prototype

Earlier live testing showed that RealEarth exposed current Meteosat-11 SEVIRI products, including channels such as:

```text
VIS 0.6
VIS 0.8
NIR 1.6
IR 3.9
WV 6.2
WV 7.3
IR 8.7
IR 9.7
IR 10.8
IR 12.0
IR 13.4
HRV
```

A live test of:

```text
Met11-SEVIRI-FD-BAND09
```

showed a current IR 10.8 µm product with a recent timestamp.

This proved that the basic satellite-discovery/point-query concept is viable.

For production Africa coverage, however, MTG/FCI through EUMETSAT should be preferred where practical.

---

## 24. RealEarth

RealEarth is extremely useful as:

- an aggregation layer;
- product-discovery tool;
- rapid prototype source;
- visualization source;
- point-value/probe source for supported products;
- fallback source.

Useful API concepts include:

```text
/api/products
/api/times
/api/time
/api/latest
/api/extents
/api/image
/api/legend
/api/shapes
/api/data
/api/probe
```

It also supports tile/WMS/KML/THREDDS-oriented access.

### Important caution

RealEarth aggregates user/provider products and should not automatically become the sole production archive or authoritative source.

Prefer direct operators such as EUMETSAT/NOAA/JMA when the integration cost is reasonable.

### Excellent use in development

RealEarth is particularly useful for quickly answering:

> Is there a product that can observe this quantity here, and can we extract something useful from it?

Then production ingestion can migrate to the authoritative operator.

---

## 25. LEO satellite precipitation / MiRS example

RealEarth exposed a `MIRS-RainRate` product derived from ATMS/JPSS processing.

This is potentially useful complementary precipitation truth.

However:

```text
latest global product timestamp
    ≠
the satellite swath crossed the target
```

Every LEO product must test actual spatial coverage.

A missing target because the orbit did not cross the location should be represented as:

```text
NO_SWATH
```

not as zero rain or provider failure.

LEO observations are intermittent snapshots; GEO provides continuity.

---

## 26. Other satellite operators for global portability

Eventually the provider registry should be able to use authoritative regional GEO operators.

### NOAA

Useful systems include:

- GOES for the Americas;
- JPSS polar products;
- NOAA Open Data Dissemination where applicable.

### JMA

Himawari is a major GEO source for East Asia/western Pacific and useful for Japan/Australia-adjacent regions depending on product/coverage.

### CMA and other operators

Relevant for regions where their geostationary platforms/products provide the best view.

The correct global abstraction is not “use Meteosat everywhere,” but:

```text
target lat/lon
    ↓
find suitable operational GEO/LEO product
    ↓
retrieve authoritative product
```

For the current Kisumu deployment, EUMETSAT is the obvious priority.

---

# PART V — COMBINING EVIDENCE WITHOUT INVENTING TRUTH

## 27. Multi-source observations describe different stages of weather

A convective event may appear sequentially as:

```text
GEO SATELLITE
cloud growth / cooling / lightning
          ↓
RADAR
precipitation echo
          ↓
SURFACE STATION / GAUGE
rain reaches ground
```

These are not redundant measurements.

Example:

```text
13:15 satellite convection begins
13:25 rapid cloud-top cooling
13:42 radar echo appears
14:06 gauge/station records precipitation
```

A model might predict:

```text
convection initiation 14:30
surface rain onset    15:00
```

Potential verification metrics include:

- convective initiation timing;
- lightning timing;
- radar-echo timing;
- surface precipitation onset;
- storm displacement;
- precipitation amount.

The project can eventually learn *how* a model fails, not merely whether a daily boolean was wrong.

---

## 28. Preserve raw observation and derived verification product

Recommended conceptual pattern:

```text
native observation
    ↓
quality control / normalization
    ↓
derived truth product
    ↓
model comparison
```

Examples:

```text
FCI radiance
  → cloud mask
  → cloud fraction

radar reflectivity
  → precip occurrence
  → rain-rate/accumulation estimate

METAR present-weather group
  → precipitation occurrence

lightning flashes
  → observed convection/lightning event
```

Where feasible, retain enough native/provenance information to recompute derived truth later.

This follows the existing project's self-correcting philosophy.

---

## 29. Observation confidence should be variable-specific

Avoid a universal ladder such as:

```text
station > radar > satellite > reanalysis
```

because the hierarchy changes with the quantity.

Examples:

### 2 m temperature

A well-sited representative surface station is usually much more directly relevant than satellite skin temperature.

### Cloud fraction

GEO satellite may be vastly more representative than one station's cloud report.

### Rain occurrence

A station that reports rain is strong evidence at that point. Radar may establish that the broader target area received rain. Satellite may provide complementary convective/precipitation evidence.

### Rain amount

A reliable gauge is excellent point truth. Radar-derived accumulation covers area but is estimated. Satellite retrievals are more indirect. Reanalysis remains modeled.

### Wind aloft

Radiosonde/AMDAR may be the relevant truth, not a surface AWS.

Source authority must therefore attach to:

```text
source × variable × geometry × quality
```

rather than source name alone.

---

## 30. Spatial representativeness matters

Open Local Weather forecasts a configured location, but observation instruments sample different geometries.

```text
station      point
radiosonde   drifting vertical profile
radar        polar/grid volume
satellite    pixel/grid
reanalysis   grid cell
```

For a city forecast, a station 60 km away and 800 m higher should not automatically overwrite a local grid estimate.

For satellite cloud verification, a small area around the configured location may be more meaningful than one pixel.

For convective precipitation, city/area occurrence may be more relevant to the user than whether a storm crossed the exact coordinate.

The truth system should preserve geometry so verification definitions can improve later.

---

# PART VI — PRACTICAL SOURCE PRIORITIES

## 31. Recommended priority for the current project

### Priority 1 — Finish generalized surface-observation discovery

Research/implement:

```text
OSCAR/Surface discovery
WIGOS identity reconciliation
WIS2 observation retrieval
existing METAR integration
```

Goal:

For an arbitrary lat/lon, discover the best usable nearby direct surface observations and preserve source/provenance.

This extends work already proving valuable at HKKI.

### Priority 2 — EUMETSAT MTG for Kisumu/Africa

Start with products that can provide robust objective truth without requiring a research-grade retrieval algorithm.

Investigate first:

- official cloud mask/cloud analysis products;
- FCI imagery required for cloud fraction if necessary;
- Lightning Imager flashes/accumulations;
- appropriate instability/cloud-top products.

Goal:

Add independent observed cloud/convection evidence to days currently judged mostly from reanalysis + airport observations.

### Priority 3 — RealEarth as rapid prototype/fallback

Use it to:

- enumerate products;
- test spatial coverage;
- test point-value retrieval;
- validate product usefulness;
- provide optional live visualization.

Do not make it the only long-term archive if authoritative access is practical.

### Priority 4 — Profile observations through WIS2

Add:

- TEMP/radiosonde;
- later PILOT;
- AMDAR if accessible/licensable.

This expands verification into atmospheric profiles but is less urgent than improving current surface/cloud/precip truth.

### Priority 5 — Radar in a region where radar is genuinely useful

Do **not** make Kisumu the first proof case.

Use:

- Europe/OPERA, or
- US/NEXRAD.

Prove radar normalization/verification there, then allow Kisumu to legitimately report no usable radar.

### Priority 6 — National/regional adapters

Add where they materially improve a deployment:

- national AWS networks;
- rain-gauge networks;
- national radar;
- lake/marine observations.

---

## 32. Suggested initial observed-reality expansion by variable

| Forecast vector | Strong candidate observed source | Secondary/fallback |
|---|---|---|
| High/low temperature | representative surface station | ERA5/reanalysis |
| Wind | surface station/METAR | reanalysis |
| Pressure | surface station/METAR | reanalysis |
| Rain occurrence | station/METAR present weather; radar where available | satellite retrieval; reanalysis |
| Rain amount | trustworthy gauge | radar estimate; reanalysis; satellite estimate |
| Rain onset | station/METAR; radar | satellite convection context |
| Thunder/convection | MTG Lightning Imager; METAR TS | GEO cloud evolution |
| Cloud cover | MTG FCI/L2 cloud product | METAR/SYNOP cloud report; reanalysis |
| Cloud-top properties | GEO satellite | model-derived equivalent |
| Upper-air temperature/humidity/wind | radiosonde/TEMP; AMDAR | reanalysis |
| Marine surface conditions | buoy/ship | reanalysis |

This is a research priority table, not a hard precedence table.

---

## 33. Ingestion should precede scoring changes

The existing roadmap's cautious sequencing is correct.

For each new source:

```text
Phase A
discover and fetch

Phase B
normalize and stamp provenance

Phase C
store beside current actuals

Phase D
measure:
  availability
  freshness
  missingness
  disagreement
  bias
  representativeness

Phase E
decide whether it should affect scoring

Phase F
rebuild historical verification if appropriate
```

This prevents a new source from changing the meaning of the historical record before its behavior is understood.

---

## 34. Provider health is part of observational truth

An observation feed can fail silently.

Track enough information to distinguish:

```text
NO_ASSET
OUT_OF_COVERAGE
SOURCE_UNAVAILABLE
STALE
METADATA_CONFLICT
NO_SWATH
NOT_REPRESENTATIVE
LICENSE_RESTRICTED
```

Never translate these into a meteorological zero.

Examples:

```text
no satellite swath ≠ no rain
no station report ≠ dry
closed radar ≠ clear sky
missing gauge value ≠ 0 mm
```

This principle already exists in Open Local Weather's three-valued METAR handling and should be generalized.

---

## 35. Live user products are a useful second output

Once these feeds exist, expose appropriate recent observations without coupling them to forecast generation.

Potential UI:

```text
Current observations

Kisumu Airport
25.8 °C
wind ...

Latest satellite
[MTG image]

Lightning
[recent flashes]

Radar
No usable local radar source
```

In Europe/US:

```text
Latest radar
[loop]
```

The UI should transparently say when a source is unavailable rather than substituting a misleading distant product.

---

# PART VII — TEST LOCATIONS

## 36. Use deliberately different environments

A global observation system should be tested where different sources dominate.

### Kisumu, Kenya

Tests:

- WIGOS/METAR dedup;
- sparse station network;
- strong elevation representativeness effects;
- weak/no usable radar;
- excellent MTG GEO satellite;
- tropical convection;
- reanalysis misses already observed.

### Central Europe / London-area example

Tests:

- dense surface observations;
- OPERA radar;
- Meteosat;
- multiple overlapping sources.

### US Midwest

Tests:

- METAR;
- dense surface networks;
- NEXRAD;
- GOES;
- strong public archives.

### Japan

Tests:

- dense observations;
- excellent radar;
- Himawari.

### Australian interior

Tests:

- sparse point observations;
- radar gaps;
- satellite dependence.

### Small ocean island

Tests:

- METAR/SYNOP importance;
- marine observations;
- GEO satellite;
- grid-vs-point representativeness.

### Mountain location

Tests:

- elevation mismatch;
- station-selection penalties;
- radar blockage;
- model-grid terrain problems.

---

# PART VIII — SOURCE-SPECIFIC RESEARCH QUESTIONS STILL OPEN

## 37. Surface/WIS2

Determine:

1. the most reliable machine-readable global OSCAR station-discovery route;
2. how best to enumerate WIS2 observations near a WIGOS station;
3. which WIS2 Global Services provide the easiest public programmatic access;
4. BUFR decoding requirements and variable mapping;
5. archive depth;
6. latency and correction/revision behavior;
7. licensing/redistribution constraints.

---

## 38. EUMETSAT/MTG

Determine:

1. exact cloud L2 products available through Data Store for Kisumu;
2. whether cloud fraction can be obtained directly rather than derived;
3. Lightning Imager archive/query mechanics;
4. product latency;
5. ROI/chunk download cost and GitHub Actions practicality;
6. authentication/token renewal;
7. archive depth;
8. redistribution rules for derived values and displayed imagery;
9. best small-footprint daily truth representation for OLW.

The target is not “download the whole satellite.” It is:

> obtain the minimum authoritative observation needed to establish cloud/convection truth around one configured location and time window.

---

## 39. RealEarth

Test programmatically:

```text
/api/products
/api/times
/api/extents
/api/data or /api/probe
```

For Kisumu:

- Meteosat SEVIRI/available MTG products;
- precipitation products;
- any cloud products that return numerical values.

Record whether each product supports point probing and historical retrieval.

---

## 40. Radar

Determine:

1. whether WRD offers a stable machine-readable metadata endpoint suitable for automated discovery;
2. authoritative live/archive access for OPERA;
3. easiest NEXRAD interface for point/area verification;
4. radar-to-target geometry calculation;
5. minimal useful derived product for OLW;
6. archive/licensing constraints.

Avoid production dependence on scraping an interactive WRD UI.

---

## 41. Upper air

Determine:

1. WIS2 TEMP availability near target regions;
2. archive access;
3. radiosonde matching radius/time;
4. AMDAR public access and licensing;
5. whether satellite sounding products add useful independent truth or merely excessive complexity at this stage.

---

# PART IX — IMPLEMENTATION GUIDANCE FOR THE NEXT AGENT

## 42. Assignment framing

Do **not** begin by redesigning Open Local Weather.

Read the existing repository first, especially:

```text
README.md
docs-internal/ARCHITECTURE.md
docs-internal/ROADMAP.md
src/openlocalweather/models.py
src/openlocalweather/store/actuals_cache.py
src/openlocalweather/verify/
src/openlocalweather/fetch/metar.py
```

The project already has:

- stored predictions;
- actuals cache;
- provenance;
- direct METAR evidence;
- deterministic verification;
- rolling/all-time model skill;
- historical rebuilding;
- graceful handling of absent observations.

The assignment is:

> Identify, ingest and preserve additional independently observed descriptions of what actually happened.

Favor additions that improve the observed-reality dataset without changing existing scoring semantics immediately.

---

## 43. Immediate recommended research/build sequence

1. Complete WIGOS/OSCAR station discovery and identity reconciliation.
2. Establish a working WIS2 surface-observation path for at least several global test locations.
3. Store new station observations with provenance beside current actuals.
4. Establish EUMETSAT MTG access for the Kisumu coordinate.
5. Identify the simplest official cloud truth product and ingest a historical/current sample.
6. Test MTG Lightning Imager data as convective truth.
7. Use RealEarth to prototype or cross-check satellite products where useful.
8. Measure new-source divergence from existing ERA5/METAR actuals.
9. Do **not** change deterministic scoring precedence until those measurements exist.
10. Implement radar proof-of-concept in an OPERA or NEXRAD region rather than forcing a Kisumu radar source that does not exist.

---

# PART X — CORE PRINCIPLES TO PRESERVE

## 44. Non-negotiable observational principles

### Recorded evidence beats convenient assumptions

If a station actually reports rain while reanalysis says dry, the discrepancy must be preserved and investigated.

### Missing is not zero

```text
None ≠ False
None ≠ 0
```

### Source identity matters

Never strip provenance from an observed value.

### Physical semantics matter

Brightness temperature is not air temperature. Reflectivity is not rainfall accumulation. Skin temperature is not 2 m temperature.

### Geometry matters

A point station, radar volume, satellite pixel and model grid cell do not observe the same spatial object.

### Freshness matters

An operational catalogue entry with stale observations is not a usable current source.

### Discovery metadata is not data

OSCAR/WRD/RBON/GBON can help locate or characterize an asset. They do not prove that an observation is available.

### Do not manufacture corroboration

The same physical station delivered through METAR and WIS2 is one observing asset, not two independent witnesses.

### Preserve the audit trail

The project should remain able to explain:

```text
what forecast was issued
what observation later arrived
where that observation came from
what physical quantity it represented
what derivation was applied
why it affected or did not affect scoring
```

---

# 45. Bottom line

The source research supports a clear direction.

Open Local Weather does **not** need a parallel observation application. It needs a progressively richer, provenance-aware observed-reality record feeding the verification system that already exists.

For the current Kisumu deployment, the highest-value source expansion appears to be:

```text
1. additional WIGOS/WIS2 surface observations
2. existing METAR evidence
3. EUMETSAT MTG FCI cloud products
4. MTG Lightning Imager
5. reanalysis retained as broad fallback/context
```

Radar should remain optional until a credible local source exists. Its absence in Kisumu is itself an important result, not a gap to hide.

For global portability:

```text
surface:
  OSCAR/WIGOS discovery
  WIS2 + METAR + national adapters

satellite:
  authoritative regional GEO operators
  EUMETSAT / NOAA / JMA / others
  RealEarth as useful aggregation/prototype layer

radar:
  WRD discovery
  regional actual-data providers such as OPERA/NEXRAD

profiles/marine:
  WIS2 TEMP/PILOT/buoy
  AMDAR where accessible
```

The goal is not to ingest every meteorological dataset available.

The goal is to add observations that let the project answer, with increasing empirical confidence:

> **What actually happened at this place, for this forecast variable and valid time?**

Every additional trustworthy answer strengthens the model-skill record. That record is what allows GFS, ECMWF, UKMET and ICON to earn different levels of trust for different forecast vectors over time.

That is the central feedback loop, and these sources exist to make its definition of reality progressively better.

---

## Public reference starting points

- Open Local Weather repository: https://github.com/dissent00/open-local-weather
- WMO OSCAR/Surface: https://oscar.wmo.int/surface/
- WMO WIS2: https://community.wmo.int/en/activity-areas/wis/wis2-overview
- wis2box station metadata documentation: https://docs.wis2box.wis.wmo.int/
- WMO RBON: https://community.wmo.int/site/knowledge-hub/programmes-and-initiatives/wmo-integrated-global-observing-system-wigos/rbon-regional-basic-observing-network
- WMO Weather Radar Database: https://wrd.mgm.gov.tr/Home/Wrd
- WMO OSCAR/Space: https://space.oscar.wmo.int/
- EUMETSAT Data Store: https://data.eumetsat.int/
- EUMETSAT MTG data access guide: https://user.eumetsat.int/resources/user-guides/mtg-data-access-guide
- EUMETSAT MTG Africa data service guide: https://user.eumetsat.int/resources/user-guides/mtg-africa-data-service-guide
- RealEarth API: https://realearth.ssec.wisc.edu/doc/api.php
- EUMETNET OPERA: https://www.eumetnet.eu/activities/observations-programme/current-activities/opera/
- NOAA Aviation Weather Center: https://aviationweather.gov/
