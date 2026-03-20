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
  onHoverEnter?: (name: string, footfall: number, svgX: number, svgY: number, e: React.MouseEvent<SVGCircleElement>) => void;
  onHoverLeave?: () => void;
}

/**
 * Parse a SVG polygon `points` string into an array of {x, y} coordinates.
 *
 * Supports two formats found in the DB:
 *   - "x1,y1 x2,y2 x3,y3 ..."  (comma-separated pairs, space between points)
 *   - "x1 y1 x2 y2 x3 y3 ..."  (space-separated flat list, alternating x/y)
 */
export function parsePolygonPoints(
  pointsStr: string,
): { x: number; y: number }[] {
  if (!pointsStr || pointsStr === 'null' || pointsStr === '') return [];

  // Normalize: replace ALL whitespace variants (tabs, newlines, CR) with single space
  const trimmed = pointsStr.replace(/[\r\n\t]+/g, ' ').trim();

  // Split on any whitespace or comma sequences to get a flat token list
  // This handles both "x,y x,y" and "x y x y" and mixed formats
  const tokens = trimmed.split(/[\s,]+/).filter(t => t.length > 0);

  // Try to detect "x,y" pairs: if original had commas inside tokens before normalization
  const hasInlineComma = pointsStr.includes(',');

  if (hasInlineComma) {
    // Original format was "x1,y1 x2,y2" — commas separate x from y within a pair
    // After split on /[\s,]+/ we get flat numbers anyway, so just fall through
    // to the flat-number parsing below
  }

  // All tokens are now individual numbers (flat list: x0 y0 x1 y1 ...)
  const nums: number[] = [];
  for (const token of tokens) {
    const n = parseFloat(token);
    if (!Number.isNaN(n)) {
      nums.push(n);
    }
  }

  const pts: { x: number; y: number }[] = [];
  for (let i = 0; i + 1 < nums.length; i += 2) {
    pts.push({ x: nums[i], y: nums[i + 1] });
  }
  return pts;
}

/**
 * Compute the centroid (average x, average y) of a polygon from its points string.
 * Returns null if parsing fails or produces NaN coordinates.
 */
export function computeCentroid(
  pointsStr: string,
): { x: number; y: number } | null {
  const pts = parsePolygonPoints(pointsStr);
  if (pts.length === 0) return null;
  const sumX = pts.reduce((acc, p) => acc + p.x, 0);
  const sumY = pts.reduce((acc, p) => acc + p.y, 0);
  const cx = sumX / pts.length;
  const cy = sumY / pts.length;
  // Guard against NaN (malformed input)
  if (Number.isNaN(cx) || Number.isNaN(cy)) return null;
  return { x: cx, y: cy };
}

/**
 * Compute the area of a polygon using the Shoelace (Gauss) formula.
 * Returns the absolute area in SVG coordinate units².
 */
export function computePolygonArea(pointsStr: string): number {
  const pts = parsePolygonPoints(pointsStr);
  if (pts.length < 3) return 0;
  let area = 0;
  const n = pts.length;
  for (let i = 0; i < n; i += 1) {
    const j = (i + 1) % n;
    area += pts[i].x * pts[j].y;
    area -= pts[j].x * pts[i].y;
  }
  return Math.abs(area) / 2;
}

/**
 * Convert polygon area (SVG units²) to a circle radius (SVG units)
 * so that the circle visually represents the same footprint as the polygon.
 * radius = sqrt(area / π)
 */
export function areaToRadius(area: number): number {
  if (area <= 0) return 0;
  return Math.sqrt(area / Math.PI);
}

/**
 * Map a normalized value (0..1) to a classic heatmap gradient:
 * yellow-orange → orange → red → deep crimson → dark wine
 * High saturation throughout — matches the reference image palette.
 */
function heatmapColor(t: number): string {
  const stops = [
    { t: 0.0,  r: 255, g: 220, b: 50  }, // warm yellow
    { t: 0.2,  r: 255, g: 160, b: 0   }, // amber-orange
    { t: 0.45, r: 240, g: 60,  b: 0   }, // deep orange-red
    { t: 0.7,  r: 190, g: 10,  b: 30  }, // vivid red
    { t: 1.0,  r: 100, g: 0,   b: 50  }, // dark wine / maroon
  ];

  // Find the two stops we're between
  let lower = stops[0];
  let upper = stops[stops.length - 1];
  for (let i = 0; i < stops.length - 1; i += 1) {
    if (t >= stops[i].t && t <= stops[i + 1].t) {
      lower = stops[i];
      upper = stops[i + 1];
      break;
    }
  }

  const range = upper.t - lower.t;
  const localT = range === 0 ? 0 : (t - lower.t) / range;
  const r = Math.round(lower.r + localT * (upper.r - lower.r));
  const g = Math.round(lower.g + localT * (upper.g - lower.g));
  const b = Math.round(lower.b + localT * (upper.b - lower.b));
  return `rgb(${r},${g},${b})`;
}

/**
 * Helper to map a category to a layer name (mirrors the logic in the main component).
 */
function getCategoryLayer(category: string | undefined | null): string {
  if (!category) return 'Retail';
  const cat = category.toLowerCase();
  if (cat.includes('entrance')) return 'Entrances';
  if (cat.includes('circulation')) return 'Circulation';
  if (cat.includes('public')) return 'Public';
  return 'Retail';
}

/**
 * Rasterize a polygon into a grid of (col, row) cells whose centers lie inside it.
 * Uses ray-casting point-in-polygon test.
 */
function rasterizePolygon(
  pts: { x: number; y: number }[],
  cellSize: number,
  cols: number,
  rows: number,
): { col: number; row: number }[] {
  if (pts.length < 3) return [];
  const minX = Math.min(...pts.map(p => p.x));
  const maxX = Math.max(...pts.map(p => p.x));
  const minY = Math.min(...pts.map(p => p.y));
  const maxY = Math.max(...pts.map(p => p.y));
  const colStart = Math.max(0, Math.floor(minX / cellSize));
  const colEnd   = Math.min(cols - 1, Math.floor(maxX / cellSize));
  const rowStart = Math.max(0, Math.floor(minY / cellSize));
  const rowEnd   = Math.min(rows - 1, Math.floor(maxY / cellSize));

  const result: { col: number; row: number }[] = [];
  for (let c = colStart; c <= colEnd; c += 1) {
    for (let r = rowStart; r <= rowEnd; r += 1) {
      const cx = (c + 0.5) * cellSize;
      const cy = (r + 0.5) * cellSize;
      // Ray-cast point-in-polygon
      const n = pts.length;
      let inside = false;
      for (let i = 0, j = n - 1; i < n; j = i, i += 1) {
        const { x: xi, y: yi } = pts[i];
        const { x: xj, y: yj } = pts[j];
        if (yi > cy !== yj > cy && cx < ((xj - xi) * (cy - yi)) / (yj - yi) + xi) {
          inside = !inside;
        }
      }
      if (inside) result.push({ col: c, row: r });
    }
  }
  return result;
}

/**
 * SVG Heatmap layer — Walkable-area concentration style.
 *
 * Strategy (matching the data model):
 *   • Circulation / Public zones ARE the walkable corridors → fill them fully,
 *     weight = footfall × Gaussian from centroid (dense center, fades at walls).
 *   • Retail / Entrance zones represent stores → they contribute a soft "spill"
 *     that bleeds outward beyond their boundary (σ larger than the polygon),
 *     simulating customers queuing/lingering near the store entrance.
 *
 * This makes corridors the hot areas and stores contribute ambient glow,
 * which is the correct mental model for a footfall flow heatmap.
 */
export function HeatmapLayer({
  points,
  imgW,
  imgH,
  layerFilters,
  onHoverEnter,
  onHoverLeave,
}: HeatmapLayerProps) {
  // ── 1. Filter ──────────────────────────────────────────────────────────────
  const filtered = useMemo(
    () =>
      points.filter(p => {
        const layer = getCategoryLayer(p.category);
        return layerFilters.includes(layer) && p.weight > 0 && !!p.rawPoints;
      }),
    [points, layerFilters],
  );

  // ── 2. Grid parameters ─────────────────────────────────────────────────────
  const COLS = 120;
  const cellSize = imgW / COLS;
  const ROWS = Math.ceil(imgH / cellSize);

  // ── 3. Build concentration grid ────────────────────────────────────────────
  const gridMap = useMemo(() => {
    const map = new Map<string, { totalW: number; name: string; footfall: number }>();

    filtered.forEach(p => {
      const pts = parsePolygonPoints(p.rawPoints!);
      if (pts.length < 3) return;

      const layer = getCategoryLayer(p.category);
      const isWalkable = layer === 'Circulation' || layer === 'Public';

      // Centroid
      const cx = pts.reduce((s, v) => s + v.x, 0) / pts.length;
      const cy = pts.reduce((s, v) => s + v.y, 0) / pts.length;

      const area = p.polygonArea ?? 0;
      const sqrtArea = area > 0 ? Math.sqrt(area) : cellSize * 3;

      if (isWalkable) {
        // ── Walkable zones (Circulation / Public): fill inside polygon ────────
        // Gaussian is wide so the whole corridor lights up (not just center).
        // σ = 60% of sqrt(area) → fairly flat distribution across the corridor.
        const sigma = sqrtArea * 0.6;
        const twoSigmaSq = 2 * sigma * sigma;
        const cells = rasterizePolygon(pts, cellSize, COLS, ROWS);

        cells.forEach(({ col, row }) => {
          const px = (col + 0.5) * cellSize;
          const py = (row + 0.5) * cellSize;
          const distSq = (px - cx) ** 2 + (py - cy) ** 2;
          // Wide Gaussian: even edge cells still get ~40% of max weight
          const gaussian = Math.exp(-distSq / twoSigmaSq);
          const contribution = p.weight * gaussian;
          const key = `${col},${row}`;
          const existing = map.get(key);
          if (!existing) {
            map.set(key, { totalW: contribution, name: p.name, footfall: p.weight });
          } else {
            const newTotal = existing.totalW + contribution;
            const dominant = contribution > existing.totalW * 0.5
              ? { name: p.name, footfall: p.weight }
              : { name: existing.name, footfall: existing.footfall };
            map.set(key, { totalW: newTotal, ...dominant });
          }
        });
      } else {
        // ── Store zones (Retail / Entrances): radial spill outside boundary ───
        // Instead of filling the store interior, we emit a Gaussian blob centered
        // on the store centroid with σ proportional to store size.
        // We render on a sparse grid AROUND the centroid (not point-in-polygon).
        // This places dots near store entrances / adjacent corridors.
        const sigma = sqrtArea * 0.55;        // spill radius ≈ store size
        const twoSigmaSq = 2 * sigma * sigma;
        const reach = Math.ceil(sigma * 2.5 / cellSize); // grid cells to check
        const colC  = Math.floor(cx / cellSize);
        const rowC  = Math.floor(cy / cellSize);

        for (let dc = -reach; dc <= reach; dc += 1) {
          for (let dr = -reach; dr <= reach; dr += 1) {
            const col = colC + dc;
            const row = rowC + dr;
            if (col < 0 || col >= COLS || row < 0 || row >= ROWS) continue;

            const px = (col + 0.5) * cellSize;
            const py = (row + 0.5) * cellSize;
            const distSq = (px - cx) ** 2 + (py - cy) ** 2;
            const gaussian = Math.exp(-distSq / twoSigmaSq);
            if (gaussian < 0.05) continue; // skip negligible contributions

            // Check: skip cells that are deeply inside the store polygon
            // (only keep cells outside or near the boundary)
            const insidePolygon = (() => {
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
            })();

            // Boundary cells and outside cells both contribute.
            // Deep interior cells are skipped (store interior stays blank).
            const nearBoundary = distSq > (sqrtArea * 0.3) ** 2;
            if (insidePolygon && !nearBoundary) continue;

            // Stores contribute at 40% weight vs corridors to avoid overwhelming them
            const contribution = p.weight * gaussian * 0.4;
            const key = `${col},${row}`;
            const existing = map.get(key);
            if (!existing) {
              map.set(key, { totalW: contribution, name: p.name, footfall: p.weight });
            } else {
              const newTotal = existing.totalW + contribution;
              const dominant = contribution > existing.totalW * 0.7
                ? { name: p.name, footfall: p.weight }
                : { name: existing.name, footfall: existing.footfall };
              map.set(key, { totalW: newTotal, ...dominant });
            }
          }
        }
      }
    });
    return map;
  }, [filtered, cellSize, COLS, ROWS]);

  // ── 4. Build render list ───────────────────────────────────────────────────
  const cells = useMemo(() => {
    const list: { col: number; row: number; w: number; name: string; footfall: number }[] = [];
    gridMap.forEach((val, key) => {
      const [c, r] = key.split(',').map(Number);
      list.push({ col: c, row: r, w: val.totalW, name: val.name, footfall: val.footfall });
    });
    return list.sort((a, b) => a.w - b.w);
  }, [gridMap]);

  const maxW = useMemo(() => Math.max(...cells.map(c => c.w), 1), [cells]);

  // ── 5. Debounce hover-leave ────────────────────────────────────────────────
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

  const dotRadius = cellSize * 0.44;
  const strokeW   = Math.max(cellSize * 0.05, 0.4);

  return (
    <g className="heatmap-layer">
      {cells.map(cell => {
        const norm  = cell.w / maxW;
        const color = heatmapColor(norm);
        const cx    = (cell.col + 0.5) * cellSize;
        const cy    = (cell.row + 0.5) * cellSize;
        const fillOpacity   = 0.55 + 0.45 * norm;
        const strokeOpacity = 0.15 + 0.25 * norm;

        return (
          <circle
            key={`${cell.col}-${cell.row}`}
            cx={cx}
            cy={cy}
            r={dotRadius}
            fill={color}
            fillOpacity={fillOpacity}
            stroke="#000"
            strokeWidth={strokeW}
            strokeOpacity={strokeOpacity}
            style={{
              pointerEvents: onHoverEnter ? 'auto' : 'none',
              cursor: onHoverEnter ? 'pointer' : 'default',
            }}
            onMouseEnter={onHoverEnter ? e => handleEnter(cell.name, cell.footfall, cx, cy, e) : undefined}
            onMouseLeave={onHoverLeave ? handleLeave : undefined}
          />
        );
      })}
    </g>
  );
}

/**
 * Heatmap legend bar (horizontal gradient strip with min/max labels).
 * Displayed at top-right, similar to the reference image.
 */
export function HeatmapLegend({
  minVal,
  maxVal,
}: {
  minVal: number;
  maxVal: number;
}) {
  const gradientId = 'heatmap-legend-gradient';
  const stops = [
    { offset: '0%',   color: 'rgb(255,220,50)'  }, // warm yellow
    { offset: '20%',  color: 'rgb(255,160,0)'   }, // amber-orange
    { offset: '45%',  color: 'rgb(240,60,0)'    }, // deep orange-red
    { offset: '70%',  color: 'rgb(190,10,30)'   }, // vivid red
    { offset: '100%', color: 'rgb(100,0,50)'    }, // dark wine
  ];

  return (
    <div
      style={{
        position: 'absolute',
        top: 14,
        right: 14,
        background: 'rgba(255,255,255,0.92)',
        borderRadius: 6,
        padding: '8px 12px',
        boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
        zIndex: 500,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        gap: 4,
        minWidth: 130,
        border: '1px solid #ddd',
      }}
    >
      <svg width={120} height={14}>
        <defs>
          <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="0%">
            {stops.map(s => (
              <stop
                key={s.offset}
                offset={s.offset}
                stopColor={s.color}
              />
            ))}
          </linearGradient>
        </defs>
        <rect
          x={0}
          y={0}
          width={120}
          height={14}
          fill={`url(#${gradientId})`}
          rx={3}
        />
      </svg>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          width: '100%',
          fontSize: 10,
          color: '#555',
        }}
      >
        <span>{minVal.toLocaleString()}</span>
        <span>{maxVal.toLocaleString()}</span>
      </div>
    </div>
  );
}
