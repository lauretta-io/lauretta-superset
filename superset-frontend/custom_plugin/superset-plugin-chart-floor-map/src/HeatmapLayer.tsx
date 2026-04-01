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
import React, { useMemo } from 'react';

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
  weight: number; // per-sample KDE weight (arc/area normalised)
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
    if (yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) {
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
  let minX = Infinity,
    maxX = -Infinity,
    minY = Infinity,
    maxY = -Infinity;
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
    cat.includes('entrance') ||
    cat.includes('gate') ||
    cat.includes('door') ||
    cat.includes('entry')
  )
    return 'Entrances';
  if (
    cat.includes('circulation') ||
    cat.includes('corridor') ||
    cat.includes('walkway') ||
    cat.includes('hallway') ||
    cat.includes('lift') ||
    cat.includes('elevator') ||
    cat.includes('escalator') ||
    cat.includes('stair')
  )
    return 'Circulation';
  if (
    cat.includes('public') ||
    cat.includes('common') ||
    cat.includes('amenity') ||
    cat.includes('toilet') ||
    cat.includes('restroom') ||
    cat.includes('prayer') ||
    cat.includes('atm') ||
    cat.includes('info')
  )
    return 'Public';
  return 'Retail';
}

/* ─── Hull helpers (convex + refined detailed hull) ─────────────────────── */

function cross(
  O: { x: number; y: number },
  A: { x: number; y: number },
  B: { x: number; y: number },
): number {
  return (A.x - O.x) * (B.y - O.y) - (A.y - O.y) * (B.x - O.x);
}

function convexHull(
  inputPts: { x: number; y: number }[],
): { x: number; y: number }[] {
  if (inputPts.length < 3) return inputPts.slice();
  const pts = inputPts.slice().sort((a, b) => a.x - b.x || a.y - b.y);
  const n = pts.length;
  const hull: { x: number; y: number }[] = [];
  for (let i = 0; i < n; i += 1) {
    while (
      hull.length >= 2 &&
      cross(hull[hull.length - 2], hull[hull.length - 1], pts[i]) <= 0
    )
      hull.pop();
    hull.push(pts[i]);
  }
  const lower = hull.length + 1;
  for (let i = n - 2; i >= 0; i -= 1) {
    while (
      hull.length >= lower &&
      cross(hull[hull.length - 2], hull[hull.length - 1], pts[i]) <= 0
    )
      hull.pop();
    hull.push(pts[i]);
  }
  hull.pop();
  return hull;
}

function distance(a: { x: number; y: number }, b: { x: number; y: number }) {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return Math.sqrt(dx * dx + dy * dy);
}

function pointToSegmentDistance(
  p: { x: number; y: number },
  a: { x: number; y: number },
  b: { x: number; y: number },
): { dist: number; t: number } {
  const abx = b.x - a.x;
  const aby = b.y - a.y;
  const apx = p.x - a.x;
  const apy = p.y - a.y;
  const abSq = abx * abx + aby * aby || 1;
  const tRaw = (apx * abx + apy * aby) / abSq;
  const t = Math.max(0, Math.min(1, tRaw));
  const qx = a.x + t * abx;
  const qy = a.y + t * aby;
  const dx = p.x - qx;
  const dy = p.y - qy;
  return { dist: Math.sqrt(dx * dx + dy * dy), t };
}

function buildDetailedHull(
  inputPts: { x: number; y: number }[],
): { x: number; y: number }[] {
  if (inputPts.length < 4) return convexHull(inputPts);

  const unique = Array.from(
    new Map(inputPts.map(p => [`${p.x}:${p.y}`, p])).values(),
  );
  if (unique.length < 4) return convexHull(unique);

  const baseHull = convexHull(unique);
  if (baseHull.length < 4) return baseHull;

  const hullKeys = new Set(baseHull.map(p => `${p.x}:${p.y}`));
  const pool = unique.filter(p => !hullKeys.has(`${p.x}:${p.y}`));
  if (pool.length === 0) return baseHull;

  // Refine long edges by inserting nearby interior vertices.
  // This avoids connecting far extremities with a single straight segment.
  const refined = baseHull.slice();
  const edgeLens = refined.map((p, i) =>
    distance(p, refined[(i + 1) % refined.length]),
  );
  const avgEdge =
    edgeLens.reduce((s, d) => s + d, 0) / Math.max(edgeLens.length, 1);
  // Detail tuning: lower max edge length and allow more insertions so the
  // hull follows the store boundary more closely instead of long jumps.
  const maxEdgeLen = avgEdge * 1.35;
  const maxInsertions = Math.min(pool.length, 12_000);

  let inserted = 0;
  let changed = true;

  while (changed && inserted < maxInsertions) {
    changed = false;

    for (let i = 0; i < refined.length && inserted < maxInsertions; i += 1) {
      const a = refined[i];
      const b = refined[(i + 1) % refined.length];
      const edgeLen = distance(a, b);
      if (edgeLen <= maxEdgeLen) continue;

      let bestIdx = -1;
      let bestScore = Infinity;

      for (let j = 0; j < pool.length; j += 1) {
        const p = pool[j];
        const { dist, t } = pointToSegmentDistance(p, a, b);
        if (t <= 0.07 || t >= 0.93) continue;
        if (dist > edgeLen * 0.6) continue;

        // Prefer candidates close to this edge and not too close to vertices.
        const score = dist + 0.1 * Math.abs(0.5 - t) * edgeLen;
        if (score < bestScore) {
          bestScore = score;
          bestIdx = j;
        }
      }

      if (bestIdx >= 0) {
        refined.splice(i + 1, 0, pool[bestIdx]);
        pool.splice(bestIdx, 1);
        inserted += 1;
        changed = true;
      }
    }
  }

  return refined.length >= 3 ? refined : baseHull;
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
 * Professional high-contrast perceptually-uniform palette:
 * Grey → Bright Cyan → Bright Lime → Bright Yellow → Orange → Deep Red
 */
function heatmapColor(t: number): string {
  const stops = [
    { p: 0.0, r: 50, g: 120, b: 255 }, // Light blue (Cool / Low)
    { p: 0.25, r: 0, g: 210, b: 255 }, // Cyan
    { p: 0.5, r: 0, g: 220, b: 60 }, // Green (Medium)
    { p: 0.75, r: 255, g: 210, b: 0 }, // Yellow
    { p: 0.9, r: 255, g: 100, b: 0 }, // Orange (High)
    { p: 1.0, r: 255, g: 0, b: 0 }, // Bright red (Very High)
  ];
  let lo = stops[0];
  let hi = stops[stops.length - 1];
  for (let i = 0; i < stops.length - 1; i += 1) {
    if (t >= stops[i].p && t <= stops[i + 1].p) {
      lo = stops[i];
      hi = stops[i + 1];
      break;
    }
  }
  const range = hi.p - lo.p || 1;
  const f = (t - lo.p) / range;
  return `rgb(${Math.round(lo.r + f * (hi.r - lo.r))},${Math.round(lo.g + f * (hi.g - lo.g))},${Math.round(lo.b + f * (hi.b - lo.b))})`;
}

// Shared color-normalisation constants used by both heatmap rendering and legend bins.
const HEATMAP_DYNAMIC_COLOR_GAMMA = 0.45;
const HEATMAP_SPATIAL_DECAY_GAMMA = 0.35;

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
   *  DOT_SPACING      — visual dot grid step (SVG units). Adaptive based on
   *                     map dimensions. Larger maps still increase spacing,
   *                     but with a cap so density does not become too sparse.
   *                     Target ~24000 dots with safety cap.
   *  SIGMA            — KDE bandwidth: heat decays to ~14% at distance σ.
   *  INTERIOR_SPACING — sample interval inside ALL polygons (Retail +
   *                     Entrance/Circulation/Public).
   *  PERCENTILE_CLAMP — top (1-p)% cells clamp to max colour.
   *  GAMMA            — power-curve for contrast.  1.2 keeps midtones warm.
   * ───────────────────────────────────────────────────────────────────── */

  // Adaptive DOT_SPACING:
  // - More target cells than before, so large maps keep better continuity.
  // - Capped max spacing to avoid sparse/separated blobs on huge images.
  const minDotSpacing = 7;
  const maxDotSpacing = 26;
  const targetDotCount = 24000;
  const adaptiveDotSpacing = Math.min(
    maxDotSpacing,
    Math.max(minDotSpacing, Math.sqrt((imgW * imgH) / targetDotCount)),
  );

  // SIGMA scales with map size so heat from each source spreads further on
  // large maps, preventing isolated cold gaps between stores.
  // Small map (~700px diag) → SIGMA ≈ 28; large map (~6800px diag) → SIGMA capped at 70
  const mapDiag = Math.sqrt(imgW * imgW + imgH * imgH);
  const SIGMA = Math.min(60, Math.max(30, mapDiag * 0.015));
  const INTERIOR_SPACING = 16;
  const PERCENTILE_CLAMP = 0.98;
  const GAMMA = 1.2;

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
  const { retailBBoxes, nonRetailBBoxes, buildingHull, sources } =
    useMemo(() => {
      const retailPolys: { x: number; y: number }[][] = [];
      const retailVertices: { x: number; y: number }[] = []; // hull built from these only
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
          const sampledPts =
            interiorPts.length > 0 ? interiorPts : [{ x: cx, y: cy }];
          const wPer = p.weight / sampledPts.length;
          for (let j = 0; j < sampledPts.length; j += 1)
            src.push({
              x: sampledPts[j].x,
              y: sampledPts[j].y,
              weight: wPer,
              totalWeight: p.weight,
              name: p.name,
            });
        }
      }

      // Build detailed hull from retail vertices to avoid long straight jumps
      // between far edges; fallback behavior remains stable for sparse inputs.
      const hull =
        retailVertices.length >= 3 ? buildDetailedHull(retailVertices) : [];

      return {
        retailBBoxes: computeBBoxes(retailPolys),
        nonRetailBBoxes: computeBBoxes(nonRetailPolys),
        buildingHull: hull,
        sources: src,
      };
    }, [points, INTERIOR_SPACING]);

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
    if (buildingHull.length < 3 && nonRetailBBoxes.length === 0)
      return { dotGrid: [], cellSize: adaptiveDotSpacing };

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

    // Use adaptive spacing right from the start, then adjust upward if still too many dots
    let cs = adaptiveDotSpacing;
    const estW = gMaxX - gMinX;
    const estH = gMaxY - gMinY;

    // If even with adaptive spacing we'd get too many cells, increase spacing further
    const estCellCount = (estW / cs) * (estH / cs);
    if (estCellCount > MAX_DOT_CELLS * 1.2) {
      cs = Math.ceil(Math.sqrt((estW * estH) / (MAX_DOT_CELLS * 0.9)));
    }

    const cols = Math.ceil(estW / cs);
    const rows = Math.ceil(estH / cs);
    const grid: { col: number; row: number; px: number; py: number }[] = [];

    for (let col = 0; col < cols; col += 1) {
      const px = gMinX + (col + 0.5) * cs;
      for (let row = 0; row < rows; row += 1) {
        const py = gMinY + (row + 0.5) * cs;

        // Accept if inside the retail hull OR inside a non-retail polygon
        const inHull =
          buildingHull.length >= 3 && pointInPolygon(px, py, buildingHull);
        const inNonRetail = isInsideAny(px, py, nonRetailBBoxes);
        if (!inHull && !inNonRetail) continue;

        grid.push({ col, row, px, py });
      }
    }

    if (grid.length > MAX_DOT_CELLS) {
      const step = Math.ceil(grid.length / MAX_DOT_CELLS);
      return {
        dotGrid: grid.filter((_, idx) => idx % step === 0),
        cellSize: cs,
      };
    }
    return { dotGrid: grid, cellSize: cs };
  }, [buildingHull, retailBBoxes, nonRetailBBoxes, adaptiveDotSpacing]);

  /* ── STAGE 3 — KDE with spatial grid index ────────────────────────────
   *
   * KDE(p) = Σ_i  w_i · exp( −‖p − s_i‖² / 2σ² )
   *
   * Performance optimisation: instead of iterating every source for every
   * dot (O(dots × sources)), we build a coarse spatial hash-grid keyed by
   * (bucketCol, bucketRow) with bucket size = cutoff.  For each dot we only
   * look up the 9 surrounding buckets — typically 10-50× fewer comparisons.
   */
  const { kdeGrid, maxKDE } = useMemo(() => {
    if (sources.length === 0 || dotGrid.length === 0)
      return { kdeGrid: [] as any[], maxKDE: 1 };

    const twoSigmaSq = 2 * SIGMA * SIGMA;
    const cutoff = 4.5 * SIGMA;
    const cutoffSq = cutoff * cutoff;

    // ── Build spatial hash-grid for sources ──────────────────────────────
    // Bucket size = cutoff so only the 3×3 neighbourhood needs checking.
    const bucketSize = cutoff;
    const srcGrid = new Map<string, typeof sources>();

    for (let j = 0; j < sources.length; j += 1) {
      const s = sources[j];
      const bCol = Math.floor(s.x / bucketSize);
      const bRow = Math.floor(s.y / bucketSize);
      const key = `${bCol},${bRow}`;
      let bucket = srcGrid.get(key);
      if (!bucket) {
        bucket = [];
        srcGrid.set(key, bucket);
      }
      bucket.push(s);
    }

    // ── KDE loop ─────────────────────────────────────────────────────────
    let globalMax = 0;
    const result: {
      col: number;
      row: number;
      px: number;
      py: number;
      kde: number;
      nearestName: string;
      nearestFootfall: number;
    }[] = [];

    for (let i = 0; i < dotGrid.length; i += 1) {
      const { col, row, px, py } = dotGrid[i];
      let kdeVal = 0;
      let totalFairContrib = 0;
      let weightedFootfall = 0;
      const storeEnergy: Record<string, number> = {};

      // Only check sources in the 3×3 bucket neighbourhood
      const bColCenter = Math.floor(px / bucketSize);
      const bRowCenter = Math.floor(py / bucketSize);

      for (let dbCol = -1; dbCol <= 1; dbCol += 1) {
        for (let dbRow = -1; dbRow <= 1; dbRow += 1) {
          const bucket = srcGrid.get(
            `${bColCenter + dbCol},${bRowCenter + dbRow}`,
          );
          if (!bucket) continue;

          for (let j = 0; j < bucket.length; j += 1) {
            const s = bucket[j];
            const dx = px - s.x;
            const dy = py - s.y;
            const dSq = dx * dx + dy * dy;
            if (dSq > cutoffSq) continue;

            const rawContrib = Math.exp(-dSq / twoSigmaSq);
            const contrib = s.weight * rawContrib;
            kdeVal += contrib;

            const fairWeight = rawContrib * (s.weight / s.totalWeight);
            totalFairContrib += fairWeight;
            weightedFootfall += contrib;

            storeEnergy[s.name] = (storeEnergy[s.name] || 0) + contrib;
          }
        }
      }

      let dominantName = '';
      let maxEnergy = -1;
      for (const name in storeEnergy) {
        if (storeEnergy[name] > maxEnergy) {
          maxEnergy = storeEnergy[name];
          dominantName = name;
        }
      }

      if (kdeVal > globalMax) globalMax = kdeVal;
      result.push({
        col,
        row,
        px,
        py,
        kde: kdeVal,
        nearestName: dominantName,
        nearestFootfall:
          totalFairContrib > 0 ? weightedFootfall / totalFairContrib : 0,
      });
    }

    return { kdeGrid: result, maxKDE: globalMax || 1 };
  }, [dotGrid, sources, SIGMA]);

  /* ── STAGE 4 — Normalise ──────────────────────────────────────────────
   *
   * Two separate normalised values are produced per cell:
   *
   * norm          — KDE-based (spatial density). Used for radius + opacity.
   * Reflects how many overlapping sources influence a cell.
   *
   * footfallNorm  — Footfall-based (per-store total). Used for color.
   * Derived from the dominant store's actual footfall so that
   * large polygons with high footfall are NOT diluted by their
   * sample count. Ensures bolder color = higher footfall.
   */
  const { robustMaxFootfall } = useMemo(() => {
    if (points.length === 0) return { robustMaxFootfall: 1 };

    // Clamp at 96th percentile to ignore 1-2 large Entrance/Outlier polygons
    // (e.g. red blobs near the perimeter). The effective max is anchored to the
    // top retail stores so the colour scale is not crushed by outliers.
    const footfalls = points
      .map(p => p.weight)
      .filter(w => w > 0)
      .sort((a, b) => a - b);
    if (footfalls.length === 0) return { robustMaxFootfall: 1 };

    const clampIdx = Math.floor(footfalls.length * 0.96);
    const robustMax = footfalls[clampIdx] || footfalls[footfalls.length - 1];

    return { robustMaxFootfall: Math.max(robustMax, 1) };
  }, [points]);

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
        // Opacity: spatial density — 1.0 at the centre of a source, 0.0 at the edge.
        const norm = Math.pow(Math.min(c.logNorm / clampVal, 1.0), GAMMA);

        // Color: use the dominant store's raw footfall ratio.
        const clampedFootfall = Math.min(c.nearestFootfall, robustMaxFootfall);
        const footfallRatio = clampedFootfall / robustMaxFootfall;

        // Step 1: Base color strength — apply gamma to lift small stores so
        // outliers do not crush the rest of the colour scale.
        const baseColorStrength = Math.pow(
          footfallRatio,
          HEATMAP_DYNAMIC_COLOR_GAMMA,
        );

        // Step 2: Independent spatial decay — forces Orange/Red to fall off
        // clearly toward Yellow/Green away from the source centre.
        const spatialDecay = Math.pow(norm, HEATMAP_SPATIAL_DECAY_GAMMA);

        // Step 3: Combine into final colour input.
        const footfallNorm = baseColorStrength * spatialDecay;

        return { ...c, norm, footfallNorm };
      })
      .sort((a, b) => a.norm - b.norm);
  }, [kdeGrid, maxKDE, robustMaxFootfall, PERCENTILE_CLAMP, GAMMA]);

  /* ── STAGE 5 — Render ─────────────────────────────────────────────────
   *
   * COLOR   ← footfallNorm (dominant store's total footfall, log-compressed)
   * Large high-footfall stores always appear bold/hot.
   * Polygon size does not dilute color.
   *
   * OPACITY ← norm (KDE spatial density)
   * Cells in low-activity areas stay faint but visible.
   *
   * GEOMETRY: Full-pixel squares (no gaps) for continuous coverage visualization.
   * Each cell rendered as a square filling its grid cell completely.
   *
   * Painting order: low-intensity first, high-intensity on top.
   */
  // Uncomment to render the building hull outline for debugging:
  // const debugHullPoints = useMemo(
  //   () => buildingHull.map(p => `${p.x},${p.y}`).join(' '),
  //   [buildingHull],
  // );

  return (
    <g className="heatmap-layer" style={{ mixBlendMode: 'multiply' }}>
      {sortedCells.map(cell => {
        const color = heatmapColor(cell.footfallNorm);

        const baseOpacity = 0.15;
        const maxOpacity = 0.85;

        const fillOpacity =
          baseOpacity + (maxOpacity - baseOpacity) * Math.pow(cell.norm, 1.2);

        return (
          <rect
            key={`h-${cell.col}-${cell.row}`}
            x={cell.px - cellSize / 2}
            y={cell.py - cellSize / 2}
            width={cellSize + 0.75}
            height={cellSize + 0.75}
            fill={color}
            fillOpacity={fillOpacity}
            stroke="none"
            strokeWidth={0}
          />
        );
      })}
      {/* {buildingHull.length >= 3 && (
        <polygon
          points={debugHullPoints}
          fill="none"
          stroke="#ff0000" // Viền màu đỏ để dễ nhìn
          strokeWidth={8}
          strokeOpacity={0.95}
          vectorEffect="non-scaling-stroke"
          pointerEvents="none"
        />
      )} */}
    </g>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
 *  HEATMAP LEGEND - Color Bins Helper
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * Generate discrete color bins for heatmap legend display
 * Similar to polygon legend, divides the footfall range into 6 equal bins
 */
export function getHeatmapColorBins(
  minVal: number,
  maxVal: number,
): {
  min: number;
  max: number;
  fromColor: string;
  toColor: string;
  label: string;
}[] {
  const BIN_COUNT = 6;

  if (maxVal <= minVal) {
    const color = heatmapColor(0);
    return Array.from({ length: BIN_COUNT }, () => ({
      min: minVal,
      max: minVal,
      fromColor: color,
      toColor: color,
      label: minVal.toLocaleString(),
    }));
  }

  const range = maxVal - minVal;
  const binSize = Math.ceil(range / BIN_COUNT);
  const safeRange = Math.max(range, 1);

  return Array.from({ length: BIN_COUNT }, (_, index) => {
    const min = minVal + index * binSize;
    const max =
      index === BIN_COUNT - 1
        ? maxVal
        : Math.min(maxVal, minVal + (index + 1) * binSize - 1);

    const minRatio = Math.max(0, Math.min(1, (min - minVal) / safeRange));
    const maxRatio = Math.max(0, Math.min(1, (max - minVal) / safeRange));

    // Mirror heatmap renderer: footfallRatio^gamma for color strength.
    const baseMinStrength = Math.pow(minRatio, HEATMAP_DYNAMIC_COLOR_GAMMA);
    const baseMaxStrength = Math.pow(maxRatio, HEATMAP_DYNAMIC_COLOR_GAMMA);

    // Legend colors mirror the heatmap at peak spatial density (spatialDecay = 1),
    // showing the color a store at this footfall range displays at its centre.
    // Formula: heatmapColor(footfallRatio ^ DYNAMIC_GAMMA) with spatialDecay = 1.
    const fromColor = heatmapColor(baseMinStrength);
    const toColor = heatmapColor(baseMaxStrength);

    return {
      min,
      max,
      fromColor,
      toColor,
      label: min.toLocaleString(),
    };
  });
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
  const colorBins = getHeatmapColorBins(minVal, maxVal);

  // Dynamically size the bar so labels never overflow.
  // Each label is at most maxVal digits + locale separators (~1 extra char per 3 digits).
  // At font-size 9px, each character is ~5.5px wide. We need BIN_COUNT slots.
  const longestLabel = colorBins.reduce(
    (longest, bin) => (bin.label.length > longest.length ? bin.label : longest),
    '',
  );
  const maxLabelWidth = longestLabel.length * 5.8; // px per char at 9px font
  const minBarWidth = 280;
  const barWidth = Math.max(minBarWidth, colorBins.length * maxLabelWidth);

  return (
    <div
      style={{
        position: 'absolute',
        top: 14,
        left: 14,
        background: 'white',
        borderRadius: 8,
        padding: '12px 16px',
        boxShadow: '0 2px 8px rgba(0, 0, 0, 0.15)',
        zIndex: 500,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'stretch',
        gap: 10,
        border: '1px solid #ddd',
      }}
    >
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: '#333',
          textAlign: 'center',
          letterSpacing: 0.3,
        }}
      >
        Foot Traffic Concentration
      </div>

      {/* Continuous gradient bar across all bins */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 0,
          width: barWidth,
        }}
      >
        {/* Gradient bar */}
        <div
          style={{
            height: 14,
            borderRadius: '2px',
            backgroundImage: `linear-gradient(90deg, ${[
              ...colorBins.map(
                (bin, idx) =>
                  `${bin.fromColor} ${((idx / colorBins.length) * 100).toFixed(1)}%`,
              ),
              `${colorBins[colorBins.length - 1].toColor} 100%`,
            ].join(', ')})`,
          }}
        />

        {/* Tick marks at each milestone boundary */}
        <div style={{ position: 'relative', height: 6 }}>
          {colorBins.map((bin, idx) => {
            const pct = (idx / colorBins.length) * 100;
            return (
              <div
                key={idx}
                style={{
                  position: 'absolute',
                  left: `${pct}%`,
                  top: 0,
                  width: 1,
                  height: 6,
                  backgroundColor: '#999',
                  transform: 'translateX(-50%)',
                }}
              />
            );
          })}
        </div>

        {/* Labels aligned to each tick */}
        <div style={{ position: 'relative', height: 14 }}>
          {colorBins.map((bin, idx) => {
            const pct = (idx / colorBins.length) * 100;
            return (
              <span
                key={idx}
                style={{
                  position: 'absolute',
                  left: `${pct}%`,
                  transform: idx === 0 ? 'none' : 'translateX(-50%)',
                  fontSize: 9,
                  color: '#666',
                  fontWeight: 500,
                  whiteSpace: 'nowrap',
                }}
              >
                {bin.label}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}
