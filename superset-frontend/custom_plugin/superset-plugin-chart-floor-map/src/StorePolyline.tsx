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

export interface StorePolylineProps {
  store: any;
  index: number;
  isHovered: boolean;
  onHoverEnter: (e: React.MouseEvent<SVGPolylineElement>) => void;
  onHoverLeave: () => void;
}

export const getFootfallColor = (
  footfall: number | undefined | null,
): string => {
  if (footfall === undefined || footfall === null || footfall === 0) {
    return 'rgb(237, 237, 237)'; // Light gray - No Data
  }
  if (footfall <= 1) {
    return 'rgb(252, 217, 171)'; // Light beige
  }
  if (footfall <= 1051) {
    return 'rgb(255, 198, 104)'; // Orange
  }
  if (footfall <= 2101) {
    return 'rgb(255, 179, 0)'; // Darker orange
  }
  if (footfall <= 3151) {
    return 'rgb(225, 158, 0)'; // Even darker orange
  }
  if (footfall <= 4201) {
    return 'rgb(196, 138, 0)'; // Brown-orange
  }
  return 'rgb(168, 118, 0)'; // Dark brown-orange
};

export function StorePolyline({
  store,
  index,
  isHovered,
  onHoverEnter,
  onHoverLeave,
}: StorePolylineProps) {
  const fillColor = getFootfallColor(store.total_footfall);

  return (
    <g key={index}>
      {/* Polyline */}
      <polyline
        onMouseEnter={onHoverEnter}
        onMouseLeave={onHoverLeave}
        style={{
          stroke: '#000000',
          fill: fillColor,
          opacity: 0.7,
          transition: 'all 0.3s ease',
          pointerEvents: 'auto',
          zIndex: isHovered ? 10 : 2,
          cursor: 'pointer',
          strokeWidth: isHovered ? 4 : 2,
          strokeLinejoin: 'round',
          strokeLinecap: 'round',
        }}
        points={store.points}
      />
    </g>
  );
}
