'use client';

import { FormEvent, useEffect, useState } from 'react';
import MapCanvas from './MapCanvas';

const categories = [
  'beach',
  'cafe',
  'restaurant',
  'heritage',
  'temple',
  'shopping',
  'nature',
  'activity',
  'events',
];
const api = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';
const pondicherryToday = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(
  new Date()
);
type Place = {
  id: string;
  name: string;
  category: string;
  address?: string;
  rating?: number;
  open_now?: boolean;
  opening_hours?: string[];
  retrieved_at?: string;
  osm_uri?: string;
  source?: string;
  verification?: string;
  location: { lat: number; lng: number };
  price_range?: {
    start?: { amount: string; currency: string };
    end?: { amount: string; currency: string };
    source?: string;
  } | null;
};
type Stop = {
  place: Place;
  start_time: string;
  duration_minutes: number;
  travel_minutes: number;
  travel_distance_meters: number;
  reason: string;
  cost_info?: {
    status: string;
    note: string;
    price_range?: Place['price_range'];
    estimate?: { amount: number; currency: string; source: string };
  };
  constraint_assessment?: {
    overall: string;
    checks: { constraint: string; status: string; detail: string }[];
  };
  meal_period?: string | null;
  meal_service_status?: string | null;
  planned_open?: boolean | null;
  route_geometry?: { coordinates?: number[][] } | null;
};
type Plan = {
  id?: string;
  budget_inr: number;
  total_duration_minutes: number;
  travel_minutes: number;
  distance_meters: number;
  stops: Stop[];
  warnings: string[];
  weather?: {
    source: string;
    freshness: string;
    date: string;
    precipitation_probability_max?: number;
    weather_code?: number;
  } | null;
  weather_advisory?: string;
  budget_status: string;
  cost_summary?: {
    known_provider_price_ranges: { place_name: string; price_range?: Place['price_range'] }[];
    estimated_costs: {
      place_name: string;
      estimate?: { amount: number; currency: string; source: string };
    }[];
    unknown_costs: { item?: string; place_name?: string; reason: string }[];
  };
  weather_alternatives?: { place: Place; indoor_type_evidence: string[]; note: string }[];
};
type SavedPlan = {
  id: string;
  plan_date: string;
  summary: Record<string, number | null>;
  created_at: string;
};

function createOwnerId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export default function Home() {
  const [category, setCategory] = useState('beach');
  const [places, setPlaces] = useState<Place[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [message, setMessage] = useState('Discover OpenStreetMap places or build an outing plan.');
  const [busy, setBusy] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [searchRadiusMeters, setSearchRadiusMeters] = useState<number | null>(null);
  const [ownerId, setOwnerId] = useState('');
  const [savedPlans, setSavedPlans] = useState<SavedPlan[]>([]);
  const [lastRequest, setLastRequest] = useState<Record<string, unknown> | null>(null);
  const [savedMessage, setSavedMessage] = useState('');

  async function refreshSaved(id: string) {
    try {
      const res = await fetch(`${api}/api/itineraries/saved?owner_id=${encodeURIComponent(id)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? 'Could not load saved plans');
      setSavedPlans(data.itineraries);
    } catch (error) {
      setSavedPlans([]);
      setSavedMessage(error instanceof Error ? error.message : 'Saved plans unavailable');
    }
  }

  useEffect(() => {
    let id = localStorage.getItem('feastymap-owner-id');
    if (!id) {
      id = createOwnerId();
      localStorage.setItem('feastymap-owner-id', id);
    }
    setOwnerId(id);
    void refreshSaved(id);
  }, []);

  async function discover() {
    setBusy(true);
    setMessage('Searching OpenStreetMap places…');
    try {
      const query = searchText.trim() || 'Pondicherry';
      const res = await fetch(
        `${api}/api/places?category=${encodeURIComponent(category)}&query=${encodeURIComponent(query)}`
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? 'Place search failed');
      setPlaces(data.places);
      setSearchRadiusMeters(data.scope?.radius_meters ?? null);
      setMessage(
        data.places.length
          ? `${data.places.length} places · OpenStreetMap via Overpass · mapped details may be incomplete`
          : (data.notice ?? 'No places returned for this search.')
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Place search unavailable');
      setPlaces([]);
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setPlan(null);
    setMessage('Finding places and checking actual route estimates…');
    const form = new FormData(e.currentTarget);
    const payload = {
      start_location: form.get('start_location'),
      date: form.get('date'),
      start_time: form.get('start_time'),
      duration_hours: Number(form.get('duration_hours')),
      budget_inr: Number(form.get('budget_inr')),
      interests: form.getAll('interests'),
      transport: form.get('transport'),
      group_size: Number(form.get('group_size')),
      dietary_preferences: form.getAll('dietary_preferences'),
      accessibility_needs: form.getAll('accessibility_needs'),
      group_type: form.get('group_type'),
    };
    setLastRequest(payload);
    try {
      const res = await fetch(`${api}/api/itineraries`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? 'Planning failed');
      setPlan(data.itinerary);
      setSearchRadiusMeters(data.scope?.radius_meters ?? null);
      setMessage('Plan assembled from provider results. Review venue details before travelling.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Planner unavailable');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <header>
        <a className="brand" href="#">
          feasty<span>map</span>
        </a>
        <span className="city">PONDICHERRY, INDIA</span>
        <a className="quiet" href="#planner">
          Plan an outing ↗
        </a>
      </header>
      <section className="hero">
        <div className="heroCopy">
          <p className="eyebrow">THE GOOD DAY STARTS HERE</p>
          <h1>
            Find your way
            <br />
            to <em>something lovely.</em>
          </h1>
          <p>Real places, thoughtful routes and a day shaped around you. Start in Pondicherry.</p>
          <a className="button" href="#planner">
            Build my day <span>↓</span>
          </a>
        </div>
        <div className="heroPhoto">
          <div className="photoLabel">
            <span>01 — THE FRENCH QUARTER</span>
            <b>Sea air, slow mornings.</b>
          </div>
        </div>
        <div className="heroStamp">
          11°56′N
          <br />
          79°50′E
        </div>
      </section>
      <section className="searchPanel">
        <div>
          <span className="eyebrow">A LITTLE INSPIRATION</span>
          <h2>What are you in the mood for?</h2>
          <p className="scopeNote">
            Pondicherry city is the only supported destination. Search and recommendations stay
            within the provisional{' '}
            {searchRadiusMeters == null
              ? 'configured city'
              : `${(searchRadiusMeters / 1000).toFixed(0)} km`}{' '}
            search area.
          </p>
        </div>
        <div className="searchRow">
          <input
            aria-label="Search for a place or neighborhood in Pondicherry"
            placeholder="Place or neighborhood in Pondicherry"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
          <select
            aria-label="Place category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            {categories.map((c) => (
              <option key={c} value={c}>
                {c[0].toUpperCase() + c.slice(1)}
              </option>
            ))}
          </select>
          <button className="button dark" onClick={discover} disabled={busy}>
            Explore places <span>↗</span>
          </button>
        </div>
      </section>
      {places.length > 0 && (
        <section className="results">
          <p className="eyebrow">OPENSTREETMAP RESULTS · PONDICHERRY</p>
          <p className="scopeNote">
            Pondicherry city is the only supported destination. Results use a configurable initial
            city search area; fringe coverage is provisional.
          </p>
          <MapCanvas places={places} />
          <div className="cards">
            {places.map((p) => (
              <article className="card" key={p.id}>
                <div className="cardtop">
                  <span>{p.category}</span>
                  <span>{p.rating != null ? `★ ${p.rating}` : 'Rating unavailable'}</span>
                </div>
                <h3>{p.name}</h3>
                <p>{p.address ?? 'Address unavailable'}</p>
                <p className="muted">
                  {p.open_now === true
                    ? 'Reported open now'
                    : p.open_now === false
                      ? 'Reported closed now'
                      : 'Current opening status unavailable'}
                </p>
                <p className="muted">
                  {p.opening_hours?.length
                    ? `Mapped hours (not current confirmation): ${p.opening_hours.join(' · ')}`
                    : 'Mapped opening hours unavailable'}
                </p>
                {p.retrieved_at && (
                  <p className="muted">
                    Provider data retrieved {new Date(p.retrieved_at).toLocaleString()}
                  </p>
                )}
                {p.osm_uri && (
                  <a href={p.osm_uri} target="_blank" rel="noreferrer">
                    View OpenStreetMap object ↗
                  </a>
                )}
              </article>
            ))}
          </div>
        </section>
      )}
      <section className="planner" id="planner">
        <div className="plannerIntro">
          <p className="eyebrow">YOUR DAY, YOUR WAY</p>
          <h2>
            A plan that
            <br />
            <em>feels like you.</em>
          </h2>
          <p>
            Tell us the essentials. We’ll build a route from places returned by the live providers.
          </p>
          <div className="note">
            Place data comes from community mapping and may be incomplete or out of date. Unverified
            prices stay unknown.
          </div>
        </div>
        <form onSubmit={submit}>
          <label>
            Starting point (can be outside Pondicherry for travel into the city)
            <input name="start_location" defaultValue="Pondicherry" minLength={2} required />
          </label>
          <div className="two">
            <label>
              Date
              <input type="date" name="date" defaultValue={pondicherryToday} required />
            </label>
            <label>
              Start time
              <input type="time" name="start_time" defaultValue="09:00" required />
            </label>
          </div>
          <label>
            Group type
            <select name="group_type">
              <option value="mixed">Mixed group</option>
              <option value="solo">Solo</option>
              <option value="couple">Couple</option>
              <option value="family">Family</option>
              <option value="friends">Friends</option>
              <option value="seniors">Seniors</option>
            </select>
          </label>
          <label>Dietary preferences</label>
          <div className="checks">
            {['vegetarian', 'vegan', 'halal', 'jain', 'gluten_free'].map((value) => (
              <label className="check" key={value}>
                <input type="checkbox" name="dietary_preferences" value={value} />
                {value.replace('_', ' ')}
              </label>
            ))}
          </div>
          <label>Accessibility needs</label>
          <div className="checks">
            {['step_free', 'wheelchair', 'low_walking', 'accessible_restroom'].map((value) => (
              <label className="check" key={value}>
                <input type="checkbox" name="accessibility_needs" value={value} />
                {value.replace('_', ' ')}
              </label>
            ))}
          </div>
          <div className="two">
            <label>
              How long?
              <select name="duration_hours">
                <option value="3">Short outing · 3h</option>
                <option value="5">Half day · 5h</option>
                <option value="9">Full day · 9h</option>
              </select>
            </label>
            <label>
              Budget (₹)
              <input type="number" name="budget_inr" min="0" defaultValue="2500" />
            </label>
          </div>
          <label>Interests</label>
          <div className="checks">
            {categories.map((c, i) => (
              <label className="check" key={c}>
                <input
                  type="checkbox"
                  name="interests"
                  value={c}
                  defaultChecked={i === 0 || i === 1}
                />{' '}
                {c}
              </label>
            ))}
          </div>
          <div className="two">
            <label>
              Getting around
              <select name="transport">
                <option value="drive">Drive</option>
                <option value="walk">Walk</option>
                <option value="bicycle">Bicycle</option>
                <option value="transit" disabled>
                  Transit (unavailable)
                </option>
              </select>
            </label>
            <label>
              Group size
              <input name="group_size" type="number" min="1" max="30" defaultValue="2" />
            </label>
          </div>
          <button className="button dark full" disabled={busy}>
            {busy ? 'Working…' : 'Create my itinerary'} <span>↗</span>
          </button>
        </form>
      </section>
      <section className="output" aria-live="polite">
        <p>{message}</p>
        {plan && (
          <>
            <div className="summary">
              <b>{plan.total_duration_minutes} min</b>
              <span>total time</span>
              <b>{plan.travel_minutes} min</b>
              <span>in transit</span>
              <b>{(plan.distance_meters / 1000).toFixed(1)} km</b>
              <span>route distance</span>
              <b>₹ —</b>
              <span>
                {plan.cost_summary?.known_provider_price_ranges.length ?? 0} provider price ranges
              </span>
              <b>₹{plan.budget_inr.toLocaleString('en-IN')}</b>
              <span>budget target · fit unverified</span>
            </div>
            <p className="weatherNote">
              {plan.weather
                ? `Weather · ${plan.weather.source} · ${plan.weather.date}: ${plan.weather.precipitation_probability_max == null ? 'precipitation probability unavailable' : `${plan.weather.precipitation_probability_max}% max precipitation chance`} · ${plan.weather.freshness}`
                : plan.weather_advisory}
            </p>
            {plan.weather_advisory && <p className="weatherNote">{plan.weather_advisory}</p>}
            {plan.stops.length > 0 && (
              <>
                <p className="scopeNote">
                  Itinerary stops are provider-mapped places inside the configured Pondicherry
                  search area.
                </p>
                <MapCanvas
                  places={plan.stops.map((stop) => stop.place)}
                  routeGeometries={plan.stops.map((stop) => stop.route_geometry ?? null)}
                />
              </>
            )}
            {plan.cost_summary && (
              <p className="weatherNote">
                Costs: {plan.cost_summary.known_provider_price_ranges.length} provider range(s),{' '}
                {plan.cost_summary.estimated_costs.length} estimate(s),{' '}
                {plan.cost_summary.unknown_costs.length} unknown item(s). Budget fit is unverified.
              </p>
            )}
            {!!plan.weather_alternatives?.length && (
              <div className="weatherNote">
                <b>Indoor alternatives from places already found</b>
                {plan.weather_alternatives.map((alternative) => (
                  <p key={alternative.place.id}>
                    {alternative.place.name} · provider type:{' '}
                    {alternative.indoor_type_evidence.join(', ')} · {alternative.note}
                  </p>
                ))}
              </div>
            )}
            <button
              className="button dark"
              disabled={!ownerId || !lastRequest}
              onClick={async () => {
                setSavedMessage('Saving…');
                try {
                  const res = await fetch(`${api}/api/itineraries/saved`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      owner_id: ownerId,
                      request: lastRequest,
                      itinerary: plan,
                    }),
                  });
                  const data = await res.json();
                  if (!res.ok) throw new Error(data.detail ?? 'Save failed');
                  await refreshSaved(ownerId);
                  setSavedMessage('Itinerary saved to the configured database.');
                } catch (error) {
                  setSavedMessage(error instanceof Error ? error.message : 'Save failed');
                }
              }}
            >
              Save itinerary ↗
            </button>
            {plan.stops.length === 0 ? (
              <p>No feasible stops with confirmed route estimates were returned.</p>
            ) : (
              plan.stops.map((s, i: number) => (
                <article className="stop" key={s.place.id}>
                  <span className="step">{String(i + 1).padStart(2, '0')}</span>
                  <div>
                    <h3>{s.place.name}</h3>
                    <p>
                      {s.start_time} · {s.duration_minutes} min stop · {s.travel_minutes} min /{' '}
                      {(s.travel_distance_meters / 1000).toFixed(1)} km route
                    </p>
                    <p>{s.reason}</p>
                    {s.cost_info && (
                      <p>
                        Cost:{' '}
                        {s.cost_info.status === 'provider_range' && s.cost_info.price_range
                          ? `${s.cost_info.price_range.start?.currency ?? s.cost_info.price_range.end?.currency ?? ''} ${s.cost_info.price_range.start?.amount ?? '—'}–${s.cost_info.price_range.end?.amount ?? '—'} (${s.cost_info.price_range.source ?? 'provider'})`
                          : s.cost_info.status === 'estimated' && s.cost_info.estimate
                            ? `${s.cost_info.estimate.currency} ${s.cost_info.estimate.amount} estimate from ${s.cost_info.estimate.source}`
                            : s.cost_info.note}
                      </p>
                    )}
                    {s.constraint_assessment?.checks.map((check) => (
                      <p key={check.constraint}>
                        {check.constraint}: {check.status.replaceAll('_', ' ')} · {check.detail}
                      </p>
                    ))}
                    {s.meal_period && (
                      <p>
                        {s.meal_period} time window · {s.meal_service_status}
                      </p>
                    )}
                    {s.planned_open === null && (
                      <p>Opening hours for this planned visit are unknown.</p>
                    )}
                    {s.place.osm_uri && (
                      <a href={s.place.osm_uri} target="_blank" rel="noreferrer">
                        View on OpenStreetMap ↗
                      </a>
                    )}
                    <button
                      className="replace"
                      disabled={busy || !lastRequest}
                      onClick={async () => {
                        setBusy(true);
                        try {
                          const res = await fetch(`${api}/api/itineraries/replace-stop`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                              request: lastRequest,
                              itinerary: plan,
                              stop_index: i,
                              category: s.place.category,
                              excluded_place_ids: plan.stops.map((stop) => stop.place.id),
                            }),
                          });
                          const data = await res.json();
                          if (!res.ok) throw new Error(data.detail ?? 'No alternative available');
                          setPlan(data.itinerary);
                          setMessage(
                            `Replaced ${s.place.name} with ${data.itinerary.stops[i].place.name}.`
                          );
                        } catch (error) {
                          setMessage(error instanceof Error ? error.message : 'Replacement failed');
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      Find alternative
                    </button>
                  </div>
                </article>
              ))
            )}
            <ul className="warnings">
              {plan.warnings.map((w: string) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </>
        )}
      </section>
      <section className="saved">
        <p className="eyebrow">YOUR SAVED PLANS</p>
        {savedMessage && <p>{savedMessage}</p>}
        {savedPlans.map((saved) => (
          <article className="savedRow" key={saved.id}>
            <span>
              {saved.plan_date} · {saved.summary.total_duration_minutes ?? '—'} min
            </span>
            <button
              onClick={async () => {
                try {
                  const res = await fetch(
                    `${api}/api/itineraries/saved/${saved.id}?owner_id=${encodeURIComponent(ownerId)}`
                  );
                  const data = await res.json();
                  if (!res.ok) throw new Error(data.detail);
                  setPlan(data.itinerary);
                  setLastRequest(data.request);
                  setMessage(
                    `Saved plan refreshed with current provider data (${data.refreshed_at}).`
                  );
                } catch (error) {
                  setSavedMessage(error instanceof Error ? error.message : 'Load failed');
                }
              }}
            >
              Load & refresh
            </button>
            <button
              onClick={async () => {
                try {
                  const res = await fetch(
                    `${api}/api/itineraries/saved/${saved.id}?owner_id=${encodeURIComponent(ownerId)}`,
                    { method: 'DELETE' }
                  );
                  const data = await res.json();
                  if (!res.ok) throw new Error(data.detail);
                  await refreshSaved(ownerId);
                } catch (error) {
                  setSavedMessage(error instanceof Error ? error.message : 'Delete failed');
                }
              }}
            >
              Delete
            </button>
          </article>
        ))}
      </section>
      <footer>
        <span className="brand">
          feasty<span>map</span>
        </span>
        <span>
          Good days, well spent. &nbsp;·&nbsp; Place data © OpenStreetMap contributors; routing by
          OSRM. Weather by Open-Meteo.
        </span>
      </footer>
    </main>
  );
}
