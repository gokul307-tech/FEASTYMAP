# Local setup and deployment

## Providers and fair use

- Place search uses Photon for start-location geocoding and Overpass API for mapped points of interest. Search is bounded to the configured Pondicherry circle. OSM data is community-mapped, can be incomplete or stale, and does not establish venue operation, prices, menus, or reservations.
- Routes use OSRM-compatible public endpoints for driving, walking, and cycling. Transit is not provided in this configuration. Public routing servers are shared best-effort services and may throttle or reject traffic.
- The interactive map uses OpenStreetMap standard raster tiles by default and displays contributor attribution. Follow the tile usage policy: use normal interactive viewing, honor caching, do not prefetch, bulk-download, or build offline tile archives. The public tile server has no SLA and should not be assumed to support production traffic.
- These public services need no API keys, but are not unlimited or guaranteed. Keep request volumes modest. Backend responses are cached in memory for up to 15 minutes (bounded to 128 small entries) and requests to shared hosts are paced. Use an appropriately hosted provider for sustained production traffic.
- References: [Photon API](https://github.com/komoot/photon/blob/master/docs/api-v1.md) and [Photon fair-use guidance](https://github.com/komoot/photon); [Overpass public instance guidance](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html); [OSRM API](https://project-osrm.org/docs/v5.24.0/api/); [OSRM public routing service](https://routing.openstreetmap.de/about.html); [OSM tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

## Windows local setup

1. In PowerShell, move to `feastymap-next`. If `.env` does not exist, create it by copying `.env.example`; never overwrite an existing `.env`. Configure `DATABASE_URL` and review the frontend URL/CORS entries. No Google keys are required. Keep database credentials only in this backend-loaded `.env`; do not prefix them with `NEXT_PUBLIC_`.
2. Install backend dependencies: `python -m pip install -r backend/requirements.txt`.
3. In terminal 1, start the backend from this directory: `uvicorn app.main:app --app-dir backend --reload --port 8000`.
4. In terminal 2, start the frontend from this directory: `npm install` then `npm run dev` (port 4028).
5. Open `http://localhost:4028`; API health is at `http://localhost:8000/health`. The frontend API URL is `NEXT_PUBLIC_API_BASE_URL`; the backend CORS allowlist is `CORS_ALLOWED_ORIGINS`.

## Environment variables

- `PHOTON_API_URL`: Photon geocoding endpoint.
- `OVERPASS_API_URL`: Overpass interpreter endpoint.
- `OSRM_DRIVING_URL`, `OSRM_WALKING_URL`, `OSRM_BICYCLING_URL`: OSRM-compatible route endpoints.
- `PUBLIC_PROVIDER_USER_AGENT`: descriptive backend User-Agent for public provider requests.
- `NEXT_PUBLIC_MAP_TILE_URL`: Leaflet tile URL template, with `{z}/{x}/{y}` placeholders.
- `NEXT_PUBLIC_MAP_ATTRIBUTION`: attribution HTML displayed by Leaflet; preserve tile/provider attribution requirements.
- `WEATHER_API_URL`: Open-Meteo endpoint (existing weather feature).
- `DATABASE_URL`: backend-only PostgreSQL URL; for Neon use the pooled `postgresql+psycopg://...` URL.
- `NEXT_PUBLIC_API_BASE_URL`: browser-visible FastAPI base URL.
- `CORS_ALLOWED_ORIGINS`: comma-separated exact frontend origins allowed by FastAPI.
- `PONDICHERRY_SEARCH_RADIUS_METERS`: configurable provisional circle radius (default 10,000 m; valid 1–20,000 m).

The search circle is centered on the sourced reference coordinate `11.916064, 79.812325`; it is product-defined, not an official city boundary. Karaikal, Mahe, Yanam, and distant locations remain unsupported destination stops. Starting points may be outside the destination circle for travel into Pondicherry.

## PostgreSQL and saved itineraries

The existing migration is `backend/migrations/001_initial.sql`; there is no Alembic migration system. Inspect it and confirm a backup/target database before applying. The script is idempotent for the existing schema and does not delete rows. From PowerShell, with `DATABASE_URL` set in the shell, run `psql --set ON_ERROR_STOP=on $env:DATABASE_URL -f backend/migrations/001_initial.sql`. The UI supports saved itinerary create/list/load/refresh/delete. Saved rows store provider IDs; current refresh supports OpenStreetMap IDs. Older rows stored under Google Places cannot be refreshed by the new provider and return a clear conflict; they are not deleted or migrated automatically.

## Ordered smoke checks

1. `GET /health` should return `status: ok`; confirm the UI opens at port 4028.
2. Search a category around Pondicherry. Results should have `osm:` IDs and visible OpenStreetMap attribution; try Karaikal/Mahe/Yanam and expect an out-of-scope message.
3. Open a result's OpenStreetMap object link and confirm its marker uses the returned coordinates.
4. Generate an outing using a resolvable starting address or explicit `lat,lng`; choose drive, walk, or bicycle. The itinerary should show separate visit/travel time and route lines; unknown prices and tags remain unknown. Transit should report unsupported.
5. Replace a stop and confirm the replacement is in-scope and the affected route is recalculated.
6. Save the plan; list it, load/refresh it, and delete it. Verify that existing Neon records remain present.
7. If a public provider is unavailable, retry later; do not loop requests. Review backend logs for provider-specific safe error messages and browser console/network panel for CORS or map tile errors.

## Managed deployment

Before public deployment, choose hosted or self-operated provider endpoints with an appropriate usage agreement/capacity, review OSM data attribution and tile policy, configure production CORS, shared rate limiting, authentication, Neon connection secrets, and health/readiness monitoring. Public demo endpoints provide no production SLA. This project has not been deployed as part of the provider migration.
