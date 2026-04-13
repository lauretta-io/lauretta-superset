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
 *   • Walkable space  = convex hull of ALL polygons ∪ individual polygon
 *     interiors.  Any point inside the hull or inside any polygon receives
 *     a heatmap dot.
 *
 *   • Heat sources    = polygon interiors, weighted by each store's footfall.
 *     Large polygons are sampled with adaptive interior grids; small ones
 *     fall back to a single centroid point.
 *
 *   • KDE bandwidth σ = SIGMA SVG units.  Sized so heat from a store
 *     reaches the middle of the corridor (~half corridor width) but does
 *     not cross into the opposite store.
 *
 *   • Rendering: uniform dot grid on the walkable space.  Each dot's color
 *     and opacity is driven by its KDE value.  Low-KDE dots are nearly
 *     invisible (the floor-plan image shows through); high-KDE dots are
 *     vivid and opaque.
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

// Build a more detailed boundary from convex hull by inserting nearby interior
// vertices along long edges. This keeps the hull tight to polygon boundaries
// without inflating beyond farthest units.
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

  const refined = baseHull.slice();
  const edgeLens = refined.map((p, i) =>
    distance(p, refined[(i + 1) % refined.length]),
  );
  const avgEdge =
    edgeLens.reduce((s, d) => s + d, 0) / Math.max(edgeLens.length, 1);
  // Lower threshold gives more edge detail than a coarse convex hull.
  const maxEdgeLen = avgEdge * 1.2;
  const maxInsertions = Math.min(
    pool.length,
    Math.max(1800, baseHull.length * 12),
  );

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
        if (t <= 0.04 || t >= 0.96) continue;
        if (dist > edgeLen * 0.45) continue;

        const score = dist + 0.08 * Math.abs(0.5 - t) * edgeLen;
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

/* ─── Sample points uniformly from polygon interior ─────────────────────── */

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

function downsampleVerticesForHull(
  pts: { x: number; y: number }[],
  cell: number,
): { x: number; y: number }[] {
  if (pts.length === 0 || cell <= 1) return pts;
  const grid = new Map<string, { x: number; y: number }>();
  for (let i = 0; i < pts.length; i += 1) {
    const p = pts[i];
    const key = `${Math.round(p.x / cell)}:${Math.round(p.y / cell)}`;
    if (!grid.has(key)) grid.set(key, p);
  }
  return Array.from(grid.values());
}

/* ─── Color scale ─────────────────────────────────────────────────────────
 *
 * Professional high-contrast perceptually-uniform palette:
 * Blue (Cool/Low) → Cyan → Green (Medium) → Yellow → Orange → Red (Very High)
 *
 * Input t ∈ [0, 1] is the final combined footfall+spatial value.
 * This function is the single source of truth for all color output —
 * both the heatmap renderer and the legend bar use it exclusively.
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

/* ─── Shared color-normalisation constants ────────────────────────────────
 *
 * HEATMAP_DYNAMIC_COLOR_GAMMA  — compresses the footfall ratio so low-footfall
 *   stores still show readable color instead of being near-black.
 *   Applied as:  baseColorStrength = footfallRatio ^ GAMMA_COLOR
 *
 * HEATMAP_SPATIAL_DECAY_GAMMA  — forces color to fall off spatially away from
 *   a store's centre, so only the core of a high-footfall store is fully hot.
 *   Applied as:  spatialDecay = norm ^ GAMMA_SPATIAL
 *
 * Final color input: t = baseColorStrength × spatialDecay
 *
 * LEGEND CONTRACT — the legend must show the color a store actually appears at
 * its spatial peak (norm = 1 ⟹ spatialDecay = 1^GAMMA_SPATIAL = 1).
 * Therefore at peak:  t_peak = footfallRatio ^ GAMMA_COLOR
 * The legend gradient is built by forward-computing t_peak across [0, robustMax].
 * This guarantees: a color seen on the heatmap at store centre matches the same
 * footfall value on the legend bar — no inverse-gamma approximation needed.
 */
const HEATMAP_DYNAMIC_COLOR_GAMMA = 0.45;
const HEATMAP_SPATIAL_DECAY_GAMMA = 0.35;

/* ═══════════════════════════════════════════════════════════════════════════
 *  MAIN COMPONENT
 * ═══════════════════════════════════════════════════════════════════════════ */

export function HeatmapLayer({
  points,
  imgW,
  imgH,
}: HeatmapLayerProps) {
  /* ─────────────────────────────────────────────────────────────────────
   *  TUNING PARAMETERS
   * ───────────────────────────────────────────────────────────────────── */

  const minDotSpacing = 7;
  const maxDotSpacing = 26;
  const targetDotCount = 24000;
  const adaptiveDotSpacing = Math.min(
    maxDotSpacing,
    Math.max(minDotSpacing, Math.sqrt((imgW * imgH) / targetDotCount)),
  );

  const mapDiag = Math.sqrt(imgW * imgW + imgH * imgH);
  const SIGMA = Math.min(60, Math.max(30, mapDiag * 0.015));
  const INTERIOR_SPACING = 16;
  const PERCENTILE_CLAMP = 0.98;
  const GAMMA = 1.2;

  /* ── STAGE 1 — Classify & build heat sources ──────────────────────── */

  const { allBBoxes, buildingHull, sources } =
    useMemo(() => {
      const allPolys: { x: number; y: number }[][] = [];
      const allVertices: { x: number; y: number }[] = [];
      const src: FootfallSource[] = [];

      for (let i = 0; i < points.length; i += 1) {
        const p = points[i];
        if (!p.rawPoints) continue;
        const pts = parsePolygonPoints(p.rawPoints);
        if (pts.length < 3) continue;

        allPolys.push(pts);
        for (let v = 0; v < pts.length; v += 1) allVertices.push(pts[v]);

        if (p.weight > 0) {
          const cx = pts.reduce((s, q) => s + q.x, 0) / pts.length;
          const cy = pts.reduce((s, q) => s + q.y, 0) / pts.length;
          // Use adaptive spacing so large polygons (e.g. open arena floors)
          // get enough interior sample points and aren't reduced to one centroid.
          // Target at least a 10×10 grid; small polygons keep INTERIOR_SPACING.
          const polyBB = makeBBox(pts);
          const polyDiag = Math.sqrt(
            Math.pow(polyBB.maxX - polyBB.minX, 2) +
              Math.pow(polyBB.maxY - polyBB.minY, 2),
          );
          const adaptiveSpacing = Math.min(
            INTERIOR_SPACING,
            Math.max(1, polyDiag / 10),
          );
          const interiorPts = sampleInterior(pts, adaptiveSpacing);
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

      // Detailed hull: wrap farther polygons while following nearer edge geometry.
      // Downsample dense vertex clouds first to keep interactions responsive.
      const hullInput =
        allVertices.length > 6000
          ? downsampleVerticesForHull(
              allVertices,
              Math.max(2, Math.min(imgW, imgH) / 700),
            )
          : allVertices;
      const hull = hullInput.length >= 3 ? buildDetailedHull(hullInput) : [];

      return {
        allBBoxes: computeBBoxes(allPolys),
        buildingHull: hull,
        sources: src,
      };
    }, [points, INTERIOR_SPACING, imgW, imgH]);

  /* ── STAGE 2 — Walkable dot grid ─────────────────────────────────── */

  const MAX_DOT_CELLS = 80_000;

  const { dotGrid, cellSize } = useMemo(() => {
    if (buildingHull.length < 3 && allBBoxes.length === 0)
      return { dotGrid: [], cellSize: adaptiveDotSpacing };

    const hullBB = buildingHull.length >= 3 ? makeBBox(buildingHull) : null;
    let gMinX = hullBB?.minX ?? Infinity;
    let gMinY = hullBB?.minY ?? Infinity;
    let gMaxX = hullBB?.maxX ?? -Infinity;
    let gMaxY = hullBB?.maxY ?? -Infinity;
    for (const bb of allBBoxes) {
      if (bb.minX < gMinX) gMinX = bb.minX;
      if (bb.minY < gMinY) gMinY = bb.minY;
      if (bb.maxX > gMaxX) gMaxX = bb.maxX;
      if (bb.maxY > gMaxY) gMaxY = bb.maxY;
    }

    let cs = adaptiveDotSpacing;
    const estW = gMaxX - gMinX;
    const estH = gMaxY - gMinY;

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

        const inHull =
          buildingHull.length >= 3 && pointInPolygon(px, py, buildingHull);
        const inAnyPolygon = isInsideAny(px, py, allBBoxes);
        if (!inHull && !inAnyPolygon) continue;

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
  }, [buildingHull, allBBoxes, adaptiveDotSpacing]);

  /* ── STAGE 3 — KDE with spatial grid index ────────────────────────── */

  const { kdeGrid, maxKDE } = useMemo(() => {
    if (sources.length === 0 || dotGrid.length === 0)
      return { kdeGrid: [] as any[], maxKDE: 1 };

    const twoSigmaSq = 2 * SIGMA * SIGMA;
    const cutoff = 4.5 * SIGMA;
    const cutoffSq = cutoff * cutoff;

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

  /* ── STAGE 4 — Normalise ──────────────────────────────────────────── */

  const robustMaxFootfall = useMemo(
    () => computeRobustMaxFootfall(points),
    [points],
  );

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

        const clampedFootfall = Math.min(c.nearestFootfall, robustMaxFootfall);
        const footfallRatio = clampedFootfall / robustMaxFootfall;

        const baseColorStrength = Math.pow(
          footfallRatio,
          HEATMAP_DYNAMIC_COLOR_GAMMA,
        );

        const spatialDecay = Math.pow(norm, HEATMAP_SPATIAL_DECAY_GAMMA);

        const footfallNorm = baseColorStrength * spatialDecay;

        return { ...c, norm, footfallNorm };
      })
      .sort((a, b) => a.norm - b.norm);
  }, [kdeGrid, maxKDE, robustMaxFootfall, PERCENTILE_CLAMP, GAMMA]);

  /* ── STAGE 5 — Render ─────────────────────────────────────────────── */

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
    </g>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
 *  HEATMAP LEGEND HELPERS
 *
 *  FIX — Legend color pipeline now matches the heatmap exactly.
 *
 *  OLD (broken) approach:
 *    The legend inverted the HEATMAP_COLOR_STOPS 't' positions back to
 *    footfall values using:
 *      footfall = (t ^ (1/GAMMA_COLOR)) × robustMax
 *    Then used those 't' values as colour-stop positions in the gradient.
 *    Problem: two stores at 7000 and 7400 both landed in the high-t region
 *    (near t=0.85–0.95), right in the steep orange→red band, making them
 *    look dramatically different even though the heatmap rendered them nearly
 *    identically (because spatialDecay and log-normalisation compressed the
 *    high end significantly).
 *
 *  NEW (correct) approach:
 *    Forward-compute colors directly from footfall ratios using the SAME
 *    formula the heatmap uses at peak spatial density (norm=1, spatialDecay=1):
 *
 *      t_peak = (footfall / robustMax) ^ GAMMA_COLOR
 *
 *    The gradient bar is built by sampling this formula across [0, robustMax],
 *    and tick/label positions are placed at linear footfall intervals.
 *    This guarantees: a colour seen at a store's centre on the heatmap
 *    matches the same footfall value on the legend bar exactly.
 * ═══════════════════════════════════════════════════════════════════════════ */

/** Compute robustMaxFootfall from the points array — same logic as Stage 4. */
export function computeRobustMaxFootfall(points: HeatmapPoint[]): number {
  const footfalls = points
    .map(p => p.weight)
    .filter(w => w > 0)
    .sort((a, b) => a - b);
  if (footfalls.length === 0) return 1;
  const clampIdx = Math.floor(footfalls.length * 0.96);
  return Math.max(footfalls[clampIdx] || footfalls[footfalls.length - 1], 1);
}

/**
 * Compute the legend color for a given footfall value at peak spatial density.
 *
 * Uses the same formula as the heatmap renderer at norm=1:
 *   t = (footfall / robustMax) ^ GAMMA_COLOR
 *
 * This is the canonical color lookup used by the HeatmapLegend gradient bar
 * and the sidebar store list so they are always in sync with the heatmap.
 */
export function legendColorAtFootfall(
  footfall: number,
  robustMax: number,
): string {
  const ratio = Math.min(Math.max(footfall / robustMax, 0), 1);
  const t = Math.pow(ratio, HEATMAP_DYNAMIC_COLOR_GAMMA);
  return heatmapColor(t);
}

/* ═══════════════════════════════════════════════════════════════════════════
 *  HEATMAP LEGEND
 *
 *  Simple gradient bar from Low (blue) → High (red).
 *  The gradient is sampled from heatmapColor(t) for t ∈ [0, 1].
 * ═══════════════════════════════════════════════════════════════════════════ */

export function HeatmapLegend() {
  const BAR_W = 260;
  const BAR_H = 14;

  const gradientStops = useMemo(() => {
    const N = 20;
    return Array.from({ length: N + 1 }, (_, i) => {
      const t = i / N;
      return {
        offset: `${((i / N) * 100).toFixed(1)}%`,
        color: heatmapColor(t),
      };
    });
  }, []);

  return (
    <div
      style={{
        position: 'absolute',
        bottom: 20,
        left: '50%',
        transform: 'translateX(-50%)',
        background: 'white',
        borderRadius: 8,
        padding: '10px 16px 10px',
        boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
        zIndex: 500,
        border: '1px solid #ddd',
        minWidth: BAR_W + 32,
      }}
    >
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: '#333',
          textAlign: 'center',
          marginBottom: 8,
        }}
      >
        Foot Traffic Concentration
      </div>

      <svg
        width={BAR_W}
        height={34}
        style={{ display: 'block', overflow: 'visible' }}
      >
        <defs>
          <linearGradient id="heatleg-simple" x1="0" x2="1" y1="0" y2="0">
            {gradientStops.map((s, i) => (
              <stop key={i} offset={s.offset} stopColor={s.color} />
            ))}
          </linearGradient>
        </defs>

        <rect
          x={0}
          y={0}
          width={BAR_W}
          height={BAR_H}
          rx={3}
          fill="url(#heatleg-simple)"
        />

        <text x={0} y={BAR_H + 15} fontSize={10} fill="#666" textAnchor="start">
          Low
        </text>
        <text
          x={BAR_W}
          y={BAR_H + 15}
          fontSize={10}
          fill="#666"
          textAnchor="end"
        >
          High
        </text>
      </svg>
    </div>
  );
}
