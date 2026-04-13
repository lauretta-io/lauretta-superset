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
import React from 'react';

interface StorePolygonData {
  name?: string;
  points?: string | null;
  total_footfall?: number | null;
}

export interface StorePolylineProps {
  store: StorePolygonData;
  index: number;
  storeName?: string;
  isHovered: boolean;
  layer: string;
  maxFootfall: number;
  onHoverEnter: (e: React.MouseEvent<SVGPolygonElement>) => void;
  onHoverMove?: (e: React.MouseEvent<SVGPolygonElement>) => void;
  onHoverLeave: () => void;
  onClick?: (e: React.MouseEvent<SVGPolygonElement>) => void;
}

// Color scales for each layer (6 bins from light to dark)
export const layerColorScales: Record<string, string[]> = {
  Retail: [
    'rgb(220, 242, 195)', // lightest
    'rgb(175, 218, 120)',
    'rgb(130, 194, 50)',
    'rgb(110, 164, 40)',
    'rgb(88, 135, 30)',
    'rgb(67, 106, 20)', // darkest
  ],
  Entrances: [
    'rgb(181, 196, 218)', // lightest
    'rgb(124, 156, 199)',
    'rgb(51, 119, 179)',
    'rgb(23, 103, 183)',
    'rgb(24, 85, 184)',
    'rgb(51, 63, 179)', // darkest
  ],
  Circulation: [
    'rgb(234, 201, 238)', // lightest
    'rgb(227, 163, 238)',
    'rgb(219, 124, 238)',
    'rgb(189, 93, 208)',
    'rgb(159, 63, 179)',
    'rgb(130, 28, 151)', // darkest
  ],
  Public: [
    'rgb(187, 228, 231)', // lightest
    'rgb(129, 218, 224)',
    'rgb(15, 207, 218)',
    'rgb(16, 173, 183)',
    'rgb(16, 141, 149)',
    'rgb(14, 110, 116)', // darkest
  ],
};

// Get color based on footfall, layer, and max footfall (dynamic bins)
export const getLayerFootfallColor = (
  footfall: number | undefined | null,
  layer: string,
  maxFootfall: number,
): string => {
  // No data or 0 value -> uncolored (transparent with border only)
  if (footfall === undefined || footfall === null || footfall === 0) {
    return 'rgba(237, 237, 237, 0.93)'; // Very light gray, semi-transparent
  }

  const colors = layerColorScales[layer] || layerColorScales.Retail;

  // If maxFootfall is 0 or less, return first color
  if (maxFootfall <= 1) return colors[0];

  // Calculate bin size (6 equal bins from 1 to maxFootfall)
  const binSize = Math.ceil((maxFootfall - 1) / 6);

  // Determine which bin the footfall falls into (0-indexed)
  const binIndex = Math.max(
    0,
    Math.min(Math.floor((footfall - 1) / binSize), 5),
  );

  return colors[binIndex];
};

// Get color bins for legend display
export const getColorBins = (
  maxFootfall: number,
  layer: string,
): { min: number; max: number; color: string; label: string }[] => {
  const colors = layerColorScales[layer] || layerColorScales.Retail;
  // Calculate bin size (6 equal bins from 1 to maxFootfall)
  const binSize = Math.ceil((maxFootfall - 1) / 6);

  return colors.map((color, index) => {
    const min = 1 + index * binSize;
    const max = index === 5 ? maxFootfall : 1 + (index + 1) * binSize - 1;
    return {
      min,
      max,
      color,
      label: `${min}`,
    };
  });
};

export function StorePolyline({
  store,
  index,
  storeName,
  isHovered,
  layer,
  maxFootfall,
  onHoverEnter,
  onHoverMove,
  onHoverLeave,
  onClick,
}: StorePolylineProps) {
  const fillColor = getLayerFootfallColor(
    store.total_footfall,
    layer,
    maxFootfall,
  );

  // Skip rendering if points is NULL, undefined, or empty
  if (!store.points || store.points === 'null' || store.points === '') {
    return null;
  }

  return (
    <g key={index}>
      {/* Polyline - filled with layer-based footfall color (NOT KDE) */}
      <polygon
        data-store-name={storeName || store.name || 'Unknown'}
        onMouseEnter={onHoverEnter}
        onMouseMove={onHoverMove}
        onMouseLeave={onHoverLeave}
        onClick={e => {
          e.stopPropagation();
          onClick?.(e);
        }}
        style={{
          stroke: isHovered ? '#000000' : 'rgba(0, 0, 0, 0.5)',
          fill: fillColor,
          transition: 'all 0.3s ease',
          pointerEvents: 'auto',
          zIndex: isHovered ? 10 : 2,
          cursor: 'pointer',
          strokeWidth: isHovered ? 6 : 2,
          strokeLinejoin: 'round',
          strokeLinecap: 'round',
          color: 'black',
        }}
        points={store.points}
      />
    </g>
  );
}
