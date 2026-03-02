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
import React, { useEffect, createRef, useState } from 'react';
import { styled } from '@superset-ui/core';
import {
  SupersetPluginChartFloorMapProps,
  SupersetPluginChartFloorMapStylesProps,
} from './types';
import { StorePolyline, getFootfallColor } from './StorePolyline';
import { ZoomPanWrapper } from './ZoomPanWrapper';
import floorImageCF from './images/TRX_floorplan_CF.png';
import floorImageCM from './images/TRX_floorplan_CM.png';
import floorImageGF from './images/TRX_floorplan_GF.png';
import floorImageL1 from './images/TRX_floorplan_L1.png';
import floorImageL2 from './images/TRX_floorplan_L2.png';
import floorImagePL from './images/TRX_floorplan_PL.png';
// The following Styles component is a <div> element, which has been styled using Emotion
// For docs, visit https://emotion.sh/docs/styled

// Theming variables are provided for your use via a ThemeProvider
// imported from @superset-ui/core. For variables available, please visit
// https://github.com/apache-superset/superset-ui/blob/master/packages/superset-ui-core/src/style/index.ts

const Styles = styled.div<SupersetPluginChartFloorMapStylesProps>`
  padding: 0;
  border-radius: ${({ theme }) => theme.gridUnit * 2}px;
  height: ${({ height }) => height}px;
  width: ${({ width }) => width}px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  position: relative;

  svg {
    flex: 1;
    width: 100%;
    height: 100%;
    image {
      image-rendering: crisp-edges;
      image-rendering: pixelated;
    }
  }

  h3 {
    /* You can use your props to control CSS! */
    margin-top: 0;
    margin-bottom: ${({ theme }) => theme.gridUnit * 3}px;
    font-size: ${({ theme, headerFontSize }) =>
      theme.typography.sizes[headerFontSize]}px;
    font-weight: ${({ theme, boldText }) =>
      theme.typography.weights[boldText ? 'bold' : 'normal']};
  }

  pre {
    height: ${({ theme, headerFontSize, height }) =>
      height - theme.gridUnit * 12 - theme.typography.sizes[headerFontSize]}px;
  }
`;

const TooltipBox = styled.div<{ isVisible: boolean; x: number; y: number }>`
  position: absolute;
  left: ${({ x }) => x}px;
  top: ${({ y }) => y}px;
  transform: translate(-70%, -80%);
  pointer-events: none;
  z-index: 1000;
  opacity: ${({ isVisible }) => (isVisible ? 1 : 0)};
  visibility: ${({ isVisible }) => (isVisible ? 'visible' : 'hidden')};
  transition: opacity 0.2s ease;
  background: white;
  border-radius: 6px;
  padding: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
  min-width: 180px;
  text-align: center;

  .store-name {
    font-size: 13px;
    font-weight: bold;
    margin-bottom: 4px;
  }

  .category-label {
    font-size: 9px;
    color: #999;
    margin-bottom: 2px;
  }

  .category-name {
    font-size: 12px;
    font-weight: 500;
  }

  .footfall-section {
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px solid #e0e0e0;
  }

  .footfall-label {
    font-size: 9px;
    color: #999;
    margin-bottom: 2px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .footfall-value {
    font-size: 12px;
    font-weight: 600;
  }
`;

export default function SupersetPluginChartFloorMap(
  props: SupersetPluginChartFloorMapProps,
) {
  // Map floor selection to floor images
  const floorImages: Record<string, string> = {
    C: floorImageCF,
    M: floorImageCM,
    G: floorImageGF,
    L1: floorImageL1,
    L2: floorImageL2,
    PL: floorImagePL,
  };

  // height and width are the height and width of the DOM element as it exists in the dashboard.
  // There is also a `data` prop, which is, of course, your DATA 🎉
  const { data, height, width, floorSelection } = props;
  const [hoveredStoreName, setHoveredStoreName] = useState<string | null>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const rootElem = createRef<HTMLDivElement>();

  // Get the current floor image based on selection
  const currentFloorImage = floorImages[floorSelection] || floorImages.C;

  // Group data by store name
  const groupedByStore = React.useMemo(() => {
    if (!data || !Array.isArray(data)) return {};
    return data.reduce(
      (acc: Record<string, (typeof data)[0][]>, store: any) => {
        const storeName = store.store || 'Unknown';
        if (!acc[storeName]) {
          acc[storeName] = [];
        }
        acc[storeName].push(store);
        return acc;
      },
      {},
    );
  }, [data]);

  // Often, you just want to access the DOM and do whatever you want.
  // Here, you can do that with createRef, and the useEffect hook.
  useEffect(() => {
    const root = rootElem.current as HTMLElement;
    console.log('Plugin element', root);
  });

  const handleStoreHoverEnter = (
    index: number,
    storeName: string,
    event: React.MouseEvent<SVGPolylineElement>,
  ) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const parentRect = rootElem.current?.getBoundingClientRect();

    if (parentRect) {
      setTooltipPos({
        x: rect.left - parentRect.left + rect.width / 2,
        y: rect.top - parentRect.top,
      });
    }
    setHoveredIndex(index);
    setHoveredStoreName(storeName);
  };

  const handleStoreHoverLeave = () => {
    setHoveredIndex(null);
    setHoveredStoreName(null);
  };

  const hoveredStore =
    hoveredIndex !== null && data && Array.isArray(data)
      ? data[hoveredIndex]
      : null;

  // Get the first store data for the hovered store name (to show only one tooltip)
  const displayedStore =
    hoveredStoreName && data && Array.isArray(data)
      ? data.find((store: any) => store.store === hoveredStoreName)
      : null;

  return (
    <Styles
      ref={rootElem}
      boldText={props.boldText}
      headerFontSize={props.headerFontSize}
      height={height}
      width={width}
    >
      <ZoomPanWrapper>
        <svg
          xmlns="http://www.w3.org/2000/svg"
          xmlnsXlink="http://www.w3.org/1999/xlink"
          viewBox="0 0 5700 3800"
          width="100%"
          height="100%"
          style={{ display: 'block', position: 'relative' }}
          preserveAspectRatio="xMidYMid meet"
        >
          <image
            x="0"
            y="0"
            width="5700"
            height="3800"
            xlinkHref={currentFloorImage}
            imageRendering="crisp-edges"
            style={{ pointerEvents: 'none', zIndex: 1 }}
          />
          {/* Render polylines grouped by store */}
          {data &&
            Array.isArray(data) &&
            data.map((store: any, index: number) => {
              const storeName = store.store || 'Unknown';
              const isStoreHovered = hoveredStoreName === storeName;
              return (
                <StorePolyline
                  key={index}
                  store={store}
                  index={index}
                  isHovered={isStoreHovered}
                  onHoverEnter={e => handleStoreHoverEnter(index, storeName, e)}
                  onHoverLeave={handleStoreHoverLeave}
                />
              );
            })}
        </svg>
      </ZoomPanWrapper>
      {displayedStore && hoveredStoreName !== null && (
        <TooltipBox isVisible={true} x={tooltipPos.x} y={tooltipPos.y}>
          <div className="store-name">{displayedStore.store}</div>
          {displayedStore.category && (
            <div className="category-section">
              <div className="category-label">Category</div>
              <div className="category-name">{displayedStore.category}</div>
            </div>
          )}
          {displayedStore.total_footfall !== undefined &&
            displayedStore.total_footfall !== null && (
              <div className="footfall-section">
                <div className="footfall-label">Footfall</div>
                <div
                  className="footfall-value"
                  style={{
                    color: getFootfallColor(displayedStore.total_footfall),
                  }}
                >
                  {displayedStore.total_footfall.toLocaleString()}
                </div>
              </div>
            )}
        </TooltipBox>
      )}
    </Styles>
  );
}
