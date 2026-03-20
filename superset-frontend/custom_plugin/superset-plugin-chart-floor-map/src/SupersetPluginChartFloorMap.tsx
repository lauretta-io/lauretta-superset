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
import {
  BarChartOutlined,
  FontSizeOutlined,
  SortAscendingOutlined,
  SortDescendingOutlined,
} from '@ant-design/icons';
import { styled, SupersetClient } from '@superset-ui/core';
import {
  SupersetPluginChartFloorMapProps,
  SupersetPluginChartFloorMapStylesProps,
  ViewMode,
} from './types';
import {
  StorePolyline,
  getLayerFootfallColor,
  getColorBins,
} from './StorePolyline';
import { ZoomPanWrapper, ZoomPanWrapperRef } from './ZoomPanWrapper';
import {
  HeatmapLayer,
  HeatmapLegend,
  computeCentroid,
  computePolygonArea,
} from './HeatmapLayer';

const LAURETTA_IMAGE_API_PREFIX = '/api/v1/lauretta/images/';

// Floor image URL using the image filename from config.json (e.g., "TRX_floorplan_CF.jpeg")
const getFloorImageUrl = (imageFilename?: string): string => {
  const value = imageFilename?.trim() || '';
  if (!value) return '';
  if (/^https?:\/\//i.test(value)) {
    return value;
  }
  if (value.startsWith('/')) {
    return value;
  }
  if (value.startsWith('api/v1/lauretta/images/')) {
    return `/${value}`;
  }

  return `${LAURETTA_IMAGE_API_PREFIX}${encodeURIComponent(value)}`;
};

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

  .content-layout {
    width: 100%;
    height: 100%;
    min-height: 0;
    display: flex;
  }

  .content-layout.split-view {
    display: grid;
    grid-template-columns: 2fr 10fr;
  }

  .map-panel {
    position: relative;
    min-width: 0;
    min-height: 0;
    display: flex;
    flex: 1;
  }

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
  transform: translate(-50%, -100%);
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
  position: relative;
  left: auto;
  top: auto;
  background: white;
  border-radius: 0;
  padding: 12px;
  box-shadow: none;
  border-right: 1px solid #e0e0e0;
  z-index: 2;
  height: 100%;
  max-height: 100%;
  width: 100%;
  display: flex;
  flex-direction: column;
  overflow-x: hidden;

  .widget-header {
    font-size: 14px;
    font-weight: bold;
    color: #333;
  }

  .widget-header-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
    padding-bottom: 8px;
    border-bottom: 1px solid #e0e0e0;
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

  .sort-controls {
    display: flex;
    gap: 4px;
    margin-bottom: 0;
  }

  .sort-btn {
    width: 30px;
    height: 26px;
    padding: 0;
    font-size: 12px;
    line-height: 1;
    border: 1px solid #d9d9d9;
    border-radius: 4px;
    background: #fff;
    color: #666;
    cursor: pointer;
    transition: all 0.2s ease;
    display: flex;
    align-items: center;
    justify-content: center;

    .sort-icon-pair {
      display: flex;
      align-items: center;
      gap: 2px;

      .direction-icon {
        font-size: 10px;
      }
    }

    &:hover {
      border-color: #40a9ff;
      color: #40a9ff;
    }

    &.active {
      background: #e6f7ff;
      border-color: #1890ff;
      color: #1890ff;
      font-weight: 600;
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

const ViewToggle = styled.div`
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 600;
  display: flex;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 20px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
  border: 1px solid #e0e0e0;
  overflow: hidden;
  user-select: none;

  .toggle-btn {
    padding: 6px 18px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    border: none;
    background: transparent;
    color: #595959;
    transition: all 0.2s ease;
    white-space: nowrap;
    letter-spacing: 0.3px;

    &:hover {
      color: #1890ff;
      background: rgba(24, 144, 255, 0.06);
    }

    &.active {
      background: #1890ff;
      color: #fff;
      font-weight: 600;
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
  // height and width are the height and width of the DOM element as it exists in the dashboard.
  // There is also a `data` prop, which is, of course, your DATA 🎉
  const { data, height, width, floorImage, floorSelection } = props;
  const ALL_LAYERS = ['Retail', 'Entrances', 'Circulation', 'Public'];
  const [viewMode, setViewMode] = useState<ViewMode>('polygon');
  const [hoveredItemName, setHoveredItemName] = useState<string | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const [isFullScreen, setIsFullScreen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [sortField, setSortField] = useState<'name' | 'footfall'>('footfall');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
  const [selectedItemName, setSelectedItemName] = useState<string | null>(null);
  // Separate filter states for each view mode
  const [polygonLayerFilters, setPolygonLayerFilters] = useState<string[]>(['Retail']);
  const [heatmapLayerFilters, setHeatmapLayerFilters] = useState<string[]>(ALL_LAYERS);

  // Active filters depend on current view mode
  const layerFilters = viewMode === 'heatmap' ? heatmapLayerFilters : polygonLayerFilters;
  // Heatmap hover state
  const [heatmapHoveredName, setHeatmapHoveredName] = useState<string | null>(null);
  const [heatmapTooltipPos, setHeatmapTooltipPos] = useState({ x: 0, y: 0 });
  const [isFilterLoading, setIsFilterLoading] = useState(false);
  const [floorsData, setFloorsData] = useState<
    { name: string; image: string }[]
  >([]);
  const [imageDimensions, setImageDimensions] = useState<{
    width: number;
    height: number;
  } | null>(null);
  const rootElem = createRef<HTMLDivElement>();
  const mapPanelRef = useRef<HTMLDivElement>(null);
  const zoomPanRef = useRef<ZoomPanWrapperRef>(null);

  // Fetch floors from config.json via backend API (used to resolve image when floor_image is not set)
  useEffect(() => {
    SupersetClient.get({ endpoint: '/api/v1/lauretta/floors' })
      .then(({ json }) => setFloorsData((json as any[]) || []))
      .catch(() => {});
  }, []);

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

  // Resolve floor image: prefer explicit floorImage, then look up from fetched floors data
  const resolvedImage =
    floorImage ||
    floorsData.find(f => f.name === floorSelection)?.image ||
    floorSelection;
  const currentFloorImage = getFloorImageUrl(resolvedImage);
  console.log('CURRENT FLOOR IMAGE: ', currentFloorImage);

  // Load floor image to detect its natural dimensions (so viewBox matches the real image)
  useEffect(() => {
    if (!currentFloorImage) {
      setImageDimensions(null);
      return;
    }
    const img = new Image();
    img.onload = () => {
      setImageDimensions({
        width: img.naturalWidth,
        height: img.naturalHeight,
      });
    };
    img.onerror = () => {
      setImageDimensions(null);
    };
    img.src = currentFloorImage;
  }, [currentFloorImage]);

  // Derived: use real image dimensions, fallback to 5700x3800 if not yet loaded
  const imgW = imageDimensions?.width ?? 5700;
  const imgH = imageDimensions?.height ?? 3800;

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
          total_footfall_zo: item.total_footfall_zo || 0,
          category: item.category || '',
          layer: getCategoryLayer(item.category),
        });
      }
    });
    return Array.from(itemMap.values()).sort(
      (a, b) => b.total_footfall_zo - a.total_footfall_zo,
    );
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

    result = [...result].sort((a, b) => {
      if (sortField === 'name') {
        const nameCompare = a.name.localeCompare(b.name, undefined, {
          sensitivity: 'base',
        });
        return sortDirection === 'asc' ? nameCompare : -nameCompare;
      }

      const footfallA = a.total_footfall_zo || 0;
      const footfallB = b.total_footfall_zo || 0;
      return sortDirection === 'asc'
        ? footfallA - footfallB
        : footfallB - footfallA;
    });

    return result;
  }, [uniqueItems, searchQuery, layerFilters, sortField, sortDirection]);

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
      const footfall = item.total_footfall_zo || 0;
      // Only consider items that are in the current filter
      if (layerFilters.includes(layer) && footfall > maxByLayer[layer]) {
        maxByLayer[layer] = footfall;
      }
    });

    return maxByLayer;
  }, [data, layerFilters]);

  // Build heatmap points from polygon centroids
  const heatmapPoints = React.useMemo(() => {
    if (!data || !Array.isArray(data)) return [];

    const rawPts = data
      .map((item: any) => {
        const centroid = computeCentroid(item.points || '');
        if (!centroid) return null;
        const area = computePolygonArea(item.points || '');
        return {
          x: centroid.x,
          y: centroid.y,
          weight: item.total_footfall_zo || 0,
          name: item.name || 'Unknown',
          category: item.category || '',
          polygonArea: area,
          rawPoints: item.points || '',
        };
      })
      .filter(Boolean) as {
      x: number;
      y: number;
      weight: number;
      name: string;
      category: string;
      polygonArea: number;
      rawPoints: string;
    }[];

    if (rawPts.length === 0) return [];

    // Detect the actual coordinate range from all centroids
    const allX = rawPts.map(p => p.x);
    const allY = rawPts.map(p => p.y);
    const minX = Math.min(...allX);
    const maxX = Math.max(...allX);
    const minY = Math.min(...allY);
    const maxY = Math.max(...allY);
    const rangeX = maxX - minX || 1;
    const rangeY = maxY - minY || 1;

    // If points coords are already in SVG viewBox scale (e.g. 0..5700 x 0..3800),
    // use them directly. Otherwise scale/normalize them onto the viewBox.
    // Heuristic: if maxX > imgW * 0.1 OR maxY > imgH * 0.1 → already in pixel space.
    const alreadyInPixelSpace = maxX > imgW * 0.1 || maxY > imgH * 0.1;

    if (alreadyInPixelSpace) {
      return rawPts;
    }

    // Points are in a different coordinate system → scale to fill the viewBox.
    const scaleX = imgW / rangeX;
    const scaleY = imgH / rangeY;
    const areaScale = scaleX * scaleY; // area scales by product of linear scales
    return rawPts.map(p => ({
      ...p,
      x: ((p.x - minX) / rangeX) * imgW,
      y: ((p.y - minY) / rangeY) * imgH,
      polygonArea: p.polygonArea * areaScale,
    }));
  }, [data, imgW, imgH]);

  // Global max footfall for heatmap legend (across all active layers)
  const heatmapMaxFootfall = React.useMemo(
    () => Math.max(...heatmapPoints.map(p => p.weight), 1),
    [heatmapPoints],
  );

  const heatmapMinFootfall = React.useMemo(
    () =>
      Math.min(
        ...heatmapPoints.filter(p => p.weight > 0).map(p => p.weight),
        0,
      ),
    [heatmapPoints],
  );

  // Handle layer filter change with loading (toggle multiple selections)
  // Works for both polygon and heatmap modes independently
  const handleLayerChange = (layer: string) => {
    setIsFilterLoading(true);
    const setter = viewMode === 'heatmap' ? setHeatmapLayerFilters : setPolygonLayerFilters;
    setter((prev: string[]) => {
      if (prev.includes(layer)) {
        return prev.filter((l: string) => l !== layer);
      }
      return [...prev, layer];
    });
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

  const handleSortChange = (field: 'name' | 'footfall') => {
    if (sortField === field) {
      setSortDirection(prev => (prev === 'asc' ? 'desc' : 'asc'));
      return;
    }

    setSortField(field);
    setSortDirection(field === 'name' ? 'asc' : 'desc');
  };

  const handleItemHoverEnter = (
    itemName: string,
    event: React.MouseEvent<SVGPolygonElement>,
  ) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const parentRect =
      mapPanelRef.current?.getBoundingClientRect() ||
      rootElem.current?.getBoundingClientRect();

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

  const showStorePanel = isFullScreen && uniqueItems.length > 0;

  // Calculate tooltip position for selected item
  const [selectedTooltipPos, setSelectedTooltipPos] = useState({ x: 0, y: 0 });

  // Update selected tooltip position based on the actual polygon element's screen position
  useEffect(() => {
    if (selectedItemName) {
      const updatePosition = () => {
        const itemElementId = `store-polyline-${selectedItemName.replace(/\s+/g, '-')}`;
        const element = document.getElementById(itemElementId);
        const parentRect =
          mapPanelRef.current?.getBoundingClientRect() ||
          rootElem.current?.getBoundingClientRect();

        if (element && parentRect) {
          const rect = element.getBoundingClientRect();
          setSelectedTooltipPos({
            x: rect.left - parentRect.left + rect.width / 2,
            y: rect.top - parentRect.top,
          });
        }
      };

      // Wait for the zoom animation to finish before measuring position
      const timer = setTimeout(updatePosition, 600);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [selectedItemName, height, width, showStorePanel]);

  return (
    <Styles
      ref={rootElem}
      boldText={props.boldText}
      headerFontSize={props.headerFontSize}
      height={height}
      width={width}
    >
      <div className={`content-layout ${showStorePanel ? 'split-view' : ''}`}>
        {showStorePanel && (
          <StoreListWidget>
            <div className="widget-header-row">
              <div className="widget-header">Store Footfall</div>
              <div className="sort-controls">
                <button
                  type="button"
                  className={`sort-btn ${sortField === 'name' ? 'active' : ''}`}
                  onClick={() => handleSortChange('name')}
                  title="Sort by name"
                  aria-label="Sort by name"
                >
                  {sortField === 'name' ? (
                    sortDirection === 'asc' ? (
                      <span className="sort-icon-pair">
                        <FontSizeOutlined />
                        <SortAscendingOutlined className="direction-icon" />
                      </span>
                    ) : (
                      <span className="sort-icon-pair">
                        <FontSizeOutlined />
                        <SortDescendingOutlined className="direction-icon" />
                      </span>
                    )
                  ) : (
                    <FontSizeOutlined />
                  )}
                </button>
                <button
                  type="button"
                  className={`sort-btn ${sortField === 'footfall' ? 'active' : ''}`}
                  onClick={() => handleSortChange('footfall')}
                  title="Sort by footfall"
                  aria-label="Sort by footfall"
                >
                  {sortField === 'footfall' ? (
                    sortDirection === 'asc' ? (
                      <span className="sort-icon-pair">
                        <BarChartOutlined />
                        <SortAscendingOutlined className="direction-icon" />
                      </span>
                    ) : (
                      <span className="sort-icon-pair">
                        <BarChartOutlined />
                        <SortDescendingOutlined className="direction-icon" />
                      </span>
                    )
                  ) : (
                    <BarChartOutlined />
                  )}
                </button>
              </div>
            </div>
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
                        item.total_footfall_zo,
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
                            item.total_footfall_zo,
                            item.layer,
                            maxFootfallByLayer[item.layer] || 1,
                          ),
                        }}
                      >
                        {item.total_footfall_zo.toLocaleString()}
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

        <div className="map-panel" ref={mapPanelRef}>
          {/* View mode toggle: Polygon ↔ Heatmap */}
          <ViewToggle>
            <button
              type="button"
              className={`toggle-btn ${viewMode === 'polygon' ? 'active' : ''}`}
              onClick={() => setViewMode('polygon')}
            >
              Polygon
            </button>
            <button
              type="button"
              className={`toggle-btn ${viewMode === 'heatmap' ? 'active' : ''}`}
              onClick={() => {
                // Reset heatmap filters to ALL when switching to heatmap
                setHeatmapLayerFilters(ALL_LAYERS);
                setViewMode('heatmap');
              }}
            >
              Heatmap
            </button>
          </ViewToggle>

          <ZoomPanWrapper ref={zoomPanRef}>
            <svg
              xmlns="http://www.w3.org/2000/svg"
              xmlnsXlink="http://www.w3.org/1999/xlink"
              viewBox={`0 0 ${imgW} ${imgH}`}
              width="100%"
              height="100%"
              style={{ display: 'block', position: 'relative' }}
              preserveAspectRatio="xMidYMid meet"
              onClick={handleMapClick}
            >
              <image
                x="0"
                y="0"
                width={imgW}
                height={imgH}
                xlinkHref={currentFloorImage}
                imageRendering="crisp-edges"
                style={{ pointerEvents: 'none', zIndex: 1 }}
              />
              {/* Polygon mode */}
              {viewMode === 'polygon' &&
                data &&
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
              {/* Heatmap mode */}
              {viewMode === 'heatmap' && (
                <HeatmapLayer
                  points={heatmapPoints}
                  imgW={imgW}
                  imgH={imgH}
                  layerFilters={heatmapLayerFilters}
                  onHoverEnter={(name, footfall, svgX, svgY, e) => {
                    const parentRect =
                      mapPanelRef.current?.getBoundingClientRect();
                    if (parentRect) {
                      const rect = (e.target as SVGCircleElement).getBoundingClientRect();
                      setHeatmapTooltipPos({
                        x: rect.left - parentRect.left + rect.width / 2,
                        y: rect.top - parentRect.top,
                      });
                    }
                    setHeatmapHoveredName(name);
                  }}
                  onHoverLeave={() => setHeatmapHoveredName(null)}
                />
              )}
            </svg>
          </ZoomPanWrapper>

          {/* Show tooltip for hovered item (polygon mode only) */}
          {viewMode === 'polygon' && displayedItem && hoveredItemName !== null && (
            <TooltipBox isVisible={true} x={tooltipPos.x} y={tooltipPos.y}>
              <div className="store-name">{displayedItem.name}</div>
              {displayedItem.category && (
                <div className="category-section">
                  <div className="category-label">Category</div>
                  <div className="category-name">{displayedItem.category}</div>
                </div>
              )}
              {displayedItem.total_footfall_zo !== undefined &&
                displayedItem.total_footfall_zo !== null && (
                  <div className="footfall-section">
                    <div className="footfall-label">Footfall</div>
                    <div
                      className="footfall-value"
                      style={{
                        color: 'black',
                      }}
                    >
                      {(
                        displayedItem.total_footfall_zo as number
                      ).toLocaleString()}
                    </div>
                  </div>
                )}
            </TooltipBox>
          )}

          {/* Heatmap hover tooltip */}
          {viewMode === 'heatmap' && heatmapHoveredName && (() => {
            const item = data && Array.isArray(data)
              ? (data as any[]).find(d => d.name === heatmapHoveredName)
              : null;
            return (
              <TooltipBox isVisible={true} x={heatmapTooltipPos.x} y={heatmapTooltipPos.y}>
                <div className="store-name">{heatmapHoveredName}</div>
                {item?.category && (
                  <div className="category-section">
                    <div className="category-label">Category</div>
                    <div className="category-name">{item.category}</div>
                  </div>
                )}
                <div className="footfall-section">
                  <div className="footfall-label">Footfall</div>
                  <div className="footfall-value" style={{ color: 'black' }}>
                    {(item?.total_footfall_zo as number ?? 0).toLocaleString()}
                  </div>
                </div>
              </TooltipBox>
            );
          })()}

          {/* Show tooltip for selected item (polygon mode only) */}
          {viewMode === 'polygon' && selectedItemData && !hoveredItemName && (
            <TooltipBox
              isVisible={true}
              x={selectedTooltipPos.x}
              y={selectedTooltipPos.y}
            >
              <div className="store-name">{selectedItemData.name}</div>
              {selectedItemData.category && (
                <div className="category-section">
                  <div className="category-label">Category</div>
                  <div className="category-name">
                    {selectedItemData.category}
                  </div>
                </div>
              )}
              {selectedItemData.total_footfall_zo !== undefined &&
                selectedItemData.total_footfall_zo !== null && (
                  <div className="footfall-section">
                    <div className="footfall-label">Footfall</div>
                    <div
                      className="footfall-value"
                      style={{
                        color: 'black',
                      }}
                    >
                      {(
                        selectedItemData.total_footfall_zo as number
                      ).toLocaleString()}
                    </div>
                  </div>
                )}
            </TooltipBox>
          )}

          {/* Heatmap legend (always visible in heatmap mode) */}
          {viewMode === 'heatmap' && (
            <HeatmapLegend
              minVal={heatmapMinFootfall}
              maxVal={heatmapMaxFootfall}
            />
          )}

          {/* Color Legend (polygon mode only) */}
          {viewMode === 'polygon' && isFullScreen && layerFilters.length > 0 && (
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
        </div>
      </div>
    </Styles>
  );
}
