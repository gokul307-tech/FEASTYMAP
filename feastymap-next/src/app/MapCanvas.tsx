'use client';

import dynamic from 'next/dynamic';

export type MapPlace = {
  id: string;
  name: string;
  location: { lat: number; lng: number };
};

const LeafletMap = dynamic(() => import('./LeafletMap'), {
  ssr: false,
  loading: () => <div className="mapCanvas mapMessage">Loading map…</div>,
});

export default function MapCanvas({
  places,
  routeGeometries = [],
}: {
  places: MapPlace[];
  routeGeometries?: ({ coordinates?: number[][] } | null)[];
}) {
  return <LeafletMap places={places} routeGeometries={routeGeometries} />;
}
