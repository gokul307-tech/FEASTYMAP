// BACKEND INTEGRATION POINT: Replace this entire module with a POST /api/itinerary call
import type {
  PlannerFormData,
  GeneratedItinerary,
  ItineraryStop,
  CostBreakdown,
} from './plannerTypes';
import { PLACES } from '@/lib/mockData';

const ROUTE_CENTER = { x: 50, y: 50 };
const MAX_STOPS = 5;

const TRANSPORT_RULES: Record<
  PlannerFormData['transportMethod'],
  { speedKph: number; costPerKm: number; minimumFare: number }
> = {
  walk: { speedKph: 4.5, costPerKm: 0, minimumFare: 0 },
  auto: { speedKph: 18, costPerKm: 18, minimumFare: 40 },
  cab: { speedKph: 24, costPerKm: 24, minimumFare: 80 },
  bike: { speedKph: 22, costPerKm: 10, minimumFare: 30 },
  own_vehicle: { speedKph: 24, costPerKm: 12, minimumFare: 20 },
};

function distanceKm(from: { x: number; y: number }, to: { x: number; y: number }): number {
  return Math.max(0.5, Math.hypot(to.x - from.x, to.y - from.y) * 0.25);
}

function travelForLeg(
  method: PlannerFormData['transportMethod'],
  from: { x: number; y: number },
  to: { x: number; y: number }
) {
  const rule = TRANSPORT_RULES[method];
  const distance = distanceKm(from, to);
  return {
    minutes: Math.max(5, Math.ceil((distance / rule.speedKph) * 60)),
    cost: method === 'walk' ? 0 : Math.round(Math.max(rule.minimumFare, distance * rule.costPerKm)),
  };
}

function matchesDietaryRestriction(place: (typeof PLACES)[number], restriction: string): boolean {
  const normalized = restriction.toLowerCase().trim();
  if (!normalized || normalized === 'none' || normalized === 'any') return true;

  const searchable = `${place.name} ${place.cuisine ?? ''} ${place.tags.join(' ')}`.toLowerCase();
  if (normalized.includes('vegan')) {
    return !/(chicken|meat|fish|egg|milk|cheese|dairy|butter|cream)/.test(searchable);
  }
  if (normalized.includes('vegetarian') || normalized.includes('veg')) {
    return !/(chicken|meat|mutton|fish|seafood|egg)/.test(searchable);
  }
  if (normalized.includes('halal')) return !/(bar|cocktail|pork)/.test(searchable);
  if (normalized.includes('jain')) return !/(meat|fish|egg|onion|garlic)/.test(searchable);
  return true;
}

function preferenceScore(place: (typeof PLACES)[number], data: PlannerFormData): number {
  const members =
    data.groupMode && data.groupMembers.length > 0
      ? data.groupMembers
      : [
          {
            cuisine: data.cuisine,
            flavorPreference: data.flavorPreference,
            dietaryRestriction: 'None',
          },
        ];
  const searchable = `${place.name} ${place.cuisine ?? ''} ${place.tags.join(' ')}`.toLowerCase();
  let score = place.rating * 10 + place.trendingScore / 10;

  for (const member of members) {
    if (member.cuisine === 'Any' || searchable.includes(member.cuisine.toLowerCase())) score += 18;
    if (member.flavorPreference === 'any' || searchable.includes(member.flavorPreference))
      score += 10;
    if (matchesDietaryRestriction(place, member.dietaryRestriction)) score += 8;
    else score -= 100;
  }

  if (
    place.category === 'restaurant' ||
    place.category === 'cafe' ||
    place.category === 'street_food'
  )
    score += 8;
  return score;
}

function getCostCategory(place: (typeof PLACES)[number]): ItineraryStop['costCategory'] {
  if (['cafe', 'restaurant', 'dessert', 'street_food'].includes(place.category)) return 'food';
  if (place.category === 'mall') return 'shopping';
  if (place.category === 'bar' || place.category === 'event') return 'entertainment';
  return 'food';
}

function getStopDuration(place: (typeof PLACES)[number]): number {
  if (place.category === 'mall') return 90;
  if (place.category === 'bar' || place.category === 'event') return 75;
  if (place.category === 'park') return 45;
  return 45;
}

function formatTime(totalMinutes: number): string {
  const minutes = ((totalMinutes % 1440) + 1440) % 1440;
  const hour = Math.floor(minutes / 60);
  const minute = minutes % 60;
  return `${hour % 12 === 0 ? 12 : hour % 12}:${minute.toString().padStart(2, '0')} ${hour < 12 ? 'AM' : 'PM'}`;
}

export function generateItinerary(data: PlannerFormData): GeneratedItinerary | null {
  const durationLimit = data.duration * 60;
  const candidates = PLACES.filter((place) => place.isOpenNow)
    .filter((place) =>
      data.groupMembers.every((member) =>
        matchesDietaryRestriction(place, member.dietaryRestriction)
      )
    )
    .sort((a, b) => preferenceScore(b, data) - preferenceScore(a, data));
  const selectedPlaces: typeof PLACES = [];
  let previous = ROUTE_CENTER;
  let elapsed = 0;
  let transportCostTotal = 0;

  for (const place of candidates) {
    if (selectedPlaces.length >= MAX_STOPS) break;
    if (
      selectedPlaces.some(
        (selected) => selected.category === place.category && place.category !== 'restaurant'
      )
    )
      continue;

    const leg = travelForLeg(data.transportMethod, previous, place.coordinates);
    const stopDuration = getStopDuration(place);
    const estimatedCost = Math.round((place.avgCostForTwo * data.groupSize) / 2);
    const projectedCost = transportCostTotal + leg.cost + estimatedCost;
    if (elapsed + leg.minutes + stopDuration > durationLimit || projectedCost > data.budget)
      continue;

    selectedPlaces.push(place);
    elapsed += leg.minutes + stopDuration;
    transportCostTotal += leg.cost;
    previous = place.coordinates;
  }

  if (selectedPlaces.length === 0) return null;

  const stops: ItineraryStop[] = [];
  previous = ROUTE_CENTER;
  for (const [idx, place] of selectedPlaces.entries()) {
    const leg = travelForLeg(data.transportMethod, previous, place.coordinates);
    const costCategory = getCostCategory(place);
    stops.push({
      id: `stop-${place.id}-${idx}`,
      placeId: place.id,
      placeName: place.name,
      category: place.category,
      area: place.area,
      estimatedCost: Math.round((place.avgCostForTwo * data.groupSize) / 2),
      costCategory,
      duration: getStopDuration(place),
      travelTimeFromPrev: leg.minutes,
      travelCost: leg.cost,
      openHours: place.openHours,
      isOpenNow: place.isOpenNow,
      hasOffer: place.hasOffer,
      offerLabel: place.offerLabel,
      rating: place.rating,
      image: place.image,
      imageAlt: place.imageAlt,
      notes: `Selected for your ${data.cuisine === 'Any' ? 'group' : data.cuisine} preferences. Estimated travel and spend are included.`,
      reservationAvailable: ['restaurant', 'bar', 'cafe'].includes(place.category),
    });
    previous = place.coordinates;
  }

  const foodTotal = stops
    .filter((s) => s.costCategory === 'food')
    .reduce((a, s) => a + s.estimatedCost, 0);
  const shoppingTotal = stops
    .filter((s) => s.costCategory === 'shopping')
    .reduce((a, s) => a + s.estimatedCost, 0);
  const entertainmentTotal = stops
    .filter((s) => s.costCategory === 'entertainment')
    .reduce((a, s) => a + s.estimatedCost, 0);
  const costBreakdown: CostBreakdown = {
    transport: transportCostTotal,
    food: foodTotal,
    entertainment: entertainmentTotal,
    shopping: shoppingTotal,
    total: transportCostTotal + foodTotal + entertainmentTotal + shoppingTotal,
  };

  const totalDurationMins = stops.reduce(
    (total, stop) => total + stop.duration + stop.travelTimeFromPrev,
    0
  );
  const surplus = data.budget - costBreakdown.total;

  // Calculate start/end times based on latestReturnTime
  const [retH, retM] = data.latestReturnTime.split(':').map(Number);
  const returnMinutes = retH * 60 + retM;
  const startMinutes = returnMinutes - totalDurationMins;

  return {
    id: `itin-${Date.now()}`,
    title: `${data.duration}h Outing — ${stops
      .map((stop) => stop.area)
      .filter((area, index, all) => all.indexOf(area) === index)
      .join(' → ')}`,
    stops,
    costBreakdown,
    budget: data.budget,
    totalDuration: totalDurationMins,
    startTime: formatTime(startMinutes),
    endTime: formatTime(returnMinutes),
    surplus,
    coveragePercent: Math.round((costBreakdown.total / data.budget) * 100),
  };
}
