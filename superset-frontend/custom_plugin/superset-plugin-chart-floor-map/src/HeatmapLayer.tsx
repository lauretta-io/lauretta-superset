/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import React, { useMemo, useRef, useCallback } from 'react';

/* ═══════════════════════════════════════════════════════════════════════════
 *  FLOOR-PLAN CONCENTRATION HEATMAP
 *
 *  Design:
 *   • Walkable space  = convex hull of ALL polygons MINUS retail store interiors.
 *     This correctly models "the corridors between stores" without needing
 *     explicit corridor polygons.
 *
 *   • Heat sources    = retail polygon EDGES, weighted by each store's footfall.
 *     A high-footfall store radiates heat outward into the adjacent corridor.
 *     Explicit Circulation / Entrance / Public polygons also contribute heat
 *     (sampled from their interiors) but do NOT define walkable space — they
 *     are treated as bonus heat boosts on top of the corridor field.
 *
 *   • KDE bandwidth σ = SIGMA SVG units. Sized so heat from a store edge
 *     reaches the middle of the corridor (~half corridor width) but does not
 *     cross into the opposite store.
 *
 *   • Rendering: uniform dot grid on the walkable space. Each dot's color and
 *     opacity is driven by its KDE value. Low-KDE dots are nearly invisible
 *     (the floor-plan image shows through); high-KDE dots are vivid and opaque.
 * ═══════════════════════════════════════════════════════════════════════════ */

/* ─── Types ─────────────────────────────────────────────────────────────── */

export interface HeatmapPoint {
  x: number;
  y: number;
  weight: number;
  name: string;
  category: string;
  polygonArea?: number;
  rawPoints?: string;
}

interface HeatmapLayerProps {
  points: HeatmapPoint[];
  imgW: number;
  imgH: number;
  layerFilters: string[];
  onHoverEnter?: (
    name: string,
    footfall: number,
    svgX: number,
    svgY: number,
    e: React.MouseEvent<SVGCircleElement>,
  ) => void;
  onHoverLeave?: () => void;
}

interface FootfallSource {
  x: number;
  y: number;
  weight: number;      // per-sample KDE weight (arc/area normalised)
  totalWeight: number; // original store/polygon footfall (for tooltip display)
  name: string;
}

/* ─── Polygon parsing ───────────────────────────────────────────────────── */

export function parsePolygonPoints(
  pointsStr: string,
): { x: number; y: number }[] {
  if (!pointsStr || pointsStr === 'null' || pointsStr === '') return [];
  const trimmed = pointsStr.replace(/[\r\n\t]+/g, ' ').trim();
  const tokens = trimmed.split(/[\s,]+/).filter(t => t.length > 0);
  const nums: number[] = [];
  for (const token of tokens) {
    const n = parseFloat(token);
    if (!Number.isNaN(n)) nums.push(n);
  }
  const pts: { x: number; y: number }[] = [];
  for (let i = 0; i + 1 < nums.length; i += 2) {
    pts.push({ x: nums[i], y: nums[i + 1] });
  }
  return pts;
}

export function computeCentroid(
  pointsStr: string,
): { x: number; y: number } | null {
  const pts = parsePolygonPoints(pointsStr);
  if (pts.length === 0) return null;
  const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
  const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length;
  if (Number.isNaN(cx) || Number.isNaN(cy)) return null;
  return { x: cx, y: cy };
}

export function computePolygonArea(pointsStr: string): number {
  const pts = parsePolygonPoints(pointsStr);
  if (pts.length < 3) return 0;
  let area = 0;
  const n = pts.length;
  for (let i = 0; i < n; i += 1) {
    const j = (i + 1) % n;
    area += pts[i].x * pts[j].y - pts[j].x * pts[i].y;
  }
  return Math.abs(area) / 2;
}

export function areaToRadius(area: number): number {
  return area <= 0 ? 0 : Math.sqrt(area / Math.PI);
}

/* ─── Point-in-Polygon (ray-casting) ────────────────────────────────────── */

function pointInPolygon(
  px: number,
  py: number,
  pts: { x: number; y: number }[],
): boolean {
  const n = pts.length;
  let inside = false;
  for (let i = 0, j = n - 1; i < n; j = i, i += 1) {
    const { x: xi, y: yi } = pts[i];
    const { x: xj, y: yj } = pts[j];
    if (
      yi > py !== yj > py &&
      px < ((xj - xi) * (py - yi)) / (yj - yi) + xi
    ) {
      inside = !inside;
    }
  }
  return inside;
}

/* ─── Bounding-box helper ───────────────────────────────────────────────── */

interface BBox {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  pts: { x: number; y: number }[];
}

function makeBBox(pts: { x: number; y: number }[]): BBox {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let i = 0; i < pts.length; i += 1) {
    if (pts[i].x < minX) minX = pts[i].x;
    if (pts[i].x > maxX) maxX = pts[i].x;
    if (pts[i].y < minY) minY = pts[i].y;
    if (pts[i].y > maxY) maxY = pts[i].y;
  }
  return { minX, maxX, minY, maxY, pts };
}

function computeBBoxes(polys: { x: number; y: number }[][]): BBox[] {
  return polys.map(makeBBox);
}

function isInsideAny(px: number, py: number, bboxes: BBox[]): boolean {
  for (let i = 0; i < bboxes.length; i += 1) {
    const bb = bboxes[i];
    if (px < bb.minX || px > bb.maxX || py < bb.minY || py > bb.maxY) continue;
    if (pointInPolygon(px, py, bb.pts)) return true;
  }
  return false;
}

/* ─── Category → Layer ──────────────────────────────────────────────────── */

function getCategoryLayer(category: string | undefined | null): string {
  if (!category) return 'Retail';
  const cat = category.toLowerCase().trim();
  if (
    cat.includes('entrance') || cat.includes('gate') ||
    cat.includes('door') || cat.includes('entry')
  ) return 'Entrances';
  if (
    cat.includes('circulation') || cat.includes('corridor') ||
    cat.includes('walkway') || cat.includes('hallway') ||
    cat.includes('lift') || cat.includes('elevator') ||
    cat.includes('escalator') || cat.includes('stair')
  ) return 'Circulation';
  if (
    cat.includes('public') || cat.includes('common') ||
    cat.includes('amenity') || cat.includes('toilet') ||
    cat.includes('restroom') || cat.includes('prayer') ||
    cat.includes('atm') || cat.includes('info')
  ) return 'Public';
  return 'Retail';
}

/* ─── Convex Hull (Graham scan) ─────────────────────────────────────────── */

function cross(
  O: { x: number; y: number },
  A: { x: number; y: number },
  B: { x: number; y: number },
): number {
  return (A.x - O.x) * (B.y - O.y) - (A.y - O.y) * (B.x - O.x);
}

function convexHull(inputPts: { x: number; y: number }[]): { x: number; y: number }[] {
  if (inputPts.length < 3) return inputPts.slice();
  const pts = inputPts.slice().sort((a, b) => a.x - b.x || a.y - b.y);
  const n = pts.length;
  const hull: { x: number; y: number }[] = [];
  for (let i = 0; i < n; i += 1) {
    while (hull.length >= 2 && cross(hull[hull.length - 2], hull[hull.length - 1], pts[i]) <= 0)
      hull.pop();
    hull.push(pts[i]);
  }
  const lower = hull.length + 1;
  for (let i = n - 2; i >= 0; i -= 1) {
    while (hull.length >= lower && cross(hull[hull.length - 2], hull[hull.length - 1], pts[i]) <= 0)
      hull.pop();
    hull.push(pts[i]);
  }
  hull.pop();
  return hull;
}

/* ─── Sample points uniformly from polygon interior ─────────────────────── *
 *
 * Used for ALL polygon types (Retail + Entrance/Circulation/Public).
 * Equal-split weighting: wPer = totalWeight / nSamples, so the total KDE
 * energy per polygon equals its footfall regardless of polygon size.
 */
function sampleInterior(
  pts: { x: number; y: number }[],
  spacing: number,
): { x: number; y: number }[] {
  const bb = makeBBox(pts);
  const result: { x: number; y: number }[] = [];
  for (let x = bb.minX + spacing * 0.5; x < bb.maxX; x += spacing) {
    for (let y = bb.minY + spacing * 0.5; y < bb.maxY; y += spacing) {
      if (pointInPolygon(x, y, pts)) result.push({ x, y });
    }
  }
  return result;
}

/* ─── Color scale ─────────────────────────────────────────────────────────
 *
 * Classic thermal palette: Deep Blue → Cyan → Green → Yellow → Orange → Red
 */
function heatmapColor(t: number): string {
  const stops = [
    { p: 0.00, r:   0, g:   0, b: 180 },
    { p: 0.25, r:   0, g: 200, b: 255 },
    { p: 0.50, r:   0, g: 220, b:  80 },
    { p: 0.70, r: 255, g: 230, b:   0 },
    { p: 0.85, r: 255, g: 100, b:   0 },
    { p: 1.00, r: 230, g:   0, b:   0 },
  ];
  let lo = stops[0];
  let hi = stops[stops.length - 1];
  for (let i = 0; i < stops.length - 1; i += 1) {
    if (t >= stops[i].p && t <= stops[i + 1].p) {
      lo = stops[i]; hi = stops[i + 1];
      break;
    }
  }
  const range = hi.p - lo.p || 1;
  const f = (t - lo.p) / range;
  return `rgb(${Math.round(lo.r + f * (hi.r - lo.r))},${Math.round(lo.g + f * (hi.g - lo.g))},${Math.round(lo.b + f * (hi.b - lo.b))})`;
}

/* ═══════════════════════════════════════════════════════════════════════════
 *  MAIN COMPONENT
 * ═══════════════════════════════════════════════════════════════════════════ */

export function HeatmapLayer({
  points,
  imgW,
  imgH,
  layerFilters,
  onHoverEnter,
  onHoverLeave,
}: HeatmapLayerProps) {

  /* ─────────────────────────────────────────────────────────────────────
   *  TUNING PARAMETERS
   *
   *  DOT_SPACING      — visual dot grid step (SVG units). 10 = fine grid.
   *  SIGMA            — KDE bandwidth: heat decays to ~14% at distance σ.
   *  INTERIOR_SPACING — sample interval inside ALL polygons (Retail +
   *                     Entrance/Circulation/Public).
   *  PERCENTILE_CLAMP — top (1-p)% cells clamp to max colour.
   *  GAMMA            — power-curve for contrast.  1.2 keeps midtones warm.
   * ───────────────────────────────────────────────────────────────────── */
  const DOT_SPACING       = 10;
  const SIGMA             = 28;
  const INTERIOR_SPACING  = 16;
  const PERCENTILE_CLAMP  = 0.88;
  const GAMMA             = 1.2;

  /* ── STAGE 1 — Classify & build heat sources ─────────────────────────
   *
   * ALL polygons (Retail + Entrance/Circulation/Public) contribute heat
   * from their interiors, sampled on a uniform grid at INTERIOR_SPACING.
   * Each sample carries weight = totalFootfall / nSamples, so the total
   * KDE energy per polygon equals its footfall regardless of size.
   *
   * BUILDING HULL: still built from Retail vertices only, used to define
   * the dot-grid coverage area (corridor + store interiors).
   */
  const { retailBBoxes, nonRetailBBoxes, buildingHull, sources } = useMemo(() => {
    const retailPolys:    { x: number; y: number }[][] = [];
    const retailVertices: { x: number; y: number }[]  = [];  // hull built from these only
    const src: FootfallSource[] = [];

    const nonRetailPolys: { x: number; y: number }[][] = [];

    for (let i = 0; i < points.length; i += 1) {
      const p = points[i];
      if (!p.rawPoints) continue;
      const pts = parsePolygonPoints(p.rawPoints);
      if (pts.length < 3) continue;

      const layer = getCategoryLayer(p.category);

      if (layer === 'Retail') {
        retailPolys.push(pts);
        for (let v = 0; v < pts.length; v += 1) retailVertices.push(pts[v]);
      } else {
        nonRetailPolys.push(pts);
      }

      // ALL polygons (Retail + Entrance/Circulation/Public) contribute heat
      // from their interiors, weighted by footfall.  Interior sampling gives
      // each polygon a heat field proportional to its footfall — high-footfall
      // stores show as warm/hot blobs, low-footfall stores stay cool.
      if (p.weight > 0) {
        const cx = pts.reduce((s, q) => s + q.x, 0) / pts.length;
        const cy = pts.reduce((s, q) => s + q.y, 0) / pts.length;
        const interiorPts = sampleInterior(pts, INTERIOR_SPACING);
        const sampledPts = interiorPts.length > 0 ? interiorPts : [{ x: cx, y: cy }];
        const wPer = p.weight / sampledPts.length;
        for (let j = 0; j < sampledPts.length; j += 1)
          src.push({ x: sampledPts[j].x, y: sampledPts[j].y, weight: wPer, totalWeight: p.weight, name: p.name });
      }
    }

    // Build hull from retail vertices only → tight around the building footprint
    const hull = retailVertices.length >= 3 ? convexHull(retailVertices) : [];

    return { retailBBoxes: computeBBoxes(retailPolys), nonRetailBBoxes: computeBBoxes(nonRetailPolys), buildingHull: hull, sources: src };
  }, [points]);

  /* ── STAGE 2 — Walkable dot grid ─────────────────────────────────────
   *
   * A cell is walkable iff:
   *   (a) inside the building convex hull (retail-derived), OR
   *       inside any non-retail polygon (Entrance/Circulation/Public),
   *   AND NOT inside any retail store polygon.
   *
   * Rule (a) uses OR: this means Entrance/Circulation polygons that sit
   * slightly outside the retail hull (e.g. entrance vestibules, gate areas)
   * still get dot-grid coverage and are correctly painted.
   * External empty space beyond any known polygon is never painted.
   */
  const MAX_DOT_CELLS = 80_000;

  const { dotGrid, cellSize } = useMemo(() => {
    if (buildingHull.length < 3 && nonRetailBBoxes.length === 0) return { dotGrid: [], cellSize: DOT_SPACING };

    // Bounding box that covers both the hull and all non-retail polygons
    const hullBB = buildingHull.length >= 3 ? makeBBox(buildingHull) : null;
    let gMinX = hullBB?.minX ?? Infinity;
    let gMinY = hullBB?.minY ?? Infinity;
    let gMaxX = hullBB?.maxX ?? -Infinity;
    let gMaxY = hullBB?.maxY ?? -Infinity;
    for (const bb of nonRetailBBoxes) {
      if (bb.minX < gMinX) gMinX = bb.minX;
      if (bb.minY < gMinY) gMinY = bb.minY;
      if (bb.maxX > gMaxX) gMaxX = bb.maxX;
      if (bb.maxY > gMaxY) gMaxY = bb.maxY;
    }

    let cs = DOT_SPACING;
    const estW = gMaxX - gMinX;
    const estH = gMaxY - gMinY;
    if ((estW / cs) * (estH / cs) > 5_000_000) {
      cs = Math.ceil(Math.sqrt((estW * estH) / 1_000_000));
      if (cs < DOT_SPACING) cs = DOT_SPACING;
    }

    const cols = Math.ceil(estW / cs);
    const rows = Math.ceil(estH / cs);
    const grid: { col: number; row: number; px: number; py: number }[] = [];

    for (let col = 0; col < cols; col += 1) {
      const px = gMinX + (col + 0.5) * cs;
      for (let row = 0; row < rows; row += 1) {
        const py = gMinY + (row + 0.5) * cs;

        // Accept if inside the retail hull OR inside a non-retail polygon
        const inHull = buildingHull.length >= 3 && pointInPolygon(px, py, buildingHull);
        const inNonRetail = isInsideAny(px, py, nonRetailBBoxes);
        if (!inHull && !inNonRetail) continue;

        grid.push({ col, row, px, py });
      }
    }

    if (grid.length > MAX_DOT_CELLS) {
      const step = Math.ceil(grid.length / MAX_DOT_CELLS);
      return { dotGrid: grid.filter((_, idx) => idx % step === 0), cellSize: cs };
    }
    return { dotGrid: grid, cellSize: cs };
  }, [buildingHull, retailBBoxes, nonRetailBBoxes]);

  /* ── STAGE 3 — KDE ────────────────────────────────────────────────────
   *
   * KDE(p) = Σ_i  w_i · exp( −‖p − s_i‖² / 2σ² )
   */
  const { kdeGrid, maxKDE } = useMemo(() => {
    if (sources.length === 0 || dotGrid.length === 0)
      return { kdeGrid: [] as any[], maxKDE: 1 };

    const twoSigmaSq = 2 * SIGMA * SIGMA;
    const cutoff = 3 * SIGMA;
    const cutoffSq = cutoff * cutoff;

    const srcBB = sources.map(s => ({
      s,
      minX: s.x - cutoff, maxX: s.x + cutoff,
      minY: s.y - cutoff, maxY: s.y + cutoff,
    }));

    let globalMax = 0;
    const result: {
      col: number; row: number; px: number; py: number;
      kde: number; nearestName: string; nearestFootfall: number;
    }[] = [];

    for (let i = 0; i < dotGrid.length; i += 1) {
      const { col, row, px, py } = dotGrid[i];
      let kdeVal = 0;
      let dominantName = '', dominantFootfall = 0, dominantContrib = -1;

      for (let j = 0; j < srcBB.length; j += 1) {
        const { s, minX, maxX, minY, maxY } = srcBB[j];
        if (px < minX || px > maxX || py < minY || py > maxY) continue;
        const dx = px - s.x, dy = py - s.y;
        const dSq = dx * dx + dy * dy;
        if (dSq > cutoffSq) continue;
        const contrib = s.weight * Math.exp(-dSq / twoSigmaSq);
        kdeVal += contrib;
        // Track the source whose weighted contribution is highest
        // (= the store/entrance that most "owns" this dot's colour)
        if (contrib > dominantContrib) {
          dominantContrib = contrib;
          dominantName = s.name;
          dominantFootfall = s.totalWeight;
        }
      }

      if (kdeVal > 0) {
        if (kdeVal > globalMax) globalMax = kdeVal;
        result.push({ col, row, px, py, kde: kdeVal, nearestName: dominantName, nearestFootfall: dominantFootfall });
      }
    }

    return { kdeGrid: result, maxKDE: globalMax || 1 };
  }, [dotGrid, sources]);

  /* ── STAGE 4 — Normalise ──────────────────────────────────────────────
   *
   * 1. Log-compress  logNorm = ln(1+KDE) / ln(1+maxKDE)
   * 2. Percentile clamp at PERCENTILE_CLAMP
   * 3. Power curve with GAMMA
   */
  const sortedCells = useMemo(() => {
    if (kdeGrid.length === 0) return [];
    const logMax = Math.log1p(maxKDE);

    const withLog = kdeGrid.map(c => ({
      ...c,
      logNorm: logMax > 0 ? Math.log1p(c.kde) / logMax : 0,
    }));

    const vals = withLog.map(c => c.logNorm).sort((a, b) => a - b);
    const clampIdx = Math.floor(vals.length * PERCENTILE_CLAMP);
    const clampVal = Math.max(vals[clampIdx] ?? 1, 0.01);

    return withLog
      .map(c => {
        const norm = Math.pow(Math.min(c.logNorm / clampVal, 1.0), GAMMA);
        return { ...c, norm };
      })
      .sort((a, b) => a.norm - b.norm);
  }, [kdeGrid, maxKDE]);

  /* ── STAGE 5 — Render ─────────────────────────────────────────────────
   *
   * Goal: dots connect visually in high-traffic corridors while the
   * background floor plan remains legible everywhere.
   *
   * Radius:
   *   minR = 0.32×cs — cold dots are visible but don't overlap neighbours
   *   maxR = 0.58×cs — hot dots slightly overlap → form a continuous band
   *   At DOT_SPACING=10: minR=3.2px, maxR=5.8px (gap closes at ~norm 0.75)
   *
   * Opacity: background map lines (store outlines, labels) are always
   * visible through the heatmap at every heat level.
   *   norm=0.00 → 0.04 (barely visible — background fully clear)
   *   norm=0.25 → 0.12 (light tint — store outlines easily readable)
   *   norm=0.50 → 0.24 (medium tint — floor plan lines still crisp)
   *   norm=0.75 → 0.35 (warm colour — labels still legible beneath)
   *   norm=1.00 → 0.44 (peak — vivid colour, background lines visible)
   *
   * Formula: 0.04 + norm^0.75 × 0.40
   *   Lower base (0.04) keeps cold dots nearly invisible.
   *   Higher exponent (0.75) slows the rise so midrange stays translucent.
   *   Max opacity capped at 0.44 — background lines always show through.
   *
   * Painting order: cold first, hot on top — so hot dots aren't occluded.
   */
  const maxR = cellSize * 0.58;
  const minR = cellSize * 0.32;

  const leaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleEnter = useCallback(
    (name: string, footfall: number, cx: number, cy: number, e: React.MouseEvent<SVGCircleElement>) => {
      if (leaveTimer.current) { clearTimeout(leaveTimer.current); leaveTimer.current = null; }
      onHoverEnter?.(name, footfall, cx, cy, e);
    },
    [onHoverEnter],
  );

  const handleLeave = useCallback(() => {
    leaveTimer.current = setTimeout(() => { onHoverLeave?.(); leaveTimer.current = null; }, 80);
  }, [onHoverLeave]);

  return (
    <g className="heatmap-layer">
      {sortedCells.map(cell => {
        const color = heatmapColor(cell.norm);
        const radius = minR + (maxR - minR) * cell.norm;
        // Faint hint at cold end, bold at hot end, never fully opaque
        const fillOpacity = 0.08 + Math.pow(cell.norm, 0.65) * 0.55;

        return (
          <circle
            key={`h-${cell.col}-${cell.row}`}
            cx={cell.px}
            cy={cell.py}
            r={radius}
            fill={color}
            fillOpacity={fillOpacity}
            stroke="none"
            strokeWidth={0}
            style={{
              pointerEvents: onHoverEnter ? 'auto' : 'none',
              cursor: onHoverEnter ? 'pointer' : 'default',
            }}
            onMouseEnter={
              onHoverEnter
                ? e => handleEnter(cell.nearestName, cell.nearestFootfall, cell.px, cell.py, e)
                : undefined
            }
            onMouseLeave={onHoverLeave ? handleLeave : undefined}
          />
        );
      })}
    </g>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
 *  HEATMAP LEGEND
 * ═══════════════════════════════════════════════════════════════════════════ */

export function HeatmapLegend({
  minVal,
  maxVal,
}: {
  minVal: number;
  maxVal: number;
}) {
  const gradientId = 'heatmap-legend-gradient';
  const stops = [
    { offset: '0%',   color: 'rgb(0,0,180)'   },
    { offset: '25%',  color: 'rgb(0,200,255)'  },
    { offset: '50%',  color: 'rgb(0,220,80)'   },
    { offset: '70%',  color: 'rgb(255,230,0)'  },
    { offset: '85%',  color: 'rgb(255,100,0)'  },
    { offset: '100%', color: 'rgb(230,0,0)'    },
  ];

  return (
    <div
      style={{
        position: 'absolute',
        top: 14,
        right: 14,
        background: 'rgba(255,255,255,0.97)',
        borderRadius: 10,
        padding: '12px 16px 10px',
        boxShadow: '0 2px 12px rgba(0,0,0,0.15)',
        zIndex: 500,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'stretch',
        gap: 6,
        minWidth: 170,
        border: '1px solid #e0e0e0',
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 700, color: '#222', textAlign: 'center', letterSpacing: 0.3 }}>
        Foot Traffic Concentration
      </div>

      <svg width={150} height={16}>
        <defs>
          <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="0%">
            {stops.map(s => (
              <stop key={s.offset} offset={s.offset} stopColor={s.color} />
            ))}
          </linearGradient>
        </defs>
        <rect x={0} y={0} width={150} height={16} fill={`url(#${gradientId})`} rx={4} />
      </svg>

      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#555', fontWeight: 500 }}>
        <span>Low ({minVal.toLocaleString()})</span>
        <span>High ({maxVal.toLocaleString()})</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, paddingTop: 5, borderTop: '1px solid #eee' }}>
        <svg width={108} height={22}>
          <circle cx={8}   cy={11} r={3.5} fill="rgb(0,0,180)"   opacity={0.15} />
          <circle cx={26}  cy={11} r={4}   fill="rgb(0,200,255)" opacity={0.35} />
          <circle cx={46}  cy={11} r={4.5} fill="rgb(0,220,80)"  opacity={0.52} />
          <circle cx={66}  cy={11} r={5}   fill="rgb(255,230,0)" opacity={0.68} />
          <circle cx={85}  cy={11} r={5.5} fill="rgb(255,100,0)" opacity={0.80} />
          <circle cx={103} cy={11} r={6}   fill="rgb(230,0,0)"   opacity={0.90} />
        </svg>
        <span style={{ fontSize: 9, color: '#777', fontWeight: 500 }}>density →</span>
      </div>
    </div>
  );
}
