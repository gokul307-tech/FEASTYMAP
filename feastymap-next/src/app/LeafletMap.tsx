'use client';

import { LatLngBounds } from 'leaflet';
import { useEffect } from 'react';
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, useMap } from 'react-leaflet';
import type { MapPlace } from './MapCanvas';

const pondicherry = { lat: 11.916064, lng: 79.812325 };
const tileUrl =
  process.env.NEXT_PUBLIC_MAP_TILE_URL ?? 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const attribution =
  process.env.NEXT_PUBLIC_MAP_ATTRIBUTION ??
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>';

function FitPlaces({ places }: { places: MapPlace[] }) {
  const map = useMap();
  useEffect(() => {
    if (places.length > 1) {
      const bounds = new LatLngBounds(
        places.map((place) => [place.location.lat, place.location.lng])
      );
      map.fitBounds(bounds, { padding: [24, 24], maxZoom: 14 });
    } else if (places.length === 1) {
      map.setView([places[0].location.lat, places[0].location.lng], 14);
    } else {
      map.setView([pondicherry.lat, pondicherry.lng], 12);
    }
  }, [map, places]);
  return null;
}

export default function LeafletMap({
  places,
  routeGeometries,
}: {
  places: MapPlace[];
  routeGeometries: ({ coordinates?: number[][] } | null)[];
}) {
  return (
    <div className="mapWrap">
      <MapContainer
        center={[pondicherry.lat, pondicherry.lng]}
        zoom={12}
        className="mapCanvas"
        scrollWheelZoom
      >
        <TileLayer url={tileUrl} attribution={attribution} />
        <FitPlaces places={places} />
        {routeGeometries.map((geometry, index) => {
          const latLngs = (geometry?.coordinates ?? []).map(
            ([lng, lat]) => [lat, lng] as [number, number]
          );
          return latLngs.length > 1 ? (
            <Polyline
              key={`route-${index}`}
              positions={latLngs}
              pathOptions={{ color: '#31594d', weight: 4, opacity: 0.8 }}
            />
          ) : null;
        })}
        {places.map((place) => (
          <CircleMarker
            key={place.id}
            center={[place.location.lat, place.location.lng]}
            radius={8}
            pathOptions={{ color: '#31594d', fillColor: '#d79a55', fillOpacity: 0.95 }}
          >
            <Popup>{place.name}</Popup>
          </CircleMarker>
        ))}
      </MapContainer>
      <a
        className="mapIssue"
        href="https://www.openstreetmap.org/fixthemap"
        target="_blank"
        rel="noreferrer"
      >
        Report a map issue
      </a>
    </div>
  );
}
