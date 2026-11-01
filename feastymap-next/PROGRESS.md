# FeastyMap Next progress

## Initial geographic scope
- Pondicherry/Puducherry city is the sole destination. Karaikal, Mahe, Yanam and distant areas remain out of scope.
- No authoritative legal city polygon has been selected. The application uses a provisional configurable radius around the sourced reference coordinate `11.916064, 79.812325`; default 10,000 m, configured by `PONDICHERRY_SEARCH_RADIUS_METERS` (1-20,000 m). It is not a municipal boundary.
- Starting locations may be outside the radius for travel into Pondicherry. Destination places, replacement places, place details and saved plan stops are validated against the same configured radius. Missing/invalid coordinates are rejected.

## Architecture and current provider stack
- Next.js 15 / React 19 / TypeScript frontend in this directory; FastAPI backend in `backend/`; existing Neon PostgreSQL saved itinerary integration and deterministic planner preserved.
- Photon geocodes starting addresses with India and Pondicherry bias. Explicit latitude/longitude inputs bypass geocoding.
- Overpass API searches real, mapped OSM objects with a bounded category query, a bounded response, in-memory 15-minute cache (128 entries), and per-host pacing. Place identifiers use `osm:node|way|relation:<id>` and details are refreshed in a batched Overpass request.
- OSRM-compatible services calculate actual driving, walking, and cycling road routes. Transit is unsupported and the API/UI explain this rather than silently substituting another mode.
- Leaflet / React-Leaflet renders provider coordinates, returned route geometry, and configurable raster tiles. Standard OSM attribution is visible; the UI links to the OSM map issue tool.
- Open-Meteo weather, planner constraints, budget uncertainty, dietary/accessibility assessments, saved plans and Pondicherry scope are retained. Existing DB schema/migrations are unchanged.

## Public-service limits and data quality
- Photon, Overpass, OSRM and OSM's standard tile server are public best-effort services subject to fair-use rules, throttling and service changes; they have no production SLA or unlimited-traffic guarantee. Requests are modest and bounded; use a hosted/provider-approved service for sustained production traffic.
- OSM place records are contributor-mapped and can be missing or stale. Ratings/prices/current-open status/reservation/menu details are not fabricated. OSM tags are shown as evidence only where present; missing dietary, accessibility, hours or suitability remains unknown.
- Events are not a live event feed; current OSM event search returns no candidates. Transit routing is not available. OSM's public raster tiles must not be bulk downloaded, prefetched or used for offline archives; attribution is displayed.
- Old saved stops persisted as Google Places IDs are preserved in PostgreSQL but cannot be refreshed by the OSM adapter; loading them responds with HTTP 409 explaining that limitation. They are not deleted or automatically rewritten.

## Implemented in this provider migration
- Removed Google Places, Google Routes, and Google Maps JavaScript dependencies from the active nested app request and map flow. No Google key is required for this flow.
- Added configurable `PHOTON_API_URL`, `OVERPASS_API_URL`, `OSRM_DRIVING_URL`, `OSRM_WALKING_URL`, `OSRM_BICYCLING_URL`, `PUBLIC_PROVIDER_USER_AGENT`, `WEATHER_API_URL`, `NEXT_PUBLIC_MAP_TILE_URL`, and `NEXT_PUBLIC_MAP_ATTRIBUTION` environment settings.
- Added OSM adapter tests for Photon resolution, coordinate passthrough, bounded/scoped Overpass results, OSM saved-place refresh, OSRM geometry/measurements, and unsupported transit. External providers are mocked in automated tests.
- Added React-Leaflet map and route polylines. Kept the existing Next.js design and saved itinerary API. Saved stops use the existing `provider` and `provider_place_id` database columns; no migration was needed.
- Rewrote `DEPLOYMENT.md` with Windows setup, environment variables, public service fair use, existing migration notes, expected behavior and ordered smoke checks.

## Latest verification status
- Backend `python -m unittest discover -s tests -v`: 49 tests passed. All external-provider responses in these automated tests are mocked; this does not verify live services.
- Nested frontend `npm.cmd run build`: production build passed, including TypeScript and lint checks. A prior attempt hit a Windows/OneDrive `EINVAL` in generated `.next`; clearing only this generated cache and rerunning succeeded.
- Live local check: `/health` returned 200 with `database_configured=true`; `/api/places?category=cafe&query=Pondicherry` returned 503 after the Overpass service timed out. No complete live itinerary was produced, and no additional public-provider requests were made. Photon, OSRM and weather were not individually live-verified.
- The frontend production server started on port 4028 and returned HTTP 200. FastAPI CORS preflight for `http://localhost:4028` returned HTTP 200 with the expected allow-origin. No database writes were made during this migration. The app has not been deployed.

## Remaining limits and next steps
1. Run the exact Windows smoke checklist in `DEPLOYMENT.md` using modest public service traffic; record individual Photon, Overpass, OSRM, tile and weather results, or provider failures.
2. Confirm authoritative Puducherry city GIS and licensing before changing the provisional radius or claiming municipal boundary enforcement.
3. If sustained real traffic is intended, select properly hosted geocoding/search/routing/tile providers; do not treat public demo endpoints as production services.
4. Decide how to present or retire legacy Google-provider saved plans without deleting existing records.
5. Add authentication/shared rate limiting and configure production CORS/readiness before any future deployment.

## Neon PostgreSQL setup and verification (2026-10-31)
- Neon CLI 8.15.1 authenticated and linked this project to `feastymap` (`solitary-wildflower-46220609`), organization `org-rough-bird-81223618`, production branch `br-curly-art-azc4vd9f` in `aws-ap-southeast-1`.
- `neon.ts` declares Postgres only. `neon config plan` reported no production branch changes; `neon deploy` completed with no changes and reported Postgres as the utilized service. Existing AI Gateway/object-storage credential records were not declared or used.
- Created `feastymap-next/.env` from `.env.example` because it was absent. Neon pooled `DATABASE_URL` was set to SQLAlchemy's `postgresql+psycopg://` dialect for the installed psycopg 3 driver. `DATABASE_URL_UNPOOLED` was used only for migration work. `.env` and `.neon` are gitignored; no connection string is stored in source files or printed in command output.
- Before production migration, the existing six-statement SQL migration was applied successfully to a temporary production-derived branch and all four required tables were verified. That temporary branch was deleted after the rehearsal. The same migration then succeeded on production using the direct connection; `user_preferences`, `itineraries`, `itinerary_stops`, and `source_retrievals` were verified there.
- Local FastAPI startup succeeded. `/health` returned `status=ok` and `database_configured=true`; a Neon-backed `SELECT 1` returned 1. CORS preflight for `http://localhost:4028` returned HTTP 200 with the expected allow-origin.
- A temporary saved itinerary was created through `POST /api/itineraries/saved`, retrieved through the saved-list endpoint, and deleted; follow-up list confirmed the test row was gone. Saved-plan refresh/load was not tested because it calls Google Places/Routes and no Google key was configured. No live Google API request was made.
- Backend: `python -m unittest discover -s tests -v` — 42 tests passed (provider/database mocks). Root frontend `npm.cmd run build` passed; nested `feastymap-next` frontend build passed after removing the unused `@neon/env` dependency. Neon initialization added `@neon/config` for `neon.ts`. A read-only `npm.cmd audit --json` after cleanup still reports 1 critical Next.js vulnerability (`15.5.27`) and 1 high transitive PostCSS vulnerability; npm lists Next.js `15.5.28` as a non-major fix. No dependency security fix was applied in this database setup task.
- The root `src/app/outing-planner` still uses a local sample itinerary engine and does not call FastAPI. The nested `feastymap-next` frontend does call the FastAPI API. The successful CORS preflight verifies the configured origin, but does not demonstrate root-frontend itinerary integration.
- Security check: the legacy root `.env` contained populated server-key variables (values were not printed) and had been tracked by Git. Root `.gitignore` now excludes env files, and the root `.env` was removed from the Git index without changing its local contents. None of the scanned server-key values were found in either browser bundle or in other currently tracked files. Removing it from the current index does not erase prior Git history; rotate any credentials that may have been committed and review repository history separately. No history rewrite was performed.
- No live Places, Routes, or Maps test was performed; no Google services were enabled. Neon PostgreSQL production connection, schema migration, saved-plan API create/list/delete, and CORS preflight were verified as described above.

## Commands
- Backend install: `python -m pip install -r backend/requirements.txt`
- Backend run: `uvicorn app.main:app --app-dir backend --reload --port 8000`
- Backend tests (from `backend/`): `python -m unittest discover -s tests -v`
- Frontend dev/build (from this directory): `npm run dev` / `npm run build`
- PostgreSQL migration: `psql "$DATABASE_URL" -f backend/migrations/001_initial.sql`
