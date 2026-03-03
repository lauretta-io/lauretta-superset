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
import React, { useEffect, createRef, useState, useRef } from 'react';
import { styled } from '@superset-ui/core';
import {
  SupersetPluginChartFloorMapProps,
  SupersetPluginChartFloorMapStylesProps,
} from './types';
import {
  StorePolyline,
  getLayerFootfallColor,
  getColorBins,
} from './StorePolyline';
import { ZoomPanWrapper, ZoomPanWrapperRef } from './ZoomPanWrapper';
import floorImageCF from './images/floors/TRX_floorplan_CF.jpeg';
import floorImageCM from './images/floors/TRX_floorplan_CM.jpg';
import floorImageGF from './images/floors/TRX_floorplan_GF.jpeg';
import floorImageL1 from './images/floors/TRX_floorplan_L1.jpeg';
import floorImageL2 from './images/floors/TRX_floorplan_L2.jpeg';
import floorImagePL from './images/floors/TRX_floorplan_PL.jpeg';
import layerEntrances from './images/entrances-layers.png';
import layerCirculation from './images/circulation-layers.png';
import layerPublic from './images/public-layers.png';
import layerShops from './images/shops-layers.png';
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
  transform: translate(-50%, -130%);
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

const StoreListWidget = styled.div`
  position: absolute;
  left: 20px;
  top: 20px;
  background: white;
  border-radius: 8px;
  padding: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  z-index: 500;
  max-height: calc(100% - 40px);
  width: 250px;
  display: flex;
  flex-direction: column;
  overflow-x: hidden;

  .widget-header {
    font-size: 14px;
    font-weight: bold;
    margin-bottom: 8px;
    padding-bottom: 8px;
    border-bottom: 1px solid #e0e0e0;
    color: #333;
  }

  .search-input {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid #d9d9d9;
    border-radius: 4px;
    font-size: 12px;
    margin-bottom: 10px;
    outline: none;
    transition: border-color 0.2s ease;

    &:focus {
      border-color: #40a9ff;
      box-shadow: 0 0 0 2px rgba(64, 169, 255, 0.2);
    }

    &::placeholder {
      color: #bfbfbf;
    }
  }

  .store-list {
    overflow-y: auto;
    overflow-x: hidden;
    flex: 1;
    max-height: 500px;

    &::-webkit-scrollbar {
      width: 6px;
    }

    &::-webkit-scrollbar-track {
      background: #f1f1f1;
      border-radius: 3px;
    }

    &::-webkit-scrollbar-thumb {
      background: #888;
      border-radius: 3px;
    }

    &::-webkit-scrollbar-thumb:hover {
      background: #555;
    }
  }

  .store-item {
    padding: 8px;
    margin-bottom: 6px;
    border-radius: 4px;
    background: #f9f9f9;
    border-left: 4px solid;
    transition: all 0.2s ease;
    cursor: pointer;

    &:hover {
      background: #f0f0f0;
      transform: translateX(2px);
    }

    &.selected {
      background: #e6f7ff;
      border-left-width: 6px;
      box-shadow: 0 2px 6px rgba(24, 144, 255, 0.2);
    }

    .store-name {
      font-size: 12px;
      font-weight: 600;
      color: #333;
      margin-bottom: 4px;
    }

    .store-footfall {
      font-size: 11px;
      font-weight: 500;
      display: flex;
      align-items: center;
      gap: 4px;

      .label {
        color: #999;
      }

      .value {
        font-weight: 600;
      }
    }
  }

  .no-results {
    padding: 16px;
    text-align: center;
    color: #999;
    font-size: 12px;
  }

  .layer-filter {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 10px;
    padding-bottom: 10px;
    border-bottom: 1px solid #e0e0e0;
  }

  .layer-btn {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 6px 8px;
    font-size: 9px;
    border: 2px solid #d9d9d9;
    border-radius: 6px;
    background: #fff;
    cursor: pointer;
    transition: all 0.2s ease;
    color: #666;
    min-width: 48px;

    img {
      width: 24px;
      height: 24px;
      margin-bottom: 3px;
      object-fit: contain;
    }

    &:hover {
      border-color: #40a9ff;
      color: #40a9ff;
    }

    &.active {
      background: #e6f7ff;
      border-color: #1890ff;
      color: #1890ff;
    }
  }

  .loading-overlay {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(255, 255, 255, 0.8);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    border-radius: 8px;
  }

  .loading-spinner {
    width: 40px;
    height: 40px;
    border: 3px solid #f3f3f3;
    border-top: 3px solid #1890ff;
    border-radius: 50%;
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    0% {
      transform: rotate(0deg);
    }
    100% {
      transform: rotate(360deg);
    }
  }
`;

const ColorLegend = styled.div`
  position: absolute;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  background: white;
  border-radius: 8px;
  padding: 12px 16px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  z-index: 500;
  display: flex;
  align-items: flex-start;
  gap: 20px;
  border: 1px solid #ddd;
  max-width: 90%;

  .no-data-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;

    .color-box {
      width: 40px;
      height: 12px;
      background: #fff;
      border: 1px solid #949494;
      border-radius: 2px;
    }

    span {
      font-size: 10px;
      color: #666;
    }
  }

  .layer-legend {
    display: flex;
    flex-direction: column;
    gap: 4px;

    .layer-name {
      font-size: 11px;
      font-weight: 600;
      color: #333;
      text-align: center;
    }

    .color-bins {
      display: flex;
      flex-direction: column;

      .color-row {
        display: flex;
      }

      .no-data-box {
        width: 40px;
        height: 12px;
        background: rgb(237, 237, 237);
        border-radius: 2px 0 0 2px;
      }

      .labels-row {
        display: flex;
        position: relative;
      }

      .color-box {
        width: 40px;
        height: 12px;
      }

      .color-box:first-child {
        border-radius: 2px 0 0 2px;
      }

      .color-box:last-child {
        border-radius: 0 2px 2px 0;
      }

      .bin-label {
        font-size: 9px;
        color: #666;
        white-space: nowrap;
        text-align: left;
        padding-top: 2px;
        width: 40px;
      }
    }
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
  const [hoveredItemName, setHoveredItemName] = useState<string | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const [isFullScreen, setIsFullScreen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedItemName, setSelectedItemName] = useState<string | null>(null);
  const [layerFilters, setLayerFilters] = useState<string[]>(['Retail']);
  const [isFilterLoading, setIsFilterLoading] = useState(false);
  const rootElem = createRef<HTMLDivElement>();
  const zoomPanRef = useRef<ZoomPanWrapperRef>(null);

  // Layer images mapping
  const layerImages: Record<string, string> = {
    Entrances: layerEntrances,
    Circulation: layerCirculation,
    Public: layerPublic,
    Retail: layerShops,
  };

  // Detect fullscreen mode by checking if the parent has position:fixed (Superset adds this when fullscreen)
  useEffect(() => {
    const checkFullScreen = () => {
      if (rootElem.current) {
        // Check if any parent has position fixed (indicates fullscreen in Superset dashboard)
        let parent = rootElem.current.parentElement;
        while (parent) {
          const style = window.getComputedStyle(parent);
          if (style.position === 'fixed' && style.zIndex === '3000') {
            setIsFullScreen(true);
            return;
          }
          parent = parent.parentElement;
        }
        setIsFullScreen(false);
      }
    };

    // Check initially and on resize
    checkFullScreen();

    // Use MutationObserver to detect style changes
    const observer = new MutationObserver(checkFullScreen);
    if (rootElem.current?.parentElement) {
      observer.observe(document.body, {
        attributes: true,
        subtree: true,
        attributeFilter: ['style', 'class'],
      });
    }

    return () => observer.disconnect();
  }, [height, width]);

  // Get the current floor image based on selection
  const currentFloorImage = floorImages[floorSelection] || floorImages.C;

  // Helper function to map category to layer
  const getCategoryLayer = (category: string | undefined | null): string => {
    if (!category) return 'Retail';
    const cat = category.toLowerCase();
    if (cat.includes('entrance')) return 'Entrances';
    if (cat.includes('circulation')) return 'Circulation';
    if (cat.includes('public')) return 'Public';
    return 'Retail';
  };

  // Get unique items with their total footfall for the list widget
  const uniqueItems = React.useMemo(() => {
    if (!data || !Array.isArray(data)) return [];
    const itemMap = new Map();
    data.forEach((item: any) => {
      const itemName = item.name || 'Unknown';
      if (!itemMap.has(itemName)) {
        itemMap.set(itemName, {
          name: itemName,
          footfall: item.total_footfall || 0,
          category: item.category || '',
          layer: getCategoryLayer(item.category),
        });
      }
    });
    return Array.from(itemMap.values()).sort((a, b) => b.footfall - a.footfall);
  }, [data]);

  // Filter items based on search query and layer filter
  const filteredItems = React.useMemo(() => {
    // If no layers selected, return empty array
    if (layerFilters.length === 0) return [];

    let result = uniqueItems;

    // Apply layer filter (multiple selections)
    result = result.filter(item => layerFilters.includes(item.layer));

    // Apply search filter
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase().trim();
      result = result.filter(item => item.name.toLowerCase().includes(query));
    }

    return result;
  }, [uniqueItems, searchQuery, layerFilters]);

  // Calculate max footfall per layer for dynamic color scaling
  const maxFootfallByLayer = React.useMemo(() => {
    if (!data || !Array.isArray(data)) return {};
    const maxByLayer: Record<string, number> = {
      Retail: 0,
      Entrances: 0,
      Circulation: 0,
      Public: 0,
    };

    data.forEach((item: any) => {
      const layer = getCategoryLayer(item.category);
      const footfall = item.total_footfall || 0;
      // Only consider items that are in the current filter
      if (layerFilters.includes(layer) && footfall > maxByLayer[layer]) {
        maxByLayer[layer] = footfall;
      }
    });

    return maxByLayer;
  }, [data, layerFilters]);

  // Handle layer filter change with loading (toggle multiple selections)
  const handleLayerChange = (layer: string) => {
    setIsFilterLoading(true);
    setLayerFilters(prev => {
      if (prev.includes(layer)) {
        // Remove layer if already selected (allow empty selection)
        return prev.filter(l => l !== layer);
      } else {
        // Add layer to selection
        return [...prev, layer];
      }
    });
    // Simulate processing time for visual feedback
    setTimeout(() => {
      setIsFilterLoading(false);
    }, 300);
  };

  // Handle item click from the list - zoom to the item on the map
  const handleItemClick = (itemName: string) => {
    setSelectedItemName(itemName);
    // Find the item element and zoom to it
    const itemElementId = `store-polyline-${itemName.replace(/\s+/g, '-')}`;
    if (zoomPanRef.current) {
      zoomPanRef.current.zoomToElement(itemElementId, 2.5);
    }
  };

  const handleItemHoverEnter = (
    itemName: string,
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
    setHoveredItemName(itemName);
  };

  const handleItemHoverLeave = () => {
    setHoveredItemName(null);
  };

  const handleMapClick = () => {
    setHoveredItemName(null);
    setSelectedItemName(null);
  };

  // Get the first item data for the hovered item name (to show only one tooltip)
  const displayedItem =
    hoveredItemName && data && Array.isArray(data)
      ? data.find((item: any) => item.name === hoveredItemName)
      : null;

  // Get selected item data for tooltip when an item is focused/selected
  const selectedItemData =
    selectedItemName && data && Array.isArray(data)
      ? data.find((item: any) => item.name === selectedItemName)
      : null;

  // Calculate tooltip position for selected item
  const [selectedTooltipPos, setSelectedTooltipPos] = useState({ x: 0, y: 0 });

  // Update selected tooltip position when item is selected
  useEffect(() => {
    if (selectedItemName && rootElem.current) {
      // Small delay to allow zoom animation to complete
      const updatePosition = () => {
        const parentRect = rootElem.current?.getBoundingClientRect();
        if (parentRect) {
          // Position tooltip in the center of the viewport
          setSelectedTooltipPos({
            x: parentRect.width / 2,
            y: parentRect.height / 2,
          });
        }
      };

      // Run immediately and after a delay to catch zoom animation
      updatePosition();
      const timer = setTimeout(updatePosition, 500);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [selectedItemName, height, width]);

  return (
    <Styles
      ref={rootElem}
      boldText={props.boldText}
      headerFontSize={props.headerFontSize}
      height={height}
      width={width}
    >
      <ZoomPanWrapper ref={zoomPanRef}>
        <svg
          xmlns="http://www.w3.org/2000/svg"
          xmlnsXlink="http://www.w3.org/1999/xlink"
          viewBox="0 0 5700 3800"
          width="100%"
          height="100%"
          style={{ display: 'block', position: 'relative' }}
          preserveAspectRatio="xMidYMid meet"
          onClick={handleMapClick}
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
            data.map((item: any, index: number) => {
              const itemName = item.name || 'Unknown';
              const itemLayer = getCategoryLayer(item.category);
              const isItemHovered = hoveredItemName === itemName;
              const isItemSelected = selectedItemName === itemName;

              // Filter polylines based on layer selection (multiple)
              if (
                layerFilters.length === 0 ||
                !layerFilters.includes(itemLayer)
              ) {
                return null;
              }

              return (
                <g
                  key={index}
                  id={`store-polyline-${itemName.replace(/\s+/g, '-')}`}
                >
                  <StorePolyline
                    store={item}
                    index={index}
                    isHovered={isItemHovered || isItemSelected}
                    onHoverEnter={e => handleItemHoverEnter(itemName, e)}
                    onHoverLeave={handleItemHoverLeave}
                    layer={itemLayer}
                    maxFootfall={maxFootfallByLayer[itemLayer] || 1}
                  />
                </g>
              );
            })}
        </svg>
      </ZoomPanWrapper>
      {isFullScreen && uniqueItems.length > 0 && (
        <StoreListWidget>
          <div className="widget-header">Store Footfall</div>
          <input
            type="text"
            className="search-input"
            placeholder="Search stores..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
          <div className="layer-filter">
            {['Retail', 'Entrances', 'Circulation', 'Public'].map(layer => (
              <button
                key={layer}
                className={`layer-btn ${layerFilters.includes(layer) ? 'active' : ''}`}
                onClick={() => handleLayerChange(layer)}
              >
                {layerImages[layer] && (
                  <img src={layerImages[layer]} alt={layer} />
                )}
                {layer}
              </button>
            ))}
          </div>
          {isFilterLoading && (
            <div className="loading-overlay">
              <div className="loading-spinner"></div>
            </div>
          )}
          <div className="store-list">
            {filteredItems.length > 0 ? (
              filteredItems.map((item, index) => (
                <div
                  key={index}
                  className={`store-item ${selectedItemName === item.name ? 'selected' : ''}`}
                  style={{
                    borderLeftColor: getLayerFootfallColor(
                      item.footfall,
                      item.layer,
                      maxFootfallByLayer[item.layer] || 1,
                    ),
                  }}
                  onClick={() => handleItemClick(item.name)}
                >
                  <div className="store-name">{item.name}</div>
                  <div className="store-footfall">
                    <span className="label">Footfall:</span>
                    <span
                      className="value"
                      style={{
                        color: getLayerFootfallColor(
                          item.footfall,
                          item.layer,
                          maxFootfallByLayer[item.layer] || 1,
                        ),
                      }}
                    >
                      {item.footfall.toLocaleString()}
                    </span>
                  </div>
                </div>
              ))
            ) : (
              <div className="no-results">No stores found</div>
            )}
          </div>
        </StoreListWidget>
      )}
      {/* Show tooltip for hovered item */}
      {displayedItem && hoveredItemName !== null && (
        <TooltipBox isVisible={true} x={tooltipPos.x} y={tooltipPos.y}>
          <div className="store-name">{displayedItem.name}</div>
          {displayedItem.category && (
            <div className="category-section">
              <div className="category-label">Category</div>
              <div className="category-name">{displayedItem.category}</div>
            </div>
          )}
          {displayedItem.total_footfall !== undefined &&
            displayedItem.total_footfall !== null && (
              <div className="footfall-section">
                <div className="footfall-label">Footfall</div>
                <div
                  className="footfall-value"
                  style={{
                    color: getLayerFootfallColor(
                      displayedItem.total_footfall as number,
                      getCategoryLayer(displayedItem.category as string),
                      maxFootfallByLayer[
                        getCategoryLayer(displayedItem.category as string)
                      ] || 1,
                    ),
                  }}
                >
                  {(displayedItem.total_footfall as number).toLocaleString()}
                </div>
              </div>
            )}
        </TooltipBox>
      )}
      {/* Show tooltip for selected item (when clicking from list) */}
      {selectedItemData && !hoveredItemName && (
        <TooltipBox
          isVisible={true}
          x={selectedTooltipPos.x}
          y={selectedTooltipPos.y}
        >
          <div className="store-name">{selectedItemData.name}</div>
          {selectedItemData.category && (
            <div className="category-section">
              <div className="category-label">Category</div>
              <div className="category-name">{selectedItemData.category}</div>
            </div>
          )}
          {selectedItemData.total_footfall !== undefined &&
            selectedItemData.total_footfall !== null && (
              <div className="footfall-section">
                <div className="footfall-label">Footfall</div>
                <div
                  className="footfall-value"
                  style={{
                    color: getLayerFootfallColor(
                      selectedItemData.total_footfall as number,
                      getCategoryLayer(selectedItemData.category as string),
                      maxFootfallByLayer[
                        getCategoryLayer(selectedItemData.category as string)
                      ] || 1,
                    ),
                  }}
                >
                  {(selectedItemData.total_footfall as number).toLocaleString()}
                </div>
              </div>
            )}
        </TooltipBox>
      )}
      {/* Color Legend */}
      {isFullScreen && layerFilters.length > 0 && (
        <ColorLegend>
          {layerFilters.map(layer => {
            const maxVal = maxFootfallByLayer[layer] || 0;
            const bins = getColorBins(maxVal, layer);
            return (
              <div key={layer} className="layer-legend">
                <div className="layer-name">{layer}</div>
                <div className="color-bins">
                  <div className="color-row">
                    <div className="no-data-box"></div>
                    {bins.map((bin, idx) => (
                      <div
                        key={idx}
                        className="color-box"
                        style={{ backgroundColor: bin.color }}
                      ></div>
                    ))}
                  </div>
                  <div className="labels-row">
                    <span className="bin-label">0</span>
                    {bins.map((bin, idx) => (
                      <span key={idx} className="bin-label">
                        {bin.label}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            );
          })}
        </ColorLegend>
      )}
    </Styles>
  );
}
